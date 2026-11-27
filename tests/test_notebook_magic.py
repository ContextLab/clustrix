"""The IPython side of clustrix: ``%load_ext``, ``%clustrix`` and ``%%remote``.

Driven through a real ``IPython.core.interactiveshell.InteractiveShell`` --
the object a notebook kernel's shell is -- with real configuration files in a
real temporary directory. Nothing is mocked: the magics are found the way
IPython finds them, cells are run with ``run_cell``, and what a cell displays
is read with IPython's own ``capture_output``.

Where a test needs something this process cannot honestly provide -- a shell
that existed *before* clustrix was imported, or a real kernel -- it starts a
real interpreter and does it there.
"""

import json
import os
import subprocess
import sys
import textwrap
import warnings

import pytest
import yaml

pytest.importorskip("IPython")
pytest.importorskip("ipywidgets")

from IPython import get_ipython  # noqa: E402
from IPython.core.interactiveshell import InteractiveShell  # noqa: E402
from IPython.utils.capture import capture_output  # noqa: E402
from traitlets.config import Config  # noqa: E402

import clustrix  # noqa: E402
from clustrix import configure, get_config  # noqa: E402
from clustrix import notebook_magic, notebook_magic_fallback  # noqa: E402
from clustrix.config import ClusterConfig  # noqa: E402
from clustrix.profile_manager import ProfileManager  # noqa: E402

WIDGET_VIEW = "application/vnd.jupyter.widget-view+json"


@pytest.fixture
def shell():
    """A real, fresh IPython shell that is the process's current one.

    The singleton rather than a bare ``InteractiveShell()``, because that is
    what ``get_ipython()`` and ``capture_output`` look for -- and it is
    cleared afterwards so the next test starts without one, exactly as the
    rest of the suite expects. History is off: it would open a SQLite file
    and a writer thread for a shell that lives a few milliseconds.
    """
    assert get_ipython() is None, "a previous test left an IPython shell behind"
    config = Config()
    config.HistoryManager.enabled = False
    ip = InteractiveShell.instance(config=config)
    try:
        yield ip
    finally:
        InteractiveShell.clear_instance()


@pytest.fixture
def loaded(shell):
    """The shell after ``%load_ext clustrix``."""
    shell.run_line_magic("load_ext", "clustrix")
    return shell


def _run(shell, cell):
    """Run a cell; return (result, captured). Fails loudly if the cell raised."""
    with capture_output() as captured:
        result = shell.run_cell(cell)
    assert (
        result.success
    ), f"the cell raised: {result.error_in_exec!r}\n{captured.stdout}"
    return result, captured


def _widgets_displayed(captured):
    return [output for output in captured.outputs if WIDGET_VIEW in output.data]


# --------------------------------------------------------------------------
# %load_ext clustrix
# --------------------------------------------------------------------------


class TestLoadingTheExtension:
    def test_the_magics_do_not_exist_until_it_is_loaded(self, shell):
        """The control for everything below."""
        assert "clustrix" not in shell.magics_manager.magics["line"]
        assert "remote" not in shell.magics_manager.magics["cell"]
        assert "cluster" not in shell.user_ns

    def test_load_ext_registers_the_three_magics(self, shell):
        """``clustrix`` was imported before this shell existed, which is the
        order that used to answer "The clustrix module is not an IPython
        extension"."""
        with capture_output() as captured:
            shell.run_line_magic("load_ext", "clustrix")

        assert "not an IPython extension" not in captured.stdout
        assert "clustrix" in shell.magics_manager.magics["line"]
        assert "remote" in shell.magics_manager.magics["cell"]
        assert "clusterfy" in shell.magics_manager.magics["cell"]
        assert "clustrix" in shell.extension_manager.loaded

    def test_load_ext_says_and_shows_nothing(self, shell):
        """Loading is not a request to see the widget."""
        with capture_output() as captured:
            shell.run_line_magic("load_ext", "clustrix")

        assert captured.stdout == ""
        assert captured.outputs == []

    def test_it_puts_the_three_entry_points_in_the_notebook_namespace(self, loaded):
        """So the notebook can *use* clustrix, not merely type its magics."""
        assert loaded.user_ns["cluster"] is clustrix.cluster
        assert loaded.user_ns["configure"] is clustrix.configure
        assert loaded.user_ns["get_config"] is clustrix.get_config

        _run(
            loaded,
            "configure(default_cores=9)\n"
            "@cluster(cores=1)\n"
            "def triple(x):\n"
            "    return 3 * x\n"
            "answer = triple(14)\n"
            "cores = get_config().default_cores\n",
        )
        assert loaded.user_ns["answer"] == 42
        assert loaded.user_ns["cores"] == 9

    def test_it_does_not_overwrite_a_name_the_notebook_already_uses(self, shell):
        shell.user_ns["cluster"] = "my own variable"

        shell.run_line_magic("load_ext", "clustrix")

        assert shell.user_ns["cluster"] == "my own variable"
        assert shell.user_ns["configure"] is clustrix.configure

    def test_loading_twice_is_harmless(self, loaded):
        with capture_output() as captured:
            loaded.run_line_magic("load_ext", "clustrix")
            loaded.run_line_magic("reload_ext", "clustrix")

        assert "Traceback" not in captured.stdout
        assert "clustrix" in loaded.magics_manager.magics["line"]
        # ``config``, not ``status``: status returns nothing by design.
        result, _ = _run(loaded, "%clustrix config")
        assert result.result is get_config()

    def test_the_extension_hook_is_importable_from_every_documented_place(self):
        assert clustrix.load_ipython_extension is notebook_magic.load_ipython_extension
        assert sorted(notebook_magic.__all__) == [
            "ClusterfyMagics",
            "IPYTHON_AVAILABLE",
            "auto_display_on_import",
            "display_config_widget",
            "load_ipython_extension",
        ]
        assert notebook_magic.IPYTHON_AVAILABLE is True


# --------------------------------------------------------------------------
# %clustrix
# --------------------------------------------------------------------------


class TestStatus:
    def _configure_a_cluster(self):
        configure(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            default_cores=12,
            default_memory="48GB",
            default_time="03:00:00",
            remote_work_dir="/scratch/researcher",
        )

    SUMMARY = [
        "backend      : slurm",
        "host         : hpc.example.edu",
        "username     : researcher",
        "resources    : 12 cores, 48GB, 03:00:00",
        "work dir     : /scratch/researcher",
    ]

    @pytest.mark.parametrize("line", ["status", ""])
    def test_status_prints_a_summary_and_returns_nothing(self, loaded, line):
        """This used to assert ``returned is get_config()``. Returning it is
        what made a notebook print the whole dataclass under the summary."""
        self._configure_a_cluster()

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", line)

        assert returned is None
        assert captured.stdout.splitlines() == self.SUMMARY

    def test_status_as_a_cell_shows_the_summary_and_no_dataclass(self, loaded):
        """The cell a user actually runs: its whole visible output."""
        self._configure_a_cluster()

        result, captured = _run(loaded, "%clustrix status")
        print(captured.stdout)

        assert result.result is None
        assert captured.stdout.splitlines() == self.SUMMARY
        assert "ClusterConfig(" not in captured.stdout

    def test_config_prints_the_summary_and_returns_the_configuration(self, loaded):
        self._configure_a_cluster()

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", "config")

        assert returned is get_config()
        assert captured.stdout.splitlines() == self.SUMMARY

    def test_the_default_configuration_is_reported_as_local(self, loaded):
        """Nothing configured runs the function here, and the status says
        so. (The default was once ``slurm``, which also ran here, and the
        status said ``cluster_type : slurm`` for that.)"""
        fresh = ClusterConfig()
        assert (fresh.cluster_type, fresh.cluster_host) == ("local", None)
        configure(**_fields(fresh))

        with capture_output() as captured:
            loaded.run_line_magic("clustrix", "status")
        print(captured.stdout)

        @clustrix.cluster
        def where():
            return os.getpid()

        assert where() == os.getpid(), "the premise: this configuration runs here"
        assert captured.stdout.splitlines() == [
            "backend      : local",
            f"resources    : {fresh.default_cores} cores, "
            f"{fresh.default_memory}, {fresh.default_time}",
        ]

    def test_a_host_that_is_set_but_not_used_is_reported_as_local(self, loaded):
        configure(
            cluster_type="ssh",
            cluster_host="box.example.edu",
            prefer_local_parallel=True,
        )

        with capture_output() as captured:
            loaded.run_line_magic("clustrix", "status")

        assert captured.stdout.splitlines()[0] == (
            "backend      : local (prefer_local_parallel is set; "
            "cluster_type is 'ssh')"
        )
        assert "box.example.edu" not in captured.stdout

    def test_a_backend_that_needs_no_host_is_not_called_local(self, loaded):
        configure(cluster_type="huggingface", cluster_host=None, username=None)

        with capture_output() as captured:
            loaded.run_line_magic("clustrix", "status")

        assert captured.stdout.splitlines()[0] == "backend      : huggingface"

    def test_no_host_line_is_printed_when_there_is_no_host(self, loaded):
        configure(cluster_type="local", cluster_host=None, username=None)

        with capture_output() as captured:
            loaded.run_line_magic("clustrix", "status")

        assert "backend      : local\n" in captured.stdout
        assert "host" not in captured.stdout
        assert "username" not in captured.stdout

    def test_it_follows_a_change_made_after_the_extension_was_loaded(self, loaded):
        with capture_output() as first:
            loaded.run_line_magic("clustrix", "status")
        configure(default_cores=64)
        with capture_output() as second:
            loaded.run_line_magic("clustrix", "status")

        assert "64 cores" not in first.stdout
        assert "64 cores" in second.stdout

    def test_a_secret_is_not_echoed_as_the_cells_result(self, loaded):
        """The magic returns the configuration, so a notebook shows it as the
        cell's ``Out[]`` -- and a notebook's outputs are saved to disk."""
        # Assembled, so no credential-shaped literal sits in the source.
        typed_password = "-".join(["typed", "passphrase", "value"])
        typed_token = "-".join(["typed", "token", "value"])
        configure(
            cluster_type="ssh",
            cluster_host="h.example.edu",
            username="me",
            password=typed_password,
            hf_token=typed_token,
        )

        _, captured = _run(loaded, "%clustrix config")

        assert "ClusterConfig(" in captured.stdout, "the result was not shown at all"
        assert typed_password not in captured.stdout
        assert typed_token not in captured.stdout

    def test_an_unknown_command_is_refused_and_lists_the_real_ones(self, loaded):
        before = ClusterConfig(**_fields(get_config()))

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", "frobnicate now")

        assert returned is None
        assert "Unknown command 'frobnicate'" in captured.stdout
        assert "status | config | config <profile> | load <file>" in captured.stdout
        assert _fields(get_config()) == _fields(before)


def _fields(config):
    from dataclasses import asdict

    return asdict(config)


class TestApplyingASavedProfile:
    def test_config_with_a_name_makes_that_profile_the_live_configuration(self, loaded):
        manager = ProfileManager()
        manager.save_profile(
            "Discovery",
            ClusterConfig(
                cluster_type="slurm",
                cluster_host="discovery.example.edu",
                username="researcher",
                default_cores=16,
                default_partition="gpu",
            ),
        )

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", "config Discovery")

        assert "Applied profile 'Discovery'" in captured.stdout
        live = get_config()
        assert returned is live
        assert (live.cluster_type, live.cluster_host, live.username) == (
            "slurm",
            "discovery.example.edu",
            "researcher",
        )
        assert live.default_cores == 16
        # A profile is the whole configuration, not just what a form shows.
        assert live.default_partition == "gpu"

    def test_a_profile_name_may_contain_spaces(self, loaded):
        with capture_output() as captured:
            loaded.run_line_magic("clustrix", "config Local all cores")

        assert "Applied profile 'Local all cores'" in captured.stdout
        assert get_config().cluster_type == "local"
        assert get_config().default_cores == -1

    def test_it_also_becomes_the_active_profile_in_the_store(self, loaded):
        """ "Make a saved profile active": the widget opened next shows it."""
        assert ProfileManager().active_profile == "Local single-core"

        loaded.run_line_magic("clustrix", "config SLURM cluster")

        assert ProfileManager().active_profile == "SLURM cluster"

    def test_the_profile_replaces_the_configuration_rather_than_merging(self, loaded):
        """Dropping a profile's unset fields left the previous host live, so
        a local profile applied after a cluster one still ran on the cluster."""
        configure(
            cluster_type="slurm",
            cluster_host="hpc.example.edu",
            username="researcher",
            key_file="~/.ssh/id_hpc",
        )

        loaded.run_line_magic("clustrix", "config Local single-core")

        live = get_config()
        assert live.cluster_type == "local"
        assert live.cluster_host is None
        assert live.username is None
        assert live.key_file is None

    def test_a_function_runs_according_to_the_profile_just_applied(self, loaded):
        configure(cluster_type="ssh", cluster_host="no-such-host.invalid")
        _run(
            loaded,
            "import os\n"
            "@cluster(cores=1)\n"
            "def here():\n"
            "    return os.getpid()\n",
        )

        _run(loaded, "%clustrix config Local single-core\nran_in = here()\n")

        assert loaded.user_ns["ran_in"] == os.getpid()

    def test_an_unknown_profile_is_refused_and_the_real_ones_are_listed(self, loaded):
        before = _fields(get_config())

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", "config No such profile")

        assert returned is None
        assert "No profile named 'No such profile'" in captured.stdout
        for name in ProfileManager().get_profile_names():
            assert name in captured.stdout
        assert _fields(get_config()) == before
        assert ProfileManager().active_profile == "Local single-core"


class TestLoadingAFile:
    SETTINGS = {
        "cluster_type": "slurm",
        "cluster_host": "from-file.example.edu",
        "username": "researcher",
        "default_cores": 24,
        "default_memory": "96GB",
        "module_loads": ["python/3.11"],
    }

    def _assert_loaded(self):
        live = get_config()
        for name, value in self.SETTINGS.items():
            assert getattr(live, name) == value, name

    def test_a_yaml_file_becomes_the_live_configuration(self, loaded, tmp_path):
        path = tmp_path / "cluster.yml"
        path.write_text(yaml.safe_dump(self.SETTINGS), encoding="utf-8")

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", f"load {path}")

        assert f"Loaded configuration from {path}" in captured.stdout
        assert returned is get_config()
        self._assert_loaded()

    def test_a_json_file_becomes_the_live_configuration(self, loaded, tmp_path):
        path = tmp_path / "cluster.json"
        path.write_text(json.dumps(self.SETTINGS), encoding="utf-8")

        loaded.run_line_magic("clustrix", f"load {path}")

        self._assert_loaded()

    def test_a_relative_path_is_relative_to_the_working_directory(
        self, loaded, tmp_path, monkeypatch
    ):
        (tmp_path / "here.yml").write_text(
            yaml.safe_dump(self.SETTINGS), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        loaded.run_line_magic("clustrix", "load here.yml")

        self._assert_loaded()

    def test_a_path_with_a_space_in_it_is_one_path(self, loaded, tmp_path):
        directory = tmp_path / "my project"
        directory.mkdir()
        path = directory / "cluster.yml"
        path.write_text(yaml.safe_dump(self.SETTINGS), encoding="utf-8")

        loaded.run_line_magic("clustrix", f"load {path}")

        self._assert_loaded()

    def test_a_file_written_by_save_to_file_loads_back(self, loaded, tmp_path):
        """The round trip the two halves of the library promise each other."""
        path = tmp_path / "saved.yml"
        ClusterConfig(**self.SETTINGS).save_to_file(str(path))
        configure(cluster_type="local", cluster_host=None, default_cores=1)

        loaded.run_line_magic("clustrix", f"load {path}")

        self._assert_loaded()

    def test_load_without_a_path_says_how_to_use_it(self, loaded):
        before = _fields(get_config())

        with capture_output() as captured:
            returned = loaded.run_line_magic("clustrix", "load")

        assert returned is None
        assert "Usage: %clustrix load <path-to-config-file>" in captured.stdout
        assert _fields(get_config()) == before

    def test_a_missing_file_is_an_error_and_changes_nothing(self, loaded, tmp_path):
        """The user named the file, so not finding it is not "no settings"."""
        configure(default_cores=5)
        before = _fields(get_config())
        missing = tmp_path / "nowhere.yml"

        with pytest.raises(FileNotFoundError, match="nowhere.yml"):
            loaded.run_line_magic("clustrix", f"load {missing}")

        assert _fields(get_config()) == before

    def test_a_malformed_file_is_an_error_and_changes_nothing(self, loaded, tmp_path):
        configure(default_cores=5)
        before = _fields(get_config())
        bad = tmp_path / "bad.yml"
        bad.write_text("cluster_type: [unterminated", encoding="utf-8")

        with pytest.raises(yaml.YAMLError):
            loaded.run_line_magic("clustrix", f"load {bad}")

        assert _fields(get_config()) == before

    def test_a_file_naming_a_removed_backend_says_which_and_changes_nothing(
        self, loaded, tmp_path
    ):
        before = _fields(get_config())
        old = tmp_path / "old.yml"
        old.write_text("cluster_type: pbs\ncluster_host: h.example.edu\n")

        with pytest.raises(ValueError, match="#140"):
            loaded.run_line_magic("clustrix", f"load {old}")

        assert _fields(get_config()) == before


# --------------------------------------------------------------------------
# %%remote and its deprecated alias
# --------------------------------------------------------------------------


class TestTheRemoteCellMagic:
    def test_it_displays_the_widget_once_and_runs_the_cell_body(self, loaded):
        _, captured = _run(loaded, "%%remote\nvalue = 6 * 7\n")

        assert len(_widgets_displayed(captured)) == 1
        assert loaded.user_ns["value"] == 42

    def test_what_it_displays_is_the_configuration_widget(self, loaded):
        _, captured = _run(loaded, "%%remote\npass\n")

        (shown,) = _widgets_displayed(captured)
        assert shown.data["text/plain"].startswith("VBox(")
        assert "clustrix-widget" in shown.data["text/plain"]
        assert shown.data[WIDGET_VIEW]["model_id"]

    def test_an_empty_cell_just_shows_the_widget(self, loaded):
        with capture_output() as captured:
            returned = loaded.run_cell_magic("remote", "", "   \n")

        assert returned is None
        assert len(_widgets_displayed(captured)) == 1

    def test_the_body_runs_in_the_notebook_namespace(self, loaded):
        """It can see earlier cells' names and leave its own behind, and
        ``cluster`` is the one ``%load_ext`` put there."""
        _run(loaded, "factor = 7")

        _run(
            loaded,
            "%%remote\n"
            "@cluster(cores=1)\n"
            "def scaled(x):\n"
            "    return x * factor\n"
            "result = scaled(6)\n",
        )

        assert loaded.user_ns["result"] == 42
        assert callable(loaded.user_ns["scaled"])

    def test_a_function_defined_under_remote_follows_a_loaded_configuration(
        self, loaded, tmp_path
    ):
        """The workflow in one piece: load a file, define, call."""
        path = tmp_path / "local.yml"
        path.write_text(
            yaml.safe_dump({"cluster_type": "local", "default_cores": 2}),
            encoding="utf-8",
        )
        _run(loaded, f"%clustrix load {path}")

        _run(
            loaded,
            "%%remote\n"
            "import os\n"
            "@cluster(cores=2)\n"
            "def statistics(values):\n"
            "    return {'sum': sum(values), 'pid': os.getpid()}\n"
            "summary = statistics([1, 2, 3, 4, 5])\n",
        )

        assert loaded.user_ns["summary"] == {"sum": 15, "pid": os.getpid()}

    def test_an_error_in_the_body_is_the_cells_error(self, loaded):
        with capture_output() as captured:
            loaded.run_cell("%%remote\nraise RuntimeError('from the cell body')\n")

        assert "RuntimeError" in captured.stdout
        assert "from the cell body" in captured.stdout
        assert len(_widgets_displayed(captured)) == 1

    def test_it_does_not_change_the_configuration_by_being_run(self, loaded):
        configure(cluster_type="slurm", cluster_host="hpc.example.edu")
        before = _fields(get_config())

        _run(loaded, "%%remote\npass\n")

        assert _fields(get_config()) == before


class TestTheDeprecatedAlias:
    def test_clusterfy_warns_and_then_does_what_remote_does(self, loaded):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with capture_output() as captured:
                loaded.run_cell_magic("clusterfy", "", "legacy_value = 'ran'\n")

        deprecations = [w for w in caught if w.category is DeprecationWarning]
        assert len(deprecations) == 1
        assert "%%clusterfy is deprecated; use %%remote instead" in str(
            deprecations[0].message
        )
        assert len(_widgets_displayed(captured)) == 1
        assert loaded.user_ns["legacy_value"] == "ran"

    def test_remote_itself_does_not_warn(self, loaded):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with capture_output():
                loaded.run_cell_magic("remote", "", "pass\n")

        assert [w for w in caught if w.category is DeprecationWarning] == []


# --------------------------------------------------------------------------
# Showing the widget without a magic
# --------------------------------------------------------------------------


class TestDisplayingTheWidgetDirectly:
    def test_display_config_widget_shows_it_and_returns_nothing(self, shell):
        with capture_output() as captured:
            returned = notebook_magic.display_config_widget()

        assert returned is None
        assert len(_widgets_displayed(captured)) == 1

    @pytest.mark.parametrize("setting", ["", "0", "no", "1", "true", "yes"])
    def test_importing_never_shows_the_widget_outside_a_notebook_kernel(
        self, shell, monkeypatch, setting
    ):
        """A terminal IPython shell has no kernel, so even opted in there is
        nowhere for a widget to go."""
        monkeypatch.setenv("CLUSTRIX_AUTO_WIDGET", setting)
        assert not hasattr(shell, "kernel")

        with capture_output() as captured:
            notebook_magic.auto_display_on_import()

        assert captured.outputs == []
        assert captured.stdout == ""


#: Run in a child, because a real kernel replaces the process's stdout and
#: display machinery and would not give them back to pytest.
_IN_A_REAL_KERNEL = """
    import json, os
    from ipykernel.inprocess import InProcessKernelManager
    from IPython import get_ipython
    from IPython.utils.capture import capture_output

    manager = InProcessKernelManager()
    manager.start_kernel()
    shell = manager.kernel.shell
    assert get_ipython() is shell and hasattr(shell, "kernel")

    view = "application/vnd.jupyter.widget-view+json"
    report = {}
    with capture_output() as captured:
        import clustrix
    report["shown_on_import"] = sum(view in o.data for o in captured.outputs)
    report["magics"] = sorted(
        name
        for kind in ("line", "cell")
        for name in shell.magics_manager.magics[kind]
        if name in ("clustrix", "remote", "clusterfy")
    )
    report["namespace"] = sorted(
        name for name in ("cluster", "configure", "get_config") if name in shell.user_ns
    )
    with capture_output() as captured:
        result = shell.run_cell("%%remote\\nvalue = 6 * 7\\n")
    report["remote_ok"] = result.success
    report["remote_shown"] = sum(view in o.data for o in captured.outputs)
    report["value"] = shell.user_ns.get("value")
    manager.shutdown_kernel()
    print()
    print(json.dumps(report))
"""


def _in_a_real_kernel(tmp_path, auto_widget):
    pytest.importorskip("ipykernel")
    environment = dict(os.environ)
    environment["HOME"] = str(tmp_path)
    environment["CLUSTRIX_CONFIG_DIR"] = str(tmp_path / ".clustrix")
    environment.pop("CLUSTRIX_AUTO_WIDGET", None)
    if auto_widget is not None:
        environment["CLUSTRIX_AUTO_WIDGET"] = auto_widget
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_IN_A_REAL_KERNEL)],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(tmp_path),
        timeout=180,
    )
    assert (
        completed.returncode == 0
    ), f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    return json.loads(completed.stdout.strip().splitlines()[-1])


class TestImportingClustrixInsideARealKernel:
    """``import clustrix`` in a notebook, with a real (in-process) kernel."""

    def test_importing_registers_the_magics_and_shows_nothing(self, tmp_path):
        """A library must not paint UI as a side effect of being imported."""
        report = _in_a_real_kernel(tmp_path, auto_widget=None)

        assert report["shown_on_import"] == 0
        assert report["magics"] == ["clusterfy", "clustrix", "remote"]
        assert report["namespace"] == ["cluster", "configure", "get_config"]
        assert report["remote_ok"] is True
        assert report["remote_shown"] == 1
        assert report["value"] == 42

    def test_the_opt_in_shows_the_widget_on_import_once(self, tmp_path):
        report = _in_a_real_kernel(tmp_path, auto_widget="1")

        assert report["shown_on_import"] == 1
        assert report["magics"] == ["clusterfy", "clustrix", "remote"]
        # ...and %%remote afterwards still shows exactly one more.
        assert report["remote_shown"] == 1


# --------------------------------------------------------------------------
# The stand-ins used when IPython is not installed
# --------------------------------------------------------------------------


class TestTheNoIPythonStandIns:
    """``notebook_magic_fallback`` is importable with or without IPython, so
    its pieces are exercised directly here. That the package really selects
    them when IPython is missing is ``tests/test_github_actions_compat.py``.

    An earlier version of these decorators returned an inner function when
    applied bare, so every magic silently did nothing without IPython.
    """

    @pytest.mark.parametrize(
        "decorator",
        [notebook_magic_fallback.cell_magic, notebook_magic_fallback.line_magic],
        ids=["cell_magic", "line_magic"],
    )
    def test_a_bare_decorator_leaves_a_method_that_runs_its_body(self, decorator):
        @notebook_magic_fallback.magics_class
        class Magics(notebook_magic_fallback.Magics):
            @decorator
            def shout(self, line, cell=""):
                return (line + cell).upper()

        assert Magics().shout("a", "b") == "AB"
        assert Magics.shout.__name__ == "shout"

    @pytest.mark.parametrize(
        "decorator",
        [notebook_magic_fallback.cell_magic, notebook_magic_fallback.line_magic],
        ids=["cell_magic", "line_magic"],
    )
    def test_a_named_decorator_leaves_a_method_that_runs_its_body(self, decorator):
        class Magics(notebook_magic_fallback.Magics):
            @decorator("renamed")
            def shout(self, line, cell=""):
                return (line + cell).upper()

        assert Magics().shout("a", "b") == "AB"

    def test_magics_class_returns_the_class_it_was_given(self):
        class Plain:
            pass

        assert notebook_magic_fallback.magics_class(Plain) is Plain

    def test_get_ipython_is_none_as_it_is_outside_a_session(self):
        assert notebook_magic_fallback.get_ipython() is None

    def test_the_stand_ins_take_the_same_shapes_as_the_real_decorators(self):
        """The real ones accept a bare method and a name too; if IPython
        changes that, the stand-ins are no longer stand-ins."""
        from IPython.core.magic import Magics, cell_magic, line_magic, magics_class

        @magics_class
        class Real(Magics):
            @line_magic
            def bare(self, line):
                return line

            @cell_magic("named")
            def with_name(self, line, cell):
                return cell

        assert Real.magics["line"]["bare"] == "bare"
        assert Real.magics["cell"]["named"] == "with_name"
