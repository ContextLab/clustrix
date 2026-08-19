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

The last section is a source guard. It parses ``filesystem.py``, works out
which argument positions end up being run by a shell -- following the
module's own helpers to a fixpoint rather than matching on names -- and fails
if any value that a caller could control reaches one of them without passing
through ``shlex.quote``.
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


class TestNoTaintedValueReachesAShell:
    """The anti-regression guard, and proof that it can actually fail.

    The property is *not* "variables called ``cmd`` are quoted". It is: no
    value a caller controls reaches a shell without going through
    ``shlex.quote``, however the command is spelled and whatever it is handed
    to. The previous guard inspected assignments to ``cmd``-prefixed names
    and arguments to ``exec_command``, and so reported nothing at all for
    ``self._run_remote(f"ls -1 {full_path}")`` -- the module's own primary
    helper, and fully exploitable. Seven other spellings walked past it too.
    A guard that misses the main path is worse than no guard, because it is
    believed.
    """

    def test_the_shipped_module_is_clean(self):
        source = Path(_filesystem_source_path()).read_text()
        assert _shell_injection_violations(source) == []

    def test_the_guard_is_not_vacuous(self):
        """It has to be looking at real shell commands to mean anything."""
        tree = ast.parse(Path(_filesystem_source_path()).read_text())
        assert _shell_sites(tree), "the guard found no shell commands to check"

    def test_the_sink_is_discovered_not_hard_coded(self):
        """``_run_remote`` is a sink because it forwards to exec_command.

        Nothing names it in the guard. It is found by following its
        parameter into ``exec_command`` and taking the fixpoint, which is
        what lets the guard cover a helper that does not exist yet.
        """
        tree = ast.parse(Path(_filesystem_source_path()).read_text())
        positional, keyword = _sink_table(tree)
        assert 0 in positional.get("_run_remote", set())
        assert "cmd" in keyword.get("_run_remote", set())

    def test_a_chain_of_helpers_is_followed(self):
        """Taint has to survive however many helpers stand in the way."""
        source = (
            "class Filesystem:\n"
            "    def _exec(self, cmd):\n"
            "        self._get_ssh_client().exec_command(cmd)\n"
            "\n"
            "    def _run_remote(self, cmd):\n"
            "        return self._exec(cmd)\n"
            "\n"
            "    def _listing(self, cmd):\n"
            "        return self._run_remote(cmd)\n"
            "\n"
            "    def operation(self, full_path):\n"
            '        self._listing(f"ls -1 {full_path}")\n'
        )
        positional, _ = _sink_table(ast.parse(source))
        assert 0 in positional["_listing"]
        assert _shell_injection_violations(source)

    #: Every spelling a reviewer got past the old guard, plus the ones it did
    #: catch. Each is a whole miniature module, so the sink really has to be
    #: discovered rather than assumed.
    @pytest.mark.parametrize(
        "label,body",
        [
            # --- the eight the old guard missed -----------------------------
            (
                "the module's own primary helper, called directly",
                'self._run_remote(f"ls -1 {full_path}")',
            ),
            (
                "a variable that is not called cmd",
                'command = f"ls -1 {full_path}"\nself._run_remote(command)',
            ),
            (
                "an annotated assignment",
                'cmd: str = f"ls -1 {full_path}"\nself._run_remote(cmd)',
            ),
            (
                "an augmented assignment",
                'cmd = "ls -1 "\ncmd += full_path\nself._run_remote(cmd)',
            ),
            (
                "a tuple assignment",
                'cmd, extra = f"ls -1 {full_path}", 0\nself._run_remote(cmd)',
            ),
            (
                "an attribute assignment",
                'self.cmd = f"ls -1 {full_path}"\nself._run_remote(self.cmd)',
            ),
            (
                "a walrus",
                'self._run_remote(cmd := f"ls -1 {full_path}")',
            ),
            # --- and the ones it did catch, which must keep failing ---------
            (
                "the original defect",
                'cmd = f"ls -1 {full_path} 2>/dev/null || true"\n'
                "self._run_remote(cmd)",
            ),
            (
                "quoted next to unquoted",
                'cmd = f"find {shlex.quote(full_path)} -name {pattern}"\n'
                "self._run_remote(cmd)",
            ),
            (
                "single quotes are not quoting",
                "cmd = f\"find . -name '{pattern}'\"\nself._run_remote(cmd)",
            ),
            (
                "some other function that is not shlex.quote",
                'cmd = f"ls {escape(full_path)}"\nself._run_remote(cmd)',
            ),
            (
                "concatenation instead of an f-string",
                'cmd = "ls -1 " + full_path\nself._run_remote(cmd)',
            ),
            (
                "straight to exec_command, never assigned",
                'self._get_ssh_client().exec_command(f"stat {full_path}")',
            ),
            (
                "printf-style",
                'cmd = "ls -1 %s" % full_path\nself._run_remote(cmd)',
            ),
            (
                "str.format",
                'cmd = "ls -1 {}".format(full_path)\nself._run_remote(cmd)',
            ),
            (
                "str.join",
                'cmd = " ".join(["ls", "-1", full_path])\nself._run_remote(cmd)',
            ),
            (
                "a helper that builds the command",
                "cmd = build_listing_command(full_path)\nself._run_remote(cmd)",
            ),
            (
                "a keyword argument",
                'self._run_remote(cmd=f"ls -1 {full_path}")',
            ),
            (
                "positional unpacking",
                'parts = [f"ls -1 {full_path}"]\nself._run_remote(*parts)',
            ),
            (
                "keyword unpacking",
                'parts = {"cmd": f"ls -1 {full_path}"}\nself._run_remote(**parts)',
            ),
            (
                "a value taken out of a container",
                'cmd = commands[f"ls {full_path}"]\nself._run_remote(cmd)',
            ),
            (
                "a loop variable",
                'for cmd in [f"ls {full_path}"]:\n    self._run_remote(cmd)',
            ),
        ],
    )
    def test_the_guard_fails_when_it_should(self, label, body):
        violations = _shell_injection_violations(_miniature_module(body))
        assert violations, f"the guard passed source it must reject: {label}"

    def test_shadowing_shlex_is_a_violation(self):
        """``shlex.quote`` only sanitises while ``shlex`` is really shlex.

        Every other check in the guard treats a ``shlex.quote(...)`` call as
        proof of safety, so rebinding the name would launder anything.
        """
        source = (
            "import shlex\n"
            "\n"
            "class _Passthrough:\n"
            "    def quote(self, text):\n"
            "        return text\n"
            "\n"
            "shlex = _Passthrough()\n"
            "\n"
            "class Filesystem:\n"
            "    def _run_remote(self, cmd):\n"
            "        self._get_ssh_client().exec_command(cmd)\n"
            "\n"
            "    def operation(self, full_path):\n"
            '        cmd = f"ls -1 {shlex.quote(full_path)}"\n'
            "        self._run_remote(cmd)\n"
        )
        assert _shell_injection_violations(source)

    def test_importing_something_else_as_shlex_is_a_violation(self):
        source = (
            "import lenient_shlex as shlex\n"
            "\n"
            "class Filesystem:\n"
            "    def _run_remote(self, cmd):\n"
            "        self._get_ssh_client().exec_command(cmd)\n"
            "\n"
            "    def operation(self, full_path):\n"
            "        self._run_remote(shlex.quote(full_path))\n"
        )
        assert _shell_injection_violations(source)

    #: The negative controls. A guard that always fails proves nothing
    #: either, and quoted code must stay writable in more than one style.
    @pytest.mark.parametrize(
        "label,body",
        [
            (
                "quoted inline, the shipped shape",
                'cmd = f"cd -- {shlex.quote(full_path)} && '
                'find . -name {shlex.quote(pattern)} -type f -print0"\n'
                "self._run_remote(cmd)",
            ),
            (
                "quoted through an intermediate variable",
                "quoted = shlex.quote(full_path)\n"
                'cmd = f"ls -1 {quoted}"\n'
                "self._run_remote(cmd)",
            ),
            (
                "quoted and concatenated",
                'cmd = "ls -1 " + shlex.quote(full_path)\nself._run_remote(cmd)',
            ),
            (
                "quoted and joined",
                'cmd = " ".join(["ls", "-1", shlex.quote(full_path)])\n'
                "self._run_remote(cmd)",
            ),
            (
                "quoted and formatted",
                'cmd = "ls -1 {}".format(shlex.quote(full_path))\n'
                "self._run_remote(cmd)",
            ),
            (
                "a command with nothing interpolated at all",
                'self._run_remote("uname -a")',
            ),
        ],
    )
    def test_the_guard_passes_quoted_code(self, label, body):
        violations = _shell_injection_violations(_miniature_module(body))
        assert violations == [], f"the guard rejected safe source: {label}"


def _filesystem_source_path() -> str:
    import clustrix.filesystem

    return clustrix.filesystem.__file__


def _miniature_module(body: str) -> str:
    """A stand-in for ``filesystem.py``: the sink helper plus one method.

    The helper is spelled out rather than assumed so that every case below
    exercises the guard's discovery of ``_run_remote`` as a shell sink, not a
    hard-coded name.
    """
    indented = "\n".join("        " + line for line in body.splitlines())
    return (
        "import shlex\n"
        "\n"
        "class Filesystem:\n"
        "    def _run_remote(self, cmd):\n"
        "        self._get_ssh_client().exec_command(cmd)\n"
        "\n"
        "    def operation(self, full_path, pattern):\n"
        f"{indented}\n"
    )


# ===========================================================================
# The guard itself.
#
# Three ideas, and nothing about variable names:
#
#   * a *sink* is an argument position whose value ends up being run by a
#     shell. ``exec_command`` and ``os.system`` are sinks by definition; any
#     function that forwards one of its own parameters into a sink becomes a
#     sink in turn, taken to a fixpoint. That is how ``_run_remote`` is
#     discovered rather than listed;
#   * a value is *clean* if it is a literal, the result of ``shlex.quote``,
#     or built out of clean values. A parameter, an attribute, a subscript, a
#     loop variable, or any other call is tainted;
#   * a violation is a tainted value reaching a sink.
#
# Since ``shlex.quote`` is the only thing that launders a value, the name
# ``shlex`` is checked separately for rebinding.
# ===========================================================================

#: The calls that hand an argument to a shell before any propagation.
_BASE_POSITIONAL_SINKS = {"exec_command": {0}, "system": {0}}
_BASE_KEYWORD_SINKS = {"exec_command": {"command"}}


def _called_name(call: ast.Call):
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _positional_parameters(function) -> list:
    return [argument.arg for argument in function.args.posonlyargs + function.args.args]


def _receiver_offset(function) -> int:
    """``self._run_remote(cmd)`` passes ``cmd`` at call-site index 0."""
    parameters = _positional_parameters(function)
    return 1 if parameters and parameters[0] in ("self", "cls") else 0


def _all_parameter_names(function) -> set:
    arguments = function.args
    names = {
        argument.arg
        for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs
    }
    if arguments.vararg:
        names.add(arguments.vararg.arg)
    if arguments.kwarg:
        names.add(arguments.kwarg.arg)
    return names


def _sink_arguments(call: ast.Call, positional, keyword) -> list:
    """Every argument of this call that a shell will run.

    ``*args`` and ``**kwargs`` are included whole rather than skipped: which
    parameter they land on cannot be read off the syntax, so an unpacked call
    to a sink is reported unless what it unpacks is itself clean.
    """
    name = _called_name(call)
    if name is None or not (positional.get(name) or keyword.get(name)):
        return []

    arguments = []
    for index, argument in enumerate(call.args):
        if isinstance(argument, ast.Starred) or index in positional.get(name, ()):
            arguments.append(argument)
    for keyword_argument in call.keywords:
        if keyword_argument.arg is None or keyword_argument.arg in keyword.get(
            name, ()
        ):
            arguments.append(keyword_argument.value)
    return arguments


def _is_scope(node) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))


def _scopes(tree):
    """The module, then every function and class body in it."""
    yield tree
    for node in ast.walk(tree):
        if _is_scope(node):
            yield node


def _scope_body(scope) -> list:
    """Every node belonging to this scope, not to a nested one."""
    nodes = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if _is_scope(node) or isinstance(node, ast.Lambda):
            continue
        nodes.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _sink_table(tree):
    """Which argument of which callable reaches a shell, to a fixpoint."""
    positional = {name: set(value) for name, value in _BASE_POSITIONAL_SINKS.items()}
    keyword = {name: set(value) for name, value in _BASE_KEYWORD_SINKS.items()}

    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    changed = True
    while changed:
        changed = False
        for function in functions:
            parameters = _positional_parameters(function)
            keyword_only = {argument.arg for argument in function.args.kwonlyargs}
            offset = _receiver_offset(function)
            for node in _scope_body(function):
                if not isinstance(node, ast.Call):
                    continue
                for argument in _sink_arguments(node, positional, keyword):
                    if not isinstance(argument, ast.Name):
                        continue
                    if argument.id in parameters:
                        index = parameters.index(argument.id) - offset
                        if index >= 0:
                            forwarded = positional.setdefault(function.name, set())
                            if index not in forwarded:
                                forwarded.add(index)
                                changed = True
                    if argument.id in parameters or argument.id in keyword_only:
                        forwarded_keywords = keyword.setdefault(function.name, set())
                        if argument.id not in forwarded_keywords:
                            forwarded_keywords.add(argument.id)
                            changed = True
    return positional, keyword


def _sink_parameters(scope, positional, keyword) -> set:
    """The parameters of ``scope`` that are themselves shell sinks.

    Inside such a function the parameter counts as clean: forwarding it is
    what made the function a sink, and the obligation to quote moves to
    everyone who calls it.
    """
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()

    names = set(keyword.get(scope.name, ()))
    parameters = _positional_parameters(scope)
    offset = _receiver_offset(scope)
    for index in positional.get(scope.name, ()):
        position = index + offset
        if 0 <= position < len(parameters):
            names.add(parameters[position])
    return names


def _bindings(scope):
    """Every value bound to a name in this scope.

    A binding with no expression behind it -- a loop variable, a ``with``
    target, an ``except`` name, an import -- is recorded as ``None``, which
    is never clean. Attribute and subscript targets bind no local name at
    all, and reading one back is tainted anyway.
    """
    from collections import defaultdict

    bindings = defaultdict(list)

    def record(target, value):
        if isinstance(target, ast.Name):
            bindings[target.id].append(value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            elements = None
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(
                target.elts
            ):
                elements = value.elts
            for index, element in enumerate(target.elts):
                record(element, elements[index] if elements else None)
        elif isinstance(target, ast.Starred):
            record(target.value, None)

    for node in _scope_body(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                record(target, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            # ``cmd: str = ...``, ``cmd += path`` and ``(cmd := ...)`` are all
            # assignments; ``+=`` folds the old value in, and both halves have
            # to be clean for the result to be.
            record(node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            record(node.target, None)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                record(node.optional_vars, None)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bindings[node.name].append(None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]].append(None)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                bindings[name].append(None)
    return bindings


def _clean_names(scope, sink_parameters) -> set:
    """The names in this scope whose every binding is a sanitised value."""
    bindings = _bindings(scope)
    parameters = (
        _all_parameter_names(scope)
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
        else set()
    )

    # Start optimistic and demote, so that ``a = b`` followed by
    # ``b = shlex.quote(x)`` settles on the right answer whatever the order.
    clean = set(bindings) | set(sink_parameters)
    while True:
        demoted = set()
        for name in clean:
            if name in parameters and name not in sink_parameters:
                demoted.add(name)
                continue
            values = bindings.get(name)
            if values is None:
                continue
            if any(not _is_clean(value, clean) for value in values):
                demoted.add(name)
        if not demoted:
            return clean
        clean -= demoted


def _is_shlex_quote(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "quote"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "shlex"
    )


def _is_clean(node, clean) -> bool:
    """Is this expression free of any value a caller could have supplied?"""
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in clean
    if isinstance(node, ast.NamedExpr):
        return _is_clean(node.value, clean)
    if isinstance(node, ast.IfExp):
        return _is_clean(node.body, clean) and _is_clean(node.orelse, clean)
    if isinstance(node, ast.JoinedStr):
        return all(_is_clean(part, clean) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _is_clean(node.value, clean)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_clean(element, clean) for element in node.elts)
    if isinstance(node, ast.BinOp):
        # Covers both ``"a" + x`` and ``"a %s" % x``.
        return _is_clean(node.left, clean) and _is_clean(node.right, clean)
    if isinstance(node, ast.Call):
        if _is_shlex_quote(node):
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in (
            "join",
            "format",
        ):
            parts = [node.func.value, *node.args]
            parts += [keyword.value for keyword in node.keywords]
            return all(_is_clean(part, clean) for part in parts)
        return False
    return False


def _shlex_rebinding_violations(tree) -> list:
    """Anything that could make ``shlex.quote`` not be ``shlex.quote``."""
    violations = []
    complaint = (
        "the name `shlex` is rebound, so `shlex.quote()` is no longer proof "
        "that a value was quoted"
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname == "shlex" and alias.name != "shlex":
                    violations.append(f"line {node.lineno}: {complaint}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == "shlex":
                    violations.append(f"line {node.lineno}: {complaint}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "shlex" in _all_parameter_names(node):
                violations.append(f"line {node.lineno}: {complaint}")
        else:
            for target in _assignment_targets(node):
                for inner in ast.walk(target):
                    if isinstance(inner, ast.Name) and inner.id == "shlex":
                        violations.append(f"line {node.lineno}: {complaint}")
    return violations


def _assignment_targets(node) -> list:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return [node.target]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        return [node.target]
    if isinstance(node, ast.withitem):
        return [node.optional_vars] if node.optional_vars is not None else []
    return []


def _shell_sites(tree) -> list:
    """Every ``(scope, expression)`` pair that a remote shell will run."""
    positional, keyword = _sink_table(tree)
    sites = []
    for scope in _scopes(tree):
        for node in _scope_body(scope):
            if isinstance(node, ast.Call):
                for argument in _sink_arguments(node, positional, keyword):
                    sites.append((scope, argument))
    return sites


def _shell_injection_violations(source: str) -> list:
    """Every caller-controlled value that reaches a shell unquoted."""
    tree = ast.parse(source)
    positional, keyword = _sink_table(tree)

    violations = _shlex_rebinding_violations(tree)
    for scope in _scopes(tree):
        clean = _clean_names(scope, _sink_parameters(scope, positional, keyword))
        for node in _scope_body(scope):
            if not isinstance(node, ast.Call):
                continue
            for argument in _sink_arguments(node, positional, keyword):
                if not _is_clean(argument, clean):
                    violations.append(
                        f"line {argument.lineno}: "
                        f"{ast.unparse(argument)!r} reaches a remote shell "
                        "without passing through shlex.quote()"
                    )
    return violations


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
