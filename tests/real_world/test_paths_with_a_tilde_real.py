"""``~`` in a key path and in a remote path, against a real sshd.

Two defects found while writing the tutorials, both invisible without a
real server:

* ``configure(key_file="~/.ssh/id_x")`` -- the natural way to write it, and
  the way a config file writes it -- reached paramiko unexpanded and failed
  with ``No such file or directory: '~/.ssh/id_x'``.
* ``cluster_ls("~/dir", config)`` returned ``[]`` for a directory that
  exists, because SFTP reads ``~`` as a directory literally named ``~``. So
  did every relative path under the default ``remote_work_dir``.

Run as ``tests/real_world/test_local_sshd_slurm.py`` describes.
"""

import os
import shutil
import uuid

import pytest
import yaml

from clustrix import cluster, configure, get_config
from clustrix.config import ClusterConfig, load_config
from clustrix.executor_connections import ConnectionManager
from clustrix.filesystem import (
    cluster_count_files,
    cluster_du,
    cluster_exists,
    cluster_find,
    cluster_glob,
    cluster_isdir,
    cluster_isfile,
    cluster_ls,
    cluster_stat,
)
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


@pytest.fixture
def key_under_home():
    """The test key copied to ``$HOME/.ssh``; returns the ``~`` spelling."""
    ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
    name = f"id_tilde_{uuid.uuid4().hex[:8]}"
    shutil.copyfile(str(KEY), os.path.join(ssh_dir, name))
    os.chmod(os.path.join(ssh_dir, name), 0o600)
    return f"~/.ssh/{name}"


def _config(key_file, **overrides) -> ClusterConfig:
    configure(
        cluster_type="ssh",
        cluster_host=HOST,
        cluster_port=PORT,
        username=USER,
        key_file=key_file,
        ssh_host_key_policy="auto_add",
        remote_work_dir="~/clustrix-realworld-tests",
        job_poll_interval=1,
        job_wait_timeout=300,
        **overrides,
    )
    return get_config()


def test_a_key_file_written_with_a_tilde_authenticates(host_facts, key_under_home):
    config = _config(key_under_home)
    assert config.key_file.startswith("~/"), "the test must exercise the tilde"

    with ConnectionManager(config) as manager:
        out, _ = manager.execute_remote_command("id -un")

    assert out.strip() == USER


def test_a_key_file_written_with_a_tilde_in_a_config_file_authenticates(
    host_facts, key_under_home, tmp_path
):
    """The same field, reached the way most people set it: a YAML file."""
    path = tmp_path / "cluster.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "cluster_type": "ssh",
                "cluster_host": HOST,
                "cluster_port": PORT,
                "username": USER,
                "key_file": key_under_home,
                "ssh_host_key_policy": "auto_add",
            }
        )
    )
    load_config(str(path))
    config = get_config()
    assert config.key_file == key_under_home

    with ConnectionManager(config) as manager:
        out, _ = manager.execute_remote_command("id -un")

    assert out.strip() == USER


def test_a_key_file_written_with_a_tilde_runs_a_job_and_lists_files(
    host_facts, key_under_home
):
    config = _config(key_under_home)

    @cluster(**RESOURCES)
    def job():
        import getpass

        return getpass.getuser()

    assert job() == USER
    # The filesystem helpers make their own connection, from the same field.
    assert cluster_isdir("/tmp", config) is True


@pytest.fixture
def remote_tree(host_facts):
    """``~/<name>/{a.csv,b.txt,sub/c.csv}`` on the test host."""
    name = f"clustrix-tilde-{uuid.uuid4().hex[:8]}"
    made = _remote(
        f"mkdir -p ~/{name}/sub && printf 'abc' > ~/{name}/a.csv && "
        f"printf 'de' > ~/{name}/b.txt && printf 'f' > ~/{name}/sub/c.csv && "
        "echo $HOME"
    )
    assert made.returncode == 0, made.stderr
    yield name, made.stdout.strip()
    _remote(f"rm -rf ~/{name}")


def test_every_helper_expands_a_tilde_on_the_cluster(remote_tree):
    name, home = remote_tree
    config = _config(KEY)
    tilde = f"~/{name}"

    assert cluster_ls(tilde, config) == ["a.csv", "b.txt", "sub"]
    assert cluster_ls(tilde, config) == cluster_ls(f"{home}/{name}", config)
    assert name in cluster_ls("~", config)
    assert cluster_exists(f"{tilde}/a.csv", config) is True
    assert cluster_exists(f"{tilde}/absent", config) is False
    assert cluster_isdir(f"{tilde}/sub", config) is True
    assert cluster_isfile(f"{tilde}/b.txt", config) is True
    assert cluster_stat(f"{tilde}/a.csv", config).size == 3
    assert cluster_find("*.csv", tilde, config) == ["a.csv", "sub/c.csv"]
    assert cluster_glob("*.csv", tilde, config) == ["a.csv"]
    assert cluster_count_files(tilde, "*", config) == 3
    assert cluster_du(tilde, config).total_bytes == 6


def test_a_relative_path_is_found_under_a_work_dir_written_with_a_tilde(
    remote_tree,
):
    """``remote_work_dir`` defaults to ``~/.clustrix/jobs``: same expansion."""
    name, _ = remote_tree
    config = _config(KEY)
    config.remote_work_dir = f"~/{name}"

    assert cluster_ls(".", config) == ["a.csv", "b.txt", "sub"]
    assert cluster_ls("sub", config) == ["c.csv"]
    assert cluster_isfile("sub/c.csv", config) is True
