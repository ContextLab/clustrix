"""Tests for expanding ``~`` in remote paths.

Shell commands expand ``~`` themselves; SFTP does not. A work directory
created correctly by ``mkdir -p ~/.clustrix/jobs`` and then uploaded into a
*relative* directory literally named ``~`` is the kind of failure that wastes
an afternoon, so the expansion is done explicitly and tested here.

These use a small stand-in for the SSH channel rather than a mock library:
what matters is the exact bytes a real ``echo $HOME`` can return, including
the awkward ones.
"""

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager


class FakeShell:
    """Records commands and replays a canned ``echo $HOME`` response."""

    def __init__(self, home_output):
        self.home_output = home_output
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return self.home_output, ""


def _manager(home_output):
    mgr = ConnectionManager(ClusterConfig(cluster_type="slurm"))
    shell = FakeShell(home_output)
    mgr.execute_remote_command = shell  # type: ignore[method-assign]
    return mgr, shell


class TestTildeExpansion:
    def test_expands_home_relative_path(self):
        mgr, _ = _manager("/remote/home/testuser\n")
        assert (
            mgr.resolve_remote_path("~/.clustrix/jobs")
            == "/remote/home/testuser/.clustrix/jobs"
        )

    def test_expands_bare_tilde(self):
        mgr, _ = _manager("/home/alice\n")
        assert mgr.resolve_remote_path("~") == "/home/alice"

    def test_absolute_paths_pass_through_untouched(self):
        mgr, shell = _manager("/home/alice\n")
        assert mgr.resolve_remote_path("/scratch/project") == "/scratch/project"
        assert shell.commands == [], "should not ask the remote host at all"

    def test_relative_paths_pass_through_untouched(self):
        mgr, shell = _manager("/home/alice\n")
        assert mgr.resolve_remote_path("work/jobs") == "work/jobs"
        assert shell.commands == []

    def test_other_users_home_is_left_for_the_shell(self):
        """``~bob/foo`` cannot be derived from ``$HOME``.

        Concatenating it produced ``/home/alicebob/foo`` -- a silently wrong
        path with no error anywhere.
        """
        mgr, shell = _manager("/home/alice\n")
        assert mgr.resolve_remote_path("~bob/foo") == "~bob/foo"
        assert shell.commands == []

    def test_trailing_slash_on_home_does_not_double_up(self):
        mgr, _ = _manager("/home/alice/\n")
        assert mgr.resolve_remote_path("~/jobs") == "/home/alice/jobs"

    def test_login_banner_before_the_path_is_ignored(self):
        """Profiles that print a banner must not corrupt the path.

        bash sources ~/.bashrc for ssh exec channels, so ``echo $HOME`` can
        come back with a MOTD ahead of the value.
        """
        mgr, _ = _manager(
            "*** Welcome to discovery ***\nUse Open OnDemand instead\n/home/alice\n"
        )
        assert mgr.resolve_remote_path("~/jobs") == "/home/alice/jobs"

    def test_unusable_home_raises_instead_of_guessing(self):
        mgr, _ = _manager("\n")
        with pytest.raises(RuntimeError, match="absolute path"):
            mgr.resolve_remote_path("~/jobs")

    def test_non_absolute_home_raises(self):
        mgr, _ = _manager("not-a-path\n")
        with pytest.raises(RuntimeError, match="absolute path"):
            mgr.resolve_remote_path("~/jobs")

    def test_home_is_resolved_once_and_cached(self):
        mgr, shell = _manager("/home/alice\n")
        mgr.resolve_remote_path("~/a")
        mgr.resolve_remote_path("~/b")
        mgr.resolve_remote_path("~/c")
        assert len(shell.commands) == 1

    def test_disconnect_clears_the_cache(self):
        """A later connect() may be a different account entirely."""
        mgr, _ = _manager("/home/alice\n")
        assert mgr.resolve_remote_path("~") == "/home/alice"

        mgr.disconnect()
        mgr.execute_remote_command = FakeShell("/home/bob\n")  # type: ignore[method-assign]
        assert mgr.resolve_remote_path("~") == "/home/bob"


class TestDefaultWorkDir:
    def test_default_is_home_relative_not_tmp(self):
        """``/tmp`` is node-local on SLURM, PBS and SGE.

        An environment built on the login node is simply absent on the compute
        node, and the job dies at exit 127 before it can write any diagnostic.
        """
        config = ClusterConfig()
        assert config.remote_work_dir.startswith("~/")
        assert "/tmp" not in config.remote_work_dir
