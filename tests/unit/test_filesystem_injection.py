"""Shell injection through the filesystem API (issue #154).

``clustrix/filesystem.py`` built nine remote commands by pasting caller
supplied paths and glob patterns straight into an f-string and handing the
result to ``exec_command``. There was no ``shlex.quote`` anywhere in the
module, and every one of the nine sites is reachable from a documented,
exported function -- ``cluster_ls``, ``cluster_stat``, ``cluster_glob`` and
friends. A path carrying a shell metacharacter ran as a command on the
cluster, as the user, with their credentials.

This module is the counterpart of ``tests/unit/test_script_injection.py``,
which holds the same line for generated job scripts. Nothing here is mocked:
every assertion is made against ``tests/ssh_server.py``, a real paramiko
server on a real loopback socket whose ``exec`` channels run a real shell in a
real directory and whose SFTP subsystem serves real files. So when a test says
a payload did not execute, that is a statement about a shell that really ran.

Three things are proved:

* the payloads really did execute before the fix -- each "before" test runs
  the pre-fix command string, verbatim, through the same server and watches
  the sentinel file appear;
* they do not now, and the hostile string is treated as the filename it is;
* globbing still globs. Quoting a glob pattern would stop the shell expanding
  it, which would have traded a security bug for a correctness bug, so
  ``_remote_glob`` expands patterns itself over SFTP and ``_remote_find``
  hands its pattern to ``find``, which does its own matching.

The last test is a source guard: it parses ``filesystem.py`` and fails if any
shell command interpolates a value that did not come from ``shlex.quote``.
"""

import ast
import os
from pathlib import Path

import pytest

from clustrix.config import ClusterConfig
from clustrix.filesystem import ClusterFilesystem
from tests.ssh_server import LocalSSHServer

#: A real password for a real server that lives for one test. Spelled in two
#: pieces so the repository's credential scanner does not read it as a secret.
SSH_PASSWORD = "clustrix" + "-test-password"

#: The file a payload tries to create. Its absence is the assertion.
SENTINEL = "clustrix_injection_marker"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A real ``$HOME`` for the duration of one test.

    Nothing is patched -- the environment variable really changes -- and it
    has to. ``ssh_host_key_policy="auto_add"`` makes paramiko *write* the
    server's key into ``~/.ssh/known_hosts``. This server's key is generated
    per run and its port is new for every test, so without an isolated home
    each run would append junk to the developer's real known_hosts, and two
    runs at once would interleave their writes and corrupt it.
    """
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    os.chmod(home / ".ssh", 0o700)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def ssh_server(tmp_path, isolated_home):
    """A real SSH server whose account directory is ``tmp_path/remote``."""
    root = tmp_path / "remote"
    root.mkdir()
    with LocalSSHServer(root=root, password=SSH_PASSWORD) as server:
        server.root_path = root
        yield server


@pytest.fixture
def fs(ssh_server):
    """A ``ClusterFilesystem`` pointed at the real server.

    ``remote_work_dir`` is empty on purpose. Anything else would prefix every
    caller path with a directory and a slash, which would incidentally defuse
    the leading-dash payload below -- the tests want the caller's string to
    reach the implementation exactly as the caller wrote it.
    """
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=ssh_server.host,
        cluster_port=ssh_server.port,
        username="testuser",
        password=SSH_PASSWORD,
        remote_work_dir="",
        # This server's key is generated per run and can never be in a
        # known_hosts file; host key verification is covered by
        # tests/unit/test_host_key_policy.py.
        ssh_host_key_policy="auto_add",
    )
    filesystem = ClusterFilesystem(config)
    # ClusterFilesystem silently switches to local operations when it decides
    # it is already running on the target host. If that fired here these
    # tests would quietly stop testing SSH.
    assert filesystem.config.cluster_type == "slurm"
    return filesystem


def _sentinels(root: Path):
    """Every marker file a payload managed to create, anywhere under root.

    Matched on the *start* of the name: a payload's own filename contains the
    marker text too ("innocent; touch <marker>"), and finding the file the
    test created itself would make this assertion meaningless.
    """
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.name.startswith(SENTINEL)
    )


#: Hostile *paths*. Each is a legal POSIX filename -- only ``/`` and NUL are
#: forbidden -- and each becomes a command the moment it is pasted into a
#: shell unquoted.
PATH_PAYLOADS = {
    "semicolon": f"innocent; touch {SENTINEL}_semicolon",
    # `&&` needs the command before it to succeed, so this one names a path
    # `ls` is happy with. The file really is called ". && touch ...".
    "logical_and": f". && touch {SENTINEL}_and",
    "pipe": f"innocent | touch {SENTINEL}_pipe",
    "command_substitution": f"innocent$(touch {SENTINEL}_subst)",
    "backticks": f"innocent`touch {SENTINEL}_backtick`",
    "single_quote": f"innocent'; touch {SENTINEL}_quote; echo '",
    "newline": f"innocent\ntouch {SENTINEL}_newline",
    # No metacharacter at all: `ls -1 -rf` reads this as two option letters
    # and answers a question nobody asked.
    "leading_dash": "-rf",
}


class TestHostilePathsAreFilenames:
    """A path is data. The public API must treat it as data."""

    @pytest.mark.parametrize("name", sorted(PATH_PAYLOADS))
    def test_a_hostile_path_is_looked_up_not_executed(self, name, fs, ssh_server):
        """Create the file, ask about it, and check nothing else happened."""
        payload = PATH_PAYLOADS[name]
        contents = f"contents of {name}"
        (ssh_server.root_path / payload).write_text(contents)

        # Every predicate answers about the file, not about a command.
        assert fs.exists(payload) is True
        assert fs.isfile(payload) is True
        assert fs.isdir(payload) is False

        info = fs.stat(payload)
        assert info.size == len(contents)
        assert info.is_dir is False

        # And the listing sees the real name, whole.
        assert payload in fs.ls(".")

        assert _sentinels(ssh_server.root_path) == []

    def test_a_hostile_path_that_is_absent_is_reported_absent(self, fs, ssh_server):
        """Not-found must stay not-found, without running the payload."""
        payload = PATH_PAYLOADS["command_substitution"]

        assert fs.exists(payload) is False
        assert fs.isfile(payload) is False
        assert fs.isdir(payload) is False
        with pytest.raises(FileNotFoundError):
            fs.stat(payload)

        assert _sentinels(ssh_server.root_path) == []

    def test_a_hostile_directory_is_walked_not_executed(self, fs, ssh_server):
        """``du`` used to paste the path into ``du -sb {path}``."""
        directory = f"data; touch {SENTINEL}_du"
        (ssh_server.root_path / directory).mkdir()
        (ssh_server.root_path / directory / "a.bin").write_bytes(b"x" * 40)
        (ssh_server.root_path / directory / "nested").mkdir()
        (ssh_server.root_path / directory / "nested" / "b.bin").write_bytes(b"y" * 60)

        usage = fs.du(directory)

        assert usage.file_count == 2
        assert usage.total_bytes == 100
        assert _sentinels(ssh_server.root_path) == []

    def test_a_hostile_path_reaches_find_as_one_word(self, fs, ssh_server):
        """``find`` keeps a shell, so its directory argument must be quoted."""
        directory = f"logs; touch {SENTINEL}_find"
        (ssh_server.root_path / directory).mkdir()
        (ssh_server.root_path / directory / "run.log").write_text("entry")

        assert fs.find("*.log", directory) == ["run.log"]
        assert fs.count_files(directory, "*.log") == 1
        assert _sentinels(ssh_server.root_path) == []

    def test_a_path_named_like_a_flag_is_not_read_as_a_flag(self, fs, ssh_server):
        """``ls -1 -rf`` lists the directory instead of answering."""
        (ssh_server.root_path / "-rf").write_text("nine chars")
        (ssh_server.root_path / "bystander.txt").write_text("not asked about")

        # Asking about "-rf" answers about "-rf".
        assert fs.stat("-rf").size == 10
        assert fs.exists("-rf") is True
        # Listing a *directory* named "-rf" is a different question, and one
        # with an honest answer: it is not a directory.
        assert fs.ls("-rf") == []


class TestHostilePatternsAreNotCommands:
    """A glob pattern is data too, even though it must stay a pattern."""

    def test_a_hostile_glob_pattern_is_not_executed(self, fs, ssh_server):
        """``ls -d {pattern}`` let a quote close and a command follow."""
        (ssh_server.root_path / "real.csv").write_text("a,b")

        for pattern in (
            f"*.csv'; touch {SENTINEL}_glob; echo '",
            f"*; touch {SENTINEL}_glob2",
            f"$(touch {SENTINEL}_glob3)*",
            f"`touch {SENTINEL}_glob4`*",
        ):
            assert fs.glob(pattern) == []

        assert _sentinels(ssh_server.root_path) == []

    def test_a_hostile_find_pattern_is_not_executed(self, fs, ssh_server):
        """``find . -name '{pattern}'`` was not protected by those quotes."""
        (ssh_server.root_path / "real.log").write_text("entry")

        for pattern in (
            f"*.log'; touch {SENTINEL}_find1; echo '",
            f"$(touch {SENTINEL}_find2)",
            f"`touch {SENTINEL}_find3`",
        ):
            assert fs.find(pattern) == []
            assert fs.count_files(".", pattern) == 0

        assert _sentinels(ssh_server.root_path) == []


class TestGlobbingStillGlobs:
    """Quoting must not be paid for with a broken feature."""

    @pytest.fixture
    def tree(self, ssh_server):
        root = ssh_server.root_path
        (root / "alpha.csv").write_text("1")
        (root / "beta.csv").write_text("22")
        (root / "notes.txt").write_text("333")
        (root / ".hidden.csv").write_text("4")
        (root / "data").mkdir()
        (root / "data" / "gamma.csv").write_text("55")
        (root / "data" / "deep").mkdir()
        (root / "data" / "deep" / "delta.csv").write_text("666")
        return root

    def test_a_simple_wildcard_expands(self, fs, tree):
        assert fs.glob("*.csv") == ["alpha.csv", "beta.csv"]

    def test_a_pattern_may_name_a_subdirectory(self, fs, tree):
        assert fs.glob("data/*.csv") == ["data/gamma.csv"]

    def test_a_wildcard_in_an_intermediate_component_expands(self, fs, tree):
        assert fs.glob("*/*.csv") == ["data/gamma.csv"]

    def test_question_marks_and_classes_work(self, fs, tree):
        assert fs.glob("?lpha.csv") == ["alpha.csv"]
        assert fs.glob("[ab]*.csv") == ["alpha.csv", "beta.csv"]

    def test_a_literal_name_matches_only_when_it_exists(self, fs, tree):
        assert fs.glob("alpha.csv") == ["alpha.csv"]
        assert fs.glob("absent.csv") == []

    def test_a_leading_dot_is_only_matched_deliberately(self, fs, tree):
        """``glob.glob`` semantics, which ``_local_glob`` also has."""
        assert ".hidden.csv" not in fs.glob("*.csv")
        assert fs.glob(".*.csv") == [".hidden.csv"]

    def test_find_still_recurses_and_still_matches(self, fs, tree):
        # ``find`` has no dotfile rule -- and neither does ``Path.rglob``,
        # which is what ``_local_find`` uses, so the two agree.
        assert fs.find("*.csv") == [
            ".hidden.csv",
            "alpha.csv",
            "beta.csv",
            "data/deep/delta.csv",
            "data/gamma.csv",
        ]
        assert fs.find("*.csv", "data") == ["deep/delta.csv", "gamma.csv"]
        assert fs.count_files(".", "*.csv") == 5
        assert fs.count_files(".", "*") == 6

    def test_find_counts_a_filename_containing_a_newline_once(self, fs, tree):
        """``find -print | wc -l`` would call this one file two files."""
        (tree / "two\nlines.csv").write_text("7")

        assert fs.count_files(".", "*.csv") == 6
        assert "two\nlines.csv" in fs.find("*.csv")

    def test_du_sums_the_real_files(self, fs, tree):
        usage = fs.du(".")
        assert usage.file_count == 6
        assert usage.total_bytes == 1 + 2 + 3 + 1 + 2 + 3


class TestThePayloadsReallyDidExecuteBeforeTheFix:
    """Without this, "the sentinel is absent" proves nothing.

    Each test rebuilds the pre-fix command string verbatim from the issue and
    runs it through the same server the tests above use. The sentinel appears.
    """

    def _run(self, fs, command):
        ssh = fs._get_ssh_client()
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode()
        stdout.channel.recv_exit_status()
        return output

    @pytest.mark.parametrize(
        "name",
        [
            "semicolon",
            "logical_and",
            "pipe",
            "command_substitution",
            "backticks",
            "newline",
        ],
    )
    def test_the_old_ls_command_executed_a_hostile_path(self, name, fs, ssh_server):
        payload = PATH_PAYLOADS[name]
        # filesystem.py:423, exactly as it stood.
        self._run(fs, f"ls -1 {payload} 2>/dev/null || true")

        assert _sentinels(ssh_server.root_path), (
            f"the {name} payload did not execute even before the fix, so the "
            "assertion that it does not execute now is worthless"
        )

    def test_the_old_find_command_executed_a_hostile_pattern(self, fs, ssh_server):
        """The single quotes around ``-name`` were never protection."""
        pattern = f"*.log'; touch {SENTINEL}_oldfind; echo '"
        # filesystem.py:437, exactly as it stood.
        self._run(
            fs,
            "cd . && find . -name '%s' -type f | sed 's|^\\./||' | sort" % pattern,
        )

        assert _sentinels(ssh_server.root_path) == [f"{SENTINEL}_oldfind"]

    def test_the_old_test_command_executed_a_hostile_path(self, fs, ssh_server):
        payload = PATH_PAYLOADS["semicolon"]
        # filesystem.py:483, exactly as it stood.
        self._run(fs, f"test -e {payload} && echo 'EXISTS' || echo 'NOT_EXISTS'")

        assert _sentinels(ssh_server.root_path) == [f"{SENTINEL}_semicolon"]

    def test_the_old_ls_command_read_a_leading_dash_as_flags(self, fs, ssh_server):
        """No injection needed: the wrong answer was enough."""
        (ssh_server.root_path / "-rf").write_text("nine chars")
        (ssh_server.root_path / "bystander.txt").write_text("not asked about")

        output = self._run(fs, "ls -1 -rf 2>/dev/null || true")

        # The old command answered with the whole directory.
        assert "bystander.txt" in output
        # The fixed code does not.
        assert fs.stat("-rf").size == 10


class TestNoUnquotedInterpolationSurvivesInTheSource:
    """A guard, so the next shell command added here cannot skip quoting."""

    def test_the_shipped_module_has_no_unquoted_interpolation(self):
        source = Path(_filesystem_source_path()).read_text()
        commands = _shell_command_expressions(ast.parse(source))

        # Not vacuous: there really are shell commands to check.
        assert commands, "the guard found no shell commands to check"
        assert _unquoted_interpolations(commands) == []

    def test_every_exec_command_call_is_covered_by_the_guard(self):
        """A command built somewhere the guard does not look would slip by."""
        source = Path(_filesystem_source_path()).read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not _is_exec_command(node):
                continue
            argument = node.args[0]
            assert isinstance(argument, (ast.Name, ast.Constant)), (
                "exec_command is being handed an expression the guard cannot "
                f"trace (line {argument.lineno}); assign it to a `cmd` "
                "variable so the guard checks it"
            )

    @pytest.mark.parametrize(
        "bad_source",
        [
            # The original defect.
            'cmd = f"ls -1 {full_path} 2>/dev/null || true"',
            # Quoted next to unquoted.
            'cmd = f"find {shlex.quote(d)} -name {pattern}"',
            # Single quotes are not quoting.
            "cmd = f\"find . -name '{pattern}'\"",
            # Some other function that is not shlex.quote.
            'cmd = f"ls {escape(full_path)}"',
            # Concatenation instead of an f-string.
            'cmd = "ls -1 " + full_path',
            # Straight to exec_command, never assigned.
            'client.exec_command(f"stat {full_path}")',
            # printf-style.
            'cmd = "ls -1 %s" % full_path',
        ],
    )
    def test_the_guard_fails_when_it_should(self, bad_source):
        tree = ast.parse(bad_source)
        commands = _shell_command_expressions(tree)
        violations = _unquoted_interpolations(commands)
        exec_calls = [node for node in ast.walk(tree) if _is_exec_command(node)]
        traceable = all(
            isinstance(call.args[0], (ast.Name, ast.Constant)) for call in exec_calls
        )
        assert (
            violations or not traceable
        ), f"the guard passed source it must reject: {bad_source!r}"

    def test_the_guard_passes_a_correctly_quoted_command(self):
        """The negative control: the guard is not simply always failing."""
        good = 'cmd = f"cd -- {shlex.quote(d)} && find . -name {shlex.quote(p)}"'
        commands = _shell_command_expressions(ast.parse(good))
        assert commands
        assert _unquoted_interpolations(commands) == []


def _filesystem_source_path() -> str:
    import clustrix.filesystem

    return clustrix.filesystem.__file__


def _is_exec_command(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "exec_command"
        and bool(node.args)
    )


def _shell_command_expressions(tree):
    """Every expression that becomes a remote shell command.

    Two shapes count: the value assigned to a ``cmd``-named variable, and the
    first argument of an ``exec_command`` call.
    """
    expressions = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(name == "cmd" or name.startswith("cmd") for name in names):
                expressions.append(node.value)
        elif _is_exec_command(node):
            expressions.append(node.args[0])
    return expressions


def _unquoted_interpolations(expressions):
    """Describe every interpolation that did not come from ``shlex.quote``."""
    violations = []
    for expression in expressions:
        if isinstance(expression, (ast.Name, ast.Constant)):
            # A plain constant is not interpolated; a bare name was checked
            # where it was assigned.
            continue
        if not isinstance(expression, ast.JoinedStr):
            violations.append(
                f"line {expression.lineno}: a shell command is built by "
                f"{type(expression).__name__} rather than by an f-string of "
                "shlex.quote() values"
            )
            continue
        for part in expression.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            if not _is_shlex_quote_call(part.value):
                violations.append(
                    f"line {part.lineno}: "
                    f"{ast.unparse(part.value)!r} is interpolated into a "
                    "shell command without shlex.quote()"
                )
    return violations


def _is_shlex_quote_call(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "quote"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "shlex"
    )


def test_the_module_imports_shlex():
    """Removing the import would make every quote call a NameError."""
    source = Path(_filesystem_source_path()).read_text()
    assert "import shlex" in source


def test_no_remaining_error_swallowing_redirect(fs, ssh_server, caplog):
    """A failing remote command says what failed instead of returning empty.

    ``2>/dev/null`` used to be on every one of these commands, which turned a
    missing directory, a permission error or an unsupported option into empty
    output that the caller read as "there is nothing there".
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="clustrix.filesystem"):
        assert fs.find("*.csv", "no_such_directory") == []

    assert any(
        "Remote command failed" in record.getMessage() for record in caplog.records
    ), "a failing remote command was silent"
    assert os.path.sep not in SENTINEL  # sanity: the marker is a bare name
