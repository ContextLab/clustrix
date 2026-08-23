"""Failures must be reported, not converted into plausible answers (issue #123).

The framing that governs every decision in here: *the library accepts an
instruction, discards it, and reports success. Silence is the defect.* "I
could not tell" must never be returned as "no".

Nothing is mocked. The connection-shaped tests drive a real in-process
paramiko server with real SFTP and real shell commands; the rest use real
functions, real sockets, real files on disk and real distribution metadata.

Two kinds of test live here, and they are not equals.

**The guarantee is behavioural.** Each test in the first half of this module
takes a real clustrix surface, breaks something real underneath it -- a file
whose permissions forbid reading, a socket that cannot be opened, an SSH
transport that has been closed, package metadata that is not valid UTF-8, a
directory where a file was expected -- and asserts that the failure was
*audible*. Audible means one of exactly three things, and each test says which
one it expects:

1. the exception propagated, carrying what went wrong;
2. a log record was really emitted, at a level someone watches, with the
   reason in it;
3. the value handed back is one the caller can tell apart from a real answer
   -- ``"unknown"`` rather than ``"running"``, ``None`` rather than ``False``.

A handler cannot pass these by being spelled differently, because the spelling
is never examined. That is the same move
``tests/unit/test_persisted_files_are_private.py`` made for file permissions
after two static guards there had been defeated, and it is made here for the
same reason.

**The lint is not the guarantee.** The second half of this module walks the
AST of the whole package -- subpackages included -- and refuses any handler
that catches everything and then does nothing about it, unless the site is
recorded: as a decision in ``JUSTIFIED_SWALLOWS`` or as a defect with an issue
number in ``TRACKED_DEFECTS``. It also refuses suppression that has no handler
at all -- a replaced ``sys``/``threading`` exception hook, ``logging.disable``,
a blanket ``warnings`` filter -- which is a swallow none of the lettered
families below can describe, and which the guard could not see until
2026-08-20. It is fast, it reaches handlers no test can
drive, and it is worth having for that. It is also porous, and this module
says how porous rather than implying otherwise: five successive AST guards in
this repository have now been defeated 12, 30, 14-and-16, 22 and 8 ways
respectively. Its reach is executable -- ``BYPASSES`` (caught), ``ACCEPTED``
(correctly ignored), ``BLIND_SPOTS`` (missed on purpose, each asserted to be
missed, counted by ``KNOWN_BLIND_SPOTS``).
"""

import ast
import importlib.metadata
import logging
import os
import pathlib
import re
import socket
import sys
import textwrap
from typing import NamedTuple

import pytest

from clustrix.config import CONFIG_DIR_ENV_VAR, ClusterConfig
from clustrix.executor_connections import ConnectionManager
from clustrix.executor_scheduler_status import SchedulerStatusManager
from clustrix.loop_analysis import (
    detect_loops_in_function,
    find_parallelizable_loops,
    SafeRangeEvaluator,
)
from clustrix.modern_notebook_widget import ModernClustrixWidget
from clustrix.notebook_magic_widget import EnhancedClusterConfigWidget
from clustrix.utils import (
    _distribution_import_names,
    _dumps_by_value,
    get_environment_info,
    resolve_remote_python,
)
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


def test_a_bug_inside_the_range_evaluator_is_not_laundered_into_unknown():
    """The narrowed tuple is the fix; the test above passes without it.

    ``safe = False`` -- "this bound is not statically known" -- is the correct
    answer for the errors constant folding can really raise, and the test
    above pins that. What it does not pin is the *narrowing*: widening either
    handler back to ``except Exception`` still returns an unknown bound for a
    ``TypeError``, so that test stays green while the defect this round fixed
    comes straight back.

    So drive something the evaluator has no correct answer for. ``local_vars``
    is real bound arguments -- ``find_parallelizable_loops`` hands the caller's
    own values straight to ``SafeRangeEvaluator`` -- and an ``int`` subclass is
    an ``int``, so ``isinstance(value, int)`` accepts it and the evaluator
    folds ``n + 1`` by calling the user's ``__add__``. When that raises
    something folding cannot raise, the only honest outcome is for it to
    propagate: reporting it as "unknown bound" hides a defect behind a
    plausible answer, which is the whole of issue #123.

    Both handlers are on this path -- ``_evaluate_binop`` first, then
    ``visit_Call`` -- so widening either one turns the raise back into
    ``safe is False`` and fails here.
    """

    class EvaluatorBug(Exception):
        """Stands in for a defect in the evaluator, not a foldable bound."""

    class Bound(int):
        def __add__(self, other):
            raise EvaluatorBug("constant folding is broken")

    evaluator = SafeRangeEvaluator({"n": Bound(5)})

    with pytest.raises(EvaluatorBug):
        evaluator.visit(ast.parse("range(n + 1)", mode="eval").body)

    # ...and the swallow-worthy error is still swallowed, or the narrowing has
    # merely been traded for a different wrong answer.
    foldable = SafeRangeEvaluator({})
    foldable.visit(ast.parse('range("a" + 1)', mode="eval").body)
    assert (foldable.safe, foldable.result) == (False, None)


def test_a_bug_inside_the_range_evaluator_reaches_the_caller():
    """The narrowing above is worth nothing if the entry point re-swallows it.

    The test before this one pins ``SafeRangeEvaluator``. It was written, and
    passed, while ``find_parallelizable_loops`` -- the only way anything in
    clustrix reaches that evaluator -- still turned the propagated bug back
    into ``[]``: ``_analyze_for_loop`` wrapped the whole analysis in
    ``except Exception: logger.debug(...); return None``, and
    ``detect_loops_in_function`` wrapped *that* in
    ``except Exception: return []``. Measured, on the same input as below:
    the evaluator raised and the caller got ``[]``, "this function has no
    parallelizable loops". So the claim that "the only honest outcome is for
    it to propagate" was true of the evaluator and false of clustrix.

    That is issue #123's own defect class living inside a fix for it, and it
    is the lesson the previous round already paid for: the function was
    pinned, the call site was not. This test is at the call site. Widening
    either outer handler back to ``except Exception`` fails it while every
    evaluator-level test above stays green.
    """

    class EvaluatorBug(Exception):
        """Stands in for a defect in the evaluator, not a foldable bound."""

    class Bound(int):
        def __add__(self, other):
            raise EvaluatorBug("constant folding is broken")

    def target(count):
        total = 0
        for index in range(count + 1):
            total += index
        return total

    with pytest.raises(EvaluatorBug):
        find_parallelizable_loops(target, (Bound(5),), {})


def test_the_entry_point_still_analyzes_an_ordinary_function():
    """The negative control for the test above.

    Narrowing the two outer handlers must not turn ordinary analysis into a
    raise, and must not stop it finding anything: an unfoldable bound is still
    an answer, not an error, and a plain function still yields its loop.
    """

    def target(count):
        results = []
        for index in range(count):
            results.append(index * 2)
        return results

    loops = detect_loops_in_function(target, (10,), {})
    assert [loop.loop_type for loop in loops] == ["for"]
    assert loops[0].range_info == {"start": 0, "stop": 10, "step": 1}

    # A function whose source cannot be read is still "no loops", not a raise:
    # that is the one condition the narrowed outer handler still answers for.
    namespace: dict = {}
    exec("def made_by_exec():\n    for i in range(3):\n        pass\n", namespace)
    assert detect_loops_in_function(namespace["made_by_exec"], (), {}) == []


def test_a_bug_inside_the_while_loop_analyzer_reaches_the_caller(monkeypatch):
    """``_analyze_while_loop`` is ``_analyze_for_loop``'s unpinned twin.

    Both handlers were narrowed from ``except Exception`` to
    ``except RecursionError`` in the same edit, and exactly one of them was
    pinned: widening ``_analyze_while_loop`` back to ``except Exception``
    left the whole suite green. That is the pattern this issue keeps paying
    for -- fix one, miss its sibling -- so the sibling is pinned here on the
    same terms as ``test_a_bug_inside_the_range_evaluator_reaches_the_caller``
    above.

    The trigger has to be built differently. The for-loop path carries the
    caller's own values into ``SafeRangeEvaluator``, so a defect can be put
    underneath it with nothing but an ``int`` subclass. Nothing on the
    while-loop path touches a user value at all: it renders the condition,
    walks the body with a ``DependencyAnalyzer`` and builds a ``LoopInfo``.
    The only collaborator that can hold a defect is the analyzer the method
    constructs, so a real subclass of it stands in -- a real
    ``ast.NodeVisitor`` raising a real exception, not a mock; production code
    cannot tell, and ``AnalyzerBug`` stands in for a defect exactly as
    ``EvaluatorBug`` does above.

    ``RecursionError`` stays caught, and must: the analyzer is a recursive
    visitor and a deeply nested body really can exhaust the interpreter's
    limit, for which "this loop is not analyzable" is a correct answer.
    Anything else is a defect, and turning it into ``[]`` -- "this function
    has no parallelizable loops" -- is the same lie one frame out that this
    issue is about.
    """
    from clustrix import loop_analysis

    class AnalyzerBug(Exception):
        """A defect in the analyzer, not a loop that cannot be analyzed."""

    class BrokenDependencyAnalyzer(loop_analysis.DependencyAnalyzer):
        def visit_Name(self, node):
            raise AnalyzerBug("the dependency analyzer is broken")

    def target():
        index = 0
        while index < 10:
            index += 1
        return index

    # The control first, so the raise below cannot be an artefact of the loop
    # never having been reached: undamaged, this while loop really is analyzed.
    loops = loop_analysis.detect_loops_in_function(target, (), {})
    assert [loop.loop_type for loop in loops] == ["while"]
    assert loops[0].iterable == "index < 10"

    monkeypatch.setattr(loop_analysis, "DependencyAnalyzer", BrokenDependencyAnalyzer)

    with pytest.raises(AnalyzerBug):
        find_parallelizable_loops(target, (), {})


#: A deeply nested loop body is what really exhausts the interpreter's stack
#: inside ``_analyze_for_loop``/``_analyze_while_loop``, and it cannot be
#: reproduced by nesting the body in the fixture: ``LoopDetector`` walks the
#: same statements one frame later, with its own ``ast.NodeVisitor``
#: recursion, so a body deep enough to overflow the dependency analyzer
#: overflows the detector too and the raise lands outside the handler under
#: test. So the exhaustion is put exactly where the production one happens --
#: inside the analyzer, as a real unbounded recursion raising a real
#: ``RecursionError`` -- and nothing else is changed. A real subclass of the
#: real class, on the same terms as ``BrokenDependencyAnalyzer`` above:
#: production code cannot tell, and nothing is mocked.
def _exhausting_analyzer(loop_analysis):
    class ExhaustingDependencyAnalyzer(loop_analysis.DependencyAnalyzer):
        def visit_Name(self, node):
            return self.visit(node)  # unbounded, and really unbounded

    return ExhaustingDependencyAnalyzer


def test_giving_up_on_a_for_loop_is_audible(monkeypatch, caplog):
    """The giveup log is the only thing that distinguishes it from an answer.

    ``_analyze_for_loop`` answers ``None`` for a ``RecursionError``, and that
    answer is correct -- the loop runs whole, sequentially, which is always
    right. It is also *exactly* what an unparallelizable loop looks like, and
    what a function with no loops at all looks like one frame further out. So
    the warning is not decoration: delete it, or demote it below the level
    anyone watches, and a loop the user expected to be chunked across a
    cluster silently runs on one core with nothing anywhere saying why.

    Measured before this test existed: deleting the ``logger.warning`` call
    outright left the whole suite green, tally for tally. The handler had no
    test at all.
    """
    from clustrix import loop_analysis

    def target():
        total = 0
        for index in range(4):
            total += index
        return total

    # The control first, undamaged, so the giveup below cannot be an artefact
    # of the loop never having been reached.
    control = detect_loops_in_function(target, (), {})
    assert [loop.loop_type for loop in control] == ["for"]

    monkeypatch.setattr(
        loop_analysis, "DependencyAnalyzer", _exhausting_analyzer(loop_analysis)
    )

    with caplog.at_level(logging.WARNING, logger="clustrix.loop_analysis"):
        loops = detect_loops_in_function(target, (), {})

    assert loops == []
    giveups = [
        record
        for record in caplog.records
        if "Gave up analyzing the for loop" in record.getMessage()
    ]
    assert giveups, [record.getMessage() for record in caplog.records]
    # The level is part of the report. WARNING is what someone watches;
    # demoting this to debug is the same silence as deleting it.
    assert [record.levelno for record in giveups] == [logging.WARNING]
    assert "will not be parallelized" in giveups[0].getMessage()
    # And the reason travels with it, not just the fact.
    assert "maximum recursion" in giveups[0].getMessage()


def test_giving_up_on_a_while_loop_is_audible(monkeypatch, caplog):
    """``_analyze_while_loop``'s giveup, on the same terms as its twin above.

    Both handlers were written in the same edit and both were unreported-on:
    deleting either ``logger.warning`` left the suite green. Pinned
    separately, because the pattern this issue keeps paying for is fixing one
    of a pair and missing the other.
    """
    from clustrix import loop_analysis

    def target():
        index = 0
        while index < 10:
            index += 1
        return index

    control = loop_analysis.detect_loops_in_function(target, (), {})
    assert [loop.loop_type for loop in control] == ["while"]

    monkeypatch.setattr(
        loop_analysis, "DependencyAnalyzer", _exhausting_analyzer(loop_analysis)
    )

    with caplog.at_level(logging.WARNING, logger="clustrix.loop_analysis"):
        loops = loop_analysis.detect_loops_in_function(target, (), {})

    assert loops == []
    giveups = [
        record
        for record in caplog.records
        if "Gave up analyzing the while loop" in record.getMessage()
    ]
    assert giveups, [record.getMessage() for record in caplog.records]
    assert [record.levelno for record in giveups] == [logging.WARNING]
    assert "will not be parallelized" in giveups[0].getMessage()


def test_a_function_whose_source_is_gone_says_loop_detection_was_skipped(caplog):
    """``[]`` is a correct answer and an indistinguishable one.

    ``detect_loops_in_function`` returns ``[]`` for a function whose source
    cannot be read, and that is right: the function ships as-is and runs
    whole. But ``[]`` is also what an ordinary function with no loops returns,
    and what a *loop-bearing* function returns when analysis gave up -- so
    without the debug line there is nothing at all to tell a caller which of
    the three happened. Deleting it left the suite green.

    The trigger is a real function with no file behind it: ``exec`` compiles
    it from a string, so ``inspect.getsource`` raises ``OSError`` for real.
    """
    namespace: dict = {}
    exec("def made_by_exec():\n    for i in range(3):\n        pass\n", namespace)

    with caplog.at_level(logging.DEBUG, logger="clustrix.loop_analysis"):
        assert detect_loops_in_function(namespace["made_by_exec"], (), {}) == []

    skipped = [
        record
        for record in caplog.records
        if "Loop detection skipped" in record.getMessage()
    ]
    assert skipped, [record.getMessage() for record in caplog.records]
    assert "made_by_exec" in skipped[0].getMessage()
    # The reason, not just the fact: "could not read source" and "this is not
    # a function" are different problems with different fixes.
    assert "source" in skipped[0].getMessage()


def test_a_defect_in_source_acquisition_is_not_reported_as_no_loops():
    """``detect_loops_in_function``'s outer tuple is a decision, not a shield.

    ``(OSError, TypeError, SyntaxError)`` is the list of ways a function's
    source is legitimately unavailable, and ``[]`` -- "no parallelizable
    loops", so the function ships whole and runs sequentially -- is a correct
    answer for every one of them. That is why the negative control above
    drives an ``exec``'d function and expects ``[]``. What nothing drove was
    anything *outside* the tuple, so widening it back to ``except Exception``
    left the suite green.

    A ``__wrapped__`` cycle is a real thing to drive it with, and it is not
    exotic: ``functools.wraps`` sets ``__wrapped__`` on every wrapper, and a
    decorator applied so that the chain closes on itself makes
    ``inspect.getsource`` -- which unwraps before it looks for a file -- raise
    ``ValueError("wrapper loop when unwrapping ...")``. That is a broken
    decorator, not a function without source, and answering "no parallelizable
    loops" for it hides the breakage behind a plausible result.
    """

    def first():
        for index in range(3):
            print(index)

    def second():
        for index in range(3):
            print(index)

    first.__wrapped__ = second
    second.__wrapped__ = first

    with pytest.raises(ValueError, match="wrapper loop"):
        find_parallelizable_loops(first, (), {})


@pytest.mark.parametrize("error", [TypeError, ValueError, OverflowError])
def test_every_error_constant_folding_can_raise_is_answered_not_raised(error, caplog):
    """Pins the *membership* of ``_evaluate_binop``'s narrowed tuple.

    Dropping ``OverflowError`` from it survived the whole suite, because
    ``visit_Call``'s tuple lists ``OverflowError`` too and caught it one frame
    out -- the observable answer (``safe is False``, ``result is None``) is
    identical either way. So this asserts *which handler answered*, by the
    line it logs. Remove any member from the folding tuple and the folding
    message stops appearing.
    """

    class Bound(int):
        def __add__(self, other):
            raise error("this bound cannot be folded")

    evaluator = SafeRangeEvaluator({"n": Bound(5)})
    with caplog.at_level(logging.DEBUG, logger="clustrix.loop_analysis"):
        evaluator.visit(ast.parse("range(n + 1)", mode="eval").body)

    assert (evaluator.safe, evaluator.result) == (False, None)
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Could not fold a constant loop bound" in message for message in messages
    ), messages


@pytest.mark.parametrize(
    "error", [TypeError, ValueError, OverflowError, RecursionError]
)
def test_every_error_reading_a_range_argument_is_answered_not_raised(error, caplog):
    """Pins the membership of ``visit_Call``'s narrowed tuple, and its report.

    ``range(-n)`` negates the bound in ``_evaluate_node``, which is *outside*
    ``_evaluate_binop``'s handler, so ``visit_Call``'s tuple is the only one
    that can answer -- drop a member from it and this raises instead of
    reporting an unknown bound.

    ``safe = False`` is also indistinguishable from an ordinary non-constant
    bound (``range(len(items))``), which is the overwhelmingly common case and
    says nothing. The debug line is the only thing separating "this bound is
    not a constant" from "folding this bound blew up", and deleting it left
    the suite green -- so it is asserted here, once per member of the tuple.
    """

    class Bound(int):
        def __neg__(self):
            raise error("this bound cannot be read")

    evaluator = SafeRangeEvaluator({"n": Bound(5)})
    with caplog.at_level(logging.DEBUG, logger="clustrix.loop_analysis"):
        evaluator.visit(ast.parse("range(-n)", mode="eval").body)

    assert (evaluator.safe, evaluator.result) == (False, None)
    folds = [
        record
        for record in caplog.records
        if "Could not fold the range()" in record.getMessage()
    ]
    assert folds, [record.getMessage() for record in caplog.records]
    assert "this bound cannot be read" in folds[0].getMessage()
    assert "treated as unknown" in folds[0].getMessage()


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
# The sites that were annotated rather than fixed
# ---------------------------------------------------------------------------
#
# Nine handlers were exposed when the lint's rule was inverted, and seven of
# them were given a `logger.debug` line and left otherwise alone. A debug line
# is not a fix: the caller still receives the same wrong answer, and the level
# is one the lint itself classifies as unwatched. Each test below drives the
# real code with a real broken input and asserts the outcome, not the source.


def test_a_connectivity_probe_that_never_ran_is_not_reported_as_unreachable(caplog):
    """ "Cannot reach that host" is a claim about somebody else's machine.

    The widget's probe returned ``False`` both for a connection that was
    refused and for a probe that never got as far as connecting -- an
    unresolvable name, a port outside 0-65535 -- and the caller renders
    ``False`` as "Cannot reach {host}:{port}. Check if the hostname/IP is
    correct and accessible". For a DNS failure that is a confident, wrong
    statement about a machine clustrix never managed to ask, and it sends the
    user to check the wrong thing.

    The trigger is real: ``.invalid`` is reserved by RFC 2606 precisely so
    that it can never resolve, and ``connect_ex`` really raises
    ``socket.gaierror`` for it.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_widget"):
        reachable, reason = EnhancedClusterConfigWidget._test_remote_connectivity(
            None, "no-such-host.invalid", 22, timeout=2
        )

    assert reachable is None, (
        "a probe that could not be run was reported as a host that could not "
        "be reached"
    )
    assert reason, "the third value has to carry why, or the caller cannot say"
    messages = [record.getMessage() for record in caplog.records]
    assert any("no-such-host.invalid" in message for message in messages), messages
    assert any(
        "says nothing about whether the host is reachable" in message
        for message in messages
    ), messages


def test_a_connection_that_was_really_refused_is_still_a_refusal(caplog):
    """The honest answers are unchanged, or the test above is just noise.

    Port 1 on the loopback interface is a real TCP connect that is really
    refused: ``connect_ex`` returns ECONNREFUSED rather than raising.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_widget"):
        reachable, reason = EnhancedClusterConfigWidget._test_remote_connectivity(
            None, "127.0.0.1", 1, timeout=2
        )

    assert reachable is False
    assert reason
    assert caplog.records == [], "a real measurement must not warn"


def test_a_listening_socket_is_reported_as_reachable(server, caplog):
    """...and so is the positive answer, against the real SSH server."""
    with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_widget"):
        reachable, reason = EnhancedClusterConfigWidget._test_remote_connectivity(
            None, server.host, server.port, timeout=5
        )

    assert (reachable, reason) == (True, "")
    # The property is that the *widget* stayed silent. caplog captures every
    # logger, and paramiko's server thread races a banner-warning into the
    # record list on loaded runners -- the prober hangs up before the
    # handshake finishes, which is the probe working.
    widget_records = [
        r for r in caplog.records if r.name == "clustrix.notebook_magic_widget"
    ]
    assert widget_records == [], [r.getMessage() for r in widget_records]


def _press_test_config(host, port):
    """Drive the widget's real "Test configuration" button for ``host:port``.

    Everything here is real: a real ``EnhancedClusterConfigWidget`` with real
    ipywidgets fields, and ``_on_test_config`` is the callback the button is
    actually wired to. Leaving the username empty stops the run right after
    the network probe, which is the step under test; ``status_output`` is a
    real ``widgets.Output``, which outside a kernel passes ``print`` straight
    through to stdout, so ``capsys`` sees exactly what a user would.
    """
    widget = EnhancedClusterConfigWidget()
    widget.cluster_type.value = "ssh"
    widget.host_field.value = host
    widget.port_field.value = port
    widget.username_field.value = ""
    widget._on_test_config(None)


def test_the_widget_does_not_tell_the_user_a_host_is_down_on_no_evidence(capsys):
    """The tri-state is only worth having if the caller renders it.

    The three tests above pin ``_test_remote_connectivity``'s return value,
    and they are not enough: deleting the caller's ``if reachable is None:``
    branch leaves all of them green and reproduces the headline defect
    verbatim -- ``.invalid``, which RFC 2606 reserves so that it can never
    resolve, comes back out of the widget as "Cannot reach
    no-such-host.invalid:22 ... Check if the hostname/IP is correct and
    accessible". The hostname is correct. Clustrix never managed to ask.

    What the user reads is the product, so assert on what the user reads.
    """
    _press_test_config("no-such-host.invalid", 22)
    printed = capsys.readouterr().out

    assert "Could not tell whether no-such-host.invalid:22 is reachable" in printed
    assert "NOT evidence that the host is down" in printed
    assert "Cannot reach" not in printed, (
        "the widget claimed a host was unreachable on the strength of a probe "
        "that never ran"
    )


def test_the_widget_still_reports_a_refusal_it_really_measured(capsys):
    """The negative control, or the test above is satisfied by saying nothing.

    Port 1 on the loopback interface is a real TCP connect that is really
    refused, so here the widget has measured the host and must say so plainly.
    """
    _press_test_config("127.0.0.1", 1)
    printed = capsys.readouterr().out

    assert "Cannot reach 127.0.0.1:1" in printed
    assert "Check if the hostname/IP is correct and accessible" in printed
    assert "Could not tell whether" not in printed, (
        "a refusal the probe really measured was downgraded to 'I could not "
        "tell', which is the opposite failure and just as misleading"
    )


def test_a_profile_file_that_could_not_be_read_says_so(tmp_path, caplog):
    """A missing entry in the Load menu is the symptom; silence was the cause.

    The widget offers only files that parse as a profile bundle. A file it
    could not open at all was dropped by the same ``return False`` as a file
    that parsed and turned out to be something else -- so the profile store
    the user is looking for disappears from the menu with nothing said, and
    the two cases call for completely different fixes.

    The trigger is a real permission bit on a real file, not a patched
    ``open``.
    """
    path = tmp_path / "profiles.yml"
    path.write_text("profiles:\n  mine: {}\n")
    os.chmod(path, 0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="clustrix.modern_notebook_widget"):
            offered = ModernClustrixWidget._looks_like_a_profile_bundle(path)
    finally:
        os.chmod(path, 0o600)

    assert offered is False
    messages = [record.getMessage() for record in caplog.records]
    assert any("profiles.yml" in message for message in messages), messages
    assert any(
        "not evidence that it holds no profiles" in message for message in messages
    ), messages

    # And the same file, readable, really is a profile bundle -- so the
    # warning above is about the permission bit and nothing else.
    assert ModernClustrixWidget._looks_like_a_profile_bundle(path) is True


def test_a_file_that_is_simply_not_a_profile_stays_quiet(tmp_path, caplog):
    """A working tree is full of YAML. Warning about all of it is noise.

    This is the answer the handler is entitled to give: the file was read in
    full and is not a profile bundle. Nothing failed, so nothing is said above
    debug.
    """
    path = tmp_path / "not-a-profile.yml"
    path.write_text("profiles: [unclosed\n")

    with caplog.at_level(logging.WARNING, logger="clustrix.modern_notebook_widget"):
        assert ModernClustrixWidget._looks_like_a_profile_bundle(path) is False

    assert caplog.records == [], [r.getMessage() for r in caplog.records]

    # Quiet is not silent. ``return False`` here is the same value the branch
    # above returns for a file that could not be read at all, and the same one
    # a perfectly good profile bundle would get if this parse ever broke; at
    # debug, the reason has to be there for anyone who goes looking for a
    # missing Load-menu entry. Deleting this line left the whole suite green.
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="clustrix.modern_notebook_widget"):
        assert ModernClustrixWidget._looks_like_a_profile_bundle(path) is False

    reasons = [
        record
        for record in caplog.records
        if "Not offering" in record.getMessage()
        and "not-a-profile.yml" in (record.getMessage())
    ]
    assert reasons, [record.getMessage() for record in caplog.records]
    assert [record.levelno for record in reasons] == [logging.DEBUG]


def _distribution_with(tmp_path, name, **files):
    """A real ``importlib.metadata`` distribution backed by real files."""
    directory = tmp_path / f"{name}-1.0.dist-info"
    directory.mkdir()
    (directory / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n")
    for filename, content in files.items():
        (directory / filename).write_bytes(content)
    return importlib.metadata.PathDistribution(directory)


def test_unreadable_top_level_metadata_is_reported(tmp_path, caplog):
    """An empty import-name list is a claim, and a load-bearing one.

    ``_distribution_import_names`` feeds ``unreproducible_module_owners``,
    which exists to *refuse* a submission that reaches into a package the
    worker cannot reinstall. A distribution whose metadata could not be read
    contributes no import names, so the submission is allowed and the job dies
    on the worker at ``import`` -- minutes later, naming a module rather than
    the metadata that could not be read.

    The trigger is real and needs no patching: ``PathDistribution.read_text``
    suppresses the missing-file and permission cases but not a decode failure,
    and a ``top_level.txt`` that is not valid UTF-8 really raises.
    """
    dist = _distribution_with(tmp_path, "brokenmeta", **{"top_level.txt": b"\xff\xfe"})

    with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
        assert _distribution_import_names(dist) == []

    messages = [record.getMessage() for record in caplog.records]
    assert any("top_level.txt" in message for message in messages), messages
    assert any("fail on the worker" in message for message in messages), messages


def test_a_distribution_whose_file_list_cannot_be_read_is_reported(tmp_path, caplog):
    """The fallback path has the same consequence, so it gets the same answer.

    With no ``top_level.txt`` the import names come from ``dist.files``, which
    reads ``RECORD``. Same real trigger, same silence before this.
    """
    dist = _distribution_with(tmp_path, "brokenrecord", RECORD=b"\xff\xfe,,\n")

    with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
        assert _distribution_import_names(dist) == []

    messages = [record.getMessage() for record in caplog.records]
    assert any("files of" in message for message in messages), messages
    assert any("fail on the worker" in message for message in messages), messages


def test_a_readable_distribution_is_read_without_a_word(tmp_path, caplog):
    """The ordinary path must stay silent, or the two warnings above are noise."""
    dist = _distribution_with(tmp_path, "goodmeta", **{"top_level.txt": b"goodmeta\n"})

    with caplog.at_level(logging.WARNING, logger="clustrix.utils"):
        assert _distribution_import_names(dist) == ["goodmeta"]

    assert caplog.records == []


def test_a_transport_failure_is_not_reported_as_a_missing_interpreter(connection):
    """The message told the user to go and install Python on the wrong machine.

    ``resolve_remote_python`` probes for ``pythonX.Y`` with ``command -v``. When
    that probe raised, it answered ``False`` -- indistinguishable from "the
    interpreter is not installed" -- and fell through to a ``RuntimeError``
    stating flatly that there is no matching interpreter on the remote host
    and listing what is there instead. Every word of that is a claim about a
    machine clustrix never managed to ask.

    The trigger is a real closed transport on the real in-process SSH server:
    ``exec_command`` on it really raises.
    """
    config = connection.config
    client = connection.ssh_client
    client.close()

    with pytest.raises(RuntimeError) as raised:
        resolve_remote_python(client, config)

    message = str(raised.value)
    assert "not evidence that" in message, message
    assert "failure of the connection" in message, message
    assert "No python" not in message, (
        "a dead transport still produced the confident claim that the remote "
        "host has no matching interpreter: " + message
    )


def test_a_config_scan_that_failed_is_not_an_empty_config_directory(
    tmp_path, monkeypatch, caplog
):
    """An empty overwrite list is a claim about the filesystem.

    The widget offers "Overwrite: <file>" for every configuration file it can
    find. When the scan itself raised, the list was emptied in silence, which
    reads as "there is nothing here to overwrite" -- and the user is one click
    from writing a new file beside the one they meant to replace.

    The trigger is a real permission bit on a real directory, and it is the
    *parent* that is closed rather than the configuration directory itself:
    ``Path.exists()`` answers False for a path that is not there but
    propagates EACCES for one it is not allowed to look for, and pathlib's
    ``glob`` swallows ``PermissionError`` internally, so closing the
    configuration directory itself would prove nothing.
    """
    outer = tmp_path / "outer"
    config_dir = outer / "conf"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv(CONFIG_DIR_ENV_VAR, str(config_dir))
    monkeypatch.chdir(tmp_path)

    widget = EnhancedClusterConfigWidget()
    os.chmod(outer, 0o000)
    try:
        with caplog.at_level(logging.WARNING, logger="clustrix.notebook_magic_widget"):
            widget._update_existing_files()
    finally:
        os.chmod(outer, 0o700)

    assert widget.save_file_select.options == ("",) or list(
        widget.save_file_select.options
    ) == [""]
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "not because there are no files" in message for message in messages
    ), messages


# ---------------------------------------------------------------------------
# THE LINT. Not the guarantee -- the guarantee is above.
# ---------------------------------------------------------------------------
#
# Read this before trusting a green run of anything below.
#
# This repository has now had five AST guards written to stop silent swallows,
# and all five were defeated: 12 ways, then 30, then 14, then 16, and then this
# one 22 ways out of 24 attempts -- and then, a round later, 7 more, in nothing
# more exotic than the ways an `except` clause can be spelled. That is not five
# unlucky implementations. It is the same result five times, and the conclusion
# it supports is that "did this handler do something useful about the failure?"
# is not decidable by inspecting the handler. A call might report or might be a no-op; following
# it needs whole-program analysis and the callee is often not even in this
# package.
#
# The precedent that actually worked here is
# tests/unit/test_persisted_files_are_private.py, which stopped reading source
# and observed the property instead. So the structure of this module is:
#
#   * the behavioural tests ABOVE are the guarantee. Each drives a real
#     clustrix surface with a real failure -- an unreadable file, a dead
#     socket, a closed SSH transport, metadata that is not valid UTF-8 -- and
#     asserts the failure was *audible*: an exception propagated, or a log
#     record was actually emitted at a level someone watches with the reason
#     in it, or the returned value is one the caller can tell apart from a
#     real answer. A handler cannot pass those by being spelled differently,
#     because the spelling is never examined.
#
#   * everything BELOW is a lint. It is fast, it runs over the whole package
#     including handlers no test can reach, and it is worth having for
#     exactly that. It is *not* evidence that a handler reports, and the 29
#     entries in KNOWN_BLIND_SPOTS are the executable statement of how much
#     it misses -- each asserted to be missed, so the list cannot quietly
#     become optimistic.
#
# The rule it applies is stated as something a handler must *do* rather than
# as a list of bad spellings, because enumerating spellings is what lost the
# earlier rounds; and its reach is measured rather than assumed, in BYPASSES
# (caught), ACCEPTED (correctly ignored) and BLIND_SPOTS (missed, on purpose,
# recorded).

#: Names that catch everything. A handler for either of these, a bare
#: ``except:``, or a tuple containing either, stops every failure.
CATCH_ALL_NAMES = frozenset({"Exception", "BaseException"})

#: The statement nodes that carry ``handlers``/``finalbody``. ``except*``
#: (PEP 654) parses to ``ast.TryStar``, which is *not* an ``ast.Try``, so a
#: scan that tested ``isinstance(node, ast.Try)`` could not see
#: ``except* Exception: pass`` at all. Both 3.11 and 3.12 are in tests.yml, so
#: that shape is reachable on CI today, which is why it is caught rather than
#: recorded. ``ast.TryStar`` does not exist before 3.11 -- and neither does
#: the syntax, so on 3.10 there is nothing to miss.
TRY_NODES = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())

#: What ``contextlib.suppress`` may be called; see ``_suppress_aliases``.
SUPPRESS_NAME = "suppress"

#: Logging methods a handler might use to report. ``exception`` is absent on
#: purpose: it attaches the traceback whatever its arguments are, so it always
#: reports. The rest are content-free when every argument is a constant --
#: ``logger.warning("")`` and ``logger.error("oops")` say nothing about the
#: failure, and restricting this to ``debug``/``info`` was two ways past this
#: lint.
LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "log"}
)

#: Names that pull the current exception out of the interpreter, so a handler
#: that uses one is reporting the failure even without an ``as`` binding.
EXCEPTION_ACCESSORS = frozenset(
    {"format_exc", "exc_info", "print_exc", "print_exception"}
)

#: Keywords that attach the failure to a log record.
EXCEPTION_KEYWORDS = frozenset({"exc_info", "stack_info"})

#: Attributes whose assignment replaces the interpreter's last-resort report.
#:
#: ``sys.excepthook = lambda *a: None`` discards every exception nobody
#: caught, in the whole process, for the rest of its life. So does
#: ``threading.excepthook``, for every thread. Neither is an ``except``
#: handler nor a ``contextlib.suppress`` call, so every family A..L below
#: presupposes something that is simply not present here: this is a swallow
#: with no handler at all, and the guard could not see one until 2026-08-20.
#: Matched on the attribute name alone, exactly as
#: :func:`_is_catch_all_expression` matches ``builtins.Exception`` -- which
#: means ``import sys as s; s.excepthook = ...`` is caught too, and which is
#: the same trade recorded as family K: erring toward reporting.
SILENCING_HOOKS = frozenset({"excepthook", "unraisablehook"})

#: Values that put a replaced hook back, so assigning them is not silencing.
#:
#: The exemption is *receiver-qualified* and the matches above are not, which
#: is not an inconsistency but the same rule applied in both directions:
#: matching ``excepthook`` on the name alone errs toward reporting, and
#: exempting ``__excepthook__`` on the name alone errs away from it.
#: ``sys.excepthook = sys.__excepthook__`` really does put the interpreter's
#: own hook back. ``sys.excepthook = _mine.__excepthook__`` names an attribute
#: of something else entirely that happens to be spelled the same way, and
#: until 2026-08-20 that was a free pass out of the check.
HOOK_RESTORERS = frozenset({"__excepthook__", "__unraisablehook__"})

#: Attributes whose assignment switches the ``logging`` module off wholesale.
#:
#: ``logging.getLogger().disabled = True`` and ``logging.root.disabled = True``
#: silence the root logger; ``logging.Logger.manager.disable = 50`` sets the
#: same global threshold ``logging.disable(50)`` sets. These were recorded as
#: unreachable on the grounds that ``clustrix/modern_notebook_widget.py``
#: assigns ``button.disabled`` six times, so matching ``disabled`` would flag
#: all six. That is an argument against a *name-only* set, and nobody needs
#: one: the check below also requires the object being assigned on to be
#: rooted at the ``logging`` module, which ``button`` is not. The six sites
#: stay unflagged, and ``test_the_lint_finds_no_unrecorded_silent_swallow``
#: scans the real file rather than taking that on trust.
LOGGING_SILENCERS = frozenset({"disabled", "disable"})

#: Statement nodes that bind a name, and so can bind one of the attributes
#: above. ``ast.NamedExpr`` is absent because a walrus target is a plain name
#: by grammar -- ``(sys.excepthook := _quiet)`` is a ``SyntaxError``, which
#: ``test_a_walrus_cannot_bind_an_attribute`` pins rather than assumes.
BINDING_NODES = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.For,
    ast.AsyncFor,
    ast.comprehension,
    ast.withitem,
)

#: ``(module, function)`` calls that turn reporting off for the whole process.
#:
#: ``logging.disable(logging.CRITICAL)`` makes every ``logger.error`` in this
#: package a no-op, which silences the very reports the behavioural tests
#: above assert; ``warnings.simplefilter("ignore")`` and
#: ``warnings.filterwarnings("ignore")`` do it for the four
#: ``profile_manager`` sites that report through ``warnings.warn``.
GLOBAL_SILENCERS = frozenset(
    {
        ("logging", "disable"),
        ("warnings", "simplefilter"),
        ("warnings", "filterwarnings"),
    }
)

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
    ("auth_fallbacks.py", "_colab_password"): (
        "Colab's userdata.get raises for a secret that is simply not set, "
        "which is the ordinary case for every name variant tried here -- the "
        "host-named spellings first, then the hostless ones -- and for any "
        "that do match, a following gate still decides whether the secret "
        "may be released. A missing secret moves on to the next candidate; "
        "if none supplies a password the caller raises rather than "
        "proceeding."
    ),
    ("config.py", "_read_config_bundle"): (
        "A parse failure here is not discarded: returning None hands the file "
        "to load_config, which re-reads it and raises ConfigFileError naming "
        "the file and the underlying reason. The catch-all exists so the "
        "reason is reported once, with the path attached, rather than once "
        "from a detector and again from the loader."
    ),
}

#: ``(module, qualified enclosing name)`` -> the issue tracking it.
#:
#: Separate from JUSTIFIED_SWALLOWS on purpose. These are *defects*, not
#: decisions; they are recorded so the guard stays green without anyone having
#: to pretend they are fine, and an entry here is a promise that the issue
#: exists. The stale-entry test below covers this dict too, so a fix removes
#: the entry rather than leaving a lie behind.
TRACKED_DEFECTS = {}

#: What this lint cannot see. Each one is asserted below, in
#: ``test_the_lint_admits_what_it_cannot_see``, so the list is executable
#: rather than aspirational -- if one of these ever *does* start being caught,
#: that test fails and the entry gets deleted.
#:
#: They fall into twelve root causes, and the first one is the big one:
#:
#: A. **Any call at all counts as reporting.** Six spellings are recorded
#:    below (``_record(exc)`` where ``_record`` is empty, ``errors.append``,
#:    ``int()``, ``NULL_REPORTER.report(exc)``, ``if want_to_log(): pass``,
#:    ``message = str(exc)``). Following a call to decide whether it reports
#:    needs whole-program analysis, and the callee may not even be in this
#:    package. This is why the lint is a lint and the behavioural tests above
#:    are the guarantee.
#: B. **The exception stashed and dropped.** ``_ = exc`` is indistinguishable
#:    from ``failure = exc``, which this package really does and which really
#:    does hand the failure onward. One spelling is recorded below.
#: C. **A log line that mentions a variable instead of the exception.**
#:    ``logger.debug("failed for %s", host)`` passes, because requiring the
#:    exception itself in every log call would flag handlers here that do
#:    explain themselves in prose. One spelling is recorded below.
#: D. **A narrower ``except`` that is broad in practice.** ``except OSError``
#:    around a body that only ever raises ``OSError`` is a catch-all in
#:    effect; the lint reads the name, not the body it guards. One spelling
#:    is recorded below.
#: E. **An exception replaced by a worse one.** ``raise RuntimeError("failed")``
#:    with no ``from exc`` re-raises, so it passes, while still throwing the
#:    cause away. One spelling is recorded below.
#: F. **Code that is not in a ``.py`` file in this package.** The remote job
#:    scripts assembled as strings in ``utils.py`` are never parsed here, and
#:    neither is anything in a dependency. One spelling is recorded below.
#: G. **Two swallows in one function.** Keys are per function, so a justified
#:    site licenses a second, unjustified one beside it. Narrowing the key to
#:    a line number would make every entry rot on the next edit above it.
#:    One spelling is recorded below.
#: H. **Dead code the pruner cannot model.** ``_live_statements`` folds
#:    ``if <constant>`` and ``while <constant>`` and nothing else, so a
#:    ``raise`` that can never run still reads as a re-raise. Seven spellings
#:    are recorded below: a loop over an empty tuple or list, a ``match`` case
#:    that cannot be selected, a nested handler for an exception its body
#:    cannot raise, a membership test in an empty container, and an ``await``
#:    and a ``yield`` that are never reached or never driven. Deciding a
#:    statement is unreachable in general is the halting problem; each guard
#:    added here so far has been defeated by the next spelling, and this
#:    family is recorded rather than chased for that reason. None of the
#:    seven occurs in the package --
#:    ``test_the_lint_finds_no_unrecorded_silent_swallow`` scans for real
#:    handlers, and the behavioural tests in the first half of this module
#:    are what actually guarantee those.
#: I. **An alias bound by a call.** ``_catch_all_aliases`` resolves
#:    ``_E = Exception``, ``_E = (Exception,)``, ``_A, _B = Exception,
#:    ValueError`` and ``from builtins import Exception as _E``. It does not
#:    resolve a binding produced by a *call* -- ``_ERRORS =
#:    tuple([Exception])`` -- and it will not: that is constant propagation
#:    through arbitrary expressions, which is the same whole-program problem
#:    as family A. One spelling is recorded below. This family used to be
#:    written as "an alias bound by anything but a literal", which was false
#:    in the direction that flatters the guard: an annotated binding is a
#:    literal the resolver does not see either. That hole has a different
#:    cause and is family J.
#: J. **A binding the alias resolver never walks.** ``_catch_all_aliases``
#:    iterates ``ast.Assign`` and ``ast.ImportFrom`` and nothing else, so
#:    ``_E: type = Exception`` and ``_E: tuple = (Exception,)`` -- ordinary
#:    annotated assignments, which parse to ``ast.AnnAssign`` -- bind a
#:    catch-all it cannot see, including when the value is itself an alias
#:    imported from ``builtins``. ``except (_E := Exception):`` is missed for
#:    the mirror reason: ``_is_catch_all_expression`` reads ``ast.Name`` and
#:    ``ast.Attribute``, and a walrus is an ``ast.NamedExpr``. Four spellings
#:    are recorded below. Teaching the resolver these four statement forms
#:    would close exactly these four and leave the fifth spelling open, which
#:    is how the five previous guards were lost; they are recorded rather
#:    than chased, and the behavioural tests in the first half of this module
#:    are what guarantee the handlers.
#: K. **A name that only looks like a report.** ``EXCEPTION_ACCESSORS`` is
#:    matched on the attribute name alone, so ``value = SOME.format_exc`` --
#:    an attribute of some unrelated object that happens to share a name with
#:    ``traceback.format_exc`` -- reads as reaching for the interpreter's
#:    current exception. This was filed under family H for a while, which was
#:    wrong in a way worth naming: it is not dead code, it is live code the
#:    lint mis-identifies, and no amount of better dead-branch pruning would
#:    ever catch it. Deciding what ``SOME`` is at that point is constant
#:    propagation through arbitrary expressions, which is family A's
#:    whole-program problem again. One spelling is recorded below.
#: L. **Global suppression reached by a route the names cannot spell.**
#:    ``sys.excepthook = lambda *a: None`` is a swallow with no handler
#:    anywhere in it -- every family above presupposes an ``except`` clause or
#:    a ``contextlib.suppress`` call -- and until 2026-08-20 this lint
#:    returned nothing at all for it. It is caught now, along with
#:    ``threading.excepthook``, ``sys.unraisablehook``, ``logging.disable``
#:    and the two ``warnings`` filters, under aliases and from-imports (see
#:    ``SILENCING_HOOKS`` and ``GLOBAL_SILENCERS``), and through every
#:    statement form that binds a name rather than only through ``a.b = c``:
#:    a tuple or list target, a star, a ``for`` target, a ``with ... as``
#:    (see ``BINDING_NODES``). One comma used to be enough --
#:    ``sys.excepthook, sys.unraisablehook = _quiet, _quiet`` -- because the
#:    check demanded an ``ast.Attribute`` and got an ``ast.Tuple``.
#:    ``logging.getLogger().disabled = True``, ``logging.root.disabled`` and
#:    ``logging.Logger.manager.disable`` are caught too (see
#:    ``LOGGING_SILENCERS``): what is required there is not the attribute
#:    name alone but the object it sits on being rooted at the ``logging``
#:    module, so the six ``button.disabled`` assignments in
#:    ``clustrix/modern_notebook_widget.py`` are untouched. That direction
#:    matters both ways round. Matching a *silencer* on its bare name errs
#:    toward reporting, which is family K's trade and is allowed; exempting
#:    one on its bare name errs the other way, so the exemptions are
#:    receiver-qualified -- ``sys.excepthook = _mine.__excepthook__`` and
#:    ``logging.disable(Foo.NOTSET)`` name attributes of unrelated objects
#:    and used to buy a free pass out of the check.
#:    Four spellings are recorded below that no name here reaches.
#:    ``setattr(sys, "excepthook", _quiet)`` hands the name over as a string,
#:    so there is no attribute to match -- family A's whole-program problem
#:    once more. ``warnings.filters.insert(...)`` mutates the filter list
#:    without calling any of the functions named above.
#:    ``asyncio.get_event_loop().set_exception_handler(lambda l, c: None)``
#:    discards every unhandled failure on that loop through a method call on
#:    an object the lint would have to identify first, which is family A
#:    again -- and unlike ``LOGGING_SILENCERS`` there is no module-rooted
#:    receiver to qualify it by, since the loop is ordinarily held in a
#:    local. ``sys.stderr = open(os.devnull, "w")`` silences by where the
#:    report goes rather than by turning reporting off; whether that is a
#:    swallow depends on the destination, and a capture that is read back and
#:    re-reported is the ordinary reason to assign it, so matching the name
#:    would err in the direction a lint may not. Recorded rather than chased,
#:    on the same grounds as J: teaching the check these four closes exactly
#:    these four.
KNOWN_BLIND_SPOTS = 29


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
    """Names bound to ``Exception``/``BaseException``, however indirectly.

    ``_Exc = Exception`` followed by ``except _Exc:`` is the same handler
    written in two lines, and the guard used to read only the second one.
    Three more indirections were found by red-teaming it on 2026-08-20 and are
    resolved here as well, because each is one line of code to bind and one
    line to catch:

    * ``_ERRORS = (Exception,)`` then ``except _ERRORS:`` -- a *tuple* value,
      which the Name-only branch below could not see.
    * ``_A, _B = Exception, ValueError`` then ``except _A:`` -- a tuple
      target paired elementwise with a tuple value.
    * ``_ERRORS = (Exception,)`` then ``suppress(*_ERRORS)`` -- the same
      binding reached through a starred argument (see
      ``_suppresses_everything``).
    """
    aliases = set(CATCH_ALL_NAMES)

    def names_a_catch_all(value):
        if isinstance(value, ast.Name):
            return value.id in aliases
        if isinstance(value, ast.Attribute):
            return value.attr in CATCH_ALL_NAMES
        if isinstance(value, ast.Tuple):
            return any(names_a_catch_all(element) for element in value.elts)
        return False

    def bind(name):
        if name in aliases:
            return False
        aliases.add(name)
        return True

    changed = True
    while changed:  # a chain of aliases resolves in a couple of passes
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    # `_A, _B = Exception, ValueError` -- pair them off, so
                    # only the element that really is a catch-all is bound.
                    if (
                        isinstance(target, ast.Tuple)
                        and isinstance(node.value, ast.Tuple)
                        and len(target.elts) == len(node.value.elts)
                    ):
                        for element, value in zip(target.elts, node.value.elts):
                            if isinstance(element, ast.Name) and names_a_catch_all(
                                value
                            ):
                                changed |= bind(element.id)
                    elif isinstance(target, ast.Name) and names_a_catch_all(node.value):
                        changed |= bind(target.id)
            elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
                for alias in node.names:
                    if alias.name in CATCH_ALL_NAMES:
                        changed |= bind(alias.asname or alias.name)
    return aliases


def _suppress_aliases(tree):
    """Names ``contextlib.suppress`` is reachable under in this module.

    ``from contextlib import suppress as quiet`` made ``quiet(Exception)``
    invisible, because the check below required the literal spelling.
    """
    names = {SUPPRESS_NAME}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "contextlib":
            for alias in node.names:
                if alias.name == SUPPRESS_NAME:
                    names.add(alias.asname or alias.name)
    return names


def _is_catch_all_expression(item, aliases):
    """One name in an ``except`` clause that stops everything.

    ``ast.Attribute`` is here for ``except builtins.Exception:``, which the
    Name-only test could not see. The attribute name alone is enough: a
    ``foo.Exception`` that is not the builtin would be a class someone chose
    to call ``Exception``, and treating it as a catch-all errs toward
    reporting a handler rather than toward missing one -- the only direction
    a lint may err in.
    """
    if isinstance(item, ast.Name):
        return item.id in aliases
    if isinstance(item, ast.Attribute):
        return item.attr in CATCH_ALL_NAMES
    return False


def _catches_everything(handler, aliases):
    """True for ``except:``, ``except Exception``, and every dressing of it."""
    if handler.type is None:  # a bare `except:` catches more, not less
        return True
    candidates = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    return any(_is_catch_all_expression(item, aliases) for item in candidates)


def _is_contentless_log(call):
    """A logging call that cannot be conveying what went wrong.

    Every argument a constant and no ``exc_info``/``stack_info``: the
    exception is not in it at any level, so ``logger.warning("")`` and
    ``logger.error("oops")`` are as silent as ``logger.debug("")``.
    """
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr not in LOG_METHODS:
        return False
    if any(keyword.arg in EXCEPTION_KEYWORDS for keyword in call.keywords):
        return False
    arguments = list(call.args) + [keyword.value for keyword in call.keywords]
    return all(isinstance(argument, ast.Constant) for argument in arguments)


def _walk_own_scope(node):
    """``ast.walk`` that stops at a nested ``def`` or ``lambda``.

    ``except Exception:`` followed by ``def retry(): raise`` -- a function
    nothing calls -- reads as a re-raise to a plain walk, and did. So does a
    ``lambda: (_ for _ in ()).throw(exc)``. Neither runs.

    This used to open by returning nothing when ``node`` was itself a
    ``FunctionDef``/``AsyncFunctionDef``/``Lambda``. That guard was dead: the
    only caller is :func:`_accounts_for_the_failure`, which walks *statements*
    and skips the two function-definition statement forms before calling here
    (a ``Lambda`` is an expression and can never arrive as a statement), and
    that skip is what keeps a nested ``def``'s body from counting -- removing
    the guard changed no test, while removing the caller's ``continue`` breaks
    two. Deleted rather than left standing as a second, untested spelling of
    the same rule.
    """
    todo = [node]
    while todo:
        current = todo.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            todo.append(child)


def _mentions(node, name):
    """True if ``name`` is read anywhere in ``node``."""
    if node is None or not name:
        return False
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


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
    through. What counts is narrow on purpose, because the previous, broader
    version was defeated by things that are *not* reporting: mentioning the
    bound name anywhere however deadly (``_ = exc``, ``f"{exc}"``, ``None if
    exc else None``), any statement at all that looked like bookkeeping
    (``n += 1``, ``del x``, ``assert True``, ``import os``, ``global FLAG``,
    ``cache[k] = 1``), and a ``raise`` inside a nested ``def`` nothing calls.

    A handler accounts for the failure when it

    * re-raises in its own scope, or yields/awaits out of it;
    * calls something that is not a content-free log (see
      :func:`_is_contentless_log`) -- this is broad, and it is the lint's
      largest blind spot, recorded as such;
    * reaches for the interpreter's current exception
      (``traceback.format_exc`` and friends);
    * or stashes the bound exception somewhere -- ``failure = exc``,
      ``self.error = exc``, ``results[job] = exc`` -- which this package
      really does, and which hands the failure to the caller.

    Anything else -- including every statement that merely changes local or
    even attribute state without carrying the exception -- does not.
    """
    for statement in _live_statements(body):
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Defining a function is not calling it, so neither its body nor
            # its recursion below may count towards this handler.
            continue
        for node in _walk_own_scope(statement):
            if isinstance(node, ast.Raise):
                return True
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Await)):
                return True
            if isinstance(node, ast.Attribute) and node.attr in EXCEPTION_ACCESSORS:
                return True
            if isinstance(node, ast.Call) and not _is_contentless_log(node):
                return True
            if isinstance(
                node, (ast.Assign, ast.AugAssign, ast.AnnAssign)
            ) and _mentions(node.value, bound_name):
                return True
        # Recurse into compound statements the walk above already covered for
        # expressions but whose nested *statements* need dead-branch pruning.
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(statement, field, None)
            if nested and _accounts_for_the_failure(nested, bound_name):
                return True
    return False


def _suppresses_everything(call, aliases, suppress_names):
    """``contextlib.suppress(Exception)`` is ``except Exception: pass``.

    ``suppress_names`` carries the import aliases, and ``ast.Starred`` covers
    ``suppress(*_ERRORS)`` -- both were ways past the literal-name test this
    used to be.
    """
    function = call.func
    name = function.attr if isinstance(function, ast.Attribute) else None
    if name is None:
        name = getattr(function, "id", None)
    if name not in suppress_names:
        return False
    arguments = [
        argument.value if isinstance(argument, ast.Starred) else argument
        for argument in call.args
    ]
    return any(_is_catch_all_expression(argument, aliases) for argument in arguments)


def _silencer_aliases(tree):
    """Names the process-wide silencers are reachable under in this module.

    The same move :func:`_suppress_aliases` makes for ``contextlib.suppress``,
    and for the same reason: ``import logging as lg`` or ``from warnings
    import simplefilter as quiet`` is the identical call written differently,
    and a check that reads only the canonical spelling is one import
    statement away from being decorative.

    Returns the receiver names each module may be spelled with, and the bare
    names a silencer may have been imported under.
    """
    modules = {module for module, _ in GLOBAL_SILENCERS}
    receivers = {module: {module} for module in modules}
    bare = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    receivers[alias.name].add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module in modules:
            for alias in node.names:
                if (node.module, alias.name) in GLOBAL_SILENCERS:
                    bare[alias.asname or alias.name] = (node.module, alias.name)
    return receivers, bare


def _rooted_at(expression, names):
    """Whether ``expression`` is built out of one of ``names``.

    ``logging.root``, ``logging.getLogger()`` and ``logging.Logger.manager``
    are all rooted at ``logging``; ``button`` is rooted at ``button``. Walking
    an attribute, call or subscript chain down to the name it starts from is
    what makes a check *receiver-qualified* rather than name-only, and it is
    the whole difference between flagging ``logging.getLogger().disabled``
    and flagging the six ``button.disabled`` assignments in the widget.
    """
    while True:
        if isinstance(expression, ast.Name):
            return expression.id in names
        if isinstance(expression, ast.Attribute):
            expression = expression.value
        elif isinstance(expression, ast.Call):
            expression = expression.func
        elif isinstance(expression, ast.Subscript):
            expression = expression.value
        else:
            return False


def _is_logging_notset(argument, logging_names):
    """``logging.NOTSET`` or a literal zero -- and nothing that looks like it.

    ``logging.disable(Foo.NOTSET)`` is not a re-enable: ``Foo`` is some other
    object whose attribute happens to share the name, and reading the
    attribute alone exempted it from the check. The receiver has to be the
    ``logging`` module, under whatever name it was imported as.
    """
    if isinstance(argument, ast.Attribute):
        return argument.attr == "NOTSET" and _rooted_at(argument.value, logging_names)
    return isinstance(argument, ast.Constant) and argument.value == 0


def _turns_reporting_back_on(target, call, logging_names):
    """The re-enabling spellings, which must not be flagged.

    ``logging.disable(logging.NOTSET)`` is how a process undoes a previous
    ``logging.disable``, and ``warnings.simplefilter("error")`` is the
    opposite of silencing. Flagging those would be wrong rather than merely
    noisy. An argument this cannot read is *not* treated as a re-enable: the
    only direction a lint may err in is toward reporting, and an exemption
    runs the other way -- which is why the ``NOTSET`` half is qualified by
    its receiver.
    """
    argument = call.args[0] if call.args else None
    if target == ("logging", "disable"):
        # No argument at all defaults to CRITICAL, so it silences.
        return _is_logging_notset(argument, logging_names)
    if isinstance(argument, ast.Constant):
        return argument.value != "ignore"
    return False


def _silences_the_process(call, receivers, bare):
    """``logging.disable(...)`` and the ``warnings`` filters, however spelled."""
    function = call.func
    if isinstance(function, ast.Attribute):
        holder = function.value
        holder_name = (
            holder.id if isinstance(holder, ast.Name) else getattr(holder, "attr", None)
        )
        target = next(
            (
                (module, function.attr)
                for module, names in receivers.items()
                if holder_name in names and (module, function.attr) in GLOBAL_SILENCERS
            ),
            None,
        )
    elif isinstance(function, ast.Name):
        target = bare.get(function.id)
    else:
        target = None
    if target is None:
        return False
    return not _turns_reporting_back_on(target, call, receivers["logging"])


def _flatten_target(target):
    """The individual bindings inside an assignment target.

    A tuple, a list and a star are containers; the binding is what is inside
    them. Recursive, because ``(a, (b, c)) = ...`` nests.
    """
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _flatten_target(element)
    elif isinstance(target, ast.Starred):
        yield from _flatten_target(target.value)
    elif target is not None:
        yield target


def _bound_targets(node):
    """Every name or attribute ``node`` binds, however the binding is spelled.

    ``sys.excepthook = _quiet`` was the only shape the previous check
    modelled -- it required ``isinstance(target, ast.Attribute)`` -- so one
    comma put the same assignment out of its reach: ``sys.excepthook,
    sys.unraisablehook = _quiet, _quiet`` hands it an ``ast.Tuple`` and it
    walked straight past. So did ``[sys.excepthook] = [_quiet]``, ``for
    sys.excepthook in hooks:`` and ``with _opened() as sys.excepthook:``,
    all of which are ordinary Python and all of which bind the attribute.
    Enumerating those four spellings is what the rest of this module is a
    monument to not doing, so targets are *flattened* out of their containers
    and every statement form that has one is walked.
    """
    if isinstance(node, ast.Assign):
        raw = node.targets
    elif isinstance(node, ast.withitem):
        raw = [node.optional_vars]
    else:
        raw = [node.target]
    return [bound for target in raw for bound in _flatten_target(target)]


def _bound_value(node, target):
    """The value ``target`` receives, when it can be read at all.

    A single-target assignment gives it directly, and ``a, b = x, y`` gives
    it positionally when both sides are the same length. Everything else --
    unpacking whatever a call returned, a ``for`` target, a ``with ... as`` --
    has no readable value, and ``None`` means the exemptions below do not
    apply. That is the direction a lint may err in.
    """
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return None
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for candidate in targets:
        if candidate is target:
            return node.value
        if (
            isinstance(candidate, (ast.Tuple, ast.List))
            and isinstance(node.value, (ast.Tuple, ast.List))
            and len(candidate.elts) == len(node.value.elts)
        ):
            for element, paired in zip(candidate.elts, node.value.elts):
                if element is target:
                    return paired
    return None


def _puts_the_hook_back(target, value):
    """``sys.excepthook = sys.__excepthook__``, and only that.

    Receiver-qualified: the restorer has to be named on the same object the
    hook is being assigned on. ``sys.excepthook = _mine.__excepthook__`` is
    an attribute of something else that happens to share the name, and
    granting it the exemption on the strength of that name was a way out of
    the check rather than a way into it.
    """
    return (
        isinstance(value, ast.Attribute)
        and value.attr in HOOK_RESTORERS
        and ast.dump(target.value) == ast.dump(value.value)
    )


def _turns_logging_back_on(value, logging_names):
    """``disabled = False`` and ``disable = 0`` / ``logging.NOTSET``.

    The same trade :func:`_turns_reporting_back_on` makes: switching
    reporting on is not silencing, and a value this cannot read is not
    treated as switching it on.
    """
    if isinstance(value, ast.Constant):
        return not value.value
    return _is_logging_notset(value, logging_names)


def _silences_uncaught_exceptions(node, logging_names):
    """Every binding in ``node`` that replaces a report with nothing.

    Yields ``(target, shape)``: the target rather than the statement, because
    ``ast.withitem`` and ``ast.comprehension`` carry no line number of their
    own and the binding does.
    """
    for target in _bound_targets(node):
        if not isinstance(target, ast.Attribute):
            continue
        value = _bound_value(node, target)
        if target.attr in SILENCING_HOOKS:
            if not _puts_the_hook_back(target, value):
                yield target, (
                    "an assignment to a process-wide exception hook, which "
                    "discards every failure nobody caught"
                )
        elif (
            target.attr in LOGGING_SILENCERS
            and _rooted_at(target.value, logging_names)
            and not _turns_logging_back_on(value, logging_names)
        ):
            yield target, (
                "an assignment that switches the logging module off for the "
                "whole process"
            )


def find_silent_swallows(source, module):
    """Every place in ``source`` where a failure disappears without a word.

    Exposed as a function so the repository scan and the bypass tests below
    exercise exactly the same code; a guard whose reach is only ever measured
    against the code it already passes on is not measured at all.
    """
    tree = ast.parse(source, filename=module)
    aliases = _catch_all_aliases(tree)
    suppress_names = _suppress_aliases(tree)
    receivers, bare_silencers = _silencer_aliases(tree)
    qualnames = _qualified_names(tree)
    found = []

    for node in ast.walk(tree):
        if isinstance(node, TRY_NODES):
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
        elif isinstance(node, ast.Call) and _suppresses_everything(
            node, aliases, suppress_names
        ):
            found.append(
                Swallow(
                    module,
                    _enclosing_qualname(qualnames, node),
                    node.lineno,
                    "contextlib.suppress over a catch-all",
                )
            )
        elif isinstance(node, ast.Call) and _silences_the_process(
            node, receivers, bare_silencers
        ):
            found.append(
                Swallow(
                    module,
                    _enclosing_qualname(qualnames, node),
                    node.lineno,
                    "a call that turns reporting off for the whole process",
                )
            )
        elif isinstance(node, BINDING_NODES):
            for target, shape in _silences_uncaught_exceptions(
                node, receivers["logging"]
            ):
                found.append(
                    Swallow(
                        module,
                        _enclosing_qualname(qualnames, target),
                        target.lineno,
                        shape,
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


def test_the_lint_finds_no_unrecorded_silent_swallow():
    """No handler may discard the reason a failure happened.

    This is the executable form of the issue's own acceptance criterion: every
    swallow either re-raises, or says what went wrong, or is recorded -- as a
    decision in JUSTIFIED_SWALLOWS, or as a defect with an issue number in
    TRACKED_DEFECTS.

    It is also the only cover the handlers no behavioural test can reach have
    -- and there are fewer of those than the previous round assumed. Both
    ``_distribution_import_names`` sites and the remote interpreter probe were
    marked ``# pragma: no cover - unreachable``; all three are now driven by
    real tests above (invalid UTF-8 in ``top_level.txt`` and ``RECORD``, a
    closed SSH transport), and the pragmas are gone. "Unreachable" is worth
    checking before it is written down.
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
    "a re-raise in a loop that never runs": """
        def f():
            try:
                g()
            except Exception:
                while False:
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
    # ---- found by red-teaming the inverted rule, 2026-08-20 --------------
    # Five spellings that only *mention* the bound name. The rule counted any
    # mention as reporting; none of these conveys anything anywhere.
    "an f-string built from the exception and dropped": """
        def f():
            try:
                g()
            except Exception as exc:
                f"{exc}"
    """,
    "the bound name as a bare expression statement": """
        def f():
            try:
                g()
            except Exception as exc:
                exc
    """,
    "the exception in a conditional expression that is thrown away": """
        def f():
            try:
                g()
            except Exception as exc:
                None if exc else None
    """,
    # Six statements the rule treated as unconditional accounting. None of
    # them tells anyone anything.
    "an augmented assignment to a dead local": """
        def f():
            try:
                g()
            except Exception:
                n = 0
                n += 1
    """,
    "del": """
        def f():
            x = 1
            try:
                g()
            except Exception:
                del x
    """,
    "an assert that cannot fail": """
        def f():
            try:
                g()
            except Exception:
                assert True
    """,
    "an import": """
        def f():
            try:
                g()
            except Exception:
                import os
    """,
    "a global declaration": """
        FLAG = None

        def f():
            try:
                g()
            except Exception:
                global FLAG
    """,
    "a subscript assignment that does not carry the exception": """
        def f(cache, key):
            try:
                g()
            except Exception:
                cache[key] = 1
    """,
    "an attribute assignment that does not carry the exception": """
        class C:
            def f(self):
                try:
                    g()
                except Exception:
                    self.ok = False
    """,
    # A raise the interpreter will never reach.
    "a raise inside a nested def nothing calls": """
        def f():
            try:
                g()
            except Exception:
                def retry():
                    raise
    """,
    "a raise inside a nested async def nothing awaits": """
        def f():
            try:
                g()
            except Exception:
                async def retry():
                    raise
    """,
    # A log record at a level people do watch that still says nothing.
    "an empty warning": """
        def f():
            try:
                g()
            except Exception:
                logger.warning("")
    """,
    "a constant error line with no exception in it": """
        def f():
            try:
                g()
            except Exception:
                logger.error("oops")
    """,
    # Two that were already caught, kept so a regression in either shows up
    # here rather than in the package.
    "contextlib.suppress nested inside a handler": """
        import contextlib

        def f():
            try:
                g()
            except Exception:
                with contextlib.suppress(Exception):
                    h()
    """,
    "a nested try whose handler does nothing": """
        def f():
            try:
                g()
            except Exception:
                try:
                    h()
                except Exception:
                    pass
    """,
    # ---- found by red-teaming the *spelling* of the clause, 2026-08-20 ---
    # Seven shapes, none of which the guard modelled: it recognised a bare
    # ``Name`` and nothing else. These six are one line of AST each to catch,
    # which is the whole reason they are caught rather than recorded -- the
    # families in BLIND_SPOTS are the ones that need whole-program analysis,
    # and these need none. The seventh, ``except*``, is added below the dict
    # because it is a syntax error before 3.11.
    "except builtins.Exception (dotted)": """
        import builtins

        def f():
            try:
                g()
            except builtins.Exception:
                pass
    """,
    "a name bound to a tuple containing Exception": """
        _ERRORS = (Exception,)

        def f():
            try:
                g()
            except _ERRORS:
                pass
    """,
    "a name bound by tuple unpacking": """
        _A, _B = Exception, ValueError

        def f():
            try:
                g()
            except _A:
                pass
    """,
    "contextlib.suppress imported under another name": """
        from contextlib import suppress as quiet

        def f():
            with quiet(Exception):
                g()
    """,
    "suppress over a starred tuple of exceptions": """
        from contextlib import suppress

        _ERRORS = (Exception,)

        def f():
            with suppress(*_ERRORS):
                g()
    """,
    "suppress over a dotted Exception": """
        import builtins
        import contextlib

        def f():
            with contextlib.suppress(builtins.Exception):
                g()
    """,
    # Global suppression: a swallow that belongs to no family below, because
    # every one of them presupposes an `except` handler or a `suppress` call
    # and none of these has either. `sys.excepthook = lambda *a: None`
    # discarded every uncaught exception in the process and this lint returned
    # nothing at all; `excepthook` appeared nowhere in it.
    "sys.excepthook replaced with a no-op": """
        import sys

        sys.excepthook = lambda *args: None
    """,
    "threading.excepthook replaced with a no-op": """
        import threading

        def _quiet(args):
            pass

        threading.excepthook = _quiet
    """,
    "an exception hook silenced under an import alias": """
        import sys as _s

        _s.excepthook = lambda *args: None
    """,
    "sys.unraisablehook replaced with a no-op": """
        import sys

        sys.unraisablehook = lambda unraisable: None
    """,
    "the hook assigned inside a function rather than at module level": """
        import sys

        def quieten():
            sys.excepthook = lambda *args: None
    """,
    "logging.disable over everything": """
        import logging

        logging.disable(logging.CRITICAL)
    """,
    "logging.disable with no argument at all": """
        import logging

        logging.disable()
    """,
    "logging.disable reached through a from-import": """
        from logging import disable

        disable(50)
    """,
    "logging.disable reached through a module alias": """
        import logging as _lg

        _lg.disable(_lg.CRITICAL)
    """,
    "warnings.simplefilter over everything": """
        import warnings

        warnings.simplefilter("ignore")
    """,
    "warnings.filterwarnings over everything": """
        import warnings

        warnings.filterwarnings("ignore")
    """,
    "a warnings filter whose action cannot be read": """
        import warnings

        warnings.simplefilter(ACTION)
    """,
    # ---- found by red-teaming the *binding*, 2026-08-20 -------------------
    # The hook check demanded `isinstance(target, ast.Attribute)`, so every
    # spelling that wraps the target in a container or puts it somewhere
    # other than an `=` was invisible. One comma was enough. These are one
    # AST walk each, not whole-program analysis, which is why they are caught
    # rather than recorded.
    "two hooks silenced by one tuple assignment": """
        import sys as _s

        def _quiet(*args):
            pass

        _s.excepthook, _s.unraisablehook = _quiet, _quiet
    """,
    "a hook silenced through a list target": """
        import sys

        [sys.excepthook] = [lambda *args: None]
    """,
    "a hook silenced through a starred target": """
        import sys

        sys.excepthook, *rest = hooks
    """,
    "a hook rebound by a for loop": """
        import sys

        for sys.excepthook in hooks:
            pass
    """,
    "a hook rebound by a with statement": """
        import sys

        with _opened() as sys.excepthook:
            pass
    """,
    "a hook rebound inside a comprehension": """
        import sys

        _ = [None for sys.excepthook in hooks]
    """,
    "a hook silenced by an annotated assignment": """
        import sys

        sys.excepthook: object = lambda *args: None
    """,
    # The exemptions, red-teamed the same way: each was granted on an
    # attribute name with no check of the object it was named on.
    "a restorer belonging to some other object entirely": """
        import sys

        sys.excepthook = _mine.__excepthook__
    """,
    "logging.disable exempted by an unrelated NOTSET": """
        import logging

        logging.disable(Foo.NOTSET)
    """,
    # Receiver-qualified `disabled`, which used to be recorded as
    # unreachable because a name-only match would have flagged
    # `button.disabled`. Rooting the check at the logging module catches
    # these three and none of those six.
    "the root logger switched off through getLogger": """
        import logging

        logging.getLogger().disabled = True
    """,
    "the root logger switched off through logging.root": """
        import logging

        logging.root.disabled = True
    """,
    "the global logging threshold set through the manager": """
        import logging

        logging.Logger.manager.disable = 50
    """,
}

if hasattr(ast, "TryStar"):  # PEP 654; the syntax does not parse before 3.11
    # This one reaches CI: tests.yml runs 3.11 and 3.12. ``except*`` parses to
    # ``ast.TryStar``, which is not an ``ast.Try``, so the scan walked straight
    # past it -- a whole statement form the guard could not see. It cannot be
    # exercised on 3.10, where the syntax does not exist and so neither does
    # the hole.
    BYPASSES["except* Exception (PEP 654)"] = """
        def f():
            try:
                g()
            except* Exception:
                pass
    """


@pytest.mark.parametrize("bypass", sorted(BYPASSES), ids=sorted(BYPASSES))
def test_the_lint_catches_every_known_bypass(bypass):
    """Each of these was, at some point, a way to swallow a failure silently.

    Written as data so a newly discovered spelling is one entry rather than
    one test, and so the control at the top proves the harness itself works.
    """
    source = textwrap.dedent(BYPASSES[bypass])

    found = find_silent_swallows(source, "probe.py")

    assert found, f"the lint does not catch: {bypass}"


def test_the_scan_looks_at_every_statement_form_that_has_handlers():
    """``except*`` is a second statement node, not a spelling of the first.

    The bypass entry above can only be collected on 3.11+, because the syntax
    is a parse error before that. This assertion runs everywhere and pins the
    wiring: whenever the interpreter has ``ast.TryStar``, the scan must be
    looking at it. Without that, ``except* Exception: pass`` is invisible on
    the two interpreters CI actually runs.
    """
    assert ast.Try in TRY_NODES
    if hasattr(ast, "TryStar"):
        assert ast.TryStar in TRY_NODES
    else:
        assert TRY_NODES == (ast.Try,)


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


#: Code that genuinely does report the failure, or that turns reporting back
#: on. A guard that flags these is a guard people will delete, so the
#: false-positive side is tested too.
ACCEPTED = {
    "re-raise": """
        def f():
            try:
                g()
            except Exception:
                raise
    """,
    "a re-raise in a loop that does run": """
        def f():
            try:
                g()
            except Exception:
                while True:
                    raise
    """,
    "a re-raise in the else of a loop that never runs": """
        def f():
            try:
                g()
            except Exception:
                while False:
                    pass
                else:
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
    # The re-enabling half of the global-suppression check above. Flagging
    # these would be wrong rather than merely noisy: each one turns reporting
    # back *on*, and a lint that cannot tell the two apart is a lint people
    # switch off.
    "logging.disable(logging.NOTSET) turns reporting back on": """
        import logging

        logging.disable(logging.NOTSET)
    """,
    "logging.disable(0) turns reporting back on": """
        import logging

        logging.disable(0)
    """,
    "a warnings filter that makes warnings louder": """
        import warnings

        warnings.simplefilter("error")
    """,
    "putting the interpreter's own excepthook back": """
        import sys

        sys.excepthook = sys.__excepthook__
    """,
    "an unrelated function that happens to be called disable": """
        import mymodule

        mymodule.disable(everything)
    """,
    # `disabled` is matched only on an object rooted at the logging module,
    # so the widget's six button assignments stay quiet. This is the case
    # that was used to argue the whole check could not exist.
    "a widget button being greyed out": """
        def _lock(button):
            button.disabled = True
    """,
    "a widget button being switched back on": """
        def _unlock(button):
            button.disabled = False
    """,
    "the root logger switched back on": """
        import logging

        logging.getLogger().disabled = False
    """,
    "the global logging threshold cleared": """
        import logging

        logging.Logger.manager.disable = logging.NOTSET
    """,
    "putting the interpreter's own hook back under an alias": """
        import sys as _s

        _s.excepthook = _s.__excepthook__
    """,
}


@pytest.mark.parametrize("accepted", sorted(ACCEPTED), ids=sorted(ACCEPTED))
def test_the_lint_stays_quiet_on_handlers_that_do_report(accepted):
    source = textwrap.dedent(ACCEPTED[accepted])

    assert find_silent_swallows(source, "probe.py") == []


#: The bypasses that remain open, kept next to the lettered prose in
#: KNOWN_BLIND_SPOTS so the two cannot drift apart.
BLIND_SPOTS = {
    # A. any call at all reads as reporting
    "a helper that shrugs on the handler's behalf": (
        "A",
        """
        def _record(exc):
            pass

        def f():
            try:
                g()
            except Exception as exc:
                _record(exc)
    """,
    ),
    "a bookkeeping call that reports nowhere": (
        "A",
        """
        def f(errors):
            try:
                g()
            except Exception as exc:
                errors.append(exc)
    """,
    ),
    "a call with no arguments and no effect": (
        "A",
        """
        def f():
            try:
                g()
            except Exception:
                int()
    """,
    ),
    "a reporter object that reports nowhere": (
        "A",
        """
        class _Null:
            def report(self, exc):
                pass

        NULL_REPORTER = _Null()

        def f():
            try:
                g()
            except Exception as exc:
                NULL_REPORTER.report(exc)
    """,
    ),
    "a call in a condition whose body is empty": (
        "A",
        """
        def f():
            try:
                g()
            except Exception:
                if want_to_log():
                    pass
    """,
    ),
    "a call that only formats the exception and drops it": (
        "A",
        """
        def f():
            try:
                g()
            except Exception as exc:
                message = str(exc)
    """,
    ),
    # B. the exception stashed and then dropped
    "the exception assigned to a throwaway": (
        "B",
        """
        def f():
            try:
                g()
            except Exception as exc:
                _ = exc
    """,
    ),
    # C. a log line that names something other than the exception
    "a log line that names a variable instead of the exception": (
        "C",
        """
        def f(host):
            try:
                g()
            except Exception:
                logger.debug("failed for %s", host)
    """,
    ),
    # D. a narrower except that is broad in practice
    "a narrower except that is broad in practice": (
        "D",
        """
        def f():
            try:
                open("/etc/hosts")
            except OSError:
                pass
    """,
    ),
    # E. the exception replaced by a worse one
    "an exception replaced by a worse one": (
        "E",
        """
        def f():
            try:
                g()
            except Exception:
                raise RuntimeError("failed")
    """,
    ),
    # F. code that is not in a .py file in this package
    "code assembled as a string and never parsed here": (
        "F",
        """
        REMOTE = '''
        try:
            main()
        except Exception:
            pass
        '''
    """,
    ),
    # G. two swallows in one function
    "a second, unjustified swallow beside a justified one": (
        "G",
        """
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
    ),
    # H. dead code the pruner cannot model
    "a raise in a loop over an empty tuple": (
        "H",
        """
        def f():
            try:
                g()
            except Exception:
                for _ in ():
                    raise
    """,
    ),
    "a raise in a match case that can never be selected": (
        "H",
        """
        def f():
            try:
                g()
            except Exception:
                match 0:
                    case 1:
                        raise
    """,
    ),
    "a raise in a nested handler that can never fire": (
        "H",
        """
        def f():
            try:
                g()
            except Exception:
                try:
                    pass
                except ZeroDivisionError:
                    raise
    """,
    ),
    "a raise guarded by a membership test in an empty container": (
        "H",
        """
        def f():
            try:
                g()
            except Exception:
                if 0 in ():
                    raise
    """,
    ),
    "a raise under a with block in a loop over an empty list": (
        "H",
        """
        def f(x):
            try:
                g()
            except Exception:
                for _ in []:
                    with x:
                        raise
    """,
    ),
    "an await the empty loop around it never reaches": (
        "H",
        """
        async def f():
            try:
                g()
            except Exception:
                for _ in ():
                    await h()
    """,
    ),
    "a yield in a generator nobody drains": (
        "H",
        """
        def f():
            try:
                g()
            except Exception:
                yield 1
    """,
    ),
    # I. an alias bound by a call
    "a name bound to a tuple built by a call": (
        "I",
        """
        _ERRORS = tuple([Exception])

        def f():
            try:
                g()
            except _ERRORS:
                pass
    """,
    ),
    # J. a binding the alias resolver never walks
    "a catch-all bound by an annotated assignment": (
        "J",
        """
        _E: type = Exception

        def f():
            try:
                g()
            except _E:
                pass
    """,
    ),
    "a catch-all tuple bound by an annotated assignment": (
        "J",
        """
        _E: tuple = (Exception,)

        def f():
            try:
                g()
            except _E:
                pass
    """,
    ),
    "an annotated binding chained through a builtins import": (
        "J",
        """
        from builtins import Exception as _B

        _E: type = _B

        def f():
            try:
                g()
            except _E:
                pass
    """,
    ),
    "a catch-all bound by a walrus inside the except clause": (
        "J",
        """
        def f():
            try:
                g()
            except (_E := Exception):
                pass
    """,
    ),
    # K. a name that only looks like a report
    "an exception accessor named on an unrelated object": (
        "K",
        """
        def f(SOME):
            try:
                g()
            except Exception:
                value = SOME.format_exc
    """,
    ),
    # L. global suppression reached by a route the names cannot spell
    "an exception hook replaced through setattr": (
        "L",
        """
        import sys

        setattr(sys, "excepthook", lambda *args: None)
    """,
    ),
    "the warnings filter list mutated in place": (
        "L",
        """
        import warnings

        warnings.filters.insert(0, ("ignore", None, Warning, "", 0))
    """,
    ),
    "an asyncio loop told to drop every unhandled failure": (
        "L",
        """
        import asyncio

        asyncio.get_event_loop().set_exception_handler(lambda loop, ctx: None)
    """,
    ),
    "the standard error stream pointed at the void": (
        "L",
        """
        import os
        import sys

        sys.stderr = open(os.devnull, "w")
    """,
    ),
}


@pytest.mark.parametrize("spot", sorted(BLIND_SPOTS), ids=sorted(BLIND_SPOTS))
def test_the_lint_admits_what_it_cannot_see(spot):
    """These are *not* caught, and saying so is the point.

    A guard that looks stronger than it is invites exactly the commit it was
    meant to prevent. Each entry here is documented in KNOWN_BLIND_SPOTS with
    the reason it stays open. If one starts being caught, this test fails --
    which is the signal to delete the entry and the prose together, not to
    relax the guard.
    """
    source = textwrap.dedent(BLIND_SPOTS[spot][1])
    found = find_silent_swallows(source, "executor_core.py")
    unrecorded = [swallow for swallow in found if swallow.key not in JUSTIFIED_SWALLOWS]

    assert not unrecorded, (
        f"the lint now catches {spot!r}; delete it from BLIND_SPOTS and from "
        "the KNOWN_BLIND_SPOTS prose"
    )


#: Number words the prose above uses for counts. Digits are read directly.
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _module_source():
    """This module's own text, which is the thing under test below."""
    return pathlib.Path(__file__).read_text(encoding="utf-8")


#: What a comment marker is. One definition, consulted by both readers of
#: this module's prose.
#:
#: Three rounds of review each found a fabricated count hiding somewhere, and
#: each fix closed exactly the spelling it was written for. The cause was
#: structural rather than a missing case: :func:`_flattened_source` blanked
#: ``#:`` and ``#``, while :func:`_family_paragraphs` stripped only ``^#:``
#: at the start of a line, so any marker one reader normalised and the other
#: did not was a place to hide. A count written as
#:
#:     #:    A further <N>
#:     # spellings are recorded below for the resolver family.
#:
#: (The number is elided as ``<N>`` for the same reason
#: ``test_no_spelling_count_is_stated_outside_a_family_paragraph`` elides it:
#: writing that sentence with a real number anywhere but inside a family's
#: own paragraph is what this file forbids.)
#:
#: was visible to the flat reader -- which is why no *stray* was reported,
#: the sentence sitting inside family A's span -- and invisible to family A's
#: own paragraph, which still saw one count and agreed with the entries. The
#: hole is not "the bare ``#``"; the hole is two readers disagreeing, and any
#: marker they disagree about reopens it.
#:
#: So there is one definition and both readers call it. Blanking is
#: length-preserving, because :func:`_flattened_source` reports strays by
#: line number and an offset into the flattened text has to be an offset into
#: the file. ``#:`` is blanked as a unit and not one character at a time:
#: leaving the colon behind splits the sentence just as effectively as the
#: newline did, and an earlier draft did exactly that -- it found 8 of the 12
#: counts, and the four it missed were the wrapped ones it exists for. That
#: regression is pinned directly by
#: ``test_a_comment_marker_is_blanked_as_a_unit``, so the normaliser cannot
#: quietly go back to matching characters.
_COMMENT_MARKER = re.compile(r"#:?|\n")


def _blank_comment_markers(text):
    """``text`` with every comment marker and newline replaced by spaces.

    Character for character the same length as its input. Flattening is what
    makes a count sentence findable *wherever* it is written: the prose wraps
    across ``#:`` lines and docstrings wrap across plain ones, so "Six
    spellings are recorded" followed by "below" on the next line is one
    sentence, and a scan that reads a line at a time cannot see it. That is
    not a hypothetical -- families here really do state their count across a
    line break.
    """
    return _COMMENT_MARKER.sub(lambda hit: " " * len(hit.group(0)), text)


def _flattened_source():
    """This module's text with the comment markers taken out of the way."""
    return _blank_comment_markers(_module_source())


def _prose_count(pattern):
    """The number the module's own text states at ``pattern``."""
    source = _module_source()
    matches = set(re.findall(pattern, source))
    assert matches, f"the prose no longer states a count matching {pattern!r}"
    assert len(matches) == 1, f"the prose states {matches} for {pattern!r}"
    token = matches.pop()
    return int(token) if token.isdigit() else _NUMBER_WORDS[token]


def _family_counts():
    """How many spellings each family really has, counted from the list."""
    counts: dict = {}
    for family, _ in BLIND_SPOTS.values():
        counts[family] = counts.get(family, 0) + 1
    return counts


def _family_paragraph_spans():
    """Where each family letter's write-up starts and stops in this file.

    Offsets rather than text, because two different questions are asked of
    them: what a family says about itself, and whether anything *outside*
    every family says the same kind of thing. The second question is the one
    the previous round got wrong.
    """
    source = _module_source()
    marker = re.compile(r"^#: ([A-Z])\. \*\*", re.MULTILINE)
    hits = list(marker.finditer(source))
    assert hits, "the lettered prose above KNOWN_BLIND_SPOTS is gone"
    stop_at = source.index("KNOWN_BLIND_SPOTS = ", hits[-1].start())
    spans = {}
    for index, hit in enumerate(hits):
        stop = hits[index + 1].start() if index + 1 < len(hits) else stop_at
        spans[hit.group(1)] = (hit.start(), stop)
    return spans


def _family_paragraphs():
    """The prose paragraph belonging to each family letter.

    Sliced out of this module's own text, so a count stated inside a family's
    write-up is attributed to that family and to no other. Comparing a
    number to the list it claims to describe is the whole point: the first
    version of the test below checked only the entry total, so family A
    could say "Seven" while holding six, and a family with no entries at all
    could claim nine.
    """
    source = _module_source()
    paragraphs = {}
    for family, (start, stop) in _family_paragraph_spans().items():
        # The comment markers and the line wrapping are formatting, not
        # content: "Six spellings are recorded" then "#:    below" on the next
        # line is one sentence. Normalised by the *same* function the flat
        # reader uses, so the only remaining difference between the two is
        # runs of whitespace -- which ``_SPELLING_COUNT`` matches with
        # ``\s+`` and therefore cannot tell apart. This function used to
        # strip ``^#:\s*`` and nothing else, which disagreed with the flat
        # reader about every other spelling of a marker.
        blanked = _blank_comment_markers(source[start:stop])
        paragraphs[family] = " ".join(blanked.split())
    return paragraphs


#: How a family states how many spellings it has. Every family must state it
#: exactly once, so a fabricated count cannot be added beside a true one --
#: and, by ``test_no_spelling_count_is_stated_outside_a_family_paragraph``,
#: nowhere else in this file may state one at all.
#:
#: ``\s+`` rather than a literal space because this is run against
#: :func:`_flattened_source`, where a line break inside the sentence has
#: become a run of spaces.
_SPELLING_COUNT = re.compile(r"(\w+)\s+spellings?\s+(?:are|is)\s+recorded\s+below")


@pytest.mark.parametrize(
    "family", sorted(_family_paragraphs()), ids=sorted(_family_paragraphs())
)
def test_every_family_states_how_many_spellings_it_has(family):
    """The per-family arithmetic, derived rather than asserted alongside.

    Two mutations proved this was needed and neither was exotic. Changing
    family A's "Six spellings" to "Seven" survived the whole suite. Adding to
    family E -- which has one -- a wholly fabricated sentence claiming nine
    survived it too. Only the entry total was ever checked, and a total cannot
    see a number move between families or appear out of nothing.

    So the family letter now lives on the entry, in ``BLIND_SPOTS``, and the
    number in the prose is compared against a count of the entries carrying
    that letter. Exactly one count per family: two would let a false one sit
    beside a true one.
    """
    counts = _family_counts()
    paragraph = _family_paragraphs()[family]
    stated = _SPELLING_COUNT.findall(paragraph)
    assert len(stated) == 1, (
        f"family {family} states {stated} spelling counts; it must state " "exactly one"
    )
    token = stated[0]
    number = int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]
    assert number == counts[family], (
        f"family {family} claims {token}, and {counts[family]} entries in "
        "BLIND_SPOTS carry that letter"
    )


def test_no_spelling_count_is_stated_outside_a_family_paragraph():
    """A count the family paragraphs do not contain is a count nobody checks.

    This is the third round on the same claim, and the previous two fixes each
    moved the hole rather than closing it. The second round made
    ``test_every_family_states_how_many_spellings_it_has`` read the prose --
    but it reads only what :func:`_family_paragraph_spans` hands it, which
    starts at the first ``#: A. **`` marker. So a reviewer inserted

        #: <N> spellings are recorded below for the resolver family.

    one line *above* that marker -- inside the lettered block the test is
    named for -- and the whole suite came back byte-identical to baseline.
    (The number is elided as ``<N>`` above only because this test now forbids
    writing that sentence anywhere but inside a family's own paragraph, this
    docstring included -- which is the fix demonstrating itself.)
    The same held for a count invented in the narrative two hundred lines
    higher up. Position, not content, was doing the exempting.

    So position is taken out of it here: every sentence anywhere in this file
    that states a spelling count must lie inside some family's paragraph, and
    :func:`_family_paragraph_spans` is the same function the per-family test
    uses, so the two cannot disagree about where a paragraph ends. Together
    with that test's "exactly one per family", the arithmetic is total --
    there are as many such sentences in the module as there are families, each
    one inside its own family, each one equal to the entries carrying that
    letter. Neither test can be satisfied by putting a number somewhere the
    other does not look.

    Stated limitation, because this module does not get to have an unstated
    one: what is checked is the sentence *form* in ``_SPELLING_COUNT``. A
    count phrased some other way -- "family E has nine of them" -- is prose
    nothing reads, exactly as a family whose description is wrong rather than
    whose arithmetic is wrong is caught only indirectly. Recognising more
    phrasings would be enumerating spellings, which is the failure this whole
    module is a monument to.
    """
    source = _module_source()
    flat = _flattened_source()
    assert len(flat) == len(source), "flattening moved the offsets"
    spans = _family_paragraph_spans().values()

    strays = []
    for match in _SPELLING_COUNT.finditer(flat):
        # Wholly inside, not merely starting inside: a sentence that runs off
        # the end of a paragraph is not in the text that paragraph's own test
        # reads.
        if any(start <= match.start() and match.end() <= stop for start, stop in spans):
            continue
        line = source.count("\n", 0, match.start()) + 1
        strays.append(f"line {line}: {' '.join(match.group(0).split())}")

    assert not strays, (
        "these state a spelling count outside every family paragraph, where "
        "no test compares it with the entries in BLIND_SPOTS:\n  " + "\n  ".join(strays)
    )


def test_a_comment_marker_is_blanked_as_a_unit():
    """The normaliser itself, pinned rather than inferred from its callers.

    :func:`_blank_comment_markers` had exactly one caller and no test of its
    own, so reverting it to the character-wise ``[#\n]`` blanking -- the
    precise bug the commit that introduced it is named for -- passed the
    whole module. It leaves the colon of a ``#:`` behind, and a colon splits
    a wrapped sentence just as effectively as the newline it replaced: the
    reader then finds 8 of the 12 counts and misses the four wrapped ones it
    exists for. Asserting the exact output is what makes that revert loud.
    """
    # Both literals are split immediately before the noun on purpose: this
    # file forbids itself from stating a count outside a family paragraph,
    # and an unsplit literal here would be exactly that. The runtime values
    # are the sentence; the file text never is.
    sample = "#: Six\n#:    " "spellings are recorded below.\n# and a bare marker\n"

    blanked = _blank_comment_markers(sample)

    assert len(blanked) == len(sample), "blanking moved the offsets"
    assert blanked == (
        "   Six       " "spellings are recorded below.   and a bare marker "
    )
    # Character-wise blanking leaves " : Six  :    spellings", where the
    # stranded colon stops this pattern matching at all.
    assert _SPELLING_COUNT.findall(" ".join(blanked.split())) == ["Six"]


def test_the_two_readers_of_this_module_see_the_same_counts():
    r"""The structural fix, asserted as the property rather than as a spelling.

    Three rounds each closed one hiding place and left the mechanism that
    creates them: two readers with two ideas of what a comment marker is.
    :func:`_flattened_source` blanked ``#:`` and ``#``;
    :func:`_family_paragraphs` stripped only ``^#:`` at the start of a line.
    So a marker written between the number and the noun --

        #:    A further <N>
        # spellings are recorded below for the resolver family.

    -- was one sentence to the flat reader, which therefore reported no stray
    because it sits inside family A's span, and was not a sentence at all to
    family A's own paragraph, which went on seeing a single true count.

    Both readers now normalise through :func:`_blank_comment_markers`, so
    what remains between them is runs of whitespace, which ``_SPELLING_COUNT``
    matches with ``\s+`` and cannot tell apart. This test states that as the
    invariant: every count either reader can see, the other can see too. It
    does not care which marker was used, so the next spelling of one is not
    a new hole to find.
    """
    flat = sorted(_SPELLING_COUNT.findall(_flattened_source()))
    per_family = sorted(
        stated
        for paragraph in _family_paragraphs().values()
        for stated in _SPELLING_COUNT.findall(paragraph)
    )

    assert flat == per_family, (
        "the flat reader and the per-family reader disagree about which "
        f"counts this file states: flat={flat}, families={per_family}"
    )


def test_a_walrus_cannot_bind_an_attribute():
    """Why ``ast.NamedExpr`` is absent from ``BINDING_NODES``.

    Every other statement form that binds a name is walked, and leaving one
    out on the strength of an assumption is how the previous rounds went. The
    assumption here is checkable: the grammar restricts a walrus target to a
    plain identifier, so there is no ``sys.excepthook`` shaped walrus for the
    walker to miss.
    """
    with pytest.raises(SyntaxError):
        ast.parse("(sys.excepthook := _quiet)")

    # A walrus that binds a plain name parses, and binds nothing this guard
    # is looking for.
    assert find_silent_swallows("(excepthook := _quiet)\n", "probe.py") == []


def test_a_hook_assignment_is_reported_where_the_binding_is():
    """A ``with`` item and a comprehension carry no line number of their own.

    Reporting the *statement* would have raised ``AttributeError`` on both,
    which is the kind of thing that turns a widened guard back into a narrow
    one via an exception nobody sees. The line reported is the target's.
    """
    source = textwrap.dedent("""
        import sys

        with _opened() as sys.excepthook:
            pass
        """)

    found = find_silent_swallows(source, "probe.py")

    assert [swallow.lineno for swallow in found] == [4]
    assert found[0].qualname == "<module>"


def test_the_blind_spot_list_matches_the_prose():
    """Every count the prose states, read out of the text rather than assumed.

    The previous version of this test asserted ``len(BLIND_SPOTS) ==
    KNOWN_BLIND_SPOTS`` and nothing else. It never opened the prose it is
    named for, and the same commit that wrote it left three false statements
    in that prose: the narrative quoted an entry count of 20 while the
    constant beneath it said 21, family I said "the four previous guards were
    lost" while the header two hundred lines above said five, and family I
    claimed the resolver handles "every one that is a literal" while an
    annotated binding is a literal it does not handle. A test named for
    checking prose that does not read prose is a false assurance, which is
    worse than no test at all.

    Three of the four are now mechanical. The fourth -- a family whose
    *description* is wrong rather than its arithmetic -- is caught only
    indirectly, by the family letters having to run contiguously from A and
    to be as many as the narrative claims, so a hole that is discovered but
    not written up cannot be filed under an existing letter without the count
    moving.
    """
    assert len(BLIND_SPOTS) == KNOWN_BLIND_SPOTS

    assert _prose_count(r"the (\d+)\s+#\s+entries in KNOWN_BLIND_SPOTS") == len(
        BLIND_SPOTS
    )

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    families = re.findall(r"^#: ([A-Z])\. \*\*", source, flags=re.MULTILINE)
    assert families == [
        chr(ord("A") + offset) for offset in range(len(families))
    ], families
    assert _prose_count(r"They fall into (\w+) root causes") == len(families)

    assert _prose_count(r"had (\w+) AST guards written") == _prose_count(
        r"the (\w+) previous guards were lost"
    )

    # Every letter with a paragraph has entries, and every letter on an entry
    # has a paragraph. Without this, moving the last entry out of a family
    # would leave its write-up standing with nothing to describe -- which is
    # how "an exception accessor named on an unrelated object" came to be
    # filed under H ("dead code the pruner cannot model") when it is nothing
    # of the kind: it is live code the lint mis-identifies by name.
    assert sorted(_family_paragraphs()) == sorted(_family_counts())
