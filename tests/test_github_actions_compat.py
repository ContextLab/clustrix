"""clustrix must import and degrade honestly where IPython is not installed.

``ipywidgets`` and IPython are optional (the ``widget`` extra). A plain
``pip install clustrix`` on a CI runner or a cluster login node has neither,
and everything except the notebook widget still has to work there.

Each test runs a **separate, real interpreter** in which ``import IPython``
and ``import ipywidgets`` raise ``ImportError``. That is arranged with the
import system's own switch for it -- a ``None`` entry in ``sys.modules``,
which is documented to make the import fail -- set before clustrix is first
imported. Nothing in the child is patched or replaced: the fallback code that
runs is the code a user without IPython gets.

These used to purge and re-import ``clustrix.*`` inside the pytest process
under ``patch.dict(sys.modules, ...)``, which left every later test holding a
second copy of the configuration singleton, and called the magics on a
``MagicMock`` shell.
"""

import os
import subprocess
import sys
import textwrap

#: Run first in every child: after this, importing either package fails the
#: way it does when the package is not installed.
WITHOUT_IPYTHON = textwrap.dedent("""
    import sys

    for name in (
        "IPython",
        "IPython.core",
        "IPython.core.magic",
        "IPython.display",
        "ipywidgets",
    ):
        sys.modules[name] = None
    """)


def _run_without_ipython(body, tmp_path):
    """Run ``body`` in a fresh interpreter that cannot import IPython.

    Returns the child's stdout. A non-zero exit fails the calling test with
    everything the child wrote, so a traceback is never swallowed.
    """
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env["CLUSTRIX_CONFIG_DIR"] = str(tmp_path / ".clustrix")
    completed = subprocess.run(
        [sys.executable, "-c", WITHOUT_IPYTHON + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"the interpreter without IPython exited {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    return completed.stdout


def test_ipython_really_is_unimportable_in_the_child(tmp_path):
    """The arrangement itself: without this the tests below prove nothing."""
    out = _run_without_ipython(
        """
        for name in ("IPython", "ipywidgets"):
            try:
                __import__(name)
            except ImportError:
                print("unimportable", name)
        """,
        tmp_path,
    )

    assert out.split("\n")[:2] == ["unimportable IPython", "unimportable ipywidgets"]


def test_the_package_imports_and_runs_a_function(tmp_path):
    """The point of the fallback: plain Python use is unaffected."""
    out = _run_without_ipython(
        """
        import clustrix
        from clustrix import notebook_magic

        assert notebook_magic.IPYTHON_AVAILABLE is False

        @clustrix.cluster(cores=1)
        def add(a, b):
            return a + b

        print("result", add(2, 3))
        """,
        tmp_path,
    )

    assert "result 5" in out


def test_the_line_magic_still_reports_the_configuration(tmp_path):
    """``%clustrix`` needs no display backend, so it works as a method."""
    out = _run_without_ipython(
        """
        from clustrix import configure
        from clustrix.notebook_magic import ClusterfyMagics

        configure(cluster_type="slurm", cluster_host="hpc.example.edu")
        returned = ClusterfyMagics().clustrix("status")
        print("returned", returned.cluster_host)
        """,
        tmp_path,
    )

    assert "cluster_type : slurm" in out
    assert "host         : hpc.example.edu" in out
    assert "returned hpc.example.edu" in out


def test_the_cell_magic_says_what_is_missing_and_runs_nothing(tmp_path):
    """``%%remote`` exists to show a widget; without one it must say so."""
    out = _run_without_ipython(
        """
        import warnings

        from clustrix.notebook_magic import ClusterfyMagics

        magics = ClusterfyMagics()
        print("remote returned", magics.remote("", "raise SystemExit(3)"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            print("clusterfy returned", magics.clusterfy("", ""))
        print("warned", [w.category.__name__ for w in caught])
        """,
        tmp_path,
    )

    assert out.count("The configuration widget needs IPython and ipywidgets") == 2
    assert "pip install 'clustrix[widget]'" in out
    assert "remote returned None" in out
    assert "clusterfy returned None" in out
    assert "warned ['DeprecationWarning']" in out


def test_constructing_the_widget_names_the_missing_packages(tmp_path):
    out = _run_without_ipython(
        """
        from clustrix import ModernClustrixWidget, display_modern_widget

        try:
            ModernClustrixWidget()
        except ImportError as error:
            print("refused:", error)

        print("display returned", display_modern_widget())
        """,
        tmp_path,
    )

    assert "refused: IPython and ipywidgets are required" in out
    assert "pip install 'clustrix[widget]'" in out
    assert "display returned None" in out


def test_the_import_time_hooks_do_nothing(tmp_path):
    """Neither hook may raise, print, or touch the object it is handed."""
    out = _run_without_ipython(
        """
        import os

        from clustrix.notebook_magic import (
            auto_display_on_import,
            load_ipython_extension,
        )

        class Shell:
            user_ns = {}

            def register_magic_function(self, *args):
                raise AssertionError("a magic was registered without IPython")

        os.environ["CLUSTRIX_AUTO_WIDGET"] = "1"
        auto_display_on_import()
        shell = Shell()
        load_ipython_extension(shell)
        print("namespace", shell.user_ns)
        """,
        tmp_path,
    )

    assert out == "namespace {}\n"
