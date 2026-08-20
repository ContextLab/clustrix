"""Failures must be reported, not converted into plausible answers (issue #123).

The framing that governs every decision in here: *the library accepts an
instruction, discards it, and reports success. Silence is the defect.* "I
could not tell" must never be returned as "no".

Nothing is mocked. The connection-shaped tests drive a real in-process
paramiko server with real SFTP and real shell commands; the rest use real
functions, real sockets and real files.

Two kinds of test live here:

* behavioural tests for the sites where a swallowed exception produced a
  *wrong answer* -- a finished job reported as running, an unserializable
  payload shipped by reference;
* one structural guard (:func:`test_every_bare_swallow_has_a_recorded_decision`)
  that walks the AST of the whole package and refuses any ``except Exception``
  whose body is nothing but ``pass`` / ``return None`` / ``continue``, unless
  the site is on an allowlist that records why. That makes the audit
  executable, and it is the regression test for the handlers that are
  genuinely unreachable with real inputs and so cannot be driven from a test.
"""

import ast
import logging
import pathlib
import socket
import sys

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from clustrix.executor_scheduler_status import SchedulerStatusManager
from clustrix.loop_analysis import find_parallelizable_loops
from clustrix.utils import _dumps_by_value, get_environment_info
from tests.ssh_server import LocalSSHServer

PASSWORD = "swallow-test-password"
PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "clustrix"


# ---------------------------------------------------------------------------
# Real SSH server fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "served"
    root.mkdir()
    with LocalSSHServer(root=str(root), password=PASSWORD) as running:
        yield running


@pytest.fixture
def connection(server):
    config = ClusterConfig(
        cluster_type="ssh",
        cluster_host=server.host,
        cluster_port=server.port,
        username="tester",
        password=PASSWORD,
        ssh_host_key_policy="auto_add",
        remote_work_dir=server.root,
    )
    manager = ConnectionManager(config)
    manager.setup_ssh_connection()
    try:
        yield manager
    finally:
        manager.disconnect()


# ---------------------------------------------------------------------------
# executor_scheduler_status: a job whose error file cannot be measured
# ---------------------------------------------------------------------------


def test_an_unmeasurable_error_file_is_unknown_not_running(connection, caplog):
    """A job status of "running" must mean the job is running.

    The SSH backend decides between "failed" and "running" by counting the
    lines in ``job.err``. When that count could not be taken, the answer was
    ``"running"`` -- so ``wait_for_result`` went back round the poll loop and
    kept going until ``job_wait_timeout`` expired, and the ``TimeoutError``
    then blamed a job that had already stopped. The measurement failing says
    nothing whatsoever about whether the job is running.

    The trigger here is real and needs no patching: ``job.err`` is a
    *directory*. ``sftp.stat`` reports it as present, so the code reaches the
    count; ``wc -l`` on a directory writes nothing to stdout, so parsing the
    count raises.
    """
    remote_dir = pathlib.Path(connection.config.remote_work_dir) / "job-1"
    remote_dir.mkdir()
    (remote_dir / "job.err").mkdir()

    manager = SchedulerStatusManager(connection.config, connection)
    active_jobs = {"job-1": {"remote_dir": str(remote_dir)}}

    with caplog.at_level(logging.WARNING, logger="clustrix.executor_scheduler_status"):
        status = manager.check_job_status("job-1", active_jobs)

    assert status == "unknown", (
        "an unreadable job.err was reported as a running job; the caller "
        "polls on this answer"
    )
    messages = [record.getMessage() for record in caplog.records]
    assert any("job.err" in message for message in messages), messages
    assert any("NOT known to be running" in message for message in messages), messages


def test_a_present_and_empty_error_file_still_means_running(connection):
    """The honest paths are unchanged: this is what "running" is reserved for."""
    remote_dir = pathlib.Path(connection.config.remote_work_dir) / "job-2"
    remote_dir.mkdir()
    (remote_dir / "job.err").write_text("")

    manager = SchedulerStatusManager(connection.config, connection)
    status = manager.check_job_status(
        "job-2", {"job-2": {"remote_dir": str(remote_dir)}}
    )

    assert status == "running"


def test_a_non_empty_error_file_still_means_failed(connection):
    """...and this is what "failed" is reserved for."""
    remote_dir = pathlib.Path(connection.config.remote_work_dir) / "job-3"
    remote_dir.mkdir()
    (remote_dir / "job.err").write_text("Traceback (most recent call last):\n")

    manager = SchedulerStatusManager(connection.config, connection)
    status = manager.check_job_status(
        "job-3", {"job-3": {"remote_dir": str(remote_dir)}}
    )

    assert status == "failed"


@pytest.mark.timeout(120)
def test_a_file_that_cannot_be_scanned_for_a_traceback_is_named(connection, caplog):
    """A scan that skipped a file must not look like a scan that found nothing.

    The last-resort probe greps every ``.out`` / ``.err`` / ``.log`` file in
    the job directory for a traceback. When the grep raised, the file was
    skipped in silence -- and the file that was skipped may have been the one
    holding the traceback, so an exhaustive-looking scan was not exhaustive.
    Continuing is still right (the remaining files, and the accounting query
    after them, can produce a correct verdict); losing the reason is not.

    The trigger is real: the directory listing goes through a
    ``ClusterFilesystem``, which holds its own SSH connection, while the grep
    goes through this manager's connection manager. Closing only the latter
    leaves the listing working and every subsequent command raising -- exactly
    the shape of a transport that dropped mid-probe.

    This test is slow on purpose: reaching that probe requires the retry loop
    above it to exhaust its five attempts with exponential backoff, and
    shortening that would mean changing production timing to suit a test.
    """
    remote_dir = pathlib.Path(connection.config.remote_work_dir) / "job-4"
    remote_dir.mkdir()
    (remote_dir / "slurm-99.out").write_text("some output\n")

    manager = SchedulerStatusManager(connection.config, connection)

    # A live ClusterFilesystem for the listing, a dead one for the commands.
    connection.ssh_client = None

    with caplog.at_level(logging.WARNING, logger="clustrix.executor_scheduler_status"):
        status = manager._check_job_completion_with_retry("job-4", str(remote_dir))

    assert status == "unknown"
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "slurm-99.out" in message and "skipping" in message for message in messages
    ), messages


def test_an_unreadable_error_log_says_so_instead_of_saying_there_is_none(
    server, connection, caplog
):
    """ "No error log found" is a statement about the cluster, not about us.

    This string is what the user is shown as the reason their job died, and it
    is put in front of them by ``wait_for_result`` raising ``RuntimeError``
    with it attached. Returning it after every read *failed* answers "I could
    not tell" with "there was nothing there" -- and sends the user looking for
    a job that produced no output, when in fact the output was never fetched.

    The trigger is real: the connection is closed, so the ``cat`` of each
    candidate log raises rather than returning empty output.
    """
    remote_dir = pathlib.Path(connection.config.remote_work_dir) / "job-5"
    remote_dir.mkdir()
    (remote_dir / "job.err").write_text("boom\n")

    manager = SchedulerStatusManager(connection.config, connection)
    active_jobs = {"job-5": {"remote_dir": str(remote_dir)}}

    # Sanity: with a live connection the log really is retrievable, so the
    # assertion below is about the failure path and not about an empty file.
    assert "boom" in manager.get_error_log("job-5", active_jobs)

    # Take command channels away while leaving SFTP working -- a real
    # server-side refusal, the shape a MaxSessions ceiling has.
    server.refuse_further_execs()

    with caplog.at_level(logging.WARNING, logger="clustrix.executor_scheduler_status"):
        reported = manager.get_error_log("job-5", active_jobs)

    assert "No error log found" not in reported, reported
    assert "Could not read the error log" in reported, reported
    assert "not evidence that it is absent" in reported, reported
    messages = [record.getMessage() for record in caplog.records]
    assert any("job.err" in message for message in messages), messages


# ---------------------------------------------------------------------------
# loop_analysis
# ---------------------------------------------------------------------------


def test_arguments_that_cannot_be_bound_are_reported(caplog):
    """Losing the argument values silently costs the user their parallelism.

    ``find_parallelizable_loops`` resolves ``range(n)`` against the call's
    actual arguments. When binding them raised, the failure was discarded, the
    loop came back with unknown bounds, and the submission ran sequentially --
    with nothing anywhere to say why. The degraded answer is correct, so this
    is log-and-continue; but it is the difference between a parallel run and a
    serial one, so it is a warning rather than nothing.
    """

    def target(count):
        total = 0
        for index in range(count):
            total += index
        return total

    with caplog.at_level(logging.WARNING, logger="clustrix.loop_analysis"):
        # Three positional arguments for a one-parameter function: bind_partial
        # raises TypeError, for real.
        loops = find_parallelizable_loops(target, (1, 2, 3), {})

    assert isinstance(loops, list)
    messages = [record.getMessage() for record in caplog.records]
    assert any("target" in message for message in messages), messages
    assert any("not be parallelized" in message for message in messages), messages


def test_binding_arguments_normally_says_nothing(caplog):
    """The ordinary path must not warn, or the warning above is just noise."""

    def target(count):
        total = 0
        for index in range(count):
            total += index
        return total

    with caplog.at_level(logging.WARNING, logger="clustrix.loop_analysis"):
        find_parallelizable_loops(target, (10,), {})

    assert [record.getMessage() for record in caplog.records] == []


def test_a_bound_that_cannot_be_folded_gives_no_range_rather_than_a_wrong_one():
    """Constant folding that fails must yield "unknown", never a guess.

    ``range("a" + 1)`` is a real TypeError raised by the evaluator's own
    arithmetic. None -- "this bound is not statically known" -- is the correct
    answer to hand back: the caller refuses to chunk the loop and runs it
    whole, which is always right. The handler is narrowed to the errors
    folding can actually raise so that a bug *in the evaluator* is no longer
    laundered into an unknown bound.
    """
    from clustrix.loop_analysis import SafeRangeEvaluator

    tree = ast.parse('range("a" + 1)', mode="eval")
    analyzer = SafeRangeEvaluator({})
    analyzer.visit(tree.body)

    assert analyzer.result is None
    assert analyzer.safe is False

    # And the honest path is unaffected: a bound that *can* be folded still is.
    folded = SafeRangeEvaluator({})
    folded.visit(ast.parse("range(2 + 3)", mode="eval").body)
    assert folded.safe is True
    assert folded.result == {"start": 0, "stop": 5, "step": 1}


# ---------------------------------------------------------------------------
# utils: serialization must not degrade to a by-reference payload
# ---------------------------------------------------------------------------


def test_an_unserializable_payload_is_refused_rather_than_shipped_by_reference():
    """The last-resort ``pickle.dumps`` was the bug, not the safety net.

    stdlib pickle stores a function or class by qualified name. On a worker --
    a fresh interpreter with no ``__main__`` to resolve that name against --
    the bytes it produced failed as "Can't get attribute", naming something
    the user never wrote. So the fallback almost always *succeeded* locally
    and turned a serialization failure into a remote failure minutes later,
    with an unrelated message. ``_dumps_by_value``'s own docstring already
    promised the exception propagates instead; now it does, and it names every
    strategy that was tried.

    A live socket is refused by dill, dill-with-recurse and cloudpickle alike,
    so this exercises the real end of the cascade.
    """
    with socket.socket() as live_socket:
        with pytest.raises(RuntimeError) as raised:
            _dumps_by_value(live_socket)

    message = str(raised.value)
    assert "Cannot serialize this job by value" in message
    for strategy in ("dill(recurse=True)", "dill", "cloudpickle"):
        assert strategy in message, message


def test_a_serializable_payload_still_round_trips():
    """The cascade must still degrade through its stages, not just raise."""
    import dill

    def made_here(x):
        return x + 1

    payload = _dumps_by_value(made_here)
    assert dill.loads(payload)(41) == 42


def test_a_failed_environment_capture_is_reported(monkeypatch, caplog):
    """An empty package list must not be indistinguishable from a real one.

    ``get_environment_info`` returns "" when ``pip list`` cannot be run, and
    "" reads downstream as "this environment has no packages" -- which is
    never true. The empty return is kept (callers treat it as advisory), but
    the reason no longer disappears with it.

    Pointing ``sys.executable`` at a path that does not exist is a real
    failure: ``subprocess.run`` really raises ``FileNotFoundError``.
    """
    monkeypatch.setattr(sys, "executable", "/nonexistent/python-that-is-not-there")

    with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
        assert get_environment_info() == ""

    messages = [record.getMessage() for record in caplog.records]
    assert any("pip list" in message for message in messages), messages


# ---------------------------------------------------------------------------
# The structural guard: every remaining bare swallow is a recorded decision
# ---------------------------------------------------------------------------

#: ``file:line-anchored-name`` -> why this handler may discard the reason.
#:
#: Adding an entry is a deliberate act with a written justification, which is
#: the whole point: the failure mode this issue is about is a handler nobody
#: ever decided on. Entries are keyed by module and enclosing function so they
#: survive edits above them.
JUSTIFIED_SWALLOWS = {
    ("executor_core.py", "__del__"): (
        "A finaliser runs at an interpreter-defined time or never, possibly "
        "while modules are already torn down. An exception raised from it is "
        "printed and discarded by the interpreter anyway, and there is no "
        "caller left to give a correct or incorrect answer to. `with "
        "ClusterExecutor(...)` is the real teardown story; this is a backstop."
    ),
    ("auth_fallbacks.py", "get_cluster_password"): (
        "Colab's userdata.get raises for a secret that is simply not set, "
        "which is the ordinary case for the three name variants that do not "
        "match. Three further credential sources follow, and if none supplies "
        "a password the caller raises rather than proceeding."
    ),
}


def _enclosing_function(tree, node):
    """Name of the innermost function containing ``node``, or '<module>'."""
    best = "<module>"
    best_span = None
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(candidate, "end_lineno", None)
        if end is None or not (candidate.lineno <= node.lineno <= end):
            continue
        span = end - candidate.lineno
        if best_span is None or span < best_span:
            best, best_span = candidate.name, span
    return best


def _is_bare_exception_handler(handler):
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _body_is_only_a_shrug(body):
    """True if the handler does nothing but discard the exception."""
    for statement in body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Continue):
            continue
        if isinstance(statement, ast.Return) and (
            statement.value is None
            or (
                isinstance(statement.value, ast.Constant)
                and statement.value.value is None
            )
        ):
            continue
        return False
    return True


def test_every_bare_swallow_has_a_recorded_decision():
    """No ``except Exception`` may narrow to ``pass``/``return None``/``continue``.

    This is the executable form of the issue's own acceptance criterion: every
    swallow either re-raises, or logs with the exception attached, or is
    justified. A handler that does none of the three is the defect -- the
    library accepted an instruction, discarded it, and reported success.

    It is also the regression test for the handlers that a behavioural test
    cannot reach. Several sites (``_source_checkout_path``,
    ``_distribution_records``) are unreachable with real package metadata and
    carry ``# pragma: no cover`` for that reason; this guard is what would
    catch them reverting to silence.
    """
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if not _is_bare_exception_handler(handler):
                    continue
                if not _body_is_only_a_shrug(handler.body):
                    continue
                key = (path.name, _enclosing_function(tree, handler))
                if key in JUSTIFIED_SWALLOWS:
                    continue
                offenders.append(f"{path.name}:{handler.lineno} in {key[1]}()")

    assert not offenders, (
        "these handlers discard the reason a failure happened without "
        "re-raising, logging it, or being recorded in JUSTIFIED_SWALLOWS:\n  "
        + "\n  ".join(offenders)
    )


def test_the_allowlist_has_no_stale_entries():
    """An allowlist that outlives its sites stops describing the code."""
    live = set()
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if _is_bare_exception_handler(handler) and _body_is_only_a_shrug(
                    handler.body
                ):
                    live.add((path.name, _enclosing_function(tree, handler)))

    stale = sorted(set(JUSTIFIED_SWALLOWS) - live)
    assert not stale, f"JUSTIFIED_SWALLOWS names sites that no longer exist: {stale}"
