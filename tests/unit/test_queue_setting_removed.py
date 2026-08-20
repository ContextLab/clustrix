#!/usr/bin/env python3
"""``queue`` was accepted and read by nothing, so it is no longer accepted.

Issue #158. ``@cluster(queue=...)`` and ``ClusterConfig.default_queue`` were
resolved into ``job_config["queue"]`` and then dropped: ``utils.py`` emits
``--partition`` for SLURM and nothing else, and the only other match in the
package populates a widget text field from a saved profile. ``queue`` was the
PBS and SGE spelling of what SLURM calls a partition, and both of those
backends were removed (#140, #141), so the setting outlived its consumers.

Two candidate repairs were rejected:

* aliasing ``queue`` onto ``partition`` invents behaviour that has never
  existed on any verified backend and has to guess which one wins when both
  are set;
* warning while keeping the parameter leaves a knob whose only purpose is to
  be documented as inert.

So the decorator parameter is gone. ``@cluster(queue="gpu")`` now falls into
``**kwargs`` and hits the unrecognised-option warning that was already there,
which is why no new warning machinery had to be verified. ``default_queue``
still exists on ``ClusterConfig`` -- removing a public config field is a
separate change -- so the decorator reports it instead of ignoring it.
"""

import inspect
import logging

import pytest

from clustrix import cluster, configure
from clustrix.config import ClusterConfig, get_config
from clustrix.decorator import ClusterExecutor
from clustrix.utils import create_job_script


def twice(x):
    return x * 2


@pytest.fixture(autouse=True)
def local_config():
    """Real global config, restored afterwards."""
    config = get_config()
    saved = (config.cluster_type, config.cluster_host, config.default_queue)
    configure(cluster_type="local", cluster_host=None, default_queue=None)
    yield
    configure(cluster_type=saved[0], cluster_host=saved[1], default_queue=saved[2])


def messages(caplog):
    return [record.getMessage() for record in caplog.records]


def test_queue_is_not_a_cluster_parameter():
    """The signature is the contract; ``queue`` is no longer part of it."""
    assert "queue" not in inspect.signature(cluster).parameters


def test_passing_queue_is_reported_as_having_no_effect(caplog):
    """It lands in ``**kwargs`` and the existing warning catches it."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(queue="gpu")(twice)(21) == 42

    assert any(
        "unrecognised option(s) queue" in message for message in messages(caplog)
    ), messages(caplog)


def test_a_recognised_option_is_still_not_reported(caplog):
    """The warning must stay specific, or it teaches people to ignore it."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(partition="gpu")(twice)(21) == 42

    assert not any(
        "unrecognised option" in message for message in messages(caplog)
    ), messages(caplog)


def test_default_queue_left_in_a_config_is_reported(caplog):
    """A value in a config file or a saved widget profile still goes nowhere."""
    configure(default_queue="gpu")

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster()(twice)(21) == 42

    assert any(
        "default_queue" in message and "has no effect" in message
        for message in messages(caplog)
    ), messages(caplog)


def test_an_unset_default_queue_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster()(twice)(21) == 42

    assert not any("default_queue" in message for message in messages(caplog))


def test_queue_never_reaches_the_job_config_the_backends_read(monkeypatch):
    """The value the executor is handed carries no ``queue`` key at all.

    A real ``ClusterExecutor`` subclass that records its argument and then does
    the actual submission -- ``cluster_type="local"`` dispatches to
    ``LocalJobManager``, which runs the function here, so this is a genuine
    end-to-end submission rather than a stand-in.
    """
    seen = {}

    class RecordingExecutor(ClusterExecutor):
        def submit_job(self, func_data, job_config):
            seen.update(job_config)
            return super().submit_job(func_data, job_config)

    monkeypatch.setattr("clustrix.decorator.ClusterExecutor", RecordingExecutor)
    configure(
        cluster_type="local",
        cluster_host="host-the-local-manager-ignores",
        default_queue="gpu",
    )

    assert cluster(queue="gpu")(twice)(21) == 42
    assert seen, "the executor was never reached"
    assert "queue" not in seen, seen
    assert "partition" in seen, seen


def test_a_slurm_script_asks_for_a_partition_and_knows_no_queue(tmp_path):
    """Proof the removed knob had no consumer: only ``partition`` reaches SLURM."""
    config = ClusterConfig(cluster_type="slurm", remote_work_dir=str(tmp_path))

    script = create_job_script(
        "slurm",
        {"cores": 2, "memory": "4GB", "time": "01:00:00", "partition": "compute"},
        str(tmp_path),
        config,
    )
    assert "--partition=compute" in script

    # And a stale ``queue`` key, if one were reintroduced, would still be
    # invisible to the generator -- which is why it had to go.
    stale = create_job_script(
        "slurm",
        {"cores": 2, "memory": "4GB", "time": "01:00:00", "queue": "gpu"},
        str(tmp_path),
        config,
    )
    assert "gpu" not in stale, stale
