"""Dead-but-accepted ClusterConfig fields say so when set (#161).

Eleven fields are accepted, stored, and read by nothing -- leftovers of the
automatic-GPU machinery whose execution path was deleted. Silently ignoring
them is the defect class #158 removed for ``default_queue``: the caller
believes they changed something. Each one now warns at construction when it
is set to a non-default value; defaults stay silent, because an unset field
is not a claim.
"""

from __future__ import annotations

import pytest

from clustrix.config import ClusterConfig

DEAD_FIELDS = [
    "max_gpu_parallel_jobs",
    "gpu_detection_enabled",
    "gpu_memory_fraction",
    "local_parallel_threshold",
    "auto_gpu_packages",
    "prefer_gpu_execution",
    "cache_credentials",
    "cuda_version_preference",
    "gpu_requirements",
    "credential_cache_ttl",
    "rapids_ecosystem",
]


def _a_non_default_value_for(field_name: str):
    """A value that differs from the field's declared default."""
    import dataclasses

    for f in dataclasses.fields(ClusterConfig):
        if f.name == field_name:
            if f.default is None:
                return "sentinel"
            if isinstance(f.default, bool):
                return not f.default
            if isinstance(f.default, int):
                return f.default + 1
            if isinstance(f.default, float):
                return f.default + 1.0
            if isinstance(f.default, str):
                return f.default + "-changed"
            if isinstance(f.default, (list, dict)):
                return type(f.default)()
            return "sentinel"
    raise AssertionError(f"{field_name} is not a ClusterConfig field")


@pytest.mark.parametrize("field_name", DEAD_FIELDS)
def test_setting_a_dead_field_warns_that_nothing_reads_it(field_name):
    value = _a_non_default_value_for(field_name)

    with pytest.warns(UserWarning, match=field_name):
        ClusterConfig(**{field_name: value})


@pytest.mark.parametrize("field_name", DEAD_FIELDS)
def test_leaving_a_dead_field_at_its_default_is_silent(field_name):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ClusterConfig()


def test_the_warning_says_the_field_has_no_effect():
    with pytest.warns(UserWarning, match="no effect"):
        ClusterConfig(gpu_detection_enabled=False)


def test_the_dead_field_is_still_stored_not_dropped():
    config = ClusterConfig(local_parallel_threshold=42)
    assert config.local_parallel_threshold == 42


def test_configure_also_announces_a_dead_field():
    """The primary runtime entry point must honour the same promise.

    ``configure()`` writes by ``setattr``, which never re-runs
    ``__post_init__ -- so without this, the announcement existed only on
    the construction path and the main way users set fields stayed silent.
    """
    import clustrix

    with pytest.warns(UserWarning, match="max_gpu_parallel_jobs"):
        clustrix.configure(max_gpu_parallel_jobs=4)


def test_configure_does_not_warn_for_live_fields():
    import warnings

    import clustrix

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        clustrix.configure(default_cores=2)
