#!/usr/bin/env python
"""Run the same function on every backend clustrix claims to support, for real.

This exists because clustrix's remote execution was broken for a long time
without anyone noticing: the test suite mocked the parts that mattered, and
"real-world testing" meant hosts CI could not reach. Anything that claims a
backend works should be reproducible by someone else in one command.

Nothing here is mocked. Each target submits a genuine job, waits for it, and
prints what came back. A target that cannot be reached is reported as skipped,
never as passing.

Usage::

    python scripts/collect_execution_evidence.py            # all reachable targets
    python scripts/collect_execution_evidence.py slurm gpu  # a subset

Credentials come from ~/.clustrix-dev-credentials (see notes/), or from the
environment: CLUSTRIX_SLURM_PASSWORD, CLUSTRIX_GPU_PASSWORD, HF_TOKEN.
Cluster hosts are never hardcoded; the SLURM and SSH+GPU targets read their
hostname from CLUSTRIX_TEST_SLURM_HOST and CLUSTRIX_TEST_SSH_HOST (plus a
shared CLUSTRIX_TEST_USERNAME). Some hosts are split-DNS internal names, so
they need a VPN; if a host variable is unset or the host does not resolve,
that is what a skip means.
"""

import argparse
import json
import os
import platform
import re
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clustrix import cluster, configure, get_config  # noqa: E402
from clustrix.config import ClusterConfig  # noqa: E402

CRED_DIR = Path.home() / ".clustrix-dev-credentials"


def _op_note(name: str) -> Dict[str, str]:
    """Parse a 1Password secure note exported to the local credential cache."""
    path = CRED_DIR / f"op_{name}.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(), strict=False)
    fields: Dict[str, str] = {}
    for field in doc.get("fields", []):
        for line in (field.get("value") or "").splitlines():
            match = re.match(r"\s*-?\s*(\w+)\s*:\s*(.+)", line)
            if match:
                fields.setdefault(match.group(1), match.group(2).strip())
    return fields


def slurm_password() -> Optional[str]:
    return os.environ.get("CLUSTRIX_SLURM_PASSWORD") or _op_note(
        "clustrix-ssh-slurm"
    ).get("password")


def gpu_password() -> Optional[str]:
    return os.environ.get("CLUSTRIX_GPU_PASSWORD") or _op_note("clustrix-ssh-gpu").get(
        "password"
    )


def hf_token() -> Optional[str]:
    if os.environ.get("HF_TOKEN"):
        return os.environ["HF_TOKEN"]
    for candidate in (
        CRED_DIR / "huggingface_token",
        Path.home() / ".cache" / "huggingface" / "token",
    ):
        if candidate.exists():
            return candidate.read_text().strip()
    return None


def host_resolves(hostname: str) -> bool:
    try:
        socket.gethostbyname(hostname)
        return True
    except OSError:
        return False


# -- the payload -------------------------------------------------------------
#
# One function for every backend, so the results are directly comparable and
# there is no room for a backend to "pass" by running something easier.


@cluster(cores=1)
def probe(n: int) -> Dict[str, Any]:
    """Report enough about the worker to prove it is not the caller."""
    import os
    import platform
    import subprocess
    import sys

    gpus = ""
    try:
        gpus = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except Exception:
        pass

    return {
        "sum": sum(range(n)),
        "host": platform.node(),
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "system": platform.system(),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "slurm_nodelist": os.environ.get("SLURM_JOB_NODELIST"),
        "gpus": gpus,
    }


# -- targets -----------------------------------------------------------------


def _test_username() -> str:
    username = os.environ.get("CLUSTRIX_TEST_USERNAME")
    if not username:
        raise RuntimeError("CLUSTRIX_TEST_USERNAME is not set")
    return username


def configure_slurm() -> str:
    host = os.environ.get("CLUSTRIX_TEST_SLURM_HOST")
    if not host:
        raise RuntimeError(
            "CLUSTRIX_TEST_SLURM_HOST is not set; export it to a reachable SLURM host"
        )
    if not host_resolves(host):
        raise RuntimeError(f"{host} does not resolve (check VPN/network access)")
    username = _test_username()
    password = slurm_password()
    if not password:
        raise RuntimeError("no SLURM password available")
    remote_work_dir = os.environ.get(
        "CLUSTRIX_TEST_SLURM_REMOTE_DIR", "~/clustrix_evidence"
    )
    configure(
        cluster_type="slurm",
        cluster_host=host,
        username=username,
        password=password,
        remote_work_dir=remote_work_dir,
        job_poll_interval=10,
        venv_setup_timeout=1800,
        auto_parallel=False,
        default_cores=1,
        default_memory="4GB",
        default_time="00:10:00",
    )
    return host


def configure_gpu() -> str:
    host = os.environ.get("CLUSTRIX_TEST_SSH_HOST")
    if not host:
        raise RuntimeError(
            "CLUSTRIX_TEST_SSH_HOST is not set; export it to a reachable SSH+GPU host"
        )
    if not host_resolves(host):
        raise RuntimeError(f"{host} does not resolve (check VPN/network access)")
    username = _test_username()
    key = Path.home() / ".ssh" / f"id_ed25519_clustrix_{username}_test_gpu"
    password = gpu_password()
    if not key.exists() and not password:
        raise RuntimeError("no SSH key or password available for the GPU host")
    configure(
        cluster_type="ssh",
        cluster_host=host,
        username=username,
        key_file=str(key) if key.exists() else None,
        password=None if key.exists() else password,
        remote_work_dir="~/.clustrix/evidence",
        job_poll_interval=5,
        venv_setup_timeout=1800,
        auto_parallel=False,
    )
    return host


def configure_hf() -> str:
    token = hf_token()
    if not token:
        raise RuntimeError("no HuggingFace token available")
    configure(
        cluster_type="huggingface",
        hf_token=token,
        hf_namespace=os.environ.get("CLUSTRIX_HF_NAMESPACE", "contextlab"),
        hf_flavor="cpu-basic",
        hf_job_timeout="15m",
        auto_parallel=False,
    )
    return "huggingface-jobs"


TARGETS = {
    "slurm": ("SLURM scheduler ($CLUSTRIX_TEST_SLURM_HOST)", configure_slurm),
    "gpu": ("SSH + GPU host ($CLUSTRIX_TEST_SSH_HOST)", configure_gpu),
    "hf": ("HuggingFace Jobs (container)", configure_hf),
}


def run_target(key: str) -> Dict[str, Any]:
    label, setup = TARGETS[key]
    print(f"\n{'=' * 72}\n{key}: {label}\n{'=' * 72}")

    # Each target starts from a clean configuration so one cannot inherit
    # another's settings and appear to work by accident.
    get_config().__dict__.update(ClusterConfig().__dict__)

    started = time.time()
    try:
        where = setup()
    except Exception as e:
        print(f"SKIPPED: {e}")
        return {"target": key, "status": "skipped", "reason": str(e)}

    print(f"submitting to {where} ...")
    try:
        result = probe(1000)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        return {"target": key, "status": "failed", "error": f"{type(e).__name__}: {e}"}

    elapsed = time.time() - started
    print(f"RESULT ({elapsed:.0f}s): {json.dumps(result, indent=2, sort_keys=True)}")

    if result["host"] == platform.node():
        # The whole point is that the work left this machine.
        print("FAILED: the job ran on the caller's own host")
        return {"target": key, "status": "failed", "error": "ran locally"}

    return {
        "target": key,
        "status": "passed",
        "seconds": round(elapsed, 1),
        "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", choices=list(TARGETS) + [], default=None)
    args = parser.parse_args()
    chosen = args.targets or list(TARGETS)

    print(
        f"caller: {platform.node()} ({platform.machine()}, "
        f"python {sys.version.split()[0]})"
    )

    results = [run_target(key) for key in chosen]

    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    for entry in results:
        line = f"{entry['target']:6s} {entry['status'].upper()}"
        if entry["status"] == "passed":
            r = entry["result"]
            line += f"  {r['host']}  python {r['python']}  {entry['seconds']}s"
        elif entry["status"] == "skipped":
            line += f"  {entry['reason']}"
        else:
            line += f"  {entry.get('error', '')}"
        print(line)

    failed = [e for e in results if e["status"] == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
