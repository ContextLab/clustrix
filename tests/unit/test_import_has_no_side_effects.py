"""``import clustrix`` must define names and do nothing else (issue #123).

Nothing here is mocked. The import-time behaviour is measured in real
subprocesses with a real, scrubbed ``$HOME``, using a real ``sys.addaudithook``
to record which files are actually opened; the first-use behaviour is measured
in-process against real files on disk.

Three things were true of the old code, and all three are asserted against
here:

* ``import clustrix`` read ``~/.clustrix`` and the current working directory.
  Importing a library must not go looking through the user's home directory
  before they have asked it for anything, and picking up a ``./clustrix.yml``
  belonging to whatever directory the process happened to start in is a
  behaviour nobody opted into.
* ``import clustrix`` *raised* ``PermissionError`` when ``~/.clustrix`` was not
  readable, because ``Path.exists()`` answers ``False`` for ENOENT but
  propagates EACCES, and that call sat outside the ``try``. An import is the
  worst possible place for that: there is no caller in a position to handle it.
* a configuration file that was found and then failed to load -- truncated
  YAML, a misspelled setting -- was skipped in silence, leaving the process on
  built-in defaults while the user believed their file was in force. A
  ``cluster_host`` that never took effect means the job runs somewhere other
  than where it was told to.
"""

import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import threading
import time

import yaml

import pytest

import clustrix.config as config_module
from clustrix.config import (
    CONFIG_DIR_ENV_VAR,
    ClusterConfig,
    ConfigFileError,
    configure,
    get_config,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Subprocess probes: import-time behaviour can only be measured once per
# interpreter, so it is measured in a fresh one.
# ---------------------------------------------------------------------------

_PROBE = textwrap.dedent(r"""
    import json, os, sys

    HOME = os.environ["HOME"]
    CWD = os.getcwd()
    opened = []

    def _hook(event, args):
        if event != "open":
            return
        try:
            path = str(args[0])
        except Exception:
            return
        if path.startswith(HOME) or path.startswith(CWD):
            opened.append(path)

    sys.addaudithook(_hook)

    import clustrix                      # noqa: F401  -- the thing under test

    during_import = sorted(set(opened))
    opened.clear()

    error = None
    values = None
    try:
        cfg = clustrix.get_config()
        values = {
            "cluster_type": cfg.cluster_type,
            "cluster_host": cfg.cluster_host,
            "default_cores": cfg.default_cores,
        }
    except BaseException as exc:
        error = "{}: {}".format(type(exc).__name__, exc)

    print("@@" + json.dumps({
        "during_import": during_import,
        "during_first_use": sorted(set(opened)),
        "error": error,
        "values": values,
    }))
    """)


def _probe(home, cwd, env_extra=None):
    """Run the probe in a subprocess with ``home`` as $HOME and ``cwd`` as cwd."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop(CONFIG_DIR_ENV_VAR, None)
    env["PYTHONPATH"] = REPO_ROOT
    env.update(env_extra or {})
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    marker = [line for line in completed.stdout.splitlines() if line.startswith("@@")]
    assert marker, (
        "probe produced no result\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return json.loads(marker[0][2:])


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def workdir(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return path


def test_import_opens_no_file_in_the_users_home_or_cwd(home, workdir):
    """The import statement itself must touch neither location.

    Both files below are real and loadable, so this is not passing by
    accident: the very next assertion shows the home one *is* read, just
    later, and case (E) below shows the cwd one is too.
    """
    clustrix_dir = home / ".clustrix"
    clustrix_dir.mkdir()
    (clustrix_dir / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromhome.example\ndefault_cores: 7\n"
    )
    (workdir / "clustrix.yml").write_text("cluster_type: ssh\ndefault_cores: 3\n")

    result = _probe(home, workdir)

    assert result["during_import"] == [], (
        "import clustrix read files under $HOME or the working directory: "
        f"{result['during_import']}"
    )
    # ... and the deferral is a deferral, not a deletion: the same file is
    # read on first use, and its values are in force.
    assert result["error"] is None
    assert result["values"]["cluster_host"] == "fromhome.example"
    assert result["values"]["default_cores"] == 7
    assert any(
        path.endswith(".clustrix/config.yml") for path in result["during_first_use"]
    ), result["during_first_use"]


def test_import_survives_a_config_directory_it_cannot_read(home, workdir):
    """An unreadable ~/.clustrix used to make ``import clustrix`` raise.

    ``Path.exists()`` propagates EACCES rather than answering False, and the
    call sat outside the try. Neither the import nor the first use may fail
    for it now: we cannot tell whether a config file is there, which is not
    the same as knowing there is one and refusing to read it.
    """
    clustrix_dir = home / ".clustrix"
    clustrix_dir.mkdir()
    (clustrix_dir / "config.yml").write_text("cluster_type: ssh\n")
    os.chmod(clustrix_dir, 0o000)
    try:
        result = _probe(home, workdir)
    finally:
        os.chmod(clustrix_dir, 0o755)

    assert result["during_import"] == []
    assert result["error"] is None, result["error"]
    # Fell through to the defaults, having said so in the log.
    assert result["values"]["cluster_type"] == "slurm"


def test_import_survives_a_malformed_config_file(home, workdir):
    """Malformed YAML must not break the import -- but must not vanish either."""
    clustrix_dir = home / ".clustrix"
    clustrix_dir.mkdir()
    (clustrix_dir / "config.yml").write_text("cluster_type: [unclosed\n  : : :\n")

    result = _probe(home, workdir)

    assert result["during_import"] == []
    assert result["error"] is not None, (
        "a malformed configuration file was silently ignored; the process "
        "would have run on built-in defaults with the user believing "
        "otherwise"
    )
    assert "ConfigFileError" in result["error"]
    assert "config.yml" in result["error"]


def test_import_survives_an_absent_config_directory(home, workdir):
    """No ~/.clustrix at all is the ordinary case and must stay silent."""
    result = _probe(home, workdir)

    assert result["during_import"] == []
    assert result["error"] is None
    assert result["values"]["cluster_type"] == "slurm"
    assert not (home / ".clustrix").exists(), "import created a config directory"


# ---------------------------------------------------------------------------
# First-use behaviour, in-process.
# ---------------------------------------------------------------------------


@pytest.fixture
def unloaded_config(tmp_path, monkeypatch):
    """Point the config directory at a throwaway and re-arm the one-time search.

    The autouse ``isolate_config_dir`` fixture already keeps the suite out of
    the developer's real ~/.clustrix; this narrows it further to a directory
    this test owns, and rewinds the "already searched" flag so the search runs
    again. ``reset_config`` restores the singleton afterwards.
    """
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    monkeypatch.setenv(CONFIG_DIR_ENV_VAR, str(config_dir))
    # cwd is part of the search path, so a stray ./clustrix.yml would make
    # these tests depend on where pytest was started from.
    monkeypatch.chdir(tmp_path / "conf")
    previously_loaded = config_module._default_config_loaded
    config_module._default_config_loaded = False
    try:
        yield config_dir
    finally:
        config_module._default_config_loaded = previously_loaded


def test_a_found_but_unusable_file_raises_instead_of_reverting_to_defaults(
    unloaded_config,
):
    """The heart of it: a discarded instruction must not be reported as success."""
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: real.example.edu\nbogus_setting: 1\n"
    )

    with pytest.raises(ConfigFileError) as raised:
        get_config()

    message = str(raised.value)
    assert "config.yml" in message
    assert "bogus_setting" in message
    assert "NOT in effect" in message


def test_an_unusable_file_keeps_failing_rather_than_failing_once(unloaded_config):
    """The flag must not stick on failure.

    Marking the search "done" after it raised would make the first call raise
    and every later one succeed on built-in defaults -- silence that arrives
    one call late, which is worse than either consistent outcome.
    """
    (unloaded_config / "config.yml").write_text("cluster_type: [unclosed\n : : :\n")

    for attempt in range(3):
        with pytest.raises(ConfigFileError):
            get_config()
        assert (
            not config_module._default_config_loaded
        ), f"the search was marked complete after failing (attempt {attempt})"


def test_an_unreadable_candidate_is_skipped_and_reported(unloaded_config, caplog):
    """ "I could not look there" is a warning, not an answer of "nothing there"."""
    os.chmod(unloaded_config, 0o000)
    try:
        with caplog.at_level("WARNING", logger="clustrix.config"):
            config = get_config()
    finally:
        os.chmod(unloaded_config, 0o755)

    assert config.cluster_type == "slurm"
    messages = [record.getMessage() for record in caplog.records]
    assert any("NOT in effect" in message for message in messages), messages
    # chdir'ing into the directory before revoking access also makes getcwd()
    # fail, which is the other half of the same "I could not look" case.
    assert any("current working directory" in message for message in messages), messages


def test_configure_applies_on_top_of_the_file_not_underneath_it(unloaded_config):
    """Precedence is defaults -> file -> runtime, whichever runs the search.

    ``configure()`` triggers the search itself for exactly this reason. If it
    did not, the first later ``get_config()`` would run the search, rebind the
    singleton from the file, and throw away everything ``configure()`` had set.
    """
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromfile.example\ndefault_cores: 7\n"
    )

    configure(default_cores=11)
    config = get_config()

    assert config.default_cores == 11, "configure() was overwritten by the file"
    assert config.cluster_host == "fromfile.example", "the file was never read"


def test_nothing_binds_the_singleton_by_name():
    """The one thing that would make deferring the search unsafe.

    Deferral is only complete because every read of the singleton goes through
    ``get_config()``. A ``from clustrix.config import _config`` binds the
    object as it stood *before* the search ran, and keeps it -- ``load_config``
    rebinds the module attribute, so a by-name importer is left holding a
    configuration that no longer exists and cannot see the user's file at all.

    The claim was written down in ``config.py`` as if it were checked; it was
    not, and it was already false outside the package -- one test fixture
    (``tests/unit/test_widget_profiles.py``) imported it by name. Asserting it
    is what makes it a claim rather than a hope.
    """
    root = pathlib.Path(REPO_ROOT)
    binding = re.compile(
        r"^\s*from\s+[\w.]*config\s+import\s+(?:[^\n]*[\s,(])?_config\b",
        re.M,
    )
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if any(
            part in {".git", "build", "dist", "venv", ".venv", "__pycache__"}
            for part in path.parts
        ):
            continue
        if path.name == os.path.basename(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in binding.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(root)}:{line}")

    assert not offenders, (
        "these bind clustrix.config._config by name, which pins the "
        "pre-search singleton; use get_config() instead:\n  " + "\n  ".join(offenders)
    )


def test_save_config_as_the_first_touch_writes_the_users_settings(
    unloaded_config, tmp_path
):
    """``save_config`` is a first-use entry point too, and it was untested.

    Deferring the search means every door into the configuration has to open
    it, and there are three: ``get_config``, ``configure`` and this one. Only
    the first two were covered, so deleting ``_ensure_default_config_loaded()``
    from ``save_config`` passed the entire suite while changing real
    behaviour: a process whose *first* configuration call is ``save_config``
    would serialise the built-in defaults -- ``cluster_host: null`` -- over the
    top of settings the user already had in ``~/.clustrix``. Round-tripping a
    config through save/load would silently erase it.

    ``_config`` is rebound to a fresh ``ClusterConfig`` here because that, plus
    the fixture's rewound flag, *is* the state of an interpreter that has not
    yet looked at the configuration.
    """
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromhome.example\ndefault_cores: 7\n"
    )
    config_module._config = ClusterConfig()

    destination = tmp_path / "saved.yml"
    config_module.save_config(str(destination))

    written = yaml.safe_load(destination.read_text())
    assert written["cluster_host"] == "fromhome.example", (
        "save_config serialised built-in defaults over the user's settings; "
        f"it wrote cluster_host={written['cluster_host']!r}"
    )
    assert written["cluster_type"] == "ssh"
    assert written["default_cores"] == 7


def test_the_search_runs_once_under_concurrent_first_use(unloaded_config):
    """Sixteen threads racing on the first call must all see the same answer.

    Deferring work to first use is where a singleton grows a race. The
    observable requirement is that no caller ever receives a configuration
    object that predates the file being applied, and that they all receive the
    *same* object -- a second one would mean two halves of the program
    configured differently.
    """
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: raced.example\ndefault_cores: 9\n"
    )

    start = threading.Barrier(16)
    seen = []
    errors = []

    def worker():
        try:
            start.wait(timeout=10)
            config = get_config()
            seen.append((id(config), config.cluster_host, config.default_cores))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(seen) == 16
    assert (
        len(set(seen)) == 1
    ), f"threads disagreed about the configuration: {set(seen)}"
    assert seen[0][1] == "raced.example"
    assert seen[0][2] == 9


def test_an_explicit_load_supersedes_the_search(unloaded_config, tmp_path):
    """``load_config`` must not be undone by a search that had not run yet.

    The mechanism is one line at the end of ``_load_config_locked``: an
    explicit load publishes ``_default_config_loaded``, because it has
    replaced the configuration wholesale and the search of the standard
    locations has nothing left to contribute. Without it the very next
    ``get_config()`` runs the search and rebinds ``_config`` to whatever is in
    the configuration directory -- the file the caller named is accepted,
    reported as loaded, and thrown away, which is the whole subject of this
    issue.

    Both halves are asserted: the flag, so the failure names the line, and the
    behaviour, so the flag cannot be set without the effect.
    """
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromsearch.example\n"
    )
    explicit = tmp_path / "explicit.yml"
    explicit.write_text("cluster_type: ssh\ncluster_host: explicit.example\n")

    config_module.load_config(str(explicit))

    assert config_module._default_config_loaded is True, (
        "an explicit load did not publish that the configuration is settled, "
        "so the next get_config() will search the standard locations and "
        "overwrite it"
    )
    assert get_config().cluster_host == "explicit.example"


#: Starts a child while the lazy search is *in flight* on another thread and
#: asks the child for its configuration. Run in a fresh interpreter because it
#: needs a clean, unsearched module state and a $HOME of its own.
#:
#: Two things about how the window is entered, because the first version of
#: this probe got both wrong and could not see two thirds of the handler it
#: was testing.
#:
#: It waits on ``_default_config_loading`` rather than sleeping half a second
#: and hoping. That flag is set inside the lock immediately before the parse
#: and cleared immediately after it, so it is true exactly while the window is
#: open; sleeping instead meant the search sometimes finished first, and the
#: probe reported a green result for a window it never entered.
#:
#: And it uses ``os.fork()`` directly rather than ``multiprocessing.Process``.
#: That is not a shortcut around multiprocessing -- it is what
#: ``multiprocessing`` calls, one layer down -- and it removes the process
#: setup, argument pickling and ``Queue`` construction that used to sit
#: between "the window is open" and the actual fork. With those in the way the
#: search could finish in the gap, which is why deleting the fork handler
#: outright still passed four runs in five.
_FORK_PROBE = textwrap.dedent(r"""
    import multiprocessing, os, select, sys, threading, time

    from clustrix import config as config_module

    ROUNDS = 5
    HANG = 15          # seconds a child gets to answer before it is a deadlock

    def child(queue):
        queue.put(config_module.get_config().cluster_host)

    def rewind():
        # The state a fresh process starts in: nothing searched, nothing
        # loaded. Reproduced here so the window can be entered more than once
        # per interpreter -- a race that reproduces one run in five is still a
        # race, and one round proves nothing. Nothing else is touched; the
        # search that follows is the real one over the real file.
        config_module._config = config_module.ClusterConfig()
        config_module._default_config_loaded = False
        config_module._default_config_loading = False

    def open_the_window():
        searcher = threading.Thread(target=config_module.get_config, daemon=True)
        searcher.start()
        deadline = time.time() + 60
        while not config_module._default_config_loading:
            if config_module._default_config_loaded or time.time() > deadline:
                return searcher, False
            time.sleep(0.0002)
        return searcher, True

    def fork_round():
        searcher, open_now = open_the_window()
        if not open_now:
            searcher.join(120)
            return "WINDOW-MISSED"
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(read_fd)
                answer = str(config_module.get_config().cluster_host)
                os.write(write_fd, answer.encode())
            finally:
                os._exit(0)
        os.close(write_fd)
        answered = bool(select.select([read_fd], [], [], HANG)[0])
        if not answered:
            os.kill(pid, 9)
            outcome = "DEADLOCK"
        else:
            outcome = "HOST " + os.read(read_fd, 4096).decode()
        os.close(read_fd)
        os.waitpid(pid, 0)
        searcher.join(120)
        return outcome

    def spawn_round():
        ctx = multiprocessing.get_context("spawn")
        # Built before the search starts: constructing a Queue is itself slow
        # enough to close the window being aimed at.
        queue = ctx.Queue()
        searcher, open_now = open_the_window()
        if not open_now:
            searcher.join(120)
            return "WINDOW-MISSED"
        process = ctx.Process(target=child, args=(queue,))
        process.start()
        process.join(HANG * 2)
        if process.is_alive():
            process.kill()
            process.join()
            outcome = "DEADLOCK"
        else:
            try:
                outcome = "HOST " + str(queue.get_nowait())
            except Exception as exc:
                outcome = "DIED exitcode=%s (%s)" % (process.exitcode, exc)
        searcher.join(120)
        return outcome

    if __name__ == "__main__":
        method = sys.argv[1]
        rounds = []
        for _ in range(ROUNDS):
            rewind()
            rounds.append(fork_round() if method == "fork" else spawn_round())
            if rounds[-1] != "HOST fromhome.example":
                break          # a single bad round is the answer; stop early
        print("@@" + " | ".join(rounds))
    """)


def _fork_probe(home, workdir, method):
    """Run ``_FORK_PROBE`` for one start method; return its ``@@`` result line."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env.pop(CONFIG_DIR_ENV_VAR, None)
    env["PYTHONPATH"] = REPO_ROOT
    probe = workdir / "fork_probe.py"
    probe.write_text(_FORK_PROBE)
    completed = subprocess.run(
        [sys.executable, str(probe), method],
        env=env,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=600,
    )
    markers = [line for line in completed.stdout.splitlines() if line.startswith("@@")]
    assert markers, (
        "probe produced no result\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return markers[0][2:]


@pytest.mark.parametrize("method", ["fork", "spawn"])
def test_a_child_process_started_during_the_search_can_read_the_config(
    home, workdir, method
):
    """``fork`` during the lazy search used to hang the child forever.

    ``fork`` copies only the calling thread. The search holds
    ``_DEFAULT_CONFIG_LOCK`` for its whole duration and now runs on whichever
    thread touches the configuration first, so a fork taken during it hands the
    child a lock recorded as held by a thread that does not exist there, plus
    ``_default_config_loading = True`` set by that same absent thread. The
    child's first ``get_config()`` then blocks with nothing that can ever wake
    it -- not a wrong answer but no answer, which is the same defect one step
    further on.

    This is reachable from ordinary use: ``LocalExecutor`` runs work in a
    ``ProcessPoolExecutor`` and ``fork`` is a real start method. Both methods
    are checked, because ``spawn`` re-imports and must keep working too.

    Both halves of the handler are covered, and neither was before:

    * deleting the whole ``os.register_at_fork`` registration leaves the child
      holding a lock no thread will release, and every round reports
      ``DEADLOCK``;
    * deleting only ``_default_config_loading = False`` leaves the child
      taking the re-entrancy early return and answering with the pre-search
      default, so every round reports ``HOST None``.

    Measured against this probe, both are 5/5 fatal. Against the version that
    slept and hoped they were 0/5 and 1/5 respectively.

    Nothing is patched or mocked. The window is held open with a real ~1 MB
    configuration file whose YAML parse genuinely takes a moment, a real
    thread, a real ``os.fork()`` and a real pipe.
    """
    clustrix_dir = home / ".clustrix"
    clustrix_dir.mkdir()
    padding = "\n".join(f"# pad {index} {'x' * 80}" for index in range(12000))
    (clustrix_dir / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromhome.example\n" + padding + "\n"
    )

    rounds = _fork_probe(home, workdir, method).split(" | ")

    assert "WINDOW-MISSED" not in rounds, (
        "the search finished before the child was started; the test never "
        f"exercised the window it exists for: {rounds}"
    )
    assert "DEADLOCK" not in rounds, (
        f"a child started with {method!r} during the lazy search never "
        f"returned from its first get_config(): {rounds}"
    )
    assert rounds == ["HOST fromhome.example"] * 5, rounds


#: The keywords the torn-write test applies. More than one, and none of them
#: equal to what the competing file sets, so a half-applied result is
#: recognisable as one rather than having to be inferred.
_CONFIGURE_KEYWORDS = {
    "cluster_host": "configured.example",
    "username": "configured-user",
    "cluster_port": 2222,
    "remote_work_dir": "/configured/work",
    "default_cores": 7,
    "default_memory": "9GB",
}


def _setattr_line():
    """The line of the ``setattr`` inside ``configure``'s apply loop.

    Found by parsing the module rather than written down, so an edit above it
    cannot silently move the preemption to a line that proves nothing.
    """
    tree = ast.parse(pathlib.Path(config_module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "configure":
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.For)
                    and isinstance(inner.target, ast.Tuple)
                    and isinstance(inner.iter, ast.Call)
                ):
                    return inner.body[-1].lineno
    raise AssertionError("could not find configure()'s apply loop")


def test_a_configure_is_not_torn_in_half_by_a_concurrent_load(
    unloaded_config, tmp_path
):
    """``configure`` must be all-or-nothing against a concurrent ``load_config``.

    ``configure`` took the lock only for the search at the top; its apply loop
    ran unlocked. The loop reads the module global ``_config`` on every
    iteration and ``load_config`` *rebinds* it, so a load landing mid-loop
    left the keywords already applied written to the object that was just
    discarded and the rest written to the new one. The call returned success
    with ``cluster_host`` silently reverted to the file's value and the other
    three keywords applied -- neither writer won, and nothing said so.

    Unforced this is rare: 200 trials of the two calls racing on a barrier,
    with ``sys.setswitchinterval`` at a nanosecond, produced 0 torn results,
    which is why the suite could not see it. The interpreter may switch
    threads at any bytecode in that loop, so the interleaving is scheduled
    here instead of waited for: a trace function on the ``configure`` thread
    releases the loader after the *first* attribute has been written and waits
    for it. Nothing is patched or replaced -- both calls are the real ones,
    running on real threads, against a real file.

    With the loop unlocked this is deterministic; the timeouts below are what
    keep it terminating once the loop *is* locked, where the loader simply
    queues and the trace function has nothing to wait for.
    """
    explicit = tmp_path / "explicit.yml"
    explicit.write_text(
        "cluster_type: ssh\ncluster_host: fromfile.example\nusername: file-user\n"
    )
    config_module._config = ClusterConfig()
    config_module._default_config_loaded = True

    setattr_line = _setattr_line()
    config_file = config_module.__file__
    released = threading.Event()
    loaded = threading.Event()
    arrivals = []

    def watch_lines(frame, event, arg):
        if event == "line" and frame.f_lineno == setattr_line:
            arrivals.append(frame.f_lineno)
            if len(arrivals) == 2:  # the first attribute is now written
                released.set()
                loaded.wait(timeout=2)
        return watch_lines

    def watch_calls(frame, event, arg):
        if (
            frame.f_code.co_name == "configure"
            and frame.f_code.co_filename == config_file
        ):
            return watch_lines
        return None

    def apply_keywords():
        sys.settrace(watch_calls)
        try:
            configure(**_CONFIGURE_KEYWORDS)
        finally:
            sys.settrace(None)

    def load_the_file():
        released.wait(timeout=30)
        config_module.load_config(str(explicit))
        loaded.set()

    threads = [
        threading.Thread(target=load_the_file),
        threading.Thread(target=apply_keywords),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for thread in threads:
        assert not thread.is_alive(), "a writer never returned"

    assert len(arrivals) == len(_CONFIGURE_KEYWORDS), (
        "the preemption was never scheduled inside the apply loop, so this "
        f"test proved nothing: {arrivals}"
    )

    final = get_config()
    from_keywords = {
        name: getattr(final, name) == value
        for name, value in _CONFIGURE_KEYWORDS.items()
    }
    assert set(from_keywords.values()) in ({True}, {False}), (
        "configure() was torn in half by a concurrent load_config: "
        f"{ {name: getattr(final, name) for name in _CONFIGURE_KEYWORDS} }"
    )
    if not any(from_keywords.values()):
        # The load won outright, which is the other legal outcome: an explicit
        # file load replaces the configuration wholesale. It must have won
        # wholesale too.
        assert final.cluster_host == "fromfile.example"
        assert final.username == "file-user"


#: Trials for the race below. A race that reproduces one time in fifty is
#: still a race, so one pass proves nothing; this is sized so the whole test
#: stays inside a few seconds while giving the interleaving many chances.
_RACE_TRIALS = 15


def test_an_explicit_load_supersedes_a_search_that_is_already_running(
    unloaded_config, tmp_path
):
    """The single-threaded version of this test cannot see the real defect.

    Moving the search from import time to first use moved it onto whichever
    thread touches the configuration first, and that thread rebinds ``_config``
    when it finishes. So an explicit ``load_config`` no longer merely has to
    beat a search that has *not started*; it has to survive one that is
    *already in flight*. Before ``load_config`` took the lock, it did not: the
    caller was told the file had loaded, ``_config`` briefly held it, and then
    the search landed on top and the process ran against ``~/.clustrix``
    instead. An accepted instruction, discarded, reported as success -- which
    is the entire subject of this issue, reintroduced by its own fix.

    Nothing is patched. The window is widened with a real half-megabyte
    configuration file, whose YAML parse genuinely takes a couple of hundred
    milliseconds, so the main thread's ``load_config`` reliably lands while the
    search is still inside it. The padding is comments, so the file is
    otherwise an ordinary valid config.
    """
    padding = "\n".join(f"# pad {index} {'x' * 80}" for index in range(4000))
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromsearch.example\n" + padding + "\n"
    )
    explicit = tmp_path / "explicit.yml"
    explicit.write_text("cluster_type: ssh\ncluster_host: explicit.example\n")

    discarded = []
    errors: list = []

    def searcher():
        try:
            get_config()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    for trial in range(_RACE_TRIALS):
        config_module._config = ClusterConfig()
        config_module._default_config_loaded = False

        thread = threading.Thread(target=searcher)
        thread.start()
        # The search is now inside the parse of the large file.
        time.sleep(0.05)
        config_module.load_config(str(explicit))
        assert (
            get_config().cluster_host == "explicit.example"
        ), f"load_config did not take effect at all (trial {trial})"

        thread.join(timeout=30)
        assert not thread.is_alive(), f"the search never finished (trial {trial})"
        if get_config().cluster_host != "explicit.example":
            discarded.append((trial, get_config().cluster_host))

    assert not errors, errors
    assert not discarded, (
        "an explicit load_config() was accepted and then thrown away by the "
        f"lazy search finishing after it, in {len(discarded)} of "
        f"{_RACE_TRIALS} trials: {discarded}"
    )
