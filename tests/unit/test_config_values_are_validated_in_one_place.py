"""A setting is refused identically however it reaches the configuration.

``ClusterConfig.__init__`` validated values; ``configure()`` did not, because
it applies keywords with ``setattr`` and ``setattr`` does not re-run
``__post_init__``. So ``configure(ssh_host_key_policy="yolo")`` was accepted,
``save_config`` wrote it, and every later process that found the file failed
to load it. There is now one validator, reached from construction, from
``configure()`` and from every file loader, and these tests drive all three
doors with the same table.

Real files in a real (temporary) configuration directory; nothing is mocked.
"""

import dataclasses
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import yaml

from clustrix.config import (
    ClusterConfig,
    configure,
    get_config,
    get_config_dir,
    load_config,
    save_config,
)
from clustrix.profile_manager import ProfileManager

#: (keywords, a fragment the refusal must contain)
REFUSED = [
    ({"ssh_host_key_policy": "yolo"}, "ssh_host_key_policy='yolo'"),
    ({"cluster_port": "abc"}, "cluster_port='abc'"),
    ({"cluster_port": 0}, "cluster_port=0"),
    ({"cluster_port": 70000}, "cluster_port=70000"),
    ({"cluster_port": True}, "cluster_port=True"),
    ({"default_cores": -3}, "default_cores=-3"),
    ({"default_cores": 0}, "default_cores=0"),
    ({"default_cores": "8"}, "default_cores='8'"),
    ({"cleanup_on_success": "yes"}, "cleanup_on_success='yes'"),
    ({"module_loads": "gcc"}, "module_loads='gcc'"),
    ({"environment_variables": ["A=1"]}, "environment_variables=['A=1']"),
    ({"cluster_type": "pbs"}, "cluster_type='pbs'"),
    ({"cluster_host": 2130706433}, "cluster_host=2130706433"),
]
IDS = [next(iter(kw)) + "=" + repr(next(iter(kw.values()))) for kw, _ in REFUSED]


@pytest.mark.parametrize("keywords,fragment", REFUSED, ids=IDS)
def test_construction_refuses_it(keywords, fragment):
    with pytest.raises(ValueError) as refusal:
        ClusterConfig(**keywords)
    print(refusal.value)
    assert fragment in str(refusal.value)


@pytest.mark.parametrize("keywords,fragment", REFUSED, ids=IDS)
def test_configure_refuses_it_and_changes_nothing(keywords, fragment):
    """Including the keywords that came *before* the bad one in the call."""
    before = dataclasses.asdict(get_config())

    with pytest.raises(ValueError) as refusal:
        configure(username="someone-else", **keywords)
    print(refusal.value)

    assert fragment in str(refusal.value)
    assert dataclasses.asdict(get_config()) == before


@pytest.mark.parametrize("keywords,fragment", REFUSED, ids=IDS)
def test_a_file_holding_it_is_refused_and_the_file_is_named(
    keywords, fragment, tmp_path
):
    path = tmp_path / "settings.yml"
    path.write_text(yaml.safe_dump(keywords))

    for loader in (load_config, ClusterConfig.load_from_file):
        with pytest.raises(ValueError) as refusal:
            loader(str(path))
        print(loader.__qualname__, "->", refusal.value)
        assert fragment in str(refusal.value)
        assert str(path) in str(refusal.value)


def test_minus_one_cores_means_every_core_and_is_accepted():
    configure(default_cores=-1)
    assert get_config().default_cores == -1
    assert ClusterConfig(default_cores=-1).default_cores == -1


def test_none_means_empty_through_configure_as_it_does_at_construction():
    """``configure(environment_variables=None)`` left a literal ``None``."""
    configure(environment_variables=None, module_loads=None)

    assert get_config().environment_variables == {}
    assert get_config().module_loads == []


def test_nothing_configure_accepts_can_break_the_next_process(tmp_path):
    """The end-to-end shape of the bug: accept, save, fail to load -- forever.

    A second interpreter is the honest check that what was saved is loadable
    by "every later process".
    """
    with pytest.raises(ValueError):
        configure(ssh_host_key_policy="yolo")
    configure(cluster_type="ssh", cluster_host="box.example.edu", cluster_port=2222)
    save_config(str(get_config_dir() / "config.yml"))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from clustrix.config import get_config; c = get_config(); "
            "print(c.cluster_host, c.cluster_port, c.ssh_host_key_policy)",
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )

    print(result.stdout, result.stderr)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["box.example.edu", "2222", "reject"]


@pytest.mark.parametrize("suffix", [".yml", ".json"])
def test_an_empty_file_is_reported_as_empty(tmp_path, suffix):
    """It used to be ``TypeError: 'NoneType' object is not iterable``."""
    path = tmp_path / f"empty{suffix}"
    path.write_text("" if suffix == ".yml" else "null")

    with pytest.raises(ValueError, match="the file is empty") as refusal:
        ClusterConfig.load_from_file(str(path))
    assert str(path) in str(refusal.value)

    manager = ProfileManager(str(tmp_path / "profiles"))
    before = manager.get_profile_names()
    with pytest.raises(ValueError, match="the file is empty"):
        manager.import_profile(str(path))
    assert manager.get_profile_names() == before


def test_a_file_holding_a_list_is_not_a_configuration(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps(["cluster_host", "x"]))

    with pytest.raises(ValueError, match="expected a mapping of settings, found list"):
        ClusterConfig.load_from_file(str(path))


def test_save_and_load_expand_the_home_directory():
    """``save_config("~/x.yml")`` created a directory literally named ``~``."""
    configure(cluster_type="ssh", cluster_host="box.example.edu")

    save_config("~/saved-by-tilde.yml")

    saved = Path.home() / "saved-by-tilde.yml"
    assert saved.exists()
    assert not Path("~").exists()
    assert ClusterConfig.load_from_file("~/saved-by-tilde.yml").cluster_host == (
        "box.example.edu"
    )
    load_config("~/saved-by-tilde.yml")
    assert get_config().cluster_host == "box.example.edu"


class TestThereIsOnePort:
    """``ssh_port`` is a deprecated way to spell ``cluster_port``, on input only.

    Two fields used to exist. The executor connected to ``cluster_port``; the
    auth helpers read ``ssh_port``; setting one left the other half of
    clustrix dialling 22.
    """

    def test_ssh_port_is_not_a_field(self):
        names = {f.name for f in dataclasses.fields(ClusterConfig)}
        assert "cluster_port" in names
        assert "ssh_port" not in names
        assert "ssh_port" not in dataclasses.asdict(ClusterConfig())

    def test_ssh_port_at_construction_sets_cluster_port_and_warns(self):
        with pytest.warns(DeprecationWarning, match="use cluster_port=2222"):
            config = ClusterConfig(ssh_port=2222)
        assert config.cluster_port == 2222

    def test_ssh_port_through_configure_sets_cluster_port_and_warns(self):
        with pytest.warns(DeprecationWarning, match="cluster_port=2201"):
            configure(ssh_port=2201)
        assert get_config().cluster_port == 2201

    def test_assigning_ssh_port_sets_cluster_port_and_warns(self):
        config = ClusterConfig()
        with pytest.warns(DeprecationWarning, match="assign cluster_port"):
            config.ssh_port = 2300
        assert config.cluster_port == 2300
        with pytest.raises(ValueError, match="cluster_port='abc'"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                config.ssh_port = "abc"
        assert config.cluster_port == 2300

    def test_cluster_port_wins_a_disagreement(self):
        """It is the port jobs were already using."""
        with pytest.warns(DeprecationWarning, match="was ignored"):
            config = ClusterConfig(cluster_port=2200, ssh_port=2222)
        assert config.cluster_port == 2200

    def test_a_file_saved_before_the_change_still_loads_silently(self, tmp_path):
        """Every old file carries ``ssh_port: 22``; that is not a claim."""
        path = tmp_path / "old.yml"
        path.write_text(
            "cluster_type: ssh\ncluster_host: box.example.edu\n"
            "cluster_port: 2200\nssh_port: 22\n"
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            config = ClusterConfig.load_from_file(str(path))

        assert config.cluster_port == 2200

    def test_a_file_that_only_set_ssh_port_gets_that_port(self, tmp_path):
        path = tmp_path / "old.yml"
        path.write_text(
            "cluster_type: ssh\ncluster_host: box.example.edu\nssh_port: 2222\n"
        )

        with pytest.warns(DeprecationWarning):
            load_config(str(path))

        assert get_config().cluster_port == 2222

    def test_an_old_profile_store_still_loads(self, tmp_path):
        store = tmp_path / "profiles"
        store.mkdir()
        (store / "profiles.yml").write_text(
            "active_profile: old\nprofiles:\n"
            "  old: {cluster_type: ssh, cluster_host: box.example.edu, "
            "ssh_port: 22, cluster_port: 2200}\n"
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            manager = ProfileManager(str(store))

        assert [str(w.message) for w in caught if "could not" in str(w.message)] == []
        assert manager.load_profile("old").cluster_port == 2200

    def test_replace_and_copy_keep_the_port(self):
        import copy

        config = ClusterConfig(cluster_port=2200)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert dataclasses.replace(config, username="x").cluster_port == 2200
            assert copy.deepcopy(config).cluster_port == 2200

    def test_the_stored_credential_records_the_port_the_executor_uses(self):
        """``AuthenticationManager`` read ``ssh_port``: 22, whatever was set.

        A real ``.env`` in the (isolated) configuration directory, written by
        the real storage path and read back off disk.
        """
        from clustrix.auth_manager import AuthenticationManager
        from clustrix.credential_manager import get_credential_manager

        manager = AuthenticationManager(
            ClusterConfig(
                cluster_type="slurm",
                cluster_host="box.example.edu",
                username="me",
                cluster_port=2200,
            )
        )

        assert manager._store_in_env_file("a-password", "box.example.edu", "me")

        written = get_credential_manager().env_file.read_text()
        assert "SSH_PORT=2200" in written.replace('"', "").replace("'", "")
