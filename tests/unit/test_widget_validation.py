"""Tests for the notebook widget's input validation.

The widget used to accept `cores=0`, `memory="banana"`, `time="soon"`, a port
of 99999 and an empty host for a remote cluster, hand all of it to
ClusterConfig, and report success. Nothing complained until the job failed on
the cluster minutes later, with an error that named none of it.

These drive the real validator against the real widgets.
"""

import pytest

pytest.importorskip("ipywidgets")

from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402


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

    @pytest.mark.parametrize("memory", ["16GB", "512Mi", "8G", "2TB", "64"])
    def test_recognised_memory_sizes(self, memory):
        assert "Memory" not in _problems(_widget("slurm", ram=memory))

    @pytest.mark.parametrize("walltime", ["01:00:00", "12:30", "48", "2-04:00:00"])
    def test_recognised_walltimes(self, walltime):
        assert "Walltime" not in _problems(_widget("slurm", time=walltime))


class TestRejectedValues:
    def test_zero_cores_is_rejected(self):
        assert "CPUs must be positive" in _problems(_widget("slurm", cpus=0))

    def test_nonsense_memory_is_rejected(self):
        assert "not a size" in _problems(_widget("slurm", ram="banana"))

    def test_empty_memory_is_rejected(self):
        assert "Memory is required" in _problems(_widget("slurm", ram=""))

    def test_nonsense_walltime_is_rejected(self):
        assert "not a duration" in _problems(_widget("slurm", time="soon"))

    def test_empty_walltime_is_rejected(self):
        assert "Walltime is required" in _problems(_widget("slurm", time=""))

    @pytest.mark.parametrize("port", [0, 99999, -1])
    def test_out_of_range_port_is_rejected(self, port):
        assert "Port must be between" in _problems(_widget("slurm", port=port))

    @pytest.mark.parametrize("cluster_type", ["ssh", "slurm", "pbs", "sge"])
    def test_remote_cluster_requires_a_host(self, cluster_type):
        assert "host is required" in _problems(_widget(cluster_type, host=""))

    @pytest.mark.parametrize("cluster_type", ["ssh", "slurm", "pbs", "sge"])
    def test_remote_cluster_requires_a_username(self, cluster_type):
        assert "username is required" in _problems(_widget(cluster_type, username=""))

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
        assert "invalid" in widget.widgets["status_pill"].value

    def test_test_submit_does_not_submit_invalid_values(self):
        widget = _widget("slurm", cpus=0, host="")
        widget._on_test_submit(widget.widgets["test_submit_btn"])

        assert "invalid" in widget.widgets["status_pill"].value


class TestBackendSpecificSections:
    """Each backend needs different settings, and only its own should show."""

    SECTIONS = {
        "remote_section": ("ssh", "slurm", "pbs", "sge"),
        "hf_section": ("huggingface",),
        "k8s_section": ("kubernetes",),
    }

    @pytest.mark.parametrize(
        "cluster_type",
        ["local", "ssh", "slurm", "pbs", "sge", "kubernetes", "huggingface"],
    )
    def test_exactly_the_right_sections_are_shown(self, cluster_type):
        widget = _widget(cluster_type)
        for section, applies_to in self.SECTIONS.items():
            expected = "block" if cluster_type in applies_to else "none"
            assert (
                widget.widgets[section].layout.display == expected
            ), f"{section} should be {expected} for {cluster_type}"

    def test_kubernetes_settings_reach_the_config(self):
        """None of these had fields, so only the defaults were ever usable."""
        widget = _widget("kubernetes")
        widget.widgets["k8s_namespace"].value = "research"
        widget.widgets["k8s_image"].value = "python:3.12-slim"
        widget.widgets["k8s_service_account"].value = "clustrix-runner"
        widget.widgets["k8s_pull_policy"].value = "Always"

        config = widget._get_config_from_widgets()
        assert config.k8s_namespace == "research"
        assert config.k8s_image == "python:3.12-slim"
        assert config.k8s_service_account == "clustrix-runner"
        assert config.k8s_pull_policy == "Always"

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
        offered = set(widget.widgets["cluster_type"].options)
        assert offered == {
            "local",
            "ssh",
            "slurm",
            "pbs",
            "sge",
            "kubernetes",
            "huggingface",
        }
