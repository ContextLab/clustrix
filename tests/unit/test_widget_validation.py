"""Tests for the notebook widget's input validation.

The widget used to accept `cores=0`, `memory="banana"`, `time="soon"`, a port
of 99999 and an empty host for a remote cluster, hand all of it to
ClusterConfig, and report success. Nothing complained until the job failed on
the cluster minutes later, with an error that named none of it.

These drive the real validator against the real widgets.
"""

import pytest

pytest.importorskip("ipywidgets")

from clustrix.modern_notebook_widget import (  # noqa: E402
    FORM_CONTROLS,
    ModernClustrixWidget,
)
from tests.widget_console import applied, pill  # noqa: E402


def _widget(cluster_type="slurm", **values):
    widget = ModernClustrixWidget()
    widget.widgets["cluster_type"].value = cluster_type
    widget._update_ui_for_cluster_type()

    # A configuration that is valid, so each test changes exactly one thing.
    defaults = {
        "cpus": 8,
        "ram": "32GB",
        "time": "02:00:00",
        "host": "hpc.example.edu",
        "username": "testuser",
        "port": 22,
    }
    if cluster_type == "huggingface":
        defaults["hf_namespace"] = "contextlab"
    defaults.update(values)

    for key, value in defaults.items():
        if key in widget.widgets:
            widget.widgets[key].value = value
    return widget


def _problems(widget):
    return " | ".join(widget._validate_widget_values())


class TestValidConfigurations:
    def test_a_complete_slurm_configuration_has_no_problems(self):
        assert _widget("slurm")._validate_widget_values() == []

    def test_local_needs_no_host_or_username(self):
        widget = _widget("local", host="", username="")
        assert widget._validate_widget_values() == []

    def test_minus_one_cores_means_all_cores(self):
        """-1 is meaningful and must not be rejected as non-positive."""
        assert _widget("slurm", cpus=-1)._validate_widget_values() == []

    @pytest.mark.parametrize("memory", ["16GB", "512Mi", "8G", "2TB", "1.5GB", "64 GB"])
    def test_recognised_memory_sizes(self, memory):
        assert _widget("slurm", ram=memory)._validate_widget_values() == []

    @pytest.mark.parametrize(
        "walltime", ["01:00:00", "12:30", "48", "2-04:00:00", "2-04", "100:59:59"]
    )
    def test_recognised_walltimes(self, walltime):
        assert _widget("slurm", time=walltime)._validate_widget_values() == []

    def test_huggingface_does_not_read_cpus_memory_or_walltime(self):
        """That backend takes its size from the hardware choice and its limit
        from its own timeout, so nonsense in the hidden row must not block."""
        widget = _widget("huggingface", cpus=0, ram="banana", time="soon")

        assert widget._validate_widget_values() == []
        assert widget.widgets["resources_row"].layout.display == "none"

    @pytest.mark.parametrize("timeout", ["", "30m", "2h", "1d", "90", "1.5h"])
    def test_recognised_huggingface_time_limits(self, timeout):
        widget = _widget("huggingface", hf_timeout=timeout)

        assert widget._validate_widget_values() == []


class TestRejectedValues:
    def test_zero_cores_is_rejected(self):
        assert "CPUs must be positive" in _problems(_widget("slurm", cpus=0))

    def test_nonsense_memory_is_rejected(self):
        assert "not a size" in _problems(_widget("slurm", ram="banana"))

    def test_empty_memory_is_rejected(self):
        assert "Memory is required" in _problems(_widget("slurm", ram=""))

    def test_nonsense_walltime_is_rejected(self):
        assert "not a duration" in _problems(_widget("slurm", time="soon"))

    @pytest.mark.parametrize("memory", ["0GB", "0", "-4GB", "GB"])
    def test_a_memory_size_needs_an_amount_above_zero(self, memory):
        assert "is not a size" in _problems(_widget("slurm", ram=memory))

    def test_a_bare_number_of_memory_is_gigabytes_as_in_the_library(self):
        """The form briefly demanded a unit. The library does not:
        ``clustrix.utils.normalize_memory("64", "slurm")`` is ``"64G"``, so
        refusing ``64`` here made a profile that clustrix accepts impossible
        to apply from the widget."""
        from clustrix.utils import normalize_memory

        assert normalize_memory("64", "slurm") == "64G"
        assert _problems(_widget("slurm", ram="64")) == ""

    def test_empty_walltime_is_rejected(self):
        assert "A time limit is required" in _problems(_widget("slurm", time=""))

    @pytest.mark.parametrize(
        "walltime", ["01:60:00", "00:00:75", "1:2:3:4", "1-", "-5"]
    )
    def test_a_walltime_with_impossible_parts_is_rejected(self, walltime):
        assert "is not a duration" in _problems(_widget("slurm", time=walltime))

    def test_an_implausible_core_count_is_rejected(self):
        assert "more than any single job can use" in _problems(
            _widget("slurm", cpus=4097)
        )
        assert _widget("slurm", cpus=4096)._validate_widget_values() == []

    @pytest.mark.parametrize("cluster_type", ["local", "ssh", "slurm", "huggingface"])
    def test_an_empty_python_executable_is_rejected(self, cluster_type):
        widget = _widget(cluster_type, python_executable="   ")

        assert "A Python executable is required" in _problems(widget)

    @pytest.mark.parametrize("timeout", ["soon", "2 hours", "h", "-1h"])
    def test_a_nonsense_huggingface_time_limit_is_rejected(self, timeout):
        widget = _widget("huggingface", hf_timeout=timeout)

        assert "is not a duration" in _problems(widget)

    @pytest.mark.parametrize("port", [0, 99999, -1])
    def test_out_of_range_port_is_rejected(self, port):
        assert "Port must be between" in _problems(_widget("slurm", port=port))

    @pytest.mark.parametrize("cluster_type", ["ssh", "slurm"])
    def test_remote_cluster_requires_a_host(self, cluster_type):
        assert "needs a host" in _problems(_widget(cluster_type, host="  "))

    @pytest.mark.parametrize("cluster_type", ["ssh", "slurm"])
    def test_remote_cluster_requires_a_username(self, cluster_type):
        assert "needs a username" in _problems(_widget(cluster_type, username=""))

    def test_the_problem_names_the_backend_the_way_its_tile_does(self):
        assert "SLURM needs a host" in _problems(_widget("slurm", host=""))
        assert "SSH needs a username" in _problems(_widget("ssh", username=""))

    def test_huggingface_requires_a_namespace(self):
        """The personal account usually cannot run jobs; an org is needed."""
        widget = _widget("huggingface", hf_namespace="")
        assert "namespace is required" in _problems(widget)

    def test_every_problem_is_reported_not_just_the_first(self):
        """One round-trip should surface everything that is wrong."""
        widget = _widget("slurm", cpus=0, ram="banana", time="soon", host="", port=0)
        problems = widget._validate_widget_values()
        assert len(problems) >= 5


class TestHandlersRefuseInvalidInput:
    def test_apply_does_not_configure_from_invalid_values(self):
        """Apply used to write nonsense straight into the global config."""
        import clustrix

        widget = _widget("slurm", ram="banana", host="")
        before = clustrix.get_config().cluster_host

        widget._on_apply_config(widget.widgets["apply_btn"])

        assert clustrix.get_config().cluster_host == before
        assert pill(widget) == "invalid"
        assert applied(widget) == "not applied"

    def test_test_submit_does_not_submit_invalid_values(self):
        widget = _widget("slurm", cpus=0, host="")
        widget._on_test_submit(widget.widgets["test_submit_btn"])

        assert pill(widget) == "invalid"


class TestTheOffendingControlsAreOutlined:
    """Saying what is wrong is half of it; the form also shows where."""

    @staticmethod
    def _outlined(widget):
        return sorted(
            key
            for key in FORM_CONTROLS
            if "cx-invalid" in widget.widgets[key]._dom_classes
        )

    def test_nothing_is_outlined_until_an_action_is_refused(self):
        widget = _widget("slurm", ram="banana", host="")

        assert self._outlined(widget) == []

    def test_a_refused_action_outlines_exactly_the_controls_at_fault(self):
        widget = _widget("slurm", cpus=0, ram="banana", host="", port=0)
        widget.widgets["env_vars"].value = "export NOPE=1"

        widget._on_apply_config(widget.widgets["apply_btn"])

        assert self._outlined(widget) == ["cpus", "env_vars", "host", "port", "ram"]
        assert {key for key, _ in widget._validation_problems()} == set(
            self._outlined(widget)
        )

    def test_the_next_edit_clears_the_outlines_and_the_stale_result(self):
        widget = _widget("slurm", ram="banana", host="")
        widget._on_apply_config(widget.widgets["apply_btn"])
        assert self._outlined(widget) == ["host", "ram"]

        widget.widgets["host"].value = "hpc.example.edu"

        assert self._outlined(widget) == []
        assert pill(widget) == ""

    def test_fixing_everything_lets_the_action_through(self):
        widget = _widget("slurm", ram="banana")
        widget._on_apply_config(widget.widgets["apply_btn"])

        widget.widgets["ram"].value = "32GB"
        widget._on_apply_config(widget.widgets["apply_btn"])

        assert self._outlined(widget) == []
        assert pill(widget) == "applied"
        assert applied(widget) == "in use"

    def test_key_setup_without_a_password_outlines_the_password(self):
        widget = _widget("ssh")

        widget._on_auto_setup_ssh(widget.widgets["auto_setup_ssh"])

        assert pill(widget) == "password needed"
        assert self._outlined(widget) == ["password"]


class TestBackendSpecificSections:
    """Each backend needs different settings, and only its own should show."""

    SECTIONS = {
        "remote_section": ("ssh", "slurm"),
        "hf_section": ("huggingface",),
        # CPUs, memory and walltime: not read by HuggingFace Jobs.
        "resources_row": ("local", "ssh", "slurm"),
        "partition": ("slurm",),
        # Nothing to connect to on this machine.
        "test_connect_btn": ("ssh", "slurm", "huggingface"),
    }

    @pytest.mark.parametrize(
        "cluster_type",
        ["local", "ssh", "slurm", "huggingface"],
    )
    def test_exactly_the_right_sections_are_shown(self, cluster_type):
        widget = _widget(cluster_type)
        for section, applies_to in self.SECTIONS.items():
            # A hidden section has display "none"; a shown one has no
            # override at all, so the stylesheet lays it out.
            shown = widget.widgets[section].layout.display != "none"
            assert shown == (
                cluster_type in applies_to
            ), f"{section} shown={shown} for {cluster_type}"

    def test_huggingface_settings_reach_the_config(self):
        widget = _widget("huggingface")
        widget.widgets["hf_namespace"].value = "contextlab"
        widget.widgets["hf_flavor"].value = "cpu-upgrade"

        config = widget._get_config_from_widgets()
        assert config.hf_namespace == "contextlab"
        assert config.hf_flavor == "cpu-upgrade"
        assert config.hf_allow_gpu_flavors is False

    def test_every_cluster_type_in_the_dropdown_is_a_real_backend(self):
        """A type the executor cannot dispatch is worse than not offering it."""
        from clustrix.executor_core import ClusterExecutor  # noqa: F401

        widget = _widget("local")
        offered = {value for _, value in widget.widgets["cluster_type"].options}
        assert offered == {
            "local",
            "ssh",
            "slurm",
            "huggingface",
        }
