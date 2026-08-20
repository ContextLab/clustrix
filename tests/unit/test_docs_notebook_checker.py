#!/usr/bin/env python3
"""``scripts/check_docs_examples.py`` has to look at the notebooks (#166).

It did not. It walked ``docs/source`` for ``.rst`` and ``.md``, read the
docstrings of every ``automodule``-d module, and reported everything green
while all seven published notebooks went unopened::

    30 files checked
    notebooks checked: 0
    notebooks present: 7

Two real regressions rode through that hole: the SLURM notebook went on
describing the ``queue`` argument after #158 removed it, and two notebooks went
on describing the pre-#152 ``cores`` behaviour.

These tests build real notebooks on disk and run the real checker over them --
including a real Jupyter kernel for the execution cases. Nothing here is
mocked, and nothing here touches a network, a cluster or a paid provider; the
notebooks that would do any of those are the ones the checker refuses to run,
which is itself one of the things under test.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_docs_examples.py"
NOTEBOOK_DIR = REPO_ROOT / "docs" / "source" / "notebooks"


def _load_checker():
    """Import the checker script as a module, the way a caller would run it."""
    spec = importlib.util.spec_from_file_location("check_docs_examples", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registering before exec matters on 3.9: dataclasses resolves annotations
    # through sys.modules[cls.__module__] and raises AttributeError without it.
    sys.modules["check_docs_examples"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# ---------------------------------------------------------------------------
# Notebook construction helpers
# ---------------------------------------------------------------------------


_next_id = itertools.count()


def code_cell(source, outputs=None, execution_count=None):
    return {
        "cell_type": "code",
        "id": f"cell{next(_next_id)}",
        "metadata": {},
        "source": source,
        "outputs": outputs or [],
        "execution_count": execution_count,
    }


def markdown_cell(source):
    return {
        "cell_type": "markdown",
        "id": f"cell{next(_next_id)}",
        "metadata": {},
        "source": source,
    }


def stdout(text):
    return {"output_type": "stream", "name": "stdout", "text": text}


def error_output(ename, evalue):
    return {
        "output_type": "error",
        "ename": ename,
        "evalue": evalue,
        "traceback": [f"{ename}: {evalue}"],
    }


def write_notebook(path: Path, cells) -> Path:
    path.write_text(
        json.dumps(
            {
                "cells": cells,
                "metadata": {
                    "kernelspec": {
                        "display_name": "Python 3",
                        "language": "python",
                        "name": "python3",
                    },
                    "language_info": {"name": "python", "version": "3"},
                },
                "nbformat": 4,
                "nbformat_minor": 5,
            }
        )
    )
    return path


def check(path: Path):
    """Run the checker over one notebook, in the child process main() uses."""
    return checker._check_file_in_subprocess(checker.TargetFile(path, "ipynb"))


def failures(results):
    return [r for r in results if not r.passed]


def details(results):
    return " || ".join(r.detail for r in results)


# ---------------------------------------------------------------------------
# Discovery: derived, never curated
# ---------------------------------------------------------------------------


def test_every_published_notebook_is_discovered():
    """The seven notebooks on disk are the seven the checker will open."""
    discovered = {
        target.path
        for target in checker._discover_under(REPO_ROOT / "docs" / "source")
        if target.kind == "ipynb"
    }
    on_disk = {
        path
        for path in (REPO_ROOT / "docs" / "source").rglob("*.ipynb")
        if ".ipynb_checkpoints" not in path.parts
    }
    assert on_disk, "no notebooks under docs/source -- this test has gone stale"
    assert discovered == on_disk


def test_a_notebook_added_tomorrow_is_discovered_tomorrow(tmp_path, monkeypatch):
    """Discovery is a walk, not a list. #166's whole point."""
    source = tmp_path / "docs" / "source" / "notebooks"
    source.mkdir(parents=True)
    brand_new = write_notebook(source / "written_today.ipynb", [code_cell("x = 1\n")])
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)

    discovered = [
        target.path
        for target in checker._discover_under(tmp_path / "docs" / "source")
        if target.kind == "ipynb"
    ]
    assert discovered == [brand_new]


def test_checkpoint_copies_are_not_documentation(tmp_path, monkeypatch):
    source = tmp_path / "docs" / "source" / "notebooks"
    (source / ".ipynb_checkpoints").mkdir(parents=True)
    write_notebook(source / ".ipynb_checkpoints" / "a-checkpoint.ipynb", [])
    real = write_notebook(source / "a.ipynb", [code_cell("x = 1\n")])
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)

    discovered = [
        target.path for target in checker._discover_under(tmp_path / "docs" / "source")
    ]
    assert discovered == [real]


# ---------------------------------------------------------------------------
# Compilation and API drift
# ---------------------------------------------------------------------------


def test_a_syntax_error_in_a_cell_fails(tmp_path):
    path = write_notebook(
        tmp_path / "broken.ipynb",
        [code_cell("x = 1\n"), code_cell("def broken(:\n    return 1\n")],
    )
    bad = failures(check(path))
    assert bad, "a cell that does not compile must not pass"
    assert any("SyntaxError" in r.detail for r in bad)
    assert any(r.block.cell_index == 1 for r in bad)


def test_a_notebook_that_does_not_compile_is_never_executed(tmp_path):
    """Compile first. Executing past a syntax error only produces cascades."""
    path = write_notebook(
        tmp_path / "broken.ipynb",
        [code_cell("open('ran.txt', 'w').write('x')\n"), code_cell("def broken(:\n")],
    )
    results = check(path)
    assert any("not executed" in r.detail for r in results)
    assert not (tmp_path / "ran.txt").exists()


def test_an_import_of_a_name_the_package_removed_fails(tmp_path):
    path = write_notebook(
        tmp_path / "gone.ipynb",
        [code_cell("from clustrix import no_such_public_name\n")],
    )
    bad = failures(check(path))
    assert any("no_such_public_name" in r.detail for r in bad)


def test_a_removed_decorator_parameter_fails(tmp_path):
    """``@cluster(queue=...)`` is #158's removal, and it is not a TypeError.

    ``cluster`` takes ``**kwargs``, so a removed keyword is accepted and
    silently does nothing. The checker asks the real package what it makes of
    the keyword names rather than keeping its own copy of the answer.
    """
    path = write_notebook(
        tmp_path / "queue.ipynb",
        [
            code_cell(
                "from clustrix import cluster\n\n\n"
                '@cluster(cores=2, queue="gpu")\n'
                "def f(x):\n"
                "    return x\n"
            )
        ],
    )
    bad = failures(check(path))
    assert bad, "@cluster(queue=...) must be reported"
    assert any("queue" in r.detail for r in bad)


def test_keywords_the_package_really_does_accept_are_not_reported(tmp_path):
    """The other half of the same check: no crying wolf over real extras."""
    path = write_notebook(
        tmp_path / "hf.ipynb",
        [
            code_cell(
                "from clustrix import cluster\n\n\n"
                '@cluster(cores=2, memory="4GB", hf_flavor="cpu-basic")\n'
                "def f(x):\n"
                "    return x\n"
            )
        ],
    )
    assert not failures(check(path)), details(check(path))


def test_a_setting_configure_rejects_fails(tmp_path):
    path = write_notebook(
        tmp_path / "cfg.ipynb",
        [code_cell("from clustrix import configure\n\nconfigure(queue='gpu')\n")],
    )
    bad = failures(check(path))
    assert any("queue" in r.detail for r in bad)


# ---------------------------------------------------------------------------
# Cluster/provider cells: marked, verified statically, never submitted
# ---------------------------------------------------------------------------


def test_an_unmarked_cell_that_would_reach_a_host_fails(tmp_path):
    path = write_notebook(
        tmp_path / "ssh.ipynb",
        [
            code_cell("from clustrix import configure\n"),
            code_cell(
                "configure(\n"
                '    cluster_type="ssh",\n'
                '    cluster_host="server.example.com",\n'
                '    username="someone",\n'
                ")\n"
            ),
        ],
    )
    results = check(path)
    bad = failures(results)
    assert any("cluster-required" in r.detail for r in bad), details(bad)
    assert any(r.block.cell_index == 1 for r in bad)
    assert any("notebook not executed" in r.detail for r in results)


def test_a_marked_cell_is_verified_but_never_run(tmp_path):
    """The ``# cluster-required`` convention the .rst blocks use, reused."""
    path = write_notebook(
        tmp_path / "marked.ipynb",
        [
            code_cell("from clustrix import configure\n"),
            code_cell(
                "# cluster-required: needs a real SSH host\n"
                "configure(\n"
                '    cluster_type="ssh",\n'
                '    cluster_host="server.example.com",\n'
                ")\n"
            ),
        ],
    )
    results = check(path)
    assert not failures(results), details(results)
    assert any("notebook not executed" in r.detail for r in results)
    assert all("executed OK" not in r.detail for r in results)


def test_a_marked_cell_is_still_compiled_and_its_imports_still_checked(tmp_path):
    path = write_notebook(
        tmp_path / "marked_broken.ipynb",
        [
            code_cell(
                "# cluster-required: needs a real SSH host\n"
                "from clustrix import no_such_public_name\n"
            )
        ],
    )
    bad = failures(check(path))
    assert any("no_such_public_name" in r.detail for r in bad)


def test_marking_one_cell_holds_back_the_whole_notebook(tmp_path):
    """Skipping a cell and running the next one produces noise, not coverage."""
    path = write_notebook(
        tmp_path / "mixed.ipynb",
        [
            code_cell(
                "# cluster-required: needs a real SSH host\n"
                "from clustrix import configure\n\n"
                'configure(cluster_type="ssh", cluster_host="h.example.com")\n'
            ),
            code_cell("open('ran.txt', 'w').write('x')\n"),
        ],
    )
    results = check(path)
    assert not failures(results), details(results)
    assert not (tmp_path / "ran.txt").exists()


def test_local_only_configuration_is_not_treated_as_remote(tmp_path):
    """``cluster_host=None`` is the local path; flagging it would cost coverage."""
    path = write_notebook(
        tmp_path / "local.ipynb",
        [
            code_cell(
                "import clustrix\n\n"
                'clustrix.configure(cluster_type="local", cluster_host=None)\n'
                "print(clustrix.get_config().cluster_type)\n"
            )
        ],
    )
    results = check(path)
    assert not failures(results), details(results)
    assert any("executed OK" in r.detail for r in results)


def test_building_a_config_object_is_not_reaching_a_host(tmp_path):
    """``complete_api_demo`` builds SLURM/SSH configs purely to print them."""
    path = write_notebook(
        tmp_path / "cfgobj.ipynb",
        [
            code_cell(
                "from clustrix.config import ClusterConfig\n\n"
                "config = ClusterConfig(\n"
                '    cluster_type="slurm",\n'
                '    cluster_host="slurm-cluster.edu",\n'
                '    username="researcher",\n'
                ")\n"
                "print(config.cluster_type)\n"
            )
        ],
    )
    results = check(path)
    assert not failures(results), details(results)
    assert any("executed OK" in r.detail for r in results)


# ---------------------------------------------------------------------------
# Execution in a clean kernel
# ---------------------------------------------------------------------------


def test_a_self_contained_notebook_runs_in_a_clean_kernel(tmp_path):
    path = write_notebook(
        tmp_path / "clean.ipynb",
        [
            markdown_cell("# heading\n"),
            code_cell("import clustrix\n\nvalue = 21\n"),
            code_cell("print(value * 2)\n"),
        ],
    )
    results = check(path)
    assert not failures(results), details(results)
    assert sum("executed OK in a clean kernel" in r.detail for r in results) == 2


def test_the_kernel_runs_the_interpreter_the_checker_runs(tmp_path):
    """Borrowing a stray ``python3`` kernelspec reports the env, not the docs."""
    path = write_notebook(
        tmp_path / "which.ipynb",
        [
            code_cell(
                "import sys\n"
                "import clustrix\n"
                "print(sys.executable)\n"
                "assert clustrix.__file__\n"
            )
        ],
    )
    assert not failures(check(path))


def test_a_cell_that_raises_fails(tmp_path):
    path = write_notebook(
        tmp_path / "raises.ipynb",
        [code_cell("value = 1\n"), code_cell("raise RuntimeError('boom')\n")],
    )
    bad = failures(check(path))
    assert any("RuntimeError" in r.detail and "boom" in r.detail for r in bad)


def test_state_carries_from_one_cell_to_the_next(tmp_path):
    """A notebook is one session; checking cells in isolation would be wrong."""
    path = write_notebook(
        tmp_path / "state.ipynb",
        [code_cell("shared = 5\n"), code_cell("assert shared == 5\n")],
    )
    assert not failures(check(path))


def test_a_cell_that_never_finishes_fails_instead_of_hanging(tmp_path, monkeypatch):
    """Bounded execution. The budget is shortened here; the code path is real."""
    monkeypatch.setattr(checker, "NOTEBOOK_CELL_TIMEOUT_SECONDS", 5)
    path = write_notebook(
        tmp_path / "hang.ipynb",
        [code_cell("import time\n\ntime.sleep(600)\n")],
    )
    results = checker.check_notebook(
        checker.TargetFile(path, "ipynb"),
        checker.extract_notebook_blocks(checker.TargetFile(path, "ipynb")),
    )
    bad = failures(results)
    assert bad, "a cell that never finishes must fail"
    assert any("did not run to completion" in r.detail for r in bad)


def test_the_notebook_timeout_is_larger_than_the_prose_one():
    """A tutorial cell is allowed to be a benchmark; a prose snippet is not."""
    assert checker.NOTEBOOK_TIMEOUT_SECONDS > checker.FILE_TIMEOUT_SECONDS
    assert (
        checker._timeout_for(checker.TargetFile(NOTEBOOK_DIR, "ipynb"))
        == checker.NOTEBOOK_TIMEOUT_SECONDS
    )


# ---------------------------------------------------------------------------
# IPython magics
# ---------------------------------------------------------------------------


def test_a_clustrix_cell_magic_body_is_checked_as_python(tmp_path):
    """``%%remote`` runs its body through ``shell.run_cell``, so it is code."""
    source, note = checker._cell_to_python("%%remote\nfrom clustrix import nope\n")
    assert "from clustrix import nope" in source
    assert "%%remote" in note

    path = write_notebook(
        tmp_path / "magic.ipynb",
        [code_cell("%%remote\nfrom clustrix import no_such_public_name\n")],
    )
    bad = failures(check(path))
    assert any("no_such_public_name" in r.detail for r in bad)


def test_an_unknown_cell_magic_body_is_reported_as_unverified():
    source, note = checker._cell_to_python("%%bash\nls -la\n")
    assert source == ""
    assert "not verified" in note.lower()


def test_shell_escapes_are_removed_and_reported():
    source, note = checker._cell_to_python("!pip install clustrix\nx = 1\n")
    assert "pip install" not in source
    assert "x = 1" in source
    assert "not verified" in note.lower()
    compile(source, "<cell>", "exec")


def test_a_live_shell_escape_stops_the_notebook_being_run(tmp_path):
    """nbclient runs the *original* source, so ``!pip install`` would install."""
    path = write_notebook(
        tmp_path / "install.ipynb",
        [
            code_cell("!pip install clustrix\n"),
            code_cell("open('ran.txt', 'w').write('x')\n"),
        ],
    )
    results = check(path)
    assert any("not executed" in r.detail for r in results), details(results)
    assert not (tmp_path / "ran.txt").exists()


def test_an_unknown_cell_magic_stops_the_notebook_being_run(tmp_path):
    path = write_notebook(
        tmp_path / "bash.ipynb",
        [
            code_cell("%%bash\necho hi\n"),
            code_cell("open('ran.txt', 'w').write('x')\n"),
        ],
    )
    results = check(path)
    assert any("not executed" in r.detail for r in results), details(results)
    assert not (tmp_path / "ran.txt").exists()


def test_an_ordinary_line_magic_does_not_stop_the_notebook_being_run(tmp_path):
    """``%time`` is safe and common; refusing it would cost real coverage."""
    path = write_notebook(
        tmp_path / "time.ipynb",
        [code_cell("%time x = sum(range(10))\nprint(x)\n")],
    )
    results = check(path)
    assert not failures(results), details(results)
    assert any("executed OK" in r.detail for r in results)


def test_a_commented_out_shell_escape_is_left_alone():
    source, note = checker._cell_to_python("# !pip install clustrix\nx = 1\n")
    assert "# !pip install clustrix" in source
    assert note == ""


# ---------------------------------------------------------------------------
# Stored output
# ---------------------------------------------------------------------------


def test_a_stored_traceback_fails(tmp_path):
    path = write_notebook(
        tmp_path / "traceback.ipynb",
        [
            code_cell(
                "value = 1\n",
                outputs=[error_output("NameError", "name 'value' is not defined")],
                execution_count=1,
            )
        ],
    )
    bad = failures(check(path))
    assert any("stored output is a traceback" in r.detail for r in bad)


def test_output_saved_from_a_scrambled_session_fails(tmp_path):
    path = write_notebook(
        tmp_path / "scrambled.ipynb",
        [
            code_cell("print('a')\n", outputs=[stdout("a\n")], execution_count=4),
            code_cell("print('b')\n", outputs=[stdout("b\n")], execution_count=2),
        ],
    )
    bad = failures(check(path))
    assert any("clean top-to-bottom run" in r.detail for r in bad), details(bad)


def test_output_saved_from_one_clean_run_passes(tmp_path):
    path = write_notebook(
        tmp_path / "clean_run.ipynb",
        [
            code_cell("print('a')\n", outputs=[stdout("a\n")], execution_count=1),
            code_cell("print('b')\n", outputs=[stdout("b\n")], execution_count=2),
        ],
    )
    results = check(path)
    assert not failures(results), details(results)


def test_a_notebook_with_no_stored_output_claims_nothing(tmp_path):
    """Stripped output is not stale output. Failing it would be crying wolf."""
    path = write_notebook(
        tmp_path / "stripped.ipynb",
        [code_cell("print('a')\n"), code_cell("print('b')\n")],
    )
    assert not failures(check(path))


def test_output_that_no_longer_matches_a_fresh_run_fails(tmp_path):
    """The cell used to print; now it also warns. The page shows only the print."""
    path = write_notebook(
        tmp_path / "stale.ipynb",
        [
            code_cell(
                "import sys\n\nprint('hello')\nprint('warned', file=sys.stderr)\n",
                outputs=[stdout("hello\n")],
                execution_count=1,
            )
        ],
    )
    bad = failures(check(path))
    assert any("stored output is stale" in r.detail for r in bad), details(bad)


def test_timings_and_hostnames_do_not_count_as_stale(tmp_path):
    """The deliberate limit: shape is compared, text is not.

    Every value printed here differs between two correct runs on two machines.
    A checker that failed on this would be switched off within a week.
    """
    path = write_notebook(
        tmp_path / "noisy.ipynb",
        [
            code_cell(
                "import multiprocessing\n"
                "import os\n"
                "import platform\n"
                "import time\n\n"
                "start = time.perf_counter()\n"
                "print(f'system     {platform.system()}')\n"
                "print(f'cpus       {os.cpu_count()}')\n"
                "print(f'start      {multiprocessing.get_start_method()}')\n"
                "print(f'elapsed    {time.perf_counter() - start:.6f}s')\n"
                "print(object())\n",
                outputs=[
                    stdout(
                        "system     Plan9\n"
                        "cpus       9999\n"
                        "start      forkserver\n"
                        "elapsed    0.000001s\n"
                        "<object object at 0x0000000000>\n"
                    )
                ],
                execution_count=1,
            )
        ],
    )
    results = check(path)
    assert not failures(results), details(results)


def test_extra_stream_chunking_is_not_staleness(tmp_path):
    """A kernel may split one print run across several stream messages."""
    path = write_notebook(
        tmp_path / "chunked.ipynb",
        [
            code_cell(
                "for i in range(3):\n    print(i)\n",
                outputs=[stdout("0\n"), stdout("1\n"), stdout("2\n")],
                execution_count=1,
            )
        ],
    )
    results = check(path)
    assert not failures(results), details(results)


# ---------------------------------------------------------------------------
# The committed notebooks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", sorted(path.name for path in NOTEBOOK_DIR.glob("*.ipynb"))
)
def test_every_committed_notebook_yields_at_least_one_check(name):
    """No published notebook may pass by not being looked at."""
    target = checker.TargetFile(NOTEBOOK_DIR / name, "ipynb")
    blocks = checker.extract_notebook_blocks(target)
    assert blocks, f"{name} produced no checkable cells"
    for block in blocks:
        assert block.cell_index is not None
        assert block.where.startswith("cell ")
