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

``config.auto_parallel`` also defaults to ``True``. The local path already
declines in this case; the remote path now uses the same check.

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
from clustrix.decorator import _create_work_chunks, _execute_parallel
from clustrix.utils import detect_loops

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


def collect(n):
    """An ordinary looping function. It cannot accept a chunk keyword."""
    values = []
    for i in range(4):
        values.append(i * 10)
    return values


def collects_extra(n, **kwargs):
    """Declares ``**kwargs``, so it can receive whatever it is handed."""
    return sorted(kwargs)


def squares_for_chunk(_chunk_range_i=None, _chunk_index=0):
    """A chunk-aware callee: it computes only its assigned slice."""
    return [i * i for i in _chunk_range_i]


def test_create_work_chunks_declines_a_call_the_callee_cannot_answer():
    """The remote chunker must not invent a keyword the function has no slot for.

    ``collect`` takes exactly one parameter and no ``**kwargs``. Building a
    chunk for it produced ``collect(4, _chunk_range_i=[...], _chunk_index=0)``,
    which is a TypeError on every chunk -- clustrix constructing a call its own
    callee cannot answer.
    """
    loop_info = detect_loops(collect, (4,), {})
    assert loop_info is not None and loop_info["variable"] == "i"

    assert _create_work_chunks(collect, (4,), {}, loop_info, 4) == []


def test_create_work_chunks_still_chunks_a_callee_that_can_answer():
    """The guard must decline only what is genuinely unanswerable.

    A ``**kwargs`` function can receive any keyword, and a function that
    declares the chunk parameters by name can too; both must still be chunked.
    """
    loop_info = {"type": "for", "variable": "i", "range": range(8)}

    kwargs_chunks = _create_work_chunks(collects_extra, (8,), {}, loop_info, 4)
    assert len(kwargs_chunks) == 4
    assert set(kwargs_chunks[0]["kwargs"]) == {"_chunk_range_i", "_chunk_index"}

    named_chunks = _create_work_chunks(squares_for_chunk, (), {}, loop_info, 4)
    assert len(named_chunks) == 4


def test_remote_parallel_returns_the_users_answer_when_it_cannot_chunk(runner):
    """Declining to parallelize must run the function, not return an empty list.

    Before the fix this raised
    ``TypeError: collect() got an unexpected keyword argument '_chunk_range_i'``
    inside the worker. Returning ``[]`` -- what an empty chunk list would have
    combined to -- would be a fabricated answer of the same class as defect 1,
    so the path falls back to a single ordinary submission instead.
    """
    loop_info = detect_loops(collect, (4,), {})

    result = _execute_parallel(
        runner, collect, (4,), {}, {"cores": 1, "memory": "1GB"}, loop_info
    )

    assert result == collect(4) == [0, 10, 20, 30]
    assert len(runner.submitted) == 1, "declining to chunk must submit one plain job"


def test_remote_parallel_really_chunks_a_chunk_aware_callee(runner):
    """The guard must not switch remote parallelization off wholesale.

    ``squares_for_chunk`` declares both chunk parameters, so the work is split,
    each slice is genuinely executed in its own interpreter, and the pieces
    combine to the whole answer.
    """
    loop_info = {"type": "for", "variable": "i", "range": range(8)}

    result = _execute_parallel(
        runner, squares_for_chunk, (), {}, {"cores": 1, "memory": "1GB"}, loop_info
    )

    assert len(runner.submitted) > 1, "a chunk-aware callee must be split up"
    assert [value for chunk in result for value in chunk] == [i * i for i in range(8)]


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
