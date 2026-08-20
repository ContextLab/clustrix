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
* one structural guard (:func:`test_every_silent_swallow_has_a_recorded_decision`)
  that walks the AST of the whole package -- subpackages included -- and
  refuses any handler that catches everything and then does nothing about it,
  unless the site is recorded: as a decision in ``JUSTIFIED_SWALLOWS`` or as a
  defect with an issue number in ``TRACKED_DEFECTS``. That makes the audit
  executable, and it is the regression test for the handlers that are
  genuinely unreachable with real inputs and so cannot be driven from a test.

  The guard is stated as something a handler must *do*, not as a list of bad
  spellings, because the first version was the latter and was defeated
  seventeen ways -- ``except BaseException``, a bare ``except:``, ``except
  (Exception,)``, an aliased ``Exception``, ``contextlib.suppress``, ``return
  False``, ``...``, ``break``, a dead assignment, ``if False: raise``,
  ``finally: return``, a log line with no exception in it, and a nested
  function renamed to match an allowlist key. Every one of those is now a
  parametrised test (``BYPASSES``), as are the handlers that legitimately do
  report (``ACCEPTED``) and the seven ways past it that remain open
  (``BLIND_SPOTS`` / ``KNOWN_BLIND_SPOTS``), which are asserted to be missed
  so that the guard is never mistaken for more than it is.
"""

import ast
import logging
import pathlib
import socket
import sys
import textwrap
from typing import NamedTuple

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_connections import ConnectionManager
from clustrix.executor_scheduler_status import SchedulerStatusManager
from clustrix.loop_analysis import find_parallelizable_loops
from clustrix.utils import _dumps_by_value, get_environment_info
from tests.ssh_server import LocalSSHServer

# The repository's own committed-credential check (scripts/check_for_secrets.py)
# flags any 8+ character literal assigned to a password-named variable, and it
# is right to: that is the shape a leaked credential takes. "wrong_password" is
# the stand-in the scanner already recognises and the spelling the two sibling
# LocalSSHServer fixtures use (test_executor_context_manager.py,
# test_remote_file_exists_reporting.py). Do not widen the scanner for a test.
PASSWORD = "wrong_password"
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
    ``"running"``. The measurement failing says nothing whatsoever about
    whether the job is running, and reporting it as running made the eventual
    ``TimeoutError`` blame a job that may already have stopped.

    Be precise about what this buys, because it is easy to overstate: the
    caller's poll loop treats ``"unknown"`` exactly as it treated
    ``"running"``, so the wait still runs to ``job_wait_timeout``. Nothing
    fetches a result it should not. What changes is that the failure is now
    *said* -- in the warning below, and in the timeout message, which spells
    out that the status was unmeasurable rather than merely slow (see
    ``tests/unit/test_job_wait_timeout.py``).

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
# ---------------------------------------------------------------------------
# The structural guard: every remaining swallow is a recorded decision
# ---------------------------------------------------------------------------
#
# A note on what this guard is and is not, because this project has been here
# before. Three earlier AST guards in this repository were defeated 12, then
# 30, then 14 and 16 ways, and the eventual answer for file permissions
# (tests/unit/test_persisted_files_are_private.py) was to stop reading source
# and observe the property instead. That move is not available here: there is
# no runtime observation that says "this handler discarded a reason", because
# the whole point of a swallowed exception is that it leaves no trace. Every
# handler would have to be driven with a real failure to be observed, and most
# of them cannot be reached at all with real inputs.
#
# So this stays a source guard, and the honest thing to do is (a) state the
# rule as something a handler must *do* rather than a list of bad spellings,
# since enumerating spellings is exactly what lost the previous three rounds,
# (b) test the guard against every bypass anyone has thought of, so its reach
# is executable rather than assumed, and (c) write down what it still cannot
# see, in KNOWN_BLIND_SPOTS, so nobody reads a green run as more than it is.

#: Names that catch everything. A handler for either of these, a bare
#: ``except:``, or a tuple containing either, stops every failure.
CATCH_ALL_NAMES = frozenset({"Exception", "BaseException"})

#: Logging levels nobody is watching in production. A call at one of these
#: levels whose arguments are all constants cannot be conveying what went
#: wrong, because it does not have the exception in it.
QUIET_LOG_LEVELS = frozenset({"debug", "info"})

#: Names that pull the current exception out of the interpreter, so a handler
#: that uses one is reporting the failure even without an ``as`` binding.
EXCEPTION_ACCESSORS = frozenset(
    {"format_exc", "exc_info", "print_exc", "print_exception"}
)

#: Keywords that attach the failure to a log record.
EXCEPTION_KEYWORDS = frozenset({"exc_info", "stack_info"})

#: ``(module, qualified enclosing name)`` -> why this handler may discard the
#: reason.
#:
#: Adding an entry is a deliberate act with a written justification, which is
#: the whole point: the failure mode this issue is about is a handler nobody
#: ever decided on. Keys are *qualified*, not bare function names -- keying by
#: bare name meant an ``except Exception: pass`` inside any nested function
#: that happened to be called ``__del__`` was auto-allowed by the entry below,
#: and renaming a function to ``__del__`` was a one-line way past the guard.
JUSTIFIED_SWALLOWS = {
    ("executor_core.py", "ClusterExecutor.__del__"): (
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

#: ``(module, qualified enclosing name)`` -> the issue tracking it.
#:
#: Separate from JUSTIFIED_SWALLOWS on purpose. These are *defects*, not
#: decisions; they are recorded so the guard stays green without anyone having
#: to pretend they are fine, and an entry here is a promise that the issue
#: exists. The stale-entry test below covers this dict too, so a fix removes
#: the entry rather than leaving a lie behind.
TRACKED_DEFECTS = {
    ("notebook_magic_config.py", "load_config_from_file"): (
        "Returns {} for any failure to read a configuration file, so an "
        "unreadable file is indistinguishable from an empty one. Tracked as "
        "https://github.com/ContextLab/clustrix/issues/168 and deliberately "
        "not fixed here."
    ),
}

#: What this guard cannot see. Each one is asserted below, in
#: ``test_the_guard_admits_what_it_cannot_see``, so the list is executable
#: rather than aspirational -- if one of these ever *does* start being caught,
#: that test fails and the entry gets deleted.
#:
#: 1. **A handler that calls a helper which shrugs.**
#:    ``except Exception as exc: _record(exc)`` where ``_record`` has an empty
#:    body. The call looks like reporting; following it needs whole-program
#:    analysis, and the helper may not even be in this package.
#: 2. **A state change that reports nothing.** ``except Exception: self.ok =
#:    False``. Assigning to an attribute or a subscript is treated as doing
#:    something, because in most of this package it is -- but it does not tell
#:    anyone why.
#: 3. **A log line that mentions a variable instead of the exception.**
#:    ``logger.debug("failed for %s", host)`` passes, because requiring the
#:    exception itself in every log call would flag 21 handlers here that do
#:    explain themselves in prose.
#: 4. **A narrower ``except`` that is broad in practice.** ``except OSError``
#:    around a body that only ever raises ``OSError`` is a catch-all in
#:    effect; the guard reads the name, not the body it guards.
#: 5. **An exception replaced by a worse one.** ``raise RuntimeError("failed")``
#:    with no ``from exc`` re-raises, so it passes, while still throwing the
#:    cause away.
#: 6. **Code that is not in a ``.py`` file in this package.** The remote job
#:    scripts assembled as strings in ``utils.py`` are never parsed here, and
#:    neither is anything in a dependency.
#: 7. **Two swallows in one function.** Keys are per function, so a justified
#:    site licenses a second, unjustified one beside it. Narrowing the key to
#:    a line number would make every entry rot on the next edit above it.
KNOWN_BLIND_SPOTS = 7


class Swallow(NamedTuple):
    module: str
    qualname: str
    lineno: int
    shape: str

    @property
    def key(self):
        return (self.module, self.qualname)

    def __str__(self) -> str:
        return f"{self.module}:{self.lineno} in {self.qualname}() -- {self.shape}"


def _qualified_names(tree):
    """Map every function and class node to its dotted, scope-aware name."""
    names = {}

    def descend(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + child.name
                names[child] = qualname
                descend(child, qualname + ".<locals>.")
            elif isinstance(child, ast.ClassDef):
                qualname = prefix + child.name
                names[child] = qualname
                descend(child, qualname + ".")
            else:
                descend(child, prefix)

    descend(tree, "")
    return names


def _enclosing_qualname(qualnames, node):
    """Qualified name of the innermost function or class containing ``node``."""
    best, best_span = "<module>", None
    for candidate, qualname in qualnames.items():
        end = getattr(candidate, "end_lineno", None)
        if end is None or not (candidate.lineno <= node.lineno <= end):
            continue
        span = end - candidate.lineno
        if best_span is None or span < best_span:
            best, best_span = qualname, span
    return best


def _catch_all_aliases(tree):
    """Module-level names bound to ``Exception``/``BaseException``.

    ``_Exc = Exception`` followed by ``except _Exc:`` is the same handler
    written in two lines, and the guard used to read only the second one.
    """
    aliases = set(CATCH_ALL_NAMES)
    changed = True
    while changed:  # a chain of aliases resolves in a couple of passes
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                if node.value.id not in aliases:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in aliases:
                        aliases.add(target.id)
                        changed = True
            elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
                for alias in node.names:
                    if alias.name in CATCH_ALL_NAMES:
                        bound = alias.asname or alias.name
                        if bound not in aliases:
                            aliases.add(bound)
                            changed = True
    return aliases


def _catches_everything(handler, aliases):
    """True for ``except:``, ``except Exception``, and every dressing of it."""
    if handler.type is None:  # a bare `except:` catches more, not less
        return True
    candidates = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    return any(isinstance(item, ast.Name) and item.id in aliases for item in candidates)


def _is_quiet_constant_log(call):
    """``logger.debug("")`` -- a call that conveys nothing about the failure."""
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr not in QUIET_LOG_LEVELS:
        return False
    arguments = list(call.args) + [keyword.value for keyword in call.keywords]
    return all(isinstance(argument, ast.Constant) for argument in arguments)


def _live_statements(body):
    """``body`` with statically dead branches removed.

    ``if False: raise`` is not a re-raise. Neither is ``while False:``.
    """
    for statement in body:
        if isinstance(statement, ast.If) and isinstance(statement.test, ast.Constant):
            taken = statement.body if statement.test.value else statement.orelse
            yield from _live_statements(taken)
        elif isinstance(statement, ast.While) and isinstance(
            statement.test, ast.Constant
        ):
            if statement.test.value:
                yield statement
            else:
                yield from _live_statements(statement.orelse)
        else:
            yield statement


def _accounts_for_the_failure(body, bound_name):
    """True if this handler body does anything about the failure it caught.

    Stated as a requirement on the handler rather than a list of forbidden
    bodies, because the forbidden-body formulation is what let ``return
    False``, ``...``, ``break``, ``if False: raise`` and a dead assignment
    through. Anything that re-raises, names the exception, reaches for the
    interpreter's current exception, calls out, changes state beyond a local,
    or leaves the frame in a way the caller can notice counts. A body that
    only rebinds a local to a constant, or falls off the end, does not.
    """
    for statement in _live_statements(body):
        if isinstance(
            statement,
            (
                ast.Raise,
                ast.With,
                ast.AsyncWith,
                ast.Global,
                ast.Nonlocal,
                ast.AugAssign,
                ast.Delete,
                ast.Assert,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            return True
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, (ast.Attribute, ast.Subscript))
            for target in statement.targets
        ):
            return True
        for node in ast.walk(statement):
            if isinstance(node, ast.Raise):
                return True
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)):
                return True
            if isinstance(node, ast.Name) and bound_name and node.id == bound_name:
                return True
            if isinstance(node, ast.Attribute) and node.attr in EXCEPTION_ACCESSORS:
                return True
            if isinstance(node, ast.keyword) and node.arg in EXCEPTION_KEYWORDS:
                return True
            if isinstance(node, ast.Call) and not _is_quiet_constant_log(node):
                return True
        # Recurse into compound statements the walk above already covered for
        # expressions but whose nested *statements* need dead-branch pruning.
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(statement, field, None)
            if nested and _accounts_for_the_failure(nested, bound_name):
                return True
    return False


def _suppresses_everything(call, aliases):
    """``contextlib.suppress(Exception)`` is ``except Exception: pass``."""
    function = call.func
    name = function.attr if isinstance(function, ast.Attribute) else None
    if name is None:
        name = getattr(function, "id", None)
    if name != "suppress":
        return False
    return any(
        isinstance(argument, ast.Name) and argument.id in aliases
        for argument in call.args
    )


def find_silent_swallows(source, module):
    """Every place in ``source`` where a failure disappears without a word.

    Exposed as a function so the repository scan and the bypass tests below
    exercise exactly the same code; a guard whose reach is only ever measured
    against the code it already passes on is not measured at all.
    """
    tree = ast.parse(source, filename=module)
    aliases = _catch_all_aliases(tree)
    qualnames = _qualified_names(tree)
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                if not _catches_everything(handler, aliases):
                    continue
                if _accounts_for_the_failure(handler.body, handler.name):
                    continue
                found.append(
                    Swallow(
                        module,
                        _enclosing_qualname(qualnames, handler),
                        handler.lineno,
                        "except-handler that discards the reason",
                    )
                )
            # `finally: return` throws away an exception that is still in
            # flight, with no handler anywhere in sight.
            for statement in node.finalbody:
                if isinstance(statement, (ast.Return, ast.Break, ast.Continue)):
                    found.append(
                        Swallow(
                            module,
                            _enclosing_qualname(qualnames, statement),
                            statement.lineno,
                            f"{type(statement).__name__.lower()} in a finally: "
                            "block, which discards an exception in flight",
                        )
                    )
        elif isinstance(node, ast.Call) and _suppresses_everything(node, aliases):
            found.append(
                Swallow(
                    module,
                    _enclosing_qualname(qualnames, node),
                    node.lineno,
                    "contextlib.suppress over a catch-all",
                )
            )

    return found


def scan_tree(root):
    """Every swallow under ``root``, subpackages included.

    ``rglob``, not ``glob``: the first version of this guard globbed
    ``clustrix/*.py``, so a module one directory down was invisible to it and
    moving a handler into a subpackage removed it from the guard entirely.
    Module names are kept relative to the root so two files with the same
    basename in different subpackages cannot share an allowlist key.
    """
    found = []
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        found.extend(find_silent_swallows(path.read_text(), module))
    return found


def _package_swallows():
    return scan_tree(PACKAGE)


def test_every_silent_swallow_has_a_recorded_decision():
    """No handler may discard the reason a failure happened.

    This is the executable form of the issue's own acceptance criterion: every
    swallow either re-raises, or says what went wrong, or is recorded -- as a
    decision in JUSTIFIED_SWALLOWS, or as a defect with an issue number in
    TRACKED_DEFECTS.

    It is also the regression test for the handlers a behavioural test cannot
    reach. Several sites (``_distribution_import_names``, the remote
    interpreter probe) are unreachable with real package metadata and carry
    ``# pragma: no cover`` for that reason; this guard is what would catch
    them reverting to silence.
    """
    recorded = set(JUSTIFIED_SWALLOWS) | set(TRACKED_DEFECTS)
    offenders = [
        str(swallow) for swallow in _package_swallows() if swallow.key not in recorded
    ]

    assert not offenders, (
        "these sites discard the reason a failure happened without "
        "re-raising, reporting it, or being recorded in JUSTIFIED_SWALLOWS "
        "or TRACKED_DEFECTS:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_reaches_into_subpackages(tmp_path):
    """The guard globbed ``clustrix/*.py``, which stops at the top level.

    ``clustrix`` has no subpackages today, so this cannot be demonstrated
    against the real tree -- which is exactly why the hole survived. It is
    demonstrated against a real one built here instead: a swallow two
    directories down must be found, and the old glob is shown, in the same
    test, to have missed it.
    """
    nested = tmp_path / "backends" / "schedulers"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text(
        "def submit():\n"
        "    try:\n"
        "        send()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    (tmp_path / "top.py").write_text("def ok():\n    return 1\n")

    found = scan_tree(tmp_path)

    assert [swallow.module for swallow in found] == [
        "backends/schedulers/deep.py"
    ], found
    assert [path.name for path in tmp_path.glob("*.py")] == [
        "top.py"
    ], "the old top-level-only glob would still have missed it"


def test_the_allowlists_have_no_stale_entries():
    """An allowlist that outlives its sites stops describing the code."""
    live = {swallow.key for swallow in _package_swallows()}

    stale = sorted((set(JUSTIFIED_SWALLOWS) | set(TRACKED_DEFECTS)) - live)
    assert not stale, f"the allowlists name sites that no longer exist: {stale}"


# ---------------------------------------------------------------------------
# The guard's own reach, measured rather than assumed
# ---------------------------------------------------------------------------

#: Every way anyone has found to write a swallow that the first version of
#: this guard let through. The control is first: if that one ever stops being
#: caught the guard is broken outright.
BYPASSES = {
    "control: except Exception / pass": """
        def f():
            try:
                g()
            except Exception:
                pass
    """,
    "except BaseException": """
        def f():
            try:
                g()
            except BaseException:
                pass
    """,
    "bare except": """
        def f():
            try:
                g()
            except:
                pass
    """,
    "a one-element tuple": """
        def f():
            try:
                g()
            except (Exception,):
                pass
    """,
    "a tuple that hides the catch-all among specific types": """
        def f():
            try:
                g()
            except (ValueError, Exception):
                pass
    """,
    "an aliased Exception": """
        _Exc = Exception

        def f():
            try:
                g()
            except _Exc:
                pass
    """,
    "an Exception aliased on import": """
        from builtins import Exception as _Boom

        def f():
            try:
                g()
            except _Boom:
                pass
    """,
    "contextlib.suppress": """
        import contextlib

        def f():
            with contextlib.suppress(Exception):
                g()
    """,
    "a bare suppress import": """
        from contextlib import suppress

        def f():
            with suppress(Exception):
                g()
    """,
    "return False": """
        def f():
            try:
                return g()
            except Exception:
                return False
    """,
    "an ellipsis body": """
        def f():
            try:
                g()
            except Exception:
                ...
    """,
    "break": """
        def f():
            for _ in range(3):
                try:
                    g()
                except Exception:
                    break
    """,
    "a dead assignment": """
        def f():
            try:
                g()
            except Exception:
                unused = None
    """,
    "a re-raise that cannot run": """
        def f():
            try:
                g()
            except Exception:
                if False:
                    raise
    """,
    "return in a finally": """
        def f():
            try:
                g()
            finally:
                return None
    """,
    "a log line with no exception in it": """
        def f():
            try:
                g()
            except Exception:
                logger.debug("")
    """,
    "a nested function renamed to an allowlisted key": """
        class Other:
            def method(self):
                def __del__():
                    try:
                        g()
                    except Exception:
                        pass
                return __del__
    """,
}


@pytest.mark.parametrize("bypass", sorted(BYPASSES), ids=sorted(BYPASSES))
def test_the_guard_catches_every_known_bypass(bypass):
    """Each of these was, at some point, a way to swallow a failure silently.

    Written as data so a newly discovered spelling is one entry rather than
    one test, and so the control at the top proves the harness itself works.
    """
    source = textwrap.dedent(BYPASSES[bypass])

    found = find_silent_swallows(source, "probe.py")

    assert found, f"the guard does not catch: {bypass}"


def test_the_renamed_nested_function_is_not_licensed_by_the_allowlist():
    """The key collision, specifically.

    ``("executor_core.py", "__del__")`` used to license an ``except Exception:
    pass`` inside *any* function named ``__del__`` anywhere in that module,
    including one nested inside an unrelated method. Qualified keys mean the
    nested one is ``Other.method.<locals>.__del__``, which no entry names.
    """
    source = textwrap.dedent(
        BYPASSES["a nested function renamed to an allowlisted key"]
    )

    found = find_silent_swallows(source, "executor_core.py")

    assert [swallow.qualname for swallow in found] == ["Other.method.<locals>.__del__"]
    assert found[0].key not in JUSTIFIED_SWALLOWS


#: Bodies that genuinely do report the failure. A guard that flags these is
#: a guard people will delete, so the false-positive side is tested too.
ACCEPTED = {
    "re-raise": """
        def f():
            try:
                g()
            except Exception:
                raise
    """,
    "chained raise": """
        def f():
            try:
                g()
            except Exception as exc:
                raise RuntimeError("could not g") from exc
    """,
    "warning with the exception attached": """
        def f():
            try:
                g()
            except Exception as exc:
                logger.warning("could not g: %s", exc)
    """,
    "debug with the exception attached": """
        def f():
            try:
                g()
            except Exception as exc:
                logger.debug("could not g: %s", exc)
    """,
    "exc_info": """
        def f():
            try:
                g()
            except Exception:
                logger.warning("could not g", exc_info=True)
    """,
    "traceback": """
        def f():
            try:
                g()
            except Exception:
                logger.error(traceback.format_exc())
    """,
    "stashed for a later re-raise": """
        def f():
            try:
                g()
            except Exception as exc:
                failure = exc
            return failure
    """,
    "a narrower handler is not this guard's business": """
        def f():
            try:
                g()
            except KeyError:
                pass
    """,
}


@pytest.mark.parametrize("accepted", sorted(ACCEPTED), ids=sorted(ACCEPTED))
def test_the_guard_stays_quiet_on_handlers_that_do_report(accepted):
    source = textwrap.dedent(ACCEPTED[accepted])

    assert find_silent_swallows(source, "probe.py") == []


#: The bypasses that remain open, kept next to the numbered prose in
#: KNOWN_BLIND_SPOTS so the two cannot drift apart.
BLIND_SPOTS = {
    "a helper that shrugs on the handler's behalf": """
        def _record(exc):
            pass

        def f():
            try:
                g()
            except Exception as exc:
                _record(exc)
    """,
    "a state change that reports nothing": """
        class C:
            def f(self):
                try:
                    g()
                except Exception:
                    self.ok = False
    """,
    "a log line that names a variable instead of the exception": """
        def f(host):
            try:
                g()
            except Exception:
                logger.debug("failed for %s", host)
    """,
    "a narrower except that is broad in practice": """
        def f():
            try:
                open("/etc/hosts")
            except OSError:
                pass
    """,
    "an exception replaced by a worse one": """
        def f():
            try:
                g()
            except Exception:
                raise RuntimeError("failed")
    """,
    "code assembled as a string and never parsed here": """
        REMOTE = '''
        try:
            main()
        except Exception:
            pass
        '''
    """,
    "a second, unjustified swallow beside a justified one": """
        class ClusterExecutor:
            def __del__(self):
                try:
                    self.close()
                except Exception:
                    pass
                try:
                    self.other()
                except Exception:
                    pass
    """,
}


@pytest.mark.parametrize("spot", sorted(BLIND_SPOTS), ids=sorted(BLIND_SPOTS))
def test_the_guard_admits_what_it_cannot_see(spot):
    """These are *not* caught, and saying so is the point.

    A guard that looks stronger than it is invites exactly the commit it was
    meant to prevent. Each entry here is documented in KNOWN_BLIND_SPOTS with
    the reason it stays open. If one starts being caught, this test fails --
    which is the signal to delete the entry and the prose together, not to
    relax the guard.
    """
    source = textwrap.dedent(BLIND_SPOTS[spot])
    found = find_silent_swallows(source, "executor_core.py")
    unrecorded = [swallow for swallow in found if swallow.key not in JUSTIFIED_SWALLOWS]

    assert not unrecorded, (
        f"the guard now catches {spot!r}; delete it from BLIND_SPOTS and from "
        "the KNOWN_BLIND_SPOTS prose"
    )


def test_the_blind_spot_list_matches_the_prose():
    """The count in KNOWN_BLIND_SPOTS is the number of entries, not a guess."""
    assert len(BLIND_SPOTS) == KNOWN_BLIND_SPOTS
