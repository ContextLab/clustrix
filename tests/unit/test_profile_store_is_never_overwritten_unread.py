"""A profile store clustrix could not read is never written over.

``ProfileManager._restore`` used to answer any failure -- corrupt YAML, or a
single key in a single profile that this version does not know -- by falling
back to the six built-in templates, and the next mutator's ``_persist`` then
wrote those templates over the file. Opening the widget and switching profile
was enough to lose every saved profile.

Everything here is a real ``ProfileManager`` over a real directory.
"""

import warnings

import pytest
import yaml

from clustrix.config import ClusterConfig
from clustrix.profile_manager import ProfileManager


def _saved_store(directory):
    """A store holding two user profiles, written by the real manager."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manager = ProfileManager(str(directory))
        manager.create_profile(
            "hpc",
            ClusterConfig(
                cluster_type="slurm", cluster_host="hpc.example.edu", username="me"
            ),
        )
        manager.create_profile(
            "box",
            ClusterConfig(
                cluster_type="ssh", cluster_host="box.example.edu", username="me"
            ),
        )
    return manager.store_path


def _reopen(directory):
    """A new session over the same directory, with what it warned about."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager = ProfileManager(str(directory))
    messages = [str(w.message) for w in caught]
    print("warnings on open:", *messages, sep="\n  ")
    return manager, messages


def _backups(store_path):
    return sorted(store_path.parent.glob(f"{store_path.name}.unreadable-*.bak"))


def test_one_unknown_key_does_not_cost_the_other_profiles(tmp_path):
    """The readable profiles load; the unreadable one is named, not dropped."""
    store = _saved_store(tmp_path)
    document = yaml.safe_load(store.read_text())
    document["profiles"]["hpc"]["setting_from_a_newer_clustrix"] = 1
    store.write_text(yaml.safe_dump(document))

    manager, messages = _reopen(tmp_path)

    assert "box" in manager.get_profile_names()
    assert manager.load_profile("box").cluster_host == "box.example.edu"
    assert "hpc" not in manager.get_profile_names()
    assert any(
        "hpc" in m and "setting_from_a_newer_clustrix" in m for m in messages
    ), messages


def test_a_mutation_after_a_partial_restore_keeps_the_original_file(tmp_path):
    """The skipped profile exists only in the file, so the file is kept."""
    store = _saved_store(tmp_path)
    document = yaml.safe_load(store.read_text())
    document["profiles"]["hpc"]["setting_from_a_newer_clustrix"] = 1
    store.write_text(yaml.safe_dump(document))
    original = store.read_text()
    manager, _ = _reopen(tmp_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager.set_active_profile("box")
        manager.set_active_profile("Local single-core")
    messages = [str(w.message) for w in caught]
    print("warnings on save:", *messages, sep="\n  ")

    backups = _backups(store)
    assert len(backups) == 1, "exactly one backup, not one per save"
    assert backups[0].read_text() == original
    assert (backups[0].stat().st_mode & 0o077) == 0
    assert any(str(backups[0]) in m for m in messages), messages
    # The new store is a readable one holding what the session could see.
    assert "box" in yaml.safe_load(store.read_text())["profiles"]


def test_a_corrupt_store_is_set_aside_not_replaced_by_the_templates(tmp_path):
    """Corrupt YAML: built-ins are shown, and the user's bytes survive."""
    store = _saved_store(tmp_path)
    corrupt = store.read_text() + "\n  this: is: not: yaml: [\n"
    store.write_text(corrupt)

    manager, messages = _reopen(tmp_path)
    assert manager.get_profile_names() == list(ProfileManager.BUILTIN_PROFILES)
    assert any("Could not read saved profiles" in m for m in messages)
    assert store.read_text() == corrupt, "opening the manager changed the file"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manager.clone_profile("Local single-core", "mine")

    backups = _backups(store)
    assert [b.read_text() for b in backups] == [corrupt]
    assert "hpc.example.edu" in backups[0].read_text()
    assert "mine" in yaml.safe_load(store.read_text())["profiles"]


def test_a_readable_store_is_saved_over_without_any_backup(tmp_path):
    """The ordinary case leaves no litter behind."""
    store = _saved_store(tmp_path)
    manager, messages = _reopen(tmp_path)
    manager.set_active_profile("hpc")

    assert _backups(store) == []
    assert not any("could not" in m.lower() for m in messages), messages
    assert yaml.safe_load(store.read_text())["active_profile"] == "hpc"


@pytest.mark.parametrize("bad_name", ["", "   ", None, 7])
def test_a_profile_cannot_be_given_an_empty_name(tmp_path, bad_name):
    """``rename_profile(x, "")`` produced a dropdown entry nobody could see."""
    manager = ProfileManager(str(tmp_path))
    before = manager.get_profile_names()

    with pytest.raises(ValueError, match="non-empty string"):
        manager.rename_profile("Local all cores", bad_name)
    with pytest.raises(ValueError, match="non-empty string"):
        manager.create_profile(bad_name, ClusterConfig())
    with pytest.raises(ValueError, match="non-empty string"):
        manager.save_profile(bad_name, ClusterConfig())
    with pytest.raises(ValueError, match="non-empty string"):
        manager.clone_profile("Local all cores", bad_name or "")

    assert manager.get_profile_names() == before


def test_a_profile_must_be_a_cluster_config(tmp_path):
    """A dict was accepted, and then every later save failed for everyone."""
    manager = ProfileManager(str(tmp_path))

    with pytest.raises(TypeError, match="must be a ClusterConfig, not dict"):
        manager.create_profile("from-a-dict", {"cluster_type": "ssh"})
    with pytest.raises(TypeError, match="must be a ClusterConfig, not dict"):
        manager.save_profile("Local all cores", {"cluster_type": "ssh"})

    assert "from-a-dict" not in manager.get_profile_names()
    # And the store still saves.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        manager.set_active_profile("Local all cores")
    assert manager.store_path.exists()
