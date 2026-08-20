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

import json
import os
import subprocess
import sys
import textwrap
import threading

import pytest

import clustrix.config as config_module
from clustrix.config import (
    CONFIG_DIR_ENV_VAR,
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
    """``load_config`` must not be undone by a search that had not run yet."""
    (unloaded_config / "config.yml").write_text(
        "cluster_type: ssh\ncluster_host: fromsearch.example\n"
    )
    explicit = tmp_path / "explicit.yml"
    explicit.write_text("cluster_type: ssh\ncluster_host: explicit.example\n")

    config_module.load_config(str(explicit))

    assert get_config().cluster_host == "explicit.example"
