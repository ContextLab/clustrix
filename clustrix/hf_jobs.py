"""HuggingFace Jobs execution backend.

HF Jobs runs a container, executes a command, and exits. That is exactly
clustrix's model -- hand over a function, run it, collect a result -- which is
why this backend exists alongside the SSH and scheduler ones, and why it is
the substrate the integration tests run against: it needs no cluster
reservation, no VPN and no institutional SSH credentials.

How a job is carried out
------------------------
There is no shared filesystem between here and the container, so the function
travels inside the job itself:

1. The function, its arguments and its keyword arguments are serialized with
   dill and base64-encoded into the ``CLUSTRIX_PAYLOAD`` environment variable.
2. The container runs a small bootstrap that installs dill, decodes the
   payload, calls the function, and prints the base64 of the dill-serialized
   result between two marker lines.
3. This side reads the job's logs and decodes what is between the markers.

Deserializing a dill payload executes arbitrary code, so the result is not
trusted on sight. Each job is given a fresh random key as a *secret*; the
bootstrap prints an HMAC-SHA256 of the bytes it emitted, and this side refuses
to unpickle anything whose tag does not verify. That bounds the trust to
"whoever holds the per-job key", rather than "whoever can write into the log
stream we happen to read". (The same weakness in the SSH and scheduler paths
is tracked as #121.)

Because dill embeds CPython bytecode, which is not portable across minor
versions, the container image defaults to the *local* interpreter's version.
Overriding ``hf_image`` with a mismatched Python is the single most likely way
to get an "unknown opcode" failure out of this backend.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import dill

    DILL_AVAILABLE = True
except ImportError:  # pragma: no cover - dill is a hard dependency of clustrix
    DILL_AVAILABLE = False

try:
    from huggingface_hub import HfApi

    HF_AVAILABLE = True
except ImportError:
    HfApi = None  # type: ignore[assignment,misc]
    HF_AVAILABLE = False


RESULT_BEGIN = "---CLUSTRIX-RESULT-BEGIN---"
RESULT_END = "---CLUSTRIX-RESULT-END---"
ERROR_BEGIN = "---CLUSTRIX-ERROR-BEGIN---"
ERROR_END = "---CLUSTRIX-ERROR-END---"

#: Flavors that cost real money. Selecting one requires an explicit opt-in so
#: that a stray ``cores=64`` in a notebook cannot quietly rent an H100.
GPU_FLAVORS = frozenset(
    {
        "t4-small",
        "t4-medium",
        "l4x1",
        "l4x4",
        "l40sx1",
        "l40sx4",
        "l40sx8",
        "a10g-small",
        "a10g-large",
        "a10g-largex2",
        "a10g-largex4",
        "a100-large",
        "h100",
        "h100x8",
    }
)

DEFAULT_FLAVOR = "cpu-basic"
DEFAULT_TIMEOUT = "30m"

#: HF rejects very large environment variables. Well under any documented
#: limit, but large enough for a function plus modest arguments; anything
#: bigger should be read from a dataset inside the job rather than shipped.
MAX_PAYLOAD_BYTES = 256 * 1024


def _bootstrap_source() -> str:
    """The program the container runs.

    Kept as a single ``python -c`` string so a job needs no uploaded files.
    Everything it emits is tagged with an HMAC over the emitted bytes, keyed
    by the per-job secret, so the caller can tell its own output apart from
    anything else that reaches the log stream.
    """
    return (
        "import base64,hashlib,hmac,os,subprocess,sys\n"
        "subprocess.run([sys.executable,'-m','pip','install','-q','dill'],check=True)\n"
        "import dill\n"
        "k=os.environ['CLUSTRIX_HMAC_KEY'].encode()\n"
        "def emit(begin,end,obj):\n"
        "    b=dill.dumps(obj)\n"
        "    print(begin)\n"
        "    print(hmac.new(k,b,hashlib.sha256).hexdigest())\n"
        "    print(base64.b64encode(b).decode())\n"
        "    print(end)\n"
        "import pickle\n"
        "p=dill.loads(base64.b64decode(os.environ['CLUSTRIX_PAYLOAD']))\n"
        "try:\n"
        "    f=dill.loads(p['function'])\n"
        "    a=pickle.loads(p['args'])\n"
        "    kw=pickle.loads(p['kwargs'])\n"
        "    r=f(*a,**kw)\n"
        f"    emit('{RESULT_BEGIN}','{RESULT_END}',r)\n"
        "except Exception as e:\n"
        "    import traceback\n"
        f"    emit('{ERROR_BEGIN}','{ERROR_END}',"
        "{'error':str(e),'traceback':traceback.format_exc()})\n"
        "    raise\n"
    )


class HFJobsManager:
    """Submit clustrix functions to HuggingFace Jobs and collect their results."""

    def __init__(self, config):
        self.config = config
        self._api: Optional[Any] = None
        self._jobs: Dict[str, Dict[str, Any]] = {}

    # -- setup ---------------------------------------------------------

    @property
    def api(self):
        """The authenticated ``HfApi``, created on first use."""
        if self._api is None:
            if not HF_AVAILABLE:
                raise RuntimeError(
                    "huggingface_hub is not installed. "
                    'Install it with: pip install "clustrix[huggingface]"'
                )
            token = getattr(self.config, "hf_token", None)
            if not token:
                raise RuntimeError(
                    "No HuggingFace token configured. Set hf_token in your "
                    "clustrix config, or export HF_TOKEN."
                )
            self._api = HfApi(token=token)
        return self._api

    def _namespace(self) -> Optional[str]:
        return getattr(self.config, "hf_namespace", None) or getattr(
            self.config, "hf_username", None
        )

    def _image(self) -> str:
        """Container image, defaulting to the local Python minor version.

        dill payloads carry CPython bytecode, so the container has to run the
        same minor version as the caller or unpickling raises "unknown opcode".
        """
        configured = getattr(self.config, "hf_image", None)
        if configured:
            return configured
        return f"python:{sys.version_info.major}.{sys.version_info.minor}-slim"

    def _flavor(self, job_config: Dict[str, Any]) -> str:
        flavor = (
            job_config.get("hf_flavor")
            or getattr(self.config, "hf_flavor", None)
            or getattr(self.config, "hf_hardware", None)
            or DEFAULT_FLAVOR
        )
        if flavor in GPU_FLAVORS and not getattr(
            self.config, "hf_allow_gpu_flavors", False
        ):
            raise ValueError(
                f"Flavor {flavor!r} is a paid GPU flavor. Set "
                "hf_allow_gpu_flavors=True to confirm you intend to be billed "
                f"for it; otherwise use one of the CPU flavors (default: "
                f"{DEFAULT_FLAVOR})."
            )
        return flavor

    # -- submission ----------------------------------------------------

    def submit_job(self, func_data: Dict[str, Any], job_config: Dict[str, Any]) -> str:
        """Serialize a function into a HuggingFace Job and start it."""
        if not DILL_AVAILABLE:
            raise RuntimeError("dill is required to submit HuggingFace Jobs")

        # serialize_function() has already done the work: "function" is dill
        # (or cloudpickle, or pickle) bytes and "args"/"kwargs" are pickle
        # bytes. Re-wrapping them rather than re-serializing keeps this
        # backend on exactly the same payload every other backend receives.
        payload = dill.dumps(
            {
                "function": func_data["function"],
                "args": func_data["args"],
                "kwargs": func_data["kwargs"],
            },
            protocol=4,
        )
        encoded = base64.b64encode(payload).decode()
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"Serialized function and arguments are {len(encoded)} bytes, "
                f"over the {MAX_PAYLOAD_BYTES}-byte limit for a HuggingFace "
                "Jobs payload. Pass large data through a Hub dataset and load "
                "it inside the function instead of closing over it."
            )

        flavor = self._flavor(job_config)
        timeout = (
            job_config.get("hf_timeout")
            or getattr(self.config, "hf_job_timeout", None)
            or DEFAULT_TIMEOUT
        )
        image = self._image()

        logger.info(
            "Submitting HuggingFace Job (image=%s flavor=%s namespace=%s payload=%dB)",
            image,
            flavor,
            self._namespace(),
            len(encoded),
        )

        # Fresh per job: the key only has to outlive this one result.
        hmac_key = secrets.token_hex(32)

        job = self.api.run_job(
            image=image,
            command=["python", "-c", _bootstrap_source()],
            env={"CLUSTRIX_PAYLOAD": encoded},
            secrets={"CLUSTRIX_HMAC_KEY": hmac_key},
            flavor=flavor,
            timeout=timeout,
            namespace=self._namespace(),
        )
        self._jobs[job.id] = {
            "flavor": flavor,
            "image": image,
            "hmac_key": hmac_key,
        }
        logger.info("HuggingFace Job %s submitted", job.id)
        return job.id

    # -- status and results --------------------------------------------

    def get_job_status(self, job_id: str) -> str:
        """Map an HF job stage onto clustrix's vocabulary."""
        info = self.api.inspect_job(job_id=job_id, namespace=self._namespace())
        stage = str(getattr(info.status, "stage", "") or "").upper()
        if stage in ("COMPLETED", "SUCCEEDED"):
            return "completed"
        if stage in ("ERROR", "FAILED", "CANCELED", "CANCELLED"):
            return "failed"
        if stage == "RUNNING":
            return "running"
        if stage in ("PENDING", "QUEUED", "UPDATING"):
            return "queued"
        logger.warning("Unrecognised HuggingFace job stage %r for %s", stage, job_id)
        return "unknown"

    def _decode_between(
        self, lines, begin: str, end: str, hmac_key: str
    ) -> Optional[Any]:
        """Decode the payload the bootstrap printed between two markers.

        The first line after the opening marker is the HMAC of the payload
        bytes. It is checked with a constant-time compare *before* anything is
        handed to dill, because unpickling is code execution: an unverified
        blob from a log stream is not something to deserialize.
        """
        collecting = False
        tag: Optional[str] = None
        chunks: List[str] = []
        for line in lines:
            line = line.strip()
            if line == begin:
                collecting = True
                continue
            if line == end:
                break
            if collecting and line:
                if tag is None:
                    tag = line
                else:
                    chunks.append(line)
        if tag is None or not chunks:
            return None

        raw = base64.b64decode("".join(chunks))
        expected = hmac.new(hmac_key.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag, expected):
            raise RuntimeError(
                "HuggingFace Job result failed its integrity check: the HMAC "
                "does not match the per-job key. Refusing to deserialize it."
            )
        return dill.loads(raw)

    def wait_for_result(self, job_id: str) -> Any:
        """Block until the job finishes, then return its result.

        Raises whatever the function raised remotely, with the remote
        traceback attached to the message -- a stack that ends inside a
        container is useless without it.
        """
        tracked = self._jobs.get(job_id)
        if tracked is None:
            raise RuntimeError(
                f"HuggingFace Job {job_id} was not submitted by this manager, "
                "so its per-job verification key is unknown and its result "
                "cannot be trusted."
            )
        hmac_key = tracked["hmac_key"]

        self.api.wait_for_job(job_id, namespace=self._namespace())

        logs = list(self.api.fetch_job_logs(job_id=job_id, namespace=self._namespace()))

        result = self._decode_between(logs, RESULT_BEGIN, RESULT_END, hmac_key)
        if result is not None:
            return result

        error = self._decode_between(logs, ERROR_BEGIN, ERROR_END, hmac_key)
        if error is not None:
            raise RuntimeError(
                f"HuggingFace Job {job_id} raised {error['error']}\n"
                f"Remote traceback:\n{error['traceback']}"
            )

        # No markers at all: the bootstrap never got far enough to print them.
        tail = "\n".join(logs[-30:]) if logs else "<no logs returned>"
        raise RuntimeError(
            f"HuggingFace Job {job_id} produced no clustrix result marker. "
            f"Last log lines:\n{tail}"
        )

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job. Returns True if the API accepted the request."""
        try:
            self.api.cancel_job(job_id=job_id, namespace=self._namespace())
            return True
        except Exception as e:
            logger.error("Could not cancel HuggingFace Job %s: %s", job_id, e)
            return False
