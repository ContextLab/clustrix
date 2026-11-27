"""``clustrix config`` and ``clustrix load`` change what the next process sees.

Each ``clustrix ...`` invocation is its own process. ``clustrix config
--cluster-type slurm --cluster-host h`` used to call ``configure()``, print
"Configuration updated successfully!" and exit -- having changed nothing but
its own memory. The README documents that command followed by ``clustrix
status`` as the way to set clustrix up.

So every test here runs the CLI as real, separate processes against a real
(temporary) ``$HOME``: the autouse fixtures in ``tests/conftest.py`` change
the real environment variables, and the child processes inherit them.
"""

import os
import socket
import stat
import subprocess
import sys

import pytest
import yaml

from clustrix.config import default_config_file, get_config_dir
from tests.ssh_server import LocalSSHServer, generate_keypair


def clustrix(*arguments, cwd=None):
    """Run ``clustrix <arguments>`` in a new interpreter."""
    result = subprocess.run(
        [sys.executable, "-m", "clustrix.cli", *arguments],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(os.environ),
        timeout=120,
    )
    print(f"$ clustrix {' '.join(arguments)}  -> exit {result.returncode}")
    print(result.stdout, result.stderr, sep="")
    return result


def test_settings_given_to_config_are_there_for_the_next_process(tmp_path):
    """The README sequence, verbatim."""
    first = clustrix(
        "config",
        "--cluster-type",
        "slurm",
        "--cluster-host",
        "cluster.example.com",
        "--cores",
        "8",
        cwd=tmp_path,
    )
    assert first.returncode == 0
    assert str(default_config_file()) in first.stdout

    second = clustrix("config", cwd=tmp_path)

    assert second.returncode == 0
    assert "cluster_type: slurm" in second.stdout
    assert "cluster_host: cluster.example.com" in second.stdout
    assert "default_cores: 8" in second.stdout


def test_a_later_config_call_adds_to_what_was_saved(tmp_path):
    clustrix("config", "--cluster-type", "ssh", "--cluster-host", "box.example.edu")
    clustrix("config", "--username", "researcher", "--memory", "32GB")

    shown = clustrix("config").stdout

    assert "cluster_host: box.example.edu" in shown
    assert "username: researcher" in shown
    assert "default_memory: 32GB" in shown


def test_the_saved_file_is_private_and_holds_no_secret(tmp_path):
    clustrix("config", "--cluster-type", "ssh", "--cluster-host", "box.example.edu")

    saved = default_config_file()
    assert saved == get_config_dir() / "config.yml"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    document = yaml.safe_load(saved.read_text())
    assert document["cluster_host"] == "box.example.edu"
    assert not {"password", "api_key", "hf_token"} & set(document)


def test_load_makes_the_file_the_default_for_the_next_process(tmp_path):
    source = tmp_path / "my-config.yml"
    source.write_text("cluster_type: ssh\ncluster_host: loaded.example.edu\n")

    loaded = clustrix("load", str(source), cwd=tmp_path)
    assert loaded.returncode == 0

    shown = clustrix("config", cwd=tmp_path).stdout
    assert "cluster_type: ssh" in shown
    assert "cluster_host: loaded.example.edu" in shown


def test_zero_cores_is_refused_not_ignored(tmp_path):
    """``if cores:`` treated ``--cores 0`` as the option not being there."""
    clustrix("config", "--cores", "6")

    refused = clustrix("config", "--cores", "0")

    assert refused.returncode != 0
    assert "default_cores=0" in refused.stderr
    assert "Traceback" not in refused.stderr
    assert "default_cores: 6" in clustrix("config").stdout


def test_minus_one_cores_is_every_core_and_is_accepted():
    assert clustrix("config", "--cores", "-1").returncode == 0
    assert "default_cores: -1" in clustrix("config").stdout


def test_there_is_no_api_key_option():
    """It set a field nothing reads, and put a secret on a command line."""
    result = clustrix("config", "--api-key", "abc")

    assert result.returncode == 2
    assert "No such option" in result.stderr
    assert "--api-key" not in clustrix("config", "--help").stdout


def test_a_malformed_default_config_is_an_error_message_not_a_traceback():
    get_config_dir().mkdir(mode=0o700, exist_ok=True)
    broken = get_config_dir() / "config.yml"
    broken.write_text("{this is: not yaml\n")

    for arguments in (["config"], ["status"], ["config", "--cores", "2"]):
        result = clustrix(*arguments)
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        assert result.stderr.startswith("Error: ")
        assert str(broken) in result.stderr
    assert broken.read_text() == "{this is: not yaml\n", "the broken file was replaced"


def test_config_file_in_a_missing_directory_creates_the_directory(tmp_path):
    """This used to assert the opposite -- exit 1, "Nothing was saved".

    That was the behaviour, not a decision: ``save_to_file`` simply could
    not write into a directory that was not there, for the CLI or for
    ``save_config("new/dir/c.yml")`` in Python. It creates the directory
    now, private, so the assertion is rewritten rather than relaxed; the
    error-message contract it was really guarding is the next test.
    """
    target = tmp_path / "no-such-directory" / "x.yml"

    result = clustrix("config", "--cluster-type", "ssh", "--config-file", str(target))

    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(target.read_text())["cluster_type"] == "ssh"
    if os.name != "nt":
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert not default_config_file().exists()


def test_config_file_that_cannot_be_written_is_an_error_message(tmp_path):
    """A destination under a regular file: no directory can be made there."""
    (tmp_path / "a-file").write_text("not a directory\n")
    target = tmp_path / "a-file" / "x.yml"

    result = clustrix("config", "--cluster-type", "ssh", "--config-file", str(target))

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert str(target) in result.stderr
    assert "Nothing was saved" in result.stderr
    assert (tmp_path / "a-file").read_text() == "not a directory\n"
    assert not default_config_file().exists()


def test_config_file_saves_there_and_leaves_the_default_alone(tmp_path):
    target = tmp_path / "elsewhere.yml"

    result = clustrix(
        "config",
        "--cluster-type",
        "ssh",
        "--cluster-host",
        "box.example.edu",
        "--config-file",
        str(target),
    )

    assert result.returncode == 0
    assert yaml.safe_load(target.read_text())["cluster_host"] == "box.example.edu"
    assert not default_config_file().exists()


def test_a_widget_profile_bundle_is_not_overwritten():
    """``config.yml`` can hold the widget's named profiles; they are not ours."""
    get_config_dir().mkdir(mode=0o700, exist_ok=True)
    bundle = get_config_dir() / "config.yml"
    text = "My Cluster:\n  cluster_type: ssh\n  cluster_host: mine.example.edu\n"
    bundle.write_text(text)

    result = clustrix(
        "config", "--cluster-type", "ssh", "--cluster-host", "box.example.edu"
    )

    assert result.returncode == 1
    assert "named profiles" in result.stderr
    assert bundle.read_text() == text


def _closed_port() -> int:
    """A real port number with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _point_at(port, **extra):
    get_config_dir().mkdir(mode=0o700, exist_ok=True)
    settings = dict(
        cluster_type="ssh",
        cluster_host="127.0.0.1",
        cluster_port=port,
        username="tester",
        ssh_connect_timeout=5,
    )
    settings.update(extra)
    (get_config_dir() / "config.yml").write_text(yaml.safe_dump(settings))


def test_status_exits_non_zero_when_the_cluster_cannot_be_reached():
    """It printed "Connection: ✗ Failed" and exited 0."""
    _point_at(_closed_port())

    result = clustrix("status")

    assert "Connection: ✗ Failed" in result.stdout
    assert result.returncode == 1


def test_status_exits_zero_when_the_cluster_answers(tmp_path):
    """The other half: a real server, a real key, a real login."""
    (tmp_path / "keys").mkdir()
    key = generate_keypair(tmp_path / "keys", "id_ed25519")
    remote = tmp_path / "remote"
    remote.mkdir()
    with LocalSSHServer(root=remote, authorized_keys=[f"{key}.pub"]) as server:
        _point_at(server.port, key_file=str(key), ssh_host_key_policy="auto_add")

        result = clustrix("status")

        assert "Connection: ✓ Connected" in result.stdout
        assert result.returncode == 0
        assert ("tester", "publickey") in server.authentications


def test_status_with_nothing_configured_says_local_and_is_not_a_failure():
    """#176 L1. It printed "No cluster configured" -- which is not what the
    situation is: functions run on this machine -- while the notebook's
    ``%clustrix status`` said "local" for the same configuration."""
    from clustrix.config import ClusterConfig
    from clustrix.notebook_magic_core import _status_summary

    result = clustrix("status")

    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0].startswith("backend      : local")
    assert "No cluster configured" not in result.stdout
    assert lines[:-1] == _status_summary(ClusterConfig()).splitlines()
    assert lines[-1].startswith("connection   : not checked")


def test_status_for_the_local_backend_makes_no_connection():
    get_config_dir().mkdir(mode=0o700, exist_ok=True)
    (get_config_dir() / "config.yml").write_text(
        yaml.safe_dump(dict(cluster_type="local", cluster_host="127.0.0.1"))
    )

    result = clustrix("status")

    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == "backend      : local"
    assert "Connection: ✗" not in result.stdout


def test_status_names_huggingface_without_claiming_a_connection():
    get_config_dir().mkdir(mode=0o700, exist_ok=True)
    (get_config_dir() / "config.yml").write_text(
        yaml.safe_dump(dict(cluster_type="huggingface"))
    )

    result = clustrix("status")

    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == "backend      : huggingface"
    assert "Connected" not in result.stdout and "not checked" in result.stdout
