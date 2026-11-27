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

    python scripts/verify_cluster_usecases.py --check         # connect, look, submit nothing
    python scripts/verify_cluster_usecases.py                 # every target
    python scripts/verify_cluster_usecases.py hf              # one target
    python scripts/verify_cluster_usecases.py --case closure  # one case

Nothing about a cluster is hardcoded. Each target is described by
environment variables -- ``scripts/cluster_test.env.example`` lists them all
with what each is for -- and a target whose host variable is unset, or whose
host does not resolve (a split-DNS name that needs a VPN), is reported as
SKIPPED, never as passing. So is a case a target cannot run: a GPU case on
a target that was not said to have GPUs.

Secrets are never read from the command line. A password comes from
``CLUSTRIX_SLURM_PASSWORD`` / ``CLUSTRIX_GPU_PASSWORD`` or
``~/.clustrix-dev-credentials``; a key from ``CLUSTRIX_TEST_<TARGET>_KEY``;
with neither, the ssh-agent and default keys are tried. The HuggingFace
token comes from ``HF_TOKEN`` or the hub's own token file. Host keys are
verified against ``known_hosts`` as always: connect once with ``ssh`` first.

``hf-gpu`` spends money and runs only when ``CLUSTRIX_TEST_HF_GPU_FLAVOR``
names the hardware to rent.
"""

import argparse
import inspect
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import textwrap
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
# A package that lives in the working tree and is installed nowhere -- the
# shape of every real project with more than one file.
sys.path.insert(0, str(_ROOT / "tests" / "unit" / "localproject"))

from clustrix import cluster, configure, explain  # noqa: E402
from clustrix.config import (  # noqa: E402
    ClusterConfig,
    SECRET_FIELDS,
    get_config,
)
from mypkg.mathutils import SCALE, Widget, triple  # noqa: E402

CRED_DIR = Path.home() / ".clustrix-dev-credentials"


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


class NotApplicable(Exception):
    """This case cannot be run on this target; the reason is the message."""


#: What the target being run is: "ssh", "slurm" or "hf", and how many GPUs
#: one job may ask it for (0: it was not said to have any).
TARGET: Dict[str, Any] = {"kind": None, "gpus": 0}


def _only_on(*kinds: str) -> None:
    if TARGET["kind"] not in kinds:
        raise NotApplicable(
            f"this case is about {' and '.join(kinds)} targets; "
            f"this one is {TARGET['kind']}"
        )


def _needs_gpus() -> int:
    if not TARGET["gpus"]:
        raise NotApplicable(
            "this target was not said to have GPUs "
            "(see cluster_test.env.example for the variable that says so)"
        )
    return int(TARGET["gpus"])


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


def case_local_module_function(decorate):
    """Calls a function imported from a sibling module of this project.

    Serializers store an importable object by reference -- "import
    mypkg.mathutils, then get triple" -- which is right for numpy and wrong for
    a package that exists on no machine but the caller's. This failed with
    `ModuleNotFoundError: No module named 'mypkg'`.
    """

    def scaled_up(value):
        return triple(value) + SCALE

    return decorate(scaled_up), scaled_up, (4,), {}


def case_local_module_class(decorate):
    """Instantiates a class defined in a project-local module."""

    def widget_value(n):
        return Widget(n).value()

    return decorate(widget_value), widget_value, (3,), {}


def case_local_instance_argument(decorate):
    """Passes an instance of a project-local class as an argument.

    The class has to travel with the argument, not just with the function.
    """

    def doubled(widget):
        return widget.value() * 2

    return decorate(doubled), doubled, (Widget(2),), {}


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
def case_loop_divided(decorate):
    """A loop at the top of a function becomes one job per piece.

    The totals must be what a direct call returns, in order, and the pieces
    must have run in more than one process -- otherwise nothing was divided
    and the first half of this proves nothing new.
    """
    _only_on("ssh", "slurm")

    def squares_up_to_each(limits):
        import os
        import socket

        totals = []
        ran_in = []
        for limit in limits:
            totals.append(sum(i * i for i in range(limit)))
            ran_in.append((socket.gethostname(), os.getpid()))
        return totals, ran_in

    limits = [200_000, 300_000, 400_000, 500_000, 600_000, 700_000]
    remote = cluster(cores=1, parallel=True)(squares_up_to_each)
    print(textwrap.indent(explain(remote), "  plan: "))

    def check(actual):
        totals, ran_in = actual
        expected, _ = squares_up_to_each(limits)
        if totals != expected:
            return False, f"totals differ: expected {_abbrev(expected)}"
        processes = sorted(set(map(tuple, ran_in)))
        if len(processes) < 2:
            return False, f"every iteration ran in one process: {processes}"
        return True, f"same totals, from {len(processes)} processes: {processes}"

    return remote, squares_up_to_each, (limits,), {}, check


def case_input_file(decorate):
    """A file that exists only on this machine is there when the job opens it.

    The path is an argument, so it is found without being declared. The
    file is made for this run and its content is random: a copy left on the
    cluster by an earlier run cannot answer for it.
    """
    _only_on("ssh", "slurm")
    folder = Path(tempfile.mkdtemp(prefix="clustrix-usecase-"))
    path = folder / "measurements.txt"
    path.write_text("\n".join(secrets.token_hex(16) for _ in range(200)) + "\n")

    def checksum(path):
        import hashlib

        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    return decorate(checksum), checksum, (str(path),), {}


def case_environment_variables(decorate):
    """Both kinds of variable reach the job: the plain list and the mapping."""
    _only_on("ssh", "slurm")
    shown, hidden = secrets.token_hex(8), secrets.token_hex(8)
    configure(
        plain_environment_variables=[f"CLUSTRIX_USECASE_PLAIN={shown} with a space"],
        environment_variables={"CLUSTRIX_USECASE_HIDDEN": hidden},
    )

    def read_variables(names):
        import os

        return [os.environ.get(name) for name in names]

    def check(actual):
        expected = [f"{shown} with a space", hidden]
        if actual == expected:
            return True, "both values arrived as they were set"
        return False, f"expected {expected}"

    names = ["CLUSTRIX_USECASE_PLAIN", "CLUSTRIX_USECASE_HIDDEN"]
    return decorate(read_variables), read_variables, (names,), {}, check


def _gpu_decorator(**options):
    """``@cluster`` asking for one job's GPUs the way this target is asked.

    HuggingFace hardware is the flavor in the configuration, and ``gpus=``
    is ignored there with a warning, so it is left out.
    """
    if TARGET["kind"] != "hf":
        options["gpus"] = 1
    return cluster(cores=1, **options)


def case_gpu_visible(decorate):
    """The job asked for a GPU. Does the driver show it one?"""
    _needs_gpus()

    def devices_seen(_):
        import os
        import subprocess

        try:
            listing = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            found, problem = listing.stdout.strip().splitlines(), listing.stderr[:300]
        except OSError as error:
            found, problem = [], str(error)
        return {
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "nvidia-smi": found,
            "problem": problem.strip(),
        }

    def check(actual):
        if not actual["nvidia-smi"]:
            return False, f"no GPU listed on the worker: {actual['problem'] or 'empty'}"
        return True, f"{len(actual['nvidia-smi'])} device(s) listed"

    return _gpu_decorator()(devices_seen), devices_seen, (0,), {}, check


def case_gpu_computation(decorate):
    """Arithmetic done on the GPU comes back right.

    The sum of squares below 100000 is an integer a float64 holds exactly,
    so the answer is compared with ``==``. The worker needs PyTorch with
    CUDA: installed here (the local environment is mirrored), or in the
    environment the target's ``_ENV`` variable names. Without it this is
    reported as not established, not as a pass.
    """
    _needs_gpus()

    def sum_of_squares_on_the_gpu(n):
        try:
            import torch
        except ImportError as error:
            return {"torch": None, "why": str(error)}
        if not torch.cuda.is_available():
            return {"torch": torch.__version__, "cuda": False}
        values = torch.arange(n, dtype=torch.float64, device="cuda")
        total = float((values * values).sum().item())
        return {
            "torch": torch.__version__,
            "cuda": True,
            "device": torch.cuda.get_device_name(0),
            "total": total,
            "bytes_on_device": int(torch.cuda.max_memory_allocated()),
        }

    n = 100_000

    def check(actual):
        if actual.get("torch") is None:
            return None, f"PyTorch is not on the worker ({actual.get('why')})"
        if not actual.get("cuda"):
            return False, f"torch {actual['torch']} on the worker sees no CUDA device"
        expected = float((n - 1) * n * (2 * n - 1) // 6)
        if actual["total"] != expected or actual["bytes_on_device"] <= 0:
            return False, f"expected total {expected} and memory in use on the device"
        return True, f"computed on {actual['device']}"

    remote = _gpu_decorator()(sum_of_squares_on_the_gpu)
    return remote, sum_of_squares_on_the_gpu, (n,), {}, check


def case_gpu_loop(decorate):
    """A divided loop with ``gpus=1``: every piece has a device of its own.

    On an ssh host clustrix hands the devices out itself; under SLURM the
    scheduler does. Either way an iteration must see exactly one.
    """
    _only_on("ssh", "slurm")
    _needs_gpus()

    def device_for_each(labels):
        import os
        import socket

        seen = []
        for label in labels:
            seen.append(
                (
                    label,
                    socket.gethostname(),
                    os.getpid(),
                    os.environ.get("CUDA_VISIBLE_DEVICES"),
                )
            )
        return seen

    labels = ["a", "b", "c", "d"]
    remote = cluster(cores=1, gpus=1, parallel=True)(device_for_each)
    print(textwrap.indent(explain(remote), "  plan: "))

    def check(actual):
        if [row[0] for row in actual] != labels:
            return False, "the labels did not come back in order"
        without = [row for row in actual if not row[3] or "," in row[3]]
        if without:
            return False, f"an iteration did not see exactly one device: {without}"
        pieces = sorted({tuple(row[1:]) for row in actual})
        return True, f"{len(pieces)} piece(s), as (host, pid, device): {pieces}"

    return remote, device_for_each, (labels,), {}, check


CASES = {
    "provenance": case_provenance,
    "arithmetic": case_arithmetic,
    "closure": case_closure,
    "module_global": case_module_global,
    "helper_call": case_helper_call,
    "custom_class": case_custom_class,
    "local_module_function": case_local_module_function,
    "local_module_class": case_local_module_class,
    "local_instance_argument": case_local_instance_argument,
    "disk_roundtrip": case_disk_roundtrip,
    "external_library": case_external_library,
    "large_argument": case_large_argument,
    "third_party_import": case_third_party_import,
    "returns_none": case_returns_none,
    "large_return": case_large_return,
    "keyword_arguments": case_keyword_arguments,
    "nested_data": case_nested_data,
    "raises": case_raises,
    "loop_divided": case_loop_divided,
    "input_file": case_input_file,
    "environment_variables": case_environment_variables,
    "gpu_visible": case_gpu_visible,
    "gpu_computation": case_gpu_computation,
    "gpu_loop": case_gpu_loop,
}


# --------------------------------------------------------------- the targets


def _variable(target: str, name: str) -> str:
    """The variable holding one setting of one target.

    ``CLUSTRIX_TEST_SLURM_PARTITION`` for the first SLURM cluster and
    ``CLUSTRIX_TEST_SLURM_PARTITION_2`` for the second: ``target`` is
    ``"SLURM"`` or ``"SLURM_2"``, and the number goes last because
    ``CLUSTRIX_TEST_SLURM_HOST_2`` is the name already in use.
    """
    kind, _, number = target.partition("_")
    return f"CLUSTRIX_TEST_{kind}_{name}" + (f"_{number}" if number else "")


def _setting(target: str, name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(_variable(target, name)) or default


def _configure_remote(
    target: str, cluster_type: str, password: Optional[str], work_dir: str
) -> str:
    """Point clustrix at the host ``CLUSTRIX_TEST_<TARGET>_HOST`` names."""
    host = _setting(target, "HOST")
    if not host:
        raise RuntimeError(f"{_variable(target, 'HOST')} is not set")
    if not resolves(host):
        raise RuntimeError(f"{host} does not resolve (check VPN/network access)")
    username = _setting(target, "USER") or os.environ.get("CLUSTRIX_TEST_USERNAME")
    if not username:
        raise RuntimeError(
            f"neither {_variable(target, 'USER')} nor CLUSTRIX_TEST_USERNAME is set"
        )
    key = _setting(target, "KEY")
    if key and not Path(key).expanduser().exists():
        raise RuntimeError(
            f"{_variable(target, 'KEY')} names {key}, which is not there"
        )
    if not key and target == "SSH":
        # Where `clustrix ssh-setup --alias test_gpu` leaves the key it made.
        made = Path.home() / ".ssh" / f"id_ed25519_clustrix_{username}_test_gpu"
        key = str(made) if made.exists() else None
    settings: Dict[str, Any] = dict(
        cluster_type=cluster_type,
        cluster_host=host,
        cluster_port=int(_setting(target, "PORT", "22")),
        username=username,
        # A key when one is named; else a password when one is stored; else
        # neither, and paramiko tries the ssh-agent and the default keys.
        key_file=str(Path(key).expanduser()) if key else None,
        password=None if key else password,
        remote_work_dir=_setting(target, "REMOTE_DIR", work_dir),
        job_poll_interval=5,
        venv_setup_timeout=1800,
        # The older cases are about one job each; the loop cases ask for
        # division themselves with parallel=True.
        auto_parallel=False,
        default_cores=1,
        default_memory="4GB",
        default_time="00:15:00",
        module_loads=(_setting(target, "MODULES") or "").split(),
    )
    if _setting(target, "PARTITION"):
        settings["default_partition"] = _setting(target, "PARTITION")
    if _setting(target, "ENV"):
        settings["conda_env_name"] = _setting(target, "ENV")
    configure(**settings)
    return host


def _configure_slurm(target: str) -> str:
    host = _configure_remote(target, "slurm", slurm_password(), "~/clustrix_usecases")
    # A scheduler is only asked for a GPU when told it has one: an sbatch
    # that names a gres the cluster lacks is refused, and that refusal
    # would say nothing about clustrix.
    TARGET.update(kind="slurm", gpus=int(_setting(target, "GPUS", "0")))
    return host


def _configure_ssh(target: str) -> str:
    host = _configure_remote(target, "ssh", gpu_password(), "~/.clustrix/usecases")
    TARGET.update(kind="ssh", gpus=int(_setting(target, "GPUS", "1")))
    return host


def configure_slurm_primary() -> str:
    return _configure_slurm("SLURM")


def configure_slurm_secondary() -> str:
    return _configure_slurm("SLURM_2")


def configure_gpu_primary() -> str:
    return _configure_ssh("SSH")


def configure_gpu_secondary() -> str:
    return _configure_ssh("SSH_2")


def _configure_hf(flavor: str) -> str:
    token = hf_token()
    if not token:
        raise RuntimeError("no HuggingFace token available (HF_TOKEN)")
    namespace = os.environ.get("CLUSTRIX_HF_NAMESPACE")
    configure(
        cluster_type="huggingface",
        hf_token=token,
        hf_namespace=namespace,
        hf_flavor=flavor,
        hf_job_timeout="20m",
        auto_parallel=False,
    )
    return f"huggingface-jobs ({namespace or 'the token owner'}, {flavor})"


def configure_hf() -> str:
    where = _configure_hf("cpu-basic")
    TARGET.update(kind="hf", gpus=0)
    return where


def configure_hf_gpu() -> str:
    flavor = os.environ.get("CLUSTRIX_TEST_HF_GPU_FLAVOR")
    if not flavor:
        raise RuntimeError(
            "CLUSTRIX_TEST_HF_GPU_FLAVOR is not set; this target rents GPU "
            "hardware and runs only when told which (e.g. t4-small)"
        )
    where = _configure_hf(flavor)
    TARGET.update(kind="hf", gpus=1)
    return where


TARGETS = {
    "slurm-1": ("SLURM · $CLUSTRIX_TEST_SLURM_HOST", configure_slurm_primary),
    "slurm-2": ("SLURM · $CLUSTRIX_TEST_SLURM_HOST_2", configure_slurm_secondary),
    "gpu-1": ("SSH+GPU · $CLUSTRIX_TEST_SSH_HOST", configure_gpu_primary),
    "gpu-2": ("SSH+GPU · $CLUSTRIX_TEST_SSH_HOST_2", configure_gpu_secondary),
    "hf": ("HuggingFace Jobs · cpu-basic", configure_hf),
    "hf-gpu": ("HuggingFace Jobs · $CLUSTRIX_TEST_HF_GPU_FLAVOR", configure_hf_gpu),
}


# -------------------------------------------------------------------- looking
#
# ``--check``: connect to each target and report what is there, submitting
# nothing and spending nothing. Run it first: a wrong partition name or a
# missing module costs a minute here and an afternoon in the job logs.

_LOOK = {
    "who and where": 'echo "$(whoami)@$(hostname)"',
    "python3": "python3 --version 2>&1 || echo 'no python3 on PATH'",
    "GPUs": (
        "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader "
        "2>/dev/null || echo 'nvidia-smi: not available here'"
    ),
}
_LOOK_SLURM = {
    "partitions (name, state, time limit, nodes, gres)": (
        "sinfo -o '%P %a %l %D %G' 2>&1 || echo 'sinfo: not available here'"
    ),
}


def check_target(key: str) -> Dict[str, Any]:
    label, setup = TARGETS[key]
    print("\n" + "=" * 78)
    print(f"CHECK: {label}")
    print("=" * 78)
    configure(**asdict(ClusterConfig()))
    try:
        where = setup()
    except Exception as e:  # noqa: BLE001
        print(f"SKIPPED: {e}")
        return {"target": key, "status": "skipped", "reason": str(e)}
    print(textwrap.indent(redacted_config(), "  "))

    try:
        if TARGET["kind"] == "hf":
            from huggingface_hub import whoami

            me = whoami(token=get_config().hf_token)
            orgs = ", ".join(org["name"] for org in me.get("orgs", [])) or "none"
            print(f"  token belongs to {me['name']}; organisations: {orgs}")
            print(f"  jobs would run as {where}; nothing was submitted")
        else:
            from clustrix.executor import ClusterExecutor

            with ClusterExecutor(get_config()) as executor:
                executor.connect()
                looks = dict(_LOOK)
                if TARGET["kind"] == "slurm":
                    looks.update(_LOOK_SLURM)
                for what, command in looks.items():
                    out, _ = executor.connection_manager.execute_remote_command(command)
                    print(f"  {what}:")
                    print(textwrap.indent(out.strip() or "(nothing)", "      "))
            here = "%d.%d" % sys.version_info[:2]
            print(
                f"  this machine runs Python {here}; the worker must have the "
                "same minor version (clustrix looks for one, and module_loads "
                f"via CLUSTRIX_TEST_*_MODULES can supply it)"
            )
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {type(e).__name__}: {_said(e)}")
        return {"target": key, "status": "failed", "passed": 0, "total": 1}
    print("  OK — reachable")
    return {"target": key, "status": "passed", "passed": 1, "total": 1}


# ------------------------------------------------------------------- running


def _said(error: BaseException, limit: int = 500) -> str:
    """What an exception says, on one line.

    Not its first line: a HuggingFace API error opens with a request id and
    says what was refused -- "missing permissions: job.write" -- three lines
    further down.
    """
    return " | ".join(line.strip() for line in str(error).splitlines() if line.strip())[
        :limit
    ]


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
        print(f"  FAIL  {type(e).__name__}: {_said(e)}")
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
    print(f"\n--- case: {name} " + "-" * max(0, 58 - len(name)))
    try:
        built = builder(decorate)
    except NotApplicable as why:
        print(f"  SKIPPED — {why}")
        return {"case": name, "ok": None, "detail": str(why)}
    remote_callable, plain, args, kwargs = built[:4]
    check = built[4] if len(built) > 4 else None

    print(source_of(plain))
    print(f"\ncall: {_format_call(plain.__name__, args, kwargs)}")

    if name == "provenance":
        return _run_provenance(remote_callable, plain, args, kwargs)
    if name == "raises":
        return _run_raises(remote_callable, plain, args, kwargs)

    try:
        actual = remote_callable(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {type(e).__name__}: {_said(e)}")
        return {"case": name, "ok": False, "detail": type(e).__name__}

    print(f"  actual  : {_abbrev(actual, 400)}")
    if check is not None:
        # The answer is not what a call on this machine returns -- a process
        # id, a device name -- so the case says what a right answer is.
        ok, note = check(actual)
        verdict = {True: "OK", False: "FAIL", None: "NOT ESTABLISHED"}[ok]
        print(f"  {verdict} — {note}")
        return {"case": name, "ok": ok, "detail": note}

    expected = plain(*args, **kwargs)
    ok = actual == expected
    print(f"  expected: {_abbrev(expected)}")
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

    configure(**asdict(ClusterConfig()))
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
    ran = [r for r in results if r["ok"] is not None]
    left_out = len(results) - len(ran)
    print(
        f"\n{label}: {passed}/{len(ran)} cases returned the correct answer"
        + (f"; {left_out} not run or not established" if left_out else "")
    )
    return {
        "target": key,
        "status": "passed" if ran and passed == len(ran) else "failed",
        "passed": passed,
        "total": len(ran),
        "left_out": [
            f"{r['case']}: {r.get('detail')}" for r in results if r["ok"] is None
        ],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", choices=list(TARGETS), default=None)
    parser.add_argument(
        "--case", action="append", dest="cases", help="run only these (repeatable)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="connect to each target and report what is there; submit nothing",
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

    if args.check:
        summaries = [check_target(t) for t in targets]
    else:
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
            for line in s.get("left_out", []):
                print(f"{'':16s} not run  {line}")

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
