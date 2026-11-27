"""Two sessions over one profile store do not erase each other's work.

Every ``ProfileManager`` holds the whole profile set in memory and writes the
whole set on every change. With two notebooks open, the last one to save used
to win outright: a profile created in the first notebook vanished the moment
the second one switched profile. Measured before the fix -- two managers each
create a profile, and a third sees only the second one's.

These are real managers over one real directory, and the last test is two
real processes.
"""

import os
import subprocess
import sys
import warnings

import pytest

from clustrix.config import ClusterConfig
from clustrix.profile_manager import ProfileManager


def ssh_profile(host):
    return ClusterConfig(cluster_type="ssh", cluster_host=host, username="me")


@pytest.fixture
def two_sessions(tmp_path):
    """Two managers opened on the same, initially empty, store."""
    return ProfileManager(str(tmp_path)), ProfileManager(str(tmp_path))


def fresh(manager):
    """What a session opened now would see."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return ProfileManager(str(manager.config_dir))


def test_profiles_created_in_both_sessions_both_survive(two_sessions):
    first, second = two_sessions

    first.create_profile("from-first", ssh_profile("first.example.edu"))
    second.create_profile("from-second", ssh_profile("second.example.edu"))

    names = fresh(first).get_profile_names()
    print(names)
    assert "from-first" in names
    assert "from-second" in names
    # The session that saved second also learned of the other's profile.
    assert second.load_profile("from-first").cluster_host == "first.example.edu"


def test_an_edit_in_one_session_is_not_undone_by_a_save_in_the_other(two_sessions):
    """The second session never touched that profile; its stale copy must lose."""
    first, second = two_sessions
    first.create_profile("shared", ssh_profile("old.example.edu"))
    second.set_active_profile("Local all cores")  # picks "shared" up

    first.save_profile("shared", ssh_profile("new.example.edu"))
    second.set_active_profile("Local single-core")

    assert fresh(first).load_profile("shared").cluster_host == "new.example.edu"
    assert second.load_profile("shared").cluster_host == "new.example.edu"


def test_a_change_made_here_wins_over_a_change_made_elsewhere(two_sessions):
    """Both edited the same profile: the one being saved is the one kept."""
    first, second = two_sessions
    first.create_profile("shared", ssh_profile("old.example.edu"))
    second.set_active_profile("Local all cores")

    first.save_profile("shared", ssh_profile("first.example.edu"))
    second.save_profile("shared", ssh_profile("second.example.edu"))

    assert fresh(first).load_profile("shared").cluster_host == "second.example.edu"


def test_a_removal_in_one_session_is_not_resurrected_by_the_other(two_sessions):
    first, second = two_sessions
    first.create_profile("doomed", ssh_profile("doomed.example.edu"))
    second.set_active_profile("Local all cores")
    assert "doomed" in second.get_profile_names()

    first.remove_profile("doomed")
    second.set_active_profile("Local single-core")

    assert "doomed" not in fresh(first).get_profile_names()
    assert "doomed" not in second.get_profile_names()
    assert second.active_profile in second.get_profile_names()


def test_a_removal_here_survives_the_other_session_still_holding_it(two_sessions):
    first, second = two_sessions
    first.create_profile("doomed", ssh_profile("doomed.example.edu"))
    second.set_active_profile("Local all cores")

    second.remove_profile("doomed")
    first.set_active_profile("Local all cores")

    assert "doomed" not in fresh(first).get_profile_names()


def test_a_rename_in_one_session_does_not_leave_both_names(two_sessions):
    first, second = two_sessions
    first.create_profile("before", ssh_profile("box.example.edu"))
    second.set_active_profile("Local all cores")

    first.rename_profile("before", "after")
    second.clone_profile("Local single-core", "unrelated")

    names = fresh(first).get_profile_names()
    assert "after" in names and "unrelated" in names
    assert "before" not in names


def test_a_session_only_password_is_kept_through_a_merge(two_sessions):
    """Secrets are never written, so a merge must not swap the object out."""
    first, second = two_sessions
    config = ssh_profile("box.example.edu")
    config.password = "<redacted>"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        first.create_profile("with-password", config)

    second.create_profile("elsewhere", ssh_profile("other.example.edu"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        first.set_active_profile("with-password")

    assert "elsewhere" in first.get_profile_names()
    assert first.load_profile("with-password").password == "<redacted>"
    assert "<redacted>" not in first.store_path.read_text()


def test_one_session_alone_writes_exactly_what_it_holds(tmp_path):
    """No merge, no surprises, when nobody else is writing."""
    manager = ProfileManager(str(tmp_path))
    manager.create_profile("mine", ssh_profile("box.example.edu"))
    manager.remove_profile("SLURM cluster")

    # Sorted: the store is YAML with sorted keys, so order is not preserved
    # across a restart. The set is what must be exact.
    assert sorted(fresh(manager).get_profile_names()) == sorted(
        manager.get_profile_names()
    )
    assert "SLURM cluster" not in fresh(manager).get_profile_names()


def test_many_processes_creating_profiles_at_once_lose_none(tmp_path):
    """The read-merge-write is one step: real processes, started together."""
    script = (
        "import sys, warnings; warnings.simplefilter('ignore');"
        "from clustrix.config import ClusterConfig;"
        "from clustrix.profile_manager import ProfileManager;"
        "m = ProfileManager(sys.argv[1]);"
        "[m.create_profile(f'{sys.argv[2]}-{i}', ClusterConfig(cluster_type='ssh'))"
        " for i in range(5)]"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), f"worker{n}"],
            env=dict(os.environ),
        )
        for n in range(4)
    ]
    assert [worker.wait(timeout=120) for worker in workers] == [0, 0, 0, 0]

    names = set(ProfileManager(str(tmp_path)).get_profile_names())
    expected = {f"worker{n}-{i}" for n in range(4) for i in range(5)}
    print(sorted(names))
    assert expected <= names, f"lost: {sorted(expected - names)}"
