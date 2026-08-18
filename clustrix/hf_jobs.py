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
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import sys
import time
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


#: Sentinel distinguishing "no block found" from "the block held None".
#: A function that legitimately returns None must not look like a missing
#: result, or every such call fails with "produced no clustrix result marker".
_MISSING = object()

RESULT_BEGIN = "---CLUSTRIX-RESULT-BEGIN---"
RESULT_END = "---CLUSTRIX-RESULT-END---"
ERROR_BEGIN = "---CLUSTRIX-ERROR-BEGIN---"
ERROR_END = "---CLUSTRIX-ERROR-END---"

#: Flavors whose names start with this are CPU tiers. Everything else HF
#: offers is a GPU tier that bills by the second.
#:
#: The gate is a prefix test rather than a list of GPU names on purpose: HF
#: adds hardware regularly (a100x8, h200x8 and rtx-pro-6000x8 all postdate the
#: first draft of this file), and a stale denylist fails *open* -- it would
#: have waved through an h200x8 while carefully blocking an "h100" that does
#: not exist. A prefix test fails closed.
CPU_FLAVOR_PREFIX = "cpu-"


def is_gpu_flavor(flavor: str) -> bool:
    """True for any flavor that is not one of the CPU tiers."""
    return not str(flavor).startswith(CPU_FLAVOR_PREFIX)


DEFAULT_FLAVOR = "cpu-basic"
DEFAULT_TIMEOUT = "30m"

#: A job that has only just finished may still be flushing its log stream.
LOG_FETCH_ATTEMPTS = 3
LOG_FETCH_DELAY_SECONDS = 2.0

#: HF rejects very large environment variables. Well under any documented
#: limit, but large enough for a function plus modest arguments; anything
#: bigger should be read from a dataset inside the job rather than shipped.
MAX_PAYLOAD_BYTES = 256 * 1024


def _token_from_hf_cli_cache() -> Optional[str]:
    """The token `hf auth login` writes, if there is one.

    HF_HOME relocates that whole directory, so a user who sets it -- common on
    shared machines and clusters with small home quotas -- was being told to log
    in again despite already having done so.
    """
    hf_home = os.environ.get("HF_HOME")
    home = (
        hf_home
        if hf_home
        else os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    )
    try:
        with open(os.path.join(home, "token")) as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _constant_time_equals(candidate: str, expected: str) -> bool:
    """Constant-time compare that tolerates junk in the candidate.

    ``hmac.compare_digest`` raises TypeError on a non-ASCII str, and the
    candidate comes off a log stream, so it may be anything at all. A tag that
    cannot even be encoded is simply not a match.
    """
    try:
        return hmac.compare_digest(candidate.encode("ascii"), expected.encode("ascii"))
    except (UnicodeEncodeError, AttributeError):
        return False


def _bootstrap_source() -> str:
    """The program the container runs.

    Kept as a single ``python -c`` string so a job needs no uploaded files.
    Everything it emits is tagged with an HMAC over the emitted bytes, keyed
    by the per-job secret, so the caller can tell its own output apart from
    anything else that reaches the log stream.
    """
    return (
        "import base64,hashlib,hmac,os,subprocess,sys\n"
        # The base image carries nothing but Python. Everything the function
        # imports has to be installed here, so CLUSTRIX_PACKAGES names what
        # config.cluster_packages asked for -- the same field the SSH and SLURM
        # backends honour, so a function is not tied to one backend.
        "_pkgs=['dill','cloudpickle']+os.environ.get('CLUSTRIX_PACKAGES','').split()\n"
        "subprocess.run([sys.executable,'-m','pip','install','-q']+_pkgs,check=True)\n"
        "import cloudpickle\n"
        "import dill\n"
        "k=os.environ['CLUSTRIX_HMAC_KEY'].encode()\n"
        "def emit(begin,end,obj):\n"
        "    b=dill.dumps(obj)\n"
        "    print(begin)\n"
        "    print(hmac.new(k,b,hashlib.sha256).hexdigest())\n"
        "    print(base64.b64encode(b).decode())\n"
        "    print(end)\n"
        "p=dill.loads(base64.b64decode(os.environ['CLUSTRIX_PAYLOAD']))\n"
        "try:\n"
        "    try:\n"
        "        f=dill.loads(p['function'])\n"
        "    except Exception:\n"
        # serialize_function() falls back to cloudpickle when dill cannot
        # handle a function, so the container has to try both.
        "        f=cloudpickle.loads(p['function'])\n"
        # dill, not pickle: args may carry classes defined in the caller's
        # __main__, which stdlib pickle can only store by qualified name.
        "    a=dill.loads(p['args'])\n"
        "    kw=dill.loads(p['kwargs'])\n"
        "    r=f(*a,**kw)\n"
        f"    emit('{RESULT_BEGIN}','{RESULT_END}',r)\n"
        "except Exception as e:\n"
        "    import traceback\n"
        # The exception OBJECT travels alongside the message so the caller can
        # catch the type the function raised. An exception that will not
        # serialize must not suppress the error report itself.
        "    _p={'error':str(e),'traceback':traceback.format_exc()}\n"
        "    try:\n"
        "        dill.dumps(e)\n"
        "        _p['exception']=e\n"
        "    except Exception:\n"
        "        pass\n"
        f"    emit('{ERROR_BEGIN}','{ERROR_END}',_p)\n"
        # The traceback stays in the job log, but the process exits cleanly.
        # A function raising ValueError is an ordinary outcome that clustrix
        # re-raises locally with its original type -- not a failed job. Exiting
        # non-zero marked the job ERROR in the Hugging Face console and sent
        # the account owner a "status changed to ERROR" email for every such
        # exception. Only a failure to *report* is a job failure, and that path
        # still propagates: if emit() itself raises, this exit is never reached.
        "    traceback.print_exc()\n"
        "    sys.exit(0)\n"
    )


class RemoteExecutionError(RuntimeError):
    """The remote function raised. Distinct from a transport problem."""


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
                    "Install it with: pip install huggingface_hub"
                )
            # The error used to say "or export HF_TOKEN" while reading only the
            # config field, so following its own advice did not work. Honour
            # the environment variable it names -- and the one the HuggingFace
            # CLI itself writes.
            token = getattr(self.config, "hf_token", None) or os.environ.get("HF_TOKEN")
            if not token:
                token = _token_from_hf_cli_cache()
            if not token:
                raise RuntimeError(
                    "No HuggingFace token configured. Set hf_token in your "
                    "clustrix config, export HF_TOKEN, or run `hf auth login`."
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

    def _extra_packages(
        self, job_config: Dict[str, Any], requirements: Optional[Dict[str, str]] = None
    ) -> List[str]:
        """Packages to pip install in the container before the function runs.

        Two sources, both the same fields the SSH and SLURM backends read, so a
        dependency declared once works on every backend:

        * the local environment, mirrored when ``replicate_local_environment``
          is on, minus anything in ``excluded_packages``. The container starts
          from a bare Python image, so without this a function importing numpy
          fails here while running fine on a cluster;
        * ``cluster_packages``, for anything the local environment does not
          have. Entries may be bare names, pinned specs, or the dict form the
          SSH path accepts for pip options.

        Unlike the cluster backends, containers are ephemeral: this reinstalls
        on every job. Set ``replicate_local_environment=False`` when the
        function only needs the standard library.
        """
        excluded = {
            str(name).lower()
            for name in (getattr(self.config, "excluded_packages", None) or [])
        }
        packages: List[str] = []

        if getattr(self.config, "replicate_local_environment", True):
            for name, version in sorted((requirements or {}).items()):
                if name.lower() not in excluded:
                    packages.append(f"{name}=={version}")

        declared = job_config.get("cluster_packages") or getattr(
            self.config, "cluster_packages", None
        )
        for spec in declared or []:
            if isinstance(spec, str) and spec.strip():
                packages.append(spec.strip())
            elif isinstance(spec, dict) and spec.get("package"):
                packages.append(str(spec["package"]))

        # pip takes these as argv, so a name with a space would split into two.
        return [p for p in packages if " " not in p]

    def _flavor(self, job_config: Dict[str, Any]) -> str:
        flavor = (
            job_config.get("hf_flavor")
            or getattr(self.config, "hf_flavor", None)
            or getattr(self.config, "hf_hardware", None)
            or DEFAULT_FLAVOR
        )
        if is_gpu_flavor(flavor) and not getattr(
            self.config, "hf_allow_gpu_flavors", False
        ):
            raise ValueError(
                f"Flavor {flavor!r} is a GPU flavor and bills by the second. "
                "Set hf_allow_gpu_flavors=True to confirm you intend to pay "
                f"for it; otherwise use a CPU flavor (default: "
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
            # Say which part is big. The old message blamed "closing over" the
            # data, which is wrong as often as not: a module-level table in the
            # user's own package gets embedded with that package, and nothing
            # in the message pointed at it.
            breakdown = ", ".join(
                f"{part}={len(func_data[part])}B"
                for part in ("function", "args", "kwargs")
                if func_data.get(part)
            )
            raise ValueError(
                f"Serialized function and arguments are {len(encoded)} bytes, "
                f"over the {MAX_PAYLOAD_BYTES}-byte limit for a HuggingFace "
                f"Jobs payload ({breakdown}). Large arguments should be passed "
                "through a Hub dataset and loaded inside the function. A large "
                "'function' usually means a big module-level object in one of "
                "your project's modules, which is embedded along with the "
                "module: move it behind a function, or install the package so "
                "the worker imports it instead of receiving a copy."
            )

        flavor = self._flavor(job_config)
        timeout = (
            job_config.get("hf_timeout")
            or getattr(self.config, "hf_job_timeout", None)
            or DEFAULT_TIMEOUT
        )
        image = self._image()
        packages = " ".join(
            self._extra_packages(job_config, func_data.get("requirements"))
        )

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
            env={"CLUSTRIX_PAYLOAD": encoded, "CLUSTRIX_PACKAGES": packages},
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

    def _decode_between(self, lines, begin: str, end: str, hmac_key: str) -> Any:
        """Decode the payload the bootstrap printed between two markers.

        Returns ``_MISSING`` when the block is absent, so that a function
        which legitimately returned ``None`` is not mistaken for a job that
        produced nothing.

        The first line after the opening marker is the HMAC of the payload
        bytes, checked with a constant-time compare *before* anything reaches
        dill, because unpickling is code execution.

        Both markers are only honoured while actually inside a block. A
        function is free to print anything it likes, including a line that
        happens to equal one of these markers, and that must not be able to
        truncate or hijack the real block.
        """
        collecting = False
        tag = None
        chunks: List[str] = []
        for line in lines:
            line = line.strip()
            if not collecting:
                if line == begin:
                    collecting = True
                continue
            if line == end:
                break
            if line:
                if tag is None:
                    tag = line
                else:
                    chunks.append(line)
        if tag is None or not chunks:
            return _MISSING

        try:
            raw = base64.b64decode("".join(chunks), validate=True)
        except (ValueError, binascii.Error) as e:
            # Truncated or interleaved logs, not an attack. Saying "integrity
            # check failed" here would send someone hunting a forgery that
            # never happened.
            raise RuntimeError(
                "HuggingFace Job result could not be decoded; the log stream "
                f"appears truncated or interleaved ({e}). Re-run the job."
            ) from e

        expected = hmac.new(hmac_key.encode(), raw, hashlib.sha256).hexdigest()
        if not _constant_time_equals(tag, expected):
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

        # fetch_job_logs returns whatever is available now, and a job that has
        # only just finished may still be flushing. A truncated tail looks
        # exactly like a missing result, so give it a couple of chances before
        # concluding the job produced nothing.
        last_error: Optional[Exception] = None
        logs: list = []
        for attempt in range(LOG_FETCH_ATTEMPTS):
            if attempt:
                time.sleep(LOG_FETCH_DELAY_SECONDS)
            logs = list(
                self.api.fetch_job_logs(job_id=job_id, namespace=self._namespace())
            )
            # Raised outside the try below: the container ships the real
            # exception object, and re-raising a remote RuntimeError inside
            # would be caught by the garbled-log handler and retried.
            remote_exception = None
            try:
                result = self._decode_between(logs, RESULT_BEGIN, RESULT_END, hmac_key)
                if result is not _MISSING:
                    return result

                error = self._decode_between(logs, ERROR_BEGIN, ERROR_END, hmac_key)
                if error is not _MISSING:
                    original = (
                        error.get("exception") if isinstance(error, dict) else None
                    )
                    if isinstance(original, BaseException):
                        remote_exception = original
                    else:
                        raise RemoteExecutionError(
                            f"HuggingFace Job {job_id} raised {error['error']}\n"
                            f"Remote traceback:\n{error['traceback']}"
                        )
            except RemoteExecutionError:
                raise
            except RuntimeError as e:
                last_error = e  # truncated/garbled: worth one more read

            if remote_exception is not None:
                raise remote_exception

        if last_error is not None:
            raise last_error

        tail = "\n".join(logs[-30:]) if logs else "<no logs returned>"
        raise RuntimeError(
            f"HuggingFace Job {job_id} produced no clustrix result marker. "
            f"Last log lines:\n{tail}"
        )

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job. Returns True if the API accepted the request.

        A False here means the job may still be running and still billing, so
        callers must not treat it as "cancelled" -- see executor_core.
        """
        try:
            self.api.cancel_job(job_id=job_id, namespace=self._namespace())
        except Exception as e:
            logger.error("Could not cancel HuggingFace Job %s: %s", job_id, e)
            return False
        finally:
            self._jobs.pop(job_id, None)
        return True
