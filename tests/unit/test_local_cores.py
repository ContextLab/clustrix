#!/usr/bin/env python3
"""``@cluster(cores=N)`` locally: honoured where it can be, reported where it cannot.

Issue #152. ``cores=8`` used to be accepted on every local route and dropped on
almost all of them -- the work ran in the caller's own process, one core, no
message. One local route can use the number: a function with a loop that
``clustrix.parallelize`` can divide has that loop run by ``cores`` worker
processes. That route is exercised, with real workers, in
``test_cluster_splits_loops.py``. Everywhere else a local call is one unit of
work and there is nothing to hand a second worker.

This module is about "everywhere else":

* every local route that cannot use the request must say so, out loud -- once
  per decorated function per distinct request, not on every call;
* a core count that cannot mean anything must be refused, not absorbed.

Nothing here is mocked. Every test drives the real decorator and the real
``LocalJobManager``.
"""

import io
import logging
import os
import threading
from contextlib import contextmanager

import pytest

from clustrix import cluster, configure, explain
from clustrix.config import get_config
from clustrix.decorator import UNCONFIRMED_REPEAT_LIMIT
from clustrix.local_executor import LocalJobManager
from clustrix.utils import serialize_function

N = 64
TOP_MARKER = (N - 1) * (N - 1)


def pid_of_whole_call(n):
    """Has a loop, but not one that can be divided: ``marker`` is read after
    the loop, so the answer depends on which iteration ran last. This can only
    ever run as one unit of work."""
    marker = 0
    for i in range(n):
        marker = i * i
    return {"marker": marker, "pid": os.getpid()}


def where_this_ran(n):
    """Report the process *and* thread that ran the call.

    A pool that actually executes something is visible here even when it is
    only one worker wide: a ``ThreadPoolExecutor`` answers on a different
    thread, a ``ProcessPoolExecutor`` in a different process.
    """
    marker = 0
    for i in range(n):
        marker = i * i
    return {
        "marker": marker,
        "pid": os.getpid(),
        "thread": threading.get_ident(),
    }


def has_no_loop_to_split(n):
    """No loop at all, so the auto-parallel route has nothing to work with."""
    return n * n


@pytest.fixture(autouse=True)
def local_config():
    """Real global config, restored afterwards."""
    config = get_config()
    saved = (
        config.cluster_type,
        config.cluster_host,
        config.auto_parallel,
        config.default_cores,
    )
    configure(cluster_type="local", cluster_host=None, auto_parallel=False)
    yield
    configure(
        cluster_type=saved[0],
        cluster_host=saved[1],
        auto_parallel=saved[2],
        default_cores=saved[3],
    )


def warnings_from(caplog):
    return [record.getMessage() for record in caplog.records]


def test_the_sequential_local_path_says_it_is_ignoring_cores(caplog):
    """No loop was split, so the eight workers were never going to exist."""
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=8)(pid_of_whole_call)(N)

    assert result["pid"] == os.getpid(), "the local path does run here"
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "has no effect here" in message for message in messages
    ), messages


def test_the_auto_parallel_route_reports_a_function_with_no_loop(caplog):
    """``auto_parallel`` is on by default, so this is the ordinary user's route.

    Nothing else covers it: with ``parallel`` unset and the shipped config, a
    function with no loop at all reaches ``_run_split_locally``, is run whole,
    and used to discard ``cores=8`` on the way past.
    """
    configure(auto_parallel=True)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(cores=8)(has_no_loop_to_split)(N) == N * N

    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message
        and "has_no_loop_to_split has no loop that can be split" in message
        for message in messages
    ), messages


def test_parallel_true_that_cannot_split_still_reports_the_request(caplog):
    """``pid_of_whole_call`` has a loop, and the engine refuses it.

    The warning says where to look for the reason, and the reason is there.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(parallel=True, cores=8)(pid_of_whole_call)(N)

    assert result == {"marker": TOP_MARKER, "pid": os.getpid()}
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message
        and "pid_of_whole_call has no loop that can be split" in message
        and "clustrix.explain(function)" in message
        for message in messages
    ), messages
    assert "marker is assigned in the loop and used after it" in explain(
        pid_of_whole_call
    )


def test_cluster_type_local_reports_the_request_and_still_returns(caplog):
    """The ``LocalJobManager`` route: a serialized job, run here as one unit."""
    configure(cluster_type="local", cluster_host="host-the-local-manager-ignores")

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=8)(pid_of_whole_call)(N)

    assert result == {"marker": TOP_MARKER, "pid": os.getpid()}
    messages = warnings_from(caplog)
    assert any(
        "cores=8" in message and "single unit of work" in message
        for message in messages
    ), messages


def test_a_default_cores_the_user_set_is_reported_too(caplog):
    """Setting the default globally is still asking, and used to say nothing.

    ``configure(default_cores=8)`` followed by a bare ``@cluster()`` gave one
    core in silence, because only the per-call keyword was treated as a
    request. The shipped default is the only value that is not an instruction.
    """
    configure(default_cores=8)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(pid_of_whole_call)(N)["pid"] == os.getpid()

    messages = warnings_from(caplog)
    assert any(
        "default_cores=8" in message and "has no effect here" in message
        for message in messages
    ), messages


def test_nothing_is_reported_when_nothing_was_requested(caplog):
    """The shipped ``default_cores`` is not a per-call instruction, and 1 is no ask.

    ``cores=1`` is checked on the parallel path as well as the plain one: that
    is where the request reaches the warning itself rather than being filtered
    out by the caller, so a warning that stopped distinguishing "one worker"
    from "several" would fire here and nowhere else.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(pid_of_whole_call)(N)["pid"] == os.getpid()
        assert cluster(cores=1)(pid_of_whole_call)(N)["pid"] == os.getpid()
        assert cluster(parallel=True, cores=1)(has_no_loop_to_split)(N) == N * N
        assert (
            cluster(parallel=True, cores=1)(pid_of_whole_call)(N)["pid"] == os.getpid()
        )

    assert get_config().default_cores > 1, "default_cores is what would go unnoticed"
    assert not any(
        "has no effect here" in message for message in warnings_from(caplog)
    ), warnings_from(caplog)


def test_the_same_request_is_reported_once_per_decorated_function(caplog):
    """A warning repeated on every call is a warning the user learns to skip.

    The message is worth saying: it tells the caller their eight workers are
    not going to exist. It is not worth saying five times, and the local path
    is precisely where a decorated function gets called in a loop. It is
    throttled per decorated function and per ``(request, reason)`` pair, so a
    second function -- a genuinely separate thing the user asked for -- still
    gets told.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        once = cluster(cores=8)(pid_of_whole_call)
        for _ in range(5):
            assert once(N)["pid"] == os.getpid()

        said = [m for m in warnings_from(caplog) if "cores=8" in m]
        assert len(said) == 1, f"five calls produced {len(said)} warnings: {said}"

        again = cluster(cores=8)(pid_of_whole_call)
        assert again(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 2, (
        "a separately decorated function has its own record and must be told "
        f"too: {said}"
    )


def test_a_changed_request_is_reported_again(caplog):
    """Throttling may not swallow a *different* configuration.

    The same decorated function, called after ``configure(default_cores=...)``
    changed underneath it, is a new request and has never been answered. A
    throttle keyed on the function alone would report the first value and go
    quiet on the second, which is the #152 silence again in a smaller box.
    """
    bare = cluster(pid_of_whole_call)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        configure(default_cores=8)
        assert bare(N)["pid"] == os.getpid()
        assert bare(N)["pid"] == os.getpid()

        configure(default_cores=16)
        assert bare(N)["pid"] == os.getpid()
        assert bare(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "has no effect here" in m]
    assert len(said) == 2, f"expected one warning per distinct request: {said}"
    assert sum("default_cores=8" in m for m in said) == 1, said
    assert sum("default_cores=16" in m for m in said) == 1, said


def test_a_different_decline_reason_is_reported_again(caplog):
    """One request, two reasons to drop it: the caller hears about both.

    ``_warn_cores_unused`` keys its record on ``(where, because)``, and
    ``test_a_changed_request_is_reported_again`` only ever varies ``where``.
    Drop ``because`` from the key -- it looks redundant, the message already
    names the request -- and the same decorated function that has been told
    "the local backend runs this once" goes quiet when it later declines for a
    completely different reason. The two facts are not interchangeable: the
    first says *this route* cannot use eight workers, the second says the
    splitting route looked and found no loop to divide.

    Both halves are asserted, because a throttle that has stopped throttling
    would pass a bare count of two.
    """
    once = cluster(cores=8)(has_no_loop_to_split)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        configure(auto_parallel=False)
        assert once(N) == N * N  # plain local path: run here, once
        assert once(N) == N * N  # ... and throttled

        configure(auto_parallel=True)
        assert once(N) == N * N  # the splitting path: no loop to split
        assert once(N) == N * N  # ... and throttled

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 2, f"expected one warning per distinct reason: {said}"
    assert sum("runs the decorated function once" in m for m in said) == 1, said
    assert sum("has no loop that can be split" in m for m in said) == 1, said


@pytest.mark.parametrize("silenced_at", [logging.ERROR, logging.CRITICAL])
def test_a_warning_nobody_could_hear_does_not_spend_the_budget(silenced_at, caplog):
    """The one message is spent on delivery, not on the attempt.

    The throttle is right -- a warning repeated on every iteration is a warning
    that gets filtered out -- but it used to record the key before asking
    whether anything was listening. A library that raises clustrix's log level,
    or a script that calls the decorated function before
    ``logging.basicConfig()``, therefore spent the single message on a record
    that went nowhere, and every later call was silent: zero warnings
    delivered. That is #152's own silence, rebuilt inside the fix for it.

    Both levels above ``WARNING`` are exercised, and that is the point of the
    parametrisation rather than tidiness. Asking the gate about ``ERROR``
    instead of ``WARNING`` -- an easy slip, since the two read alike -- is
    invisible at ``CRITICAL``, where both answers are "off". At ``ERROR`` they
    part company: the real question answers "nobody can hear this", the wrong
    one answers "somebody can", and the budget is spent on a record that went
    nowhere. The level the gate asks about must be the level the message is
    logged at, and only the notch immediately above it can show that.
    """
    decorator_logger = logging.getLogger("clustrix.decorator")
    saved = decorator_logger.level
    said_once = cluster(cores=8)(pid_of_whole_call)

    try:
        decorator_logger.setLevel(silenced_at)
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()
        heard = [m for m in warnings_from(caplog) if "cores=8" in m]
        assert not heard, f"the logger was off and something got through: {heard}"
    finally:
        decorator_logger.setLevel(saved)

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller turned warnings on and was told exactly once; got "
        f"{len(said)}: {said}"
    )


def test_a_null_handler_does_not_spend_the_budget_either(caplog):
    """The level was never the whole question: a handler has to want it too.

    ``logging.getLogger("clustrix").addHandler(logging.NullHandler())`` is the
    documented way to keep a library quiet, and under it ``isEnabledFor`` still
    answers True -- the level is untouched. What changes is delivery:
    ``Logger.callHandlers`` finds the null handler, does nothing with the
    record, and *because it found a handler* declines to fall back to
    ``logging.lastResort``. Zero warnings are emitted, and a gate that asked
    only about the level would have marked the reason as reported. The caller
    who later wires up a real handler -- which is the whole reason to start
    with a null one -- then hears nothing, ever.

    The root handlers are lifted for the silent phase because pytest installs
    its own there, and leaving them would mean the record *was* delivered,
    which is a different situation from the one under test.
    """
    decorator_logger = logging.getLogger("clustrix.decorator")
    null_handler = logging.NullHandler()
    saved_root = logging.root.handlers[:]
    said_once = cluster(cores=8)(pid_of_whole_call)

    decorator_logger.addHandler(null_handler)
    try:
        logging.root.handlers = []
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()
    finally:
        logging.root.handlers = saved_root
        decorator_logger.removeHandler(null_handler)

    assert not [m for m in warnings_from(caplog) if "cores=8" in m], (
        "a NullHandler was the only handler in the chain, so nothing was "
        "emitted; caplog should not have seen anything either"
    )

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller replaced the NullHandler with one that emits and was told "
        f"exactly once; got {len(said)}: {said}"
    )


DECORATOR_LOGGER = logging.getLogger("clustrix.decorator")


@contextmanager
def a_bare_logging_chain():
    """Run the block with the ``clustrix.decorator`` logging chain emptied.

    A test about what happens when nothing is listening cannot leave pytest's
    own root handlers in place: the record would genuinely be delivered, which
    is a different situation from the one under test. Every logger from
    ``clustrix.decorator`` up to the root is stripped of handlers and filters
    and set to propagate, ``clustrix.decorator`` is pinned at ``WARNING`` so
    that no inherited level can decide the answer instead of the thing being
    tested, and ``logging.lastResort`` -- a module global, and therefore
    everybody's -- is saved along with them. The block attaches whatever the
    scenario needs; everything is put back afterwards.
    """
    chain = [DECORATOR_LOGGER, logging.getLogger("clustrix"), logging.root]
    saved = [(lg, lg.handlers, lg.filters, lg.level, lg.propagate) for lg in chain]
    saved_last_resort = logging.lastResort
    saved_last_resort_level = (
        None if saved_last_resort is None else saved_last_resort.level
    )
    for one in chain:
        one.handlers = []
        one.filters = []
        one.propagate = True
    DECORATOR_LOGGER.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logging.lastResort = saved_last_resort
        if saved_last_resort is not None:
            saved_last_resort.level = saved_last_resort_level
        for one, handlers, filters, level, propagate in saved:
            one.handlers = handlers
            one.filters = filters
            one.level = level
            one.propagate = propagate


def collecting_handler(level=logging.WARNING):
    """A real handler that writes somewhere the test can read back."""
    handler = logging.StreamHandler(io.StringIO())
    handler.setLevel(level)
    return handler


def test_a_handler_that_is_too_high_to_emit_does_not_spend_the_budget(caplog):
    """The handler's level is a second gate, and it is not the logger's level.

    A ``clustrix`` logger carrying a single ``ERROR``-level file handler is an
    ordinary production setup -- an application that wants library errors on
    disk and nothing else. ``isEnabledFor(WARNING)`` says yes, because the
    *logger* level is untouched; ``callHandlers`` then finds the handler,
    declines to emit because ``WARNING < ERROR``, and *because it found one*
    does not fall back to ``logging.lastResort``. Nothing is emitted.

    This is the case where asking the handler gate about ``ERROR`` rather than
    ``WARNING`` -- the same one-notch slip that
    ``test_a_warning_nobody_could_hear_does_not_spend_the_budget`` pins for the
    logger level -- reads as "somebody can hear this", spends the single
    message on a record nothing received, and hands the caller permanent
    silence the moment they add the handler that would have shown it. That is
    #152's own defect rebuilt inside the fix for it.
    """
    said_once = cluster(cores=8)(pid_of_whole_call)
    too_high = collecting_handler(logging.ERROR)

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(too_high)
        assert DECORATOR_LOGGER.isEnabledFor(logging.WARNING), (
            "the level must be on, or this test would pass because the record "
            "was never made rather than because the handler refused it"
        )
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    written = too_high.stream.getvalue()
    assert (
        "cores=8" not in written
    ), f"an ERROR-level handler emitted a WARNING record: {written!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller added a handler that emits and was told exactly once; got "
        f"{len(said)}: {said}"
    )


def test_an_ancestor_handler_behind_propagate_false_does_not_spend_the_budget(
    caplog,
):
    """``propagate = False`` ends the walk, and the walk must end with it.

    ``logging.getLogger("clustrix.decorator").propagate = False`` is how an
    application says "this logger's records stop here". ``callHandlers`` obeys
    it literally: it visits ``clustrix.decorator``'s own handlers and then
    stops, never reaching the emitting handler an ancestor carries. So with a
    ``NullHandler`` here and a real handler on ``clustrix``, nothing is
    emitted -- a handler was found, so ``lastResort`` stays out of it too.

    A gate that walked to the root regardless would see the ancestor's real
    handler, answer "somebody can hear this", and spend the one message on a
    record that stopped one logger short of it.
    """
    said_once = cluster(cores=8)(pid_of_whole_call)
    unreachable = collecting_handler(logging.WARNING)

    with a_bare_logging_chain():
        DECORATOR_LOGGER.addHandler(logging.NullHandler())
        DECORATOR_LOGGER.propagate = False
        logging.getLogger("clustrix").addHandler(unreachable)
        assert DECORATOR_LOGGER.isEnabledFor(
            logging.WARNING
        ), "the level must be on, or this test would pass for the wrong reason"
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    written = unreachable.stream.getvalue()
    assert (
        "cores=8" not in written
    ), f"propagate=False was set and a record crossed it anyway: {written!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the caller let records propagate again and was told exactly once; got "
        f"{len(said)}: {said}"
    )


@pytest.mark.parametrize("last_resort_state", ["removed", "raised"])
def test_a_last_resort_that_cannot_emit_does_not_spend_the_budget(
    last_resort_state, caplog, capsys
):
    """With no handlers at all, ``lastResort`` decides -- and it can say no.

    When ``callHandlers`` finds no handler anywhere in the chain it falls back
    to ``logging.lastResort``, a module-level ``_StderrHandler`` that ships at
    ``WARNING``. That fallback is the reason an unconfigured script still sees
    this message, so the gate has to account for it. But it is a global anyone
    may change, and both of the ways it can be silenced are exercised here
    because they are separate halves of one expression: ``logging.lastResort =
    None`` is the documented way to turn the fallback off entirely, and raising
    its level is what an application does when it wants stderr quieter. A gate
    that answered "yes" for a bare chain without asking these questions would
    spend the message into a void on exactly the setup -- no handlers
    configured -- where the caller is least likely to have another way of
    finding out.
    """
    said_once = cluster(cores=8)(pid_of_whole_call)

    with a_bare_logging_chain():
        if last_resort_state == "removed":
            logging.lastResort = None
        else:
            logging.lastResort.setLevel(logging.ERROR)
        assert DECORATOR_LOGGER.isEnabledFor(
            logging.WARNING
        ), "the level must be on, or this test would pass for the wrong reason"
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    stderr = capsys.readouterr().err
    assert (
        "cores=8" not in stderr
    ), f"lastResort was silenced and still wrote the message: {stderr!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "handlers came back and the caller was told exactly once; got "
        f"{len(said)}: {said}"
    )


@pytest.mark.parametrize("attach_to", ["handler", "logger"])
def test_a_filter_that_drops_the_record_does_not_spend_the_budget(attach_to, caplog):
    """A filter can throw the record away after every level test has passed.

    Two of them can, at two different points, and both are pinned because the
    code has to know about both: ``Logger.handle`` runs *this logger's* filters
    before ``callHandlers`` gets the record at all, and ``Handler.handle`` runs
    each handler's filters after the level test and before ``emit``. Level
    checks alone see neither, so a gate built only from levels answers
    "somebody can hear this" and spends the single message on a record that was
    discarded -- #152's silence, again, rebuilt inside the fix for it.

    ``_warning_reaches_someone`` does not run the filter to find out, and says
    so: a filter is arbitrary caller code, and asking it twice per message
    would corrupt any filter that counts or rate-limits. It treats a filter as
    an obstruction instead, which is exact when the filter drops the record --
    the case here -- and pessimistic when the filter passes it, costing a
    repeated message rather than a lost one.
    """

    class DropEverything(logging.Filter):
        def filter(self, record):
            return False

    said_once = cluster(cores=8)(pid_of_whole_call)
    filtered = collecting_handler(logging.WARNING)
    if attach_to == "handler":
        filtered.addFilter(DropEverything())

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(filtered)
        if attach_to == "logger":
            DECORATOR_LOGGER.addFilter(DropEverything())
        assert DECORATOR_LOGGER.isEnabledFor(
            logging.WARNING
        ), "the level must be on, or this test would pass for the wrong reason"
        for _ in range(50):
            assert said_once(N)["pid"] == os.getpid()

    written = filtered.stream.getvalue()
    assert (
        "cores=8" not in written
    ), f"a filter said no and the record was emitted anyway: {written!r}"

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        for _ in range(3):
            assert said_once(N)["pid"] == os.getpid()

    said = [m for m in warnings_from(caplog) if "cores=8" in m]
    assert len(said) == 1, (
        "the filter is gone and the caller was told exactly once; got "
        f"{len(said)}: {said}"
    )


@pytest.mark.parametrize("attach_to", ["handler", "logger"])
def test_a_filter_that_passes_the_record_does_not_silence_the_caller(attach_to):
    """The gate may cost a repeat. It may never cost the message.

    ``_warning_reaches_someone`` treats every filter as an obstruction because
    it will not run caller code twice to find out. When the filter in fact
    *passes* the record -- a level-independent tag injector, a
    de-duplicator that lets the first of each message through, an audit filter
    that returns True after recording -- the gate is wrong, and the direction
    of that wrongness is the whole design: the reason is not marked delivered,
    so it is said again next call, and the caller hears it.

    That only holds while the emit sits **outside** the gate. Move
    ``logger.warning`` inside ``if _warning_reaches_someone():`` and this
    scenario stops producing a repeat and starts producing nothing at all --
    a caller with a perfectly ordinary filter installed is handed exactly the
    silence #152 is about, by the code written to end it. Nothing else in this
    file distinguishes those two arrangements, because every other filter test
    uses a filter that drops the record, where both arrangements look alike.
    """

    class PassEverything(logging.Filter):
        """Sees every record, changes nothing, and lets all of them through."""

        def __init__(self):
            super().__init__()
            self.seen = 0

        def filter(self, record):
            self.seen += 1
            return True

    said_once = cluster(cores=8)(pid_of_whole_call)
    listening = collecting_handler(logging.WARNING)
    benign = PassEverything()

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(listening)
        if attach_to == "handler":
            listening.addFilter(benign)
        else:
            DECORATOR_LOGGER.addFilter(benign)
        assert said_once(N)["pid"] == os.getpid()

    written = listening.stream.getvalue()
    assert "cores=8" in written, (
        "a filter that passes everything was installed and the caller was "
        f"told nothing at all: {written!r}"
    )
    assert benign.seen == 1, (
        "the record reached the filter exactly once -- more would mean the "
        f"gate ran caller code to make its decision (saw {benign.seen})"
    )


def test_an_unconfirmable_reason_repeats_but_not_forever():
    """A repeat is the fail-safe. An unbounded repeat is the thing being fixed.

    A filter the gate refuses to read never lets a reason be marked delivered,
    so before ``UNCONFIRMED_REPEAT_LIMIT`` existed this loop produced one
    warning per iteration -- the throttle switched off by the very caller
    configuration it was supposed to survive. The cap bounds that, and it is
    allowed to remove only repeats: the count is reached after the message has
    already been delivered ``UNCONFIRMED_REPEAT_LIMIT`` times, never before the
    first.
    """

    class PassEverything(logging.Filter):
        def filter(self, record):
            return True

    repeats = cluster(cores=8)(pid_of_whole_call)
    listening = collecting_handler(logging.WARNING)
    listening.addFilter(PassEverything())

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(listening)

        assert repeats(N)["pid"] == os.getpid()
        after_one = listening.stream.getvalue().count("cores=8")
        assert after_one == 1, (
            "the very first call must reach the caller whatever the cap says; "
            f"got {after_one}"
        )

        for _ in range(19):
            assert repeats(N)["pid"] == os.getpid()

    written = listening.stream.getvalue()
    said = written.count("cores=8")
    assert said == UNCONFIRMED_REPEAT_LIMIT, (
        f"20 calls under an unreadable filter said it {said} times; the cap "
        f"is {UNCONFIRMED_REPEAT_LIMIT}"
    )


def test_the_cap_does_not_outlive_the_configuration_that_caused_it():
    """Spending the repeats must not buy permanent silence.

    The cap counts only deliveries the gate could not confirm. A caller who
    installs a handler clustrix can vouch for -- ``basicConfig``, the filter
    removed -- has never been told, whatever the counter says, so the confirmed
    branch runs and they are told once. A cap checked before that branch would
    turn "we repeated ourselves three times into a filtered logger" into "this
    reason is now unspeakable", which is #152 rebuilt one more time.
    """

    class PassEverything(logging.Filter):
        def filter(self, record):
            return True

    spent = cluster(cores=8)(pid_of_whole_call)
    filtered = collecting_handler(logging.WARNING)
    filtered.addFilter(PassEverything())
    plain = collecting_handler(logging.WARNING)

    with a_bare_logging_chain():
        logging.getLogger("clustrix").addHandler(filtered)
        for _ in range(20):
            assert spent(N)["pid"] == os.getpid()
        assert (
            filtered.stream.getvalue().count("cores=8") == UNCONFIRMED_REPEAT_LIMIT
        ), "the cap must have been reached, or this test proves nothing"

        logging.getLogger("clustrix").handlers = [plain]
        for _ in range(5):
            assert spent(N)["pid"] == os.getpid()

    heard = plain.stream.getvalue().count("cores=8")
    assert heard == 1, (
        "a listener clustrix can vouch for arrived after the cap was spent "
        f"and was told {heard} times; expected exactly one"
    )


@pytest.mark.parametrize("cores", [True, False])
def test_a_bool_is_not_a_core_count(cores):
    """``bool`` subclasses ``int``, and that is not the caller's problem.

    ``isinstance(True, int)`` is true, so ``@cluster(cores=True)`` passed the
    "is it an integer" check and was read as a request for one worker, while
    ``cores=False`` was refused with "must be a positive integer", a message
    that says nothing useful about ``True``, which is not a positive integer
    in any sense the caller means. Both are refused now, and the message names
    the actual reason.
    """
    for construct in (
        lambda: cluster(cores=cores)(pid_of_whole_call),
        lambda: cluster(parallel=True, cores=cores)(pid_of_whole_call),
    ):
        with pytest.raises(ValueError, match="bool is not one"):
            construct()


@pytest.mark.parametrize("cores", [0, -2, 2.0, "4"])
def test_a_core_count_that_is_not_one_is_refused_not_absorbed(cores):
    """Zero and negative used to be swallowed in two different silent ways.

    ``cores=0`` is falsy, so ``cores or config.default_cores`` quietly replaced
    it with the default. ``cores=-2`` reached a process pool, whose
    "max_workers must be greater than 0" was caught by a sequential fallback
    that said nothing. ``-1`` is the one negative with a meaning -- every CPU
    this process may use -- and the refusal of the others says so.
    """
    with pytest.raises(ValueError, match="positive integer, or -1"):
        cluster(cores=cores)(pid_of_whole_call)

    with pytest.raises(ValueError, match="positive integer, or -1"):
        cluster(parallel=True, cores=cores)(pid_of_whole_call)


def test_every_cpu_is_an_answer_locally_and_says_nothing_when_there_is_no_loop(caplog):
    """``cores=-1`` is accepted, and is not a request this route can ignore.

    "Every CPU I may use" is satisfied by one process when there is nothing to
    divide, so unlike ``cores=8`` it is not reported as discarded.
    """
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        assert cluster(cores=-1)(pid_of_whole_call)(N)["pid"] == os.getpid()
        assert cluster(parallel=True, cores=-1)(has_no_loop_to_split)(N) == N * N

    assert not any(
        "has no effect here" in message for message in warnings_from(caplog)
    ), warnings_from(caplog)


def test_every_cpu_cannot_be_asked_of_a_slurm_job():
    """``--cpus-per-task=-1`` is what this used to send to sbatch.

    Refused before anything is connected to: the host below does not exist,
    and the test would hang on it or fail differently if it were contacted.
    """
    configure(
        cluster_type="slurm",
        cluster_host="no-such-host.invalid",
        username="nobody",
    )

    with pytest.raises(ValueError, match="cores=-1 means every CPU"):
        cluster(cores=-1)(has_no_loop_to_split)(N)

    configure(default_cores=-1)
    with pytest.raises(ValueError, match="cores=-1 means every CPU"):
        cluster(has_no_loop_to_split)(N)


def test_local_job_manager_runs_the_job_here_and_builds_no_pool():
    """It used to build a pool sized by ``cores`` and then not use it.

    A pool object built and then not used is what made ``cores`` look honoured
    here.

    The check is behavioural rather than a search of the source, which any
    reintroduction under an alias would pass. A pool that
    *runs* anything is visible: the call would answer from another thread or
    another process, and it would move when ``cores`` moved. So the job is run
    twice, with the two core counts furthest apart, and must land in this exact
    thread of this exact process both times.
    """
    manager = LocalJobManager(get_config())
    here = {"pid": os.getpid(), "thread": threading.get_ident()}

    ran = []
    for cores in (1, 8):
        job_id = manager.submit_job(
            serialize_function(where_this_ran, (N,), {}), {"cores": cores}
        )
        assert manager.get_job_status(job_id) == "completed"
        result = manager.wait_for_result(job_id)
        assert result["marker"] == TOP_MARKER
        ran.append({"pid": result["pid"], "thread": result["thread"]})

    assert ran == [here, here], (
        f"the job did not run on the caller's own thread: {ran} != {here}; "
        "something executed it through a pool"
    )
