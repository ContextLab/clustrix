#!/usr/bin/env python3
"""``@cluster`` must return the user's answer or raise -- never a substitute.

Two defects of that class lived in ``clustrix/decorator.py``.

**1. The fabricated GPU result.**
``_attempt_client_side_gpu_parallelization`` never called the user's function.
It built one hardcoded program per GPU::

    import torch
    torch.cuda.set_device(N)
    x = torch.randn(100, 100, device=device)
    y = torch.mm(x, x.t())
    result = y.trace().item()
    print(f'GPU_N_RESULT:{result}')

ran it over SSH, scraped ``GPU_<n>_RESULT:`` out of stdout and returned
``{"gpu_parallel": True, "gpu_count": ..., "results": ...}``. The caller
returned that dict straight to the user. ``func`` was reachable from that path
only as an argument to the *static analyser*
``detect_gpu_parallelizable_operations``; it was never invoked. So a user whose
cluster reported two or more GPUs got the traces of random matrices instead of
their result, with no error. ``config.auto_gpu_parallel`` defaults to ``True``,
so nobody had to opt in.

This is the same shape as ``create_simple_subprocess_fallback`` (see
``tests/unit/test_execute_single_no_fabrication.py``), which was deleted for
the same reason.

**2. The unanswerable chunk call.**
The remote loop-parallelization path injected a ``_chunk_range_<var>`` keyword
argument into the user's function without checking that the function could
accept one, so an ordinary function raised::

    TypeError: collect() got an unexpected keyword argument '_chunk_range_i'

and a function that did accept it got back a list of per-chunk results
instead of its own return value. That protocol is gone: ``@cluster`` now
divides a loop with ``clustrix.parallelize``, which never calls the user's
function with anything but the caller's arguments. What is guarded here is
that the remote path returns the function's own answer -- when it divides the
loop into jobs, and when it declines to.

Nothing here is mocked. ``SubprocessJobRunner`` is the real two-method executor
from ``test_execute_single_no_fabrication``: it writes the exact bytes
``serialize_function`` produced to disk and runs them in a fresh interpreter
through the real ``deserialize_function``. A pass means the payload clustrix
actually ships computes the caller's answer.
"""

import importlib
import re
from pathlib import Path

import pytest

import clustrix
from clustrix.decorator import _execute_single, _plan_for, _run_split_remotely

# The real executor, reused rather than re-written. tests/unit has no
# __init__.py, so pytest's default "prepend" import mode puts this directory on
# sys.path and the sibling module imports directly.
from test_execute_single_no_fabrication import (  # noqa: F401
    SubprocessJobRunner,
    runner,
)

PACKAGE_DIR = Path(clustrix.__file__).resolve().parent


# ---------------------------------------------------------------------------
# Defect 1: the fabricated GPU result
# ---------------------------------------------------------------------------


def test_the_gpu_fabrication_machinery_no_longer_exists():
    """The functions are deleted, not merely unreferenced.

    ``_attempt_client_side_gpu_parallelization`` and the three helpers that
    existed only to serve it are gone. ``clustrix.gpu_utils`` is gone with
    them: ``detect_gpu_parallelizable_operations`` was that module's only
    caller anywhere in the repo, and its four remaining public functions --
    ``detect_gpu_availability``, ``create_gpu_parallel_execution_plan``,
    ``generate_gpu_parallel_code``, ``validate_gpu_parallel_result`` -- had no
    caller at all.
    """
    import clustrix.decorator as decorator_module

    for name in (
        "_attempt_client_side_gpu_parallelization",
        "_detect_remote_gpu_count",
        "_create_client_side_gpu_plan",
        "_execute_client_side_gpu_parallel",
    ):
        assert not hasattr(decorator_module, name), f"clustrix.decorator.{name} is back"

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("clustrix.gpu_utils")


def test_no_clustrix_module_fabricates_a_gpu_result():
    """No module may emit the fabricated payload or scrape a canned GPU value.

    Matched on the two fingerprints of the deleted path: the ``gpu_parallel``
    key of the substitute return value, and the ``GPU_<n>_RESULT`` marker the
    hardcoded torch program printed for stdout scraping. Matching on those and
    not on the word "gpu" keeps the surviving inert
    ``auto_gpu_parallel``/``max_gpu_parallel_jobs`` settings from tripping it.
    """
    fabrication_markers = (
        re.compile(r"""["']gpu_parallel["']\s*:"""),
        re.compile(r"GPU_.*_RESULT"),
    )

    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if any(marker.search(line) for marker in fabrication_markers):
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    assert offenders == [], f"a GPU result is fabricated at: {offenders}"


# ---------------------------------------------------------------------------
# Defect 2: the unanswerable chunk call
#
# These are module-level so dill can ship them to the worker interpreter.
# ---------------------------------------------------------------------------

JOB = {"cores": 1, "memory": "1GB"}


def collect(n):
    """An ordinary looping function, with exactly the one parameter."""
    values = []
    for i in range(n):
        values.append(i * 10)
    return values


def collects_extra(n, **kwargs):
    """Declares ``**kwargs``: the old protocol would have filled it."""
    seen = []
    for i in range(n):
        seen.append(sorted(kwargs))
    return seen


def running_maximum(values):
    """Each iteration needs the previous one, so this loop is not divided."""
    best = values[0]
    history = []
    for value in values:
        best = max(best, value)
        history.append(best)
    return history


def test_the_chunk_keyword_protocol_no_longer_exists():
    """No module builds a call with a keyword the user never wrote."""
    import clustrix.decorator as decorator_module

    for name in (
        "_execute_parallel",
        "_create_work_chunks",
        "_combine_results",
        "_accepts_chunk_kwargs",
        "_execute_local_parallel",
        "_create_local_work_chunks",
        "_combine_local_results",
    ):
        assert not hasattr(decorator_module, name), f"clustrix.decorator.{name} is back"

    protocol = re.compile(r"_chunk_range_|_chunk_index|_parallel_\{")
    offenders = [
        f"{path}:{lineno}: {line.strip()}"
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if protocol.search(line)
    ]
    assert offenders == [], f"a chunk keyword is injected at: {offenders}"


def test_a_loop_divided_into_jobs_returns_the_users_answer(runner):
    """Four jobs, each a fresh interpreter, and the caller's own return value
    -- not a list of four pieces of it."""
    plan = _plan_for(collect)
    assert plan.parallelizable, plan.explain()

    result = _run_split_remotely(plan, runner, collect, (8,), {}, JOB, max_jobs=4)

    assert result == collect(8) == [0, 10, 20, 30, 40, 50, 60, 70]
    assert len(runner.submitted) == 4, "one job per chunk of the loop"


def test_a_function_is_never_handed_a_keyword_its_caller_did_not_pass(runner):
    """``**kwargs`` would receive anything invented. It receives nothing."""
    plan = _plan_for(collects_extra)
    assert plan.parallelizable, plan.explain()

    result = _run_split_remotely(
        plan, runner, collects_extra, (3,), {"mine": 1}, JOB, max_jobs=3
    )

    assert result == collects_extra(3, mine=1) == [["mine"]] * 3


def test_a_loop_that_cannot_be_divided_is_one_plain_job_with_the_users_answer(runner):
    """Declining to divide must run the function, not return an empty list.

    ``[]`` -- what no chunks would have combined to -- would be a fabricated
    answer of the same class as defect 1. This is what ``@cluster`` does on
    the remote path when the plan says no.
    """
    assert not _plan_for(running_maximum).parallelizable

    result = _execute_single(runner, running_maximum, ([3, 1, 4, 1, 5],), {}, JOB)

    assert result == running_maximum([3, 1, 4, 1, 5]) == [3, 3, 4, 4, 5]
    assert len(runner.submitted) == 1


# ---------------------------------------------------------------------------
# The settings the deleted path used to read
# ---------------------------------------------------------------------------


def loops_a_little(n):
    """A plain function with a loop, so both auto-* switches are in play."""
    total = 0
    for i in range(3):
        total += i
    return total * n


def test_auto_gpu_parallel_is_inert_and_says_so(caplog):
    """A silently ignored option is worse than a rejected one.

    ``auto_gpu_parallel`` is still accepted -- dropping it would break every
    existing ``@cluster(auto_gpu_parallel=...)`` call site and every
    ``clustrix.yml`` that sets it -- but it no longer switches anything on, so
    setting it deliberately must produce a warning rather than silence. The
    function's own answer is unaffected either way.
    """
    from clustrix import cluster, configure

    configure(cluster_type="local", cluster_host=None)

    @cluster(auto_gpu_parallel=True)
    def decorated(n):
        return loops_a_little(n)

    with caplog.at_level("WARNING", logger="clustrix.decorator"):
        assert decorated(5) == 15

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "auto_gpu_parallel" in record.getMessage()
    ]
    assert warnings, "setting auto_gpu_parallel must not be silently ignored"
    assert "no effect" in warnings[0]
