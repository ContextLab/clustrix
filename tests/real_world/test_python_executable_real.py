"""``python_executable`` chooses the interpreter the remote environments are
built from -- proved with a real job on a real host.

It used to be read only by the single-venv path. On the default (two-venv)
path it was ignored: the environments were built from whichever
``python3.x`` came first on the remote PATH, whatever the setting said.

The host here has no conda, so this is the virtualenv branch. On a host with
conda clustrix creates conda environments at the session's Python version
and the setting does not choose their interpreter; that branch is not
exercised by this file.

Run as ``tests/real_world/test_local_sshd_slurm.py`` describes.
"""

import sys
import uuid

import pytest

from clustrix import cluster, configure
from tests.real_world.test_local_sshd_slurm import (  # noqa: F401
    HOST,
    KEY,
    PORT,
    RESOURCES,
    USER,
    _remote,
    host_facts,
    pytestmark,
)

LOCAL_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"


def _configure(**overrides):
    configure(
        cluster_type="ssh",
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=KEY,
        ssh_host_key_policy="auto_add",
        remote_work_dir="~/clustrix-realworld-tests",
        job_poll_interval=1,
        job_wait_timeout=300,
        # A handful of packages rather than this whole environment: the
        # point is which interpreter builds it, and it is built twice.
        replicate_local_environment=False,
        **overrides,
    )


@pytest.fixture
def second_interpreter(host_facts):
    """A second way to reach the matching interpreter on the host.

    ``~/<dir>/bin/python3`` is a symlink to the real ``python3.x``, so it is
    the same version at a path nothing would find by searching PATH. A
    virtualenv records the interpreter that created it in ``pyvenv.cfg``,
    which is what the job reads back.
    """
    name = f"clustrix-alt-python-{uuid.uuid4().hex[:8]}"
    made = _remote(
        f"real=$(command -v python{LOCAL_VERSION}) && mkdir -p ~/{name}/bin && "
        f'ln -s "$real" ~/{name}/bin/python3 && echo ~/{name}/bin/python3'
    )
    if made.returncode != 0:
        pytest.skip(f"The test host has no python{LOCAL_VERSION}: {made.stderr}")
    yield made.stdout.strip()
    _remote(f"rm -rf ~/{name}")


def _where_the_environment_came_from():
    import os
    import sys

    with open(os.path.join(sys.prefix, "pyvenv.cfg")) as handle:
        return sys.prefix, handle.read()


def test_the_environment_is_built_from_the_interpreter_named(second_interpreter):
    _configure(python_executable=second_interpreter)
    prefix, described = cluster(**RESOURCES)(_where_the_environment_came_from)()
    try:
        print("pyvenv.cfg with python_executable set:\n" + described)

        # configure() merges, so the default has to be named to get it back.
        _configure(python_executable="python")
        other_prefix, default = cluster(**RESOURCES)(_where_the_environment_came_from)()
        print("pyvenv.cfg with python_executable left alone:\n" + default)

        assert f"command = {second_interpreter} -m venv" in described, (
            "the execution environment was not created by the interpreter "
            "python_executable names"
        )
        assert second_interpreter not in default
        assert other_prefix != prefix, "two interpreters shared one environment"
    finally:
        # An environment built from the symlink the fixture removes cannot
        # be reused; do not leave it on the host. Only that one: when the
        # setting was ignored, ``prefix`` is the shared default environment.
        env_root = prefix.rsplit("/", 1)[0]
        if second_interpreter in described:
            assert "/clustrix-realworld-tests/envs/py" in env_root
            _remote(f"rm -rf {env_root}")


def test_an_interpreter_that_is_not_there_is_refused_by_name(host_facts):
    _configure(python_executable="/no/such/python3")

    with pytest.raises(Exception) as caught:
        cluster(**RESOURCES)(_where_the_environment_came_from)()

    assert "python_executable='/no/such/python3' did not run" in str(caught.value)


def test_an_interpreter_of_another_version_is_refused(host_facts):
    other = "3.11" if LOCAL_VERSION != "3.11" else "3.12"
    if _remote(f"command -v python{other}").returncode != 0:
        pytest.skip(f"The test host has no python{other} to refuse.")
    _configure(python_executable=f"python{other}")

    with pytest.raises(Exception) as caught:
        cluster(**RESOURCES)(_where_the_environment_came_from)()

    message = str(caught.value)
    assert f"Python {other}" in message and f"Python {LOCAL_VERSION}" in message
