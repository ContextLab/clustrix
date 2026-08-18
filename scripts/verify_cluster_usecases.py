#!/usr/bin/env python
"""Prove clustrix returns *correct answers*, not merely that jobs exit zero.

A job that completes is not evidence. A job that completes and hands back the
value the same function computes locally is. Every case here therefore:

* prints the configuration it used, with secrets redacted;
* prints the decorated function, verbatim, as source;
* computes the expected answer **locally** by calling the undecorated function;
* runs it on the cluster and compares.

Cases are chosen to cover the ways remote execution actually breaks: closures
over local variables, arguments large enough to matter, third-party imports
that must exist on the worker, exceptions that must propagate home, and
returns (``None``, large payloads) that have broken serialization before.

Usage::

    python scripts/verify_cluster_usecases.py                 # every target
    python scripts/verify_cluster_usecases.py hf              # one target
    python scripts/verify_cluster_usecases.py --case closure  # one case

Credentials come from ~/.clustrix-dev-credentials or the environment
(CLUSTRIX_SLURM_PASSWORD, CLUSTRIX_GPU_PASSWORD, HF_TOKEN). The Dartmouth hosts
are split-DNS internal names and need the VPN; if they do not resolve the target
is reported as SKIPPED, never as passing.
"""

import argparse
import inspect
import json
import os
import re
import socket
import sys
import textwrap
import traceback
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clustrix import cluster, configure  # noqa: E402
from clustrix.config import ClusterConfig, _config, get_config  # noqa: E402

CRED_DIR = Path.home() / ".clustrix-dev-credentials"
# Derived rather than hand-listed: a fixed set silently stops covering the
# config the day someone adds a cloud credential field.
_SECRET_PATTERN = re.compile(
    r"secret|token|password|api_key|access_key|_key$|client_id|tenant_id"
    r"|subscription_id",
    re.IGNORECASE,
)
SECRET_FIELDS = {
    f.name for f in fields(ClusterConfig) if _SECRET_PATTERN.search(f.name)
} | {"environment_variables"}


# Module-level state, referenced by the cases below. These exist to be *missing*
# on the worker: a function that names them serializes fine and then dies with
# NameError unless the globals it touches travel with it.
TAX_RATE = 7


def _apply_rate(value):
    """A module-level helper the remote function calls by name."""
    return value * TAX_RATE


class Point:
    """A user-defined class that has to survive the trip in both directions."""

    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __eq__(self, other):
        return isinstance(other, Point) and (other.x, other.y) == (self.x, self.y)

    def __repr__(self):
        return f"Point(x={self.x}, y={self.y})"


# ---------------------------------------------------------------- credentials


def _op_note(name: str) -> Dict[str, str]:
    path = CRED_DIR / f"op_{name}.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(), strict=False)
    out: Dict[str, str] = {}
    for field in doc.get("fields", []):
        for line in (field.get("value") or "").splitlines():
            m = re.match(r"\s*-?\s*(\w+)\s*:\s*(.+)", line)
            if m:
                out.setdefault(m.group(1), m.group(2).strip())
    return out


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


def resolves(host: str) -> bool:
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------- printing


def redacted_config() -> str:
    """The live configuration as a `configure(...)` call, secrets redacted.

    Only settings that differ from the shipped defaults are shown -- printing
    all ninety fields buries the four that matter.
    """
    live = asdict(get_config())
    defaults = asdict(ClusterConfig())
    changed = {
        k: v for k, v in live.items() if v != defaults.get(k) and k not in {"venv_info"}
    }
    lines = ["configure("]
    for key in sorted(changed):
        if key in SECRET_FIELDS:
            value = "'<redacted>'"
        elif key == "key_file" and changed[key]:
            # The directory is this machine's home; only the name is useful.
            value = repr(f"~/.ssh/{Path(changed[key]).name}")
        else:
            value = repr(changed[key])
        lines.append(f"    {key}={value},")
    lines.append(")")
    return "\n".join(lines)


def source_of(func: Callable) -> str:
    """The function as written, including its @cluster decorator."""
    return textwrap.dedent(inspect.getsource(func)).strip()


# ----------------------------------------------------------------- the cases
#
# Each case returns (label, build) where build(decorate) -> (callable, expected).
# `decorate` applies @cluster with the right resources; the *undecorated*
# function is called locally to derive the expected answer, so the comparison
# is against something this file did not hardcode.


def case_provenance(decorate):
    """Where did this actually run? Every other case depends on the answer."""

    def whoami(_):
        import os
        import platform
        import socket
        import sys

        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
        }

    return decorate(whoami), whoami, (0,), {}


def case_arithmetic(decorate):
    """Plain compute. The baseline: does anything come back at all?"""

    def total(n):
        return sum(i * i for i in range(n))

    return decorate(total), total, (1000,), {}


def case_closure(decorate):
    """Closes over local variables that must travel with the function.

    dill has to capture `offset` and `scale` from the enclosing scope. A
    backend that ships the function by name loses them.
    """
    offset = 17
    scale = 3

    def scaled(values):
        return [v * scale + offset for v in values]

    return decorate(scaled), scaled, ([1, 2, 3, 4, 5],), {}


def case_module_global(decorate):
    """Reads a module-level constant, which is not a closure cell.

    `dill.dumps(func)` captures closure cells but not `func.__globals__`, so
    this shape used to serialize cleanly and then die on the worker with
    `NameError: name 'TAX_RATE' is not defined`.
    """

    def with_tax(amount):
        return amount * TAX_RATE

    return decorate(with_tax), with_tax, (11,), {}


def case_helper_call(decorate):
    """Calls another module-level function by name -- common, and worse.

    The helper is neither an argument nor a closure variable; it is reachable
    only through the function's globals.
    """

    def billed(amount):
        return _apply_rate(amount) + 1

    return decorate(billed), billed, (6,), {}


def case_custom_class(decorate):
    """A user-defined class crossing the wire in both directions.

    Arguments used to be serialized with stdlib pickle, which stores a class by
    qualified name, so the worker failed with "Can't get attribute 'Point'".
    """

    def translate(point, dx, dy):
        return Point(point.x + dx, point.y + dy)

    return decorate(translate), translate, (Point(2, 3), 10, 20), {}


def case_disk_roundtrip(decorate):
    """Writes a file on the worker, reads it back, and reports what it read.

    Remote jobs that process data do this; nothing else here touches a
    filesystem, and a read-only or absent working directory would go unnoticed.
    """

    def through_disk(rows):
        import csv
        import os
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "data.csv")
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["name", "value"])
            writer.writerows(rows)

        with open(path) as handle:
            parsed = list(csv.DictReader(handle))

        return {
            "rows": len(parsed),
            "total": sum(int(r["value"]) for r in parsed),
            "names": [r["name"] for r in parsed],
            "bytes_on_disk": os.path.getsize(path),
        }

    rows = [["alpha", 3], ["beta", 4], ["gamma", 5]]
    return decorate(through_disk), through_disk, (rows,), {}


def case_external_library(decorate):
    """Imports a library outside the old nine-package whitelist.

    PyYAML is installed locally and was never in that list, so this failed
    remotely with ModuleNotFoundError while running fine on the caller. Its
    distribution name also differs from its import name, which a naive
    "install what the function imports" scheme gets wrong.
    """

    def via_yaml(mapping):
        import yaml

        text = yaml.safe_dump(mapping, sort_keys=True)
        return {"roundtrip": yaml.safe_load(text), "text_lines": len(text.splitlines())}

    return decorate(via_yaml), via_yaml, ({"b": [1, 2], "a": "x"},), {}


def case_large_argument(decorate):
    """An argument big enough to exercise the payload path (~100k elements)."""

    def checksum(data):
        return {"n": len(data), "total": sum(data), "first": data[0], "last": data[-1]}

    payload = list(range(100_000))
    return decorate(checksum), checksum, (payload,), {}


def case_third_party_import(decorate):
    """Requires numpy on the worker, not just locally."""

    def stats(values):
        import numpy as np

        arr = np.array(values, dtype=float)
        return {
            "mean": round(float(arr.mean()), 6),
            "std": round(float(arr.std()), 6),
            "shape": list(arr.shape),
        }

    return decorate(stats), stats, ([1.0, 2.0, 3.0, 4.0, 5.0],), {}


def case_returns_none(decorate):
    """None is a legitimate answer and has been mistaken for 'no result'."""

    def side_effect_only(x):
        _ = x * 2
        return None

    return decorate(side_effect_only), side_effect_only, (21,), {}


def case_large_return(decorate):
    """A return value large enough to matter coming back (~50k elements)."""

    def expand(n):
        return {"values": list(range(n)), "count": n}

    return decorate(expand), expand, (50_000,), {}


def case_keyword_arguments(decorate):
    """Keyword arguments must survive the round trip too."""

    def combine(a, b=10, *, c=100):
        return a + b + c

    return decorate(combine), combine, (1,), {"b": 20, "c": 300}


def case_nested_data(decorate):
    """Nested containers, the shape real results usually have."""

    def summarise(records):
        by_group: Dict[str, List[int]] = {}
        for record in records:
            by_group.setdefault(record["group"], []).append(record["value"])
        return {g: {"n": len(v), "sum": sum(v)} for g, v in sorted(by_group.items())}

    records = [
        {"group": "a", "value": 1},
        {"group": "b", "value": 2},
        {"group": "a", "value": 3},
        {"group": "b", "value": 4},
    ]
    return decorate(summarise), summarise, (records,), {}


def case_raises(decorate):
    """A remote exception must arrive here, not be swallowed into a result."""

    def explode(x):
        raise ValueError(f"deliberate failure with x={x}")

    return decorate(explode), explode, (5,), {}


# `provenance` must run first; dict order is insertion order.
CASES = {
    "provenance": case_provenance,
    "arithmetic": case_arithmetic,
    "closure": case_closure,
    "module_global": case_module_global,
    "helper_call": case_helper_call,
    "custom_class": case_custom_class,
    "disk_roundtrip": case_disk_roundtrip,
    "external_library": case_external_library,
    "large_argument": case_large_argument,
    "third_party_import": case_third_party_import,
    "returns_none": case_returns_none,
    "large_return": case_large_return,
    "keyword_arguments": case_keyword_arguments,
    "nested_data": case_nested_data,
    "raises": case_raises,
}


# --------------------------------------------------------------- the targets


def configure_slurm_discovery() -> str:
    host = "discovery.dartmouth.edu"
    if not resolves(host):
        raise RuntimeError(f"{host} does not resolve (Dartmouth VPN required)")
    password = slurm_password()
    if not password:
        raise RuntimeError("no SLURM password available")
    configure(
        cluster_type="slurm",
        cluster_host=host,
        username="f002d6b",
        password=password,
        remote_work_dir="/dartfs-hpc/rc/home/b/f002d6b/clustrix_usecases",
        job_poll_interval=5,
        venv_setup_timeout=1800,
        auto_parallel=False,
        auto_gpu_parallel=False,
        default_cores=1,
        default_memory="4GB",
        default_time="00:15:00",
    )
    return host


def configure_slurm_ndoli() -> str:
    host = "ndoli.dartmouth.edu"
    if not resolves(host):
        raise RuntimeError(f"{host} does not resolve (Dartmouth VPN required)")
    password = slurm_password()
    if not password:
        raise RuntimeError("no SLURM password available")
    configure(
        cluster_type="slurm",
        cluster_host=host,
        username="f002d6b",
        password=password,
        remote_work_dir="/dartfs-hpc/rc/home/b/f002d6b/clustrix_usecases",
        job_poll_interval=5,
        venv_setup_timeout=1800,
        auto_parallel=False,
        auto_gpu_parallel=False,
        default_cores=1,
        default_memory="4GB",
        default_time="00:15:00",
    )
    return host


def _configure_ssh_host(host: str) -> str:
    if not resolves(host):
        raise RuntimeError(f"{host} does not resolve (Dartmouth VPN required)")
    key = Path.home() / ".ssh" / "id_ed25519_clustrix_f002d6b_test_tensor01_gpu"
    password = gpu_password()
    use_key = key.exists() and host == "tensor01.dartmouth.edu"
    if not use_key and not password:
        raise RuntimeError(f"no credentials for {host}")
    configure(
        cluster_type="ssh",
        cluster_host=host,
        username="f002d6b",
        key_file=str(key) if use_key else None,
        password=None if use_key else password,
        remote_work_dir="~/.clustrix/usecases",
        job_poll_interval=5,
        venv_setup_timeout=1800,
        auto_parallel=False,
        auto_gpu_parallel=False,
    )
    return host


def configure_gpu_tensor01() -> str:
    return _configure_ssh_host("tensor01.dartmouth.edu")


def configure_gpu_tensor02() -> str:
    return _configure_ssh_host("tensor02.dartmouth.edu")


def configure_hf() -> str:
    token = hf_token()
    if not token:
        raise RuntimeError("no HuggingFace token available")
    configure(
        cluster_type="huggingface",
        hf_token=token,
        hf_namespace=os.environ.get("CLUSTRIX_HF_NAMESPACE", "contextlab"),
        hf_flavor="cpu-basic",
        hf_job_timeout="20m",
        auto_parallel=False,
        auto_gpu_parallel=False,
    )
    return "huggingface-jobs (contextlab)"


TARGETS = {
    "slurm-discovery": ("SLURM · discovery.dartmouth.edu", configure_slurm_discovery),
    "slurm-ndoli": ("SLURM · ndoli.dartmouth.edu", configure_slurm_ndoli),
    "gpu-tensor01": ("SSH+GPU · tensor01.dartmouth.edu", configure_gpu_tensor01),
    "gpu-tensor02": ("SSH+GPU · tensor02.dartmouth.edu", configure_gpu_tensor02),
    "hf": ("HuggingFace Jobs · container", configure_hf),
}


# ------------------------------------------------------------------- running


def _format_call(name: str, args, kwargs) -> str:
    parts = [_abbrev(a) for a in args]
    parts += [f"{k}={_abbrev(v)}" for k, v in kwargs.items()]
    return f"{name}({', '.join(parts)})"


def _run_provenance(remote_callable, plain, args, kwargs) -> Dict[str, Any]:
    """Confirm the work left this machine. A failure here voids the target."""
    local = plain(*args, **kwargs)
    try:
        actual = remote_callable(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {type(e).__name__}: {str(e).splitlines()[0][:160]}")
        return {"case": "provenance", "ok": False, "detail": type(e).__name__}

    print(f"  caller: {local['host']} pid={local['pid']} {local['platform']}")
    print(f"  worker: {actual['host']} pid={actual['pid']} {actual['platform']}")
    print(f"          python {actual['python']} at {actual['executable']}")

    elsewhere = actual["host"] != local["host"] and actual["pid"] != local["pid"]
    if elsewhere:
        print("  OK — ran on a different machine")
    else:
        print("  FAIL — ran locally; every later comparison would match trivially")
    return {"case": "provenance", "ok": elsewhere, "worker": actual["host"]}


def _run_raises(remote_callable, plain, args, kwargs) -> Dict[str, Any]:
    try:
        plain(*args, **kwargs)
        local_error = None
    except Exception as e:  # noqa: BLE001
        local_error = e

    try:
        got = remote_callable(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        message_kept = str(local_error) in str(e)
        type_kept = type(e) is type(local_error)
        print(f"  local raised : {type(local_error).__name__}: {local_error}")
        print(f"  remote raised: {type(e).__name__}: {str(e).splitlines()[0][:100]}")
        if message_kept and type_kept:
            print("  OK — original type and message both preserved")
        elif message_kept:
            print(f"  FAIL — type became {type(e).__name__}; except ValueError misses")
        else:
            print("  FAIL — the original error is not visible")
        return {"case": "raises", "ok": message_kept and type_kept}

    print(f"  FAIL  no exception raised; returned {_abbrev(got)}")
    return {"case": "raises", "ok": False, "detail": "exception not propagated"}


def run_case(name: str, builder, decorate) -> Dict[str, Any]:
    remote_callable, plain, args, kwargs = builder(decorate)

    print(f"\n--- case: {name} " + "-" * max(0, 58 - len(name)))
    print(source_of(plain))
    print(f"\ncall: {_format_call(plain.__name__, args, kwargs)}")

    if name == "provenance":
        return _run_provenance(remote_callable, plain, args, kwargs)
    if name == "raises":
        return _run_raises(remote_callable, plain, args, kwargs)

    expected = plain(*args, **kwargs)
    try:
        actual = remote_callable(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {type(e).__name__}: {str(e).splitlines()[0][:160]}")
        return {"case": name, "ok": False, "detail": type(e).__name__}

    ok = actual == expected
    print(f"  expected: {_abbrev(expected)}")
    print(f"  actual  : {_abbrev(actual)}")
    if ok:
        print("  OK — values match")
    else:
        print(f"  FAIL — values differ ({_explain_mismatch(expected, actual)})")
    return {"case": name, "ok": ok}


def _explain_mismatch(expected: Any, actual: Any) -> str:
    """Say what actually differs, so a repr that looks identical is not a riddle."""
    et, at = type(expected), type(actual)
    if et is not at and et.__qualname__ == at.__qualname__:
        # Same class by name, different class object: the class was serialized
        # by value, so `isinstance(result, MyClass)` is False on return. This
        # only happens for classes defined in a __main__ script -- a class in an
        # installed package ships by reference and keeps its identity.
        return (
            f"same class name {et.__qualname__!r} but different class objects; "
            "a by-value class does not satisfy isinstance() on return"
        )
    if et is not at:
        return f"expected {et.__name__}, got {at.__name__}"
    return "same type, different contents"


def _abbrev(value: Any, limit: int = 110) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({type(value).__name__}, len={_safe_len(value)})"


def _safe_len(value: Any) -> Any:
    try:
        return len(value)
    except TypeError:
        return "n/a"


def run_target(key: str, case_names: List[str]) -> Dict[str, Any]:
    label, setup = TARGETS[key]
    print("\n" + "=" * 78)
    print(f"TARGET: {label}")
    print("=" * 78)

    _config.__dict__.update(ClusterConfig().__dict__)
    try:
        where = setup()
    except Exception as e:  # noqa: BLE001
        print(f"SKIPPED: {e}")
        return {"target": key, "status": "skipped", "reason": str(e)}

    print("\nconfiguration used (secrets redacted):\n")
    print(textwrap.indent(redacted_config(), "  "))
    print(f"\nsubmitting to {where}")

    def decorate(func):
        return cluster(cores=1)(func)

    results = []
    for name in case_names:
        try:
            results.append(run_case(name, CASES[name], decorate))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            results.append({"case": name, "ok": False, "detail": "harness error"})

        # Remoteness underwrites every later claim on this target.
        if name == "provenance" and not results[-1]["ok"]:
            print("\nABANDONING this target: remoteness was not established.")
            return {
                "target": key,
                "status": "failed",
                "passed": 0,
                "total": len(case_names),
                "results": results,
            }

    passed = sum(1 for r in results if r["ok"])
    print(f"\n{label}: {passed}/{len(results)} cases returned the correct answer")
    return {
        "target": key,
        "status": "passed" if results and passed == len(results) else "failed",
        "passed": passed,
        "total": len(results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", choices=list(TARGETS), default=None)
    parser.add_argument(
        "--case", action="append", dest="cases", help="run only these (repeatable)"
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="exit 0 even when a target was unreachable",
    )
    args = parser.parse_args()

    targets = args.targets or list(TARGETS)
    cases = args.cases or list(CASES)

    unknown = [c for c in cases if c not in CASES]
    if unknown:
        parser.error(f"unknown case(s): {', '.join(unknown)}")

    # Remoteness underwrites every other claim, so it is not optional.
    if "provenance" not in cases:
        cases = ["provenance"] + cases

    print(f"caller: {socket.gethostname()} · python {sys.version.split()[0]}")
    print(f"cases : {', '.join(cases)}")

    summaries = [run_target(t, cases) for t in targets]

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for s in summaries:
        if s["status"] == "skipped":
            print(f"{s['target']:16s} SKIPPED  {s['reason']}")
        else:
            print(
                f"{s['target']:16s} {s['status'].upper():8s} "
                f"{s['passed']}/{s['total']} correct"
            )

    failed = [s for s in summaries if s["status"] == "failed"]
    skipped = [s for s in summaries if s["status"] == "skipped"]
    if failed:
        return 1
    if skipped and not args.allow_skip:
        print(f"\n{len(skipped)} target(s) skipped; pass --allow-skip to accept that.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
