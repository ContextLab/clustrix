"""``@cluster`` dividing a loop among worker processes, through the decorator.

``test_parallelize.py`` tests the engine with an in-test backend and
``test_loop_workers.py`` tests the pool on its own. This module is the
join: the public decorator, the real analysis, real worker processes. Every
test calls a decorated function the way a user would and compares it with
calling the same function undecorated.

Nothing is simulated. "It ran somewhere else" is established by the loop
recording ``os.getpid()``; "it ran here" likewise.
"""

import inspect
import logging
import os
import random
import sys
import threading
import time
import traceback

import pytest

import clustrix
from clustrix import cluster, configure, explain
from clustrix.decorator import MIN_SECONDS_WORTH_DIVIDING
from clustrix.loop_workers import RemoteTraceback, available_cpus, shared_pool

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the worker pool's tests are POSIX-only"
)

CPUS = available_cpus()
needs_two_cpus = pytest.mark.skipif(
    CPUS < 2,
    reason=(
        f"this process may use {CPUS} CPU: with one, @cluster runs the loop "
        "here and there are no worker processes to observe"
    ),
)


# --------------------------------------------------------------------------
# Work
# --------------------------------------------------------------------------


def spin(rounds):
    """A fixed amount of arithmetic -- not a fixed amount of time.

    A loop that waits on the clock finishes in the same wall time however
    few CPUs it shares, so it cannot show that work was divided.
    """
    total = 0
    for value in range(rounds):
        total += value * value
    return total


@pytest.fixture(scope="module")
def rounds():
    """How many rounds of ``spin`` take about 0.12 s on this machine."""
    probe = 200_000
    started = time.perf_counter()
    spin(probe)
    return max(probe, int(probe * 0.12 / (time.perf_counter() - started)))


def squares_and_pids(values, rounds):
    """The loop most of these tests divide: independent, collected in order."""
    out = []
    for value in values:
        spin(rounds)
        out.append((value * value, os.getpid()))
    return out


def values_of(result):
    return [value for value, _ in result]


def pids_of(result):
    return {pid for _, pid in result}


def records(caplog, level, containing):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "clustrix.decorator"
        and record.levelno == level
        and containing in record.getMessage()
    ]


# --------------------------------------------------------------------------
# The loop is divided, and the answer is the function's own
# --------------------------------------------------------------------------


@needs_two_cpus
def test_a_cpu_bound_loop_is_divided_among_other_processes_and_is_faster(rounds):
    values = list(range(24))
    workers = min(4, CPUS)
    divided = cluster(cores=4)(squares_and_pids)

    # The pool is kept between calls; starting its interpreters is paid once
    # and is not what is being measured.
    divided(values[:workers], rounds)

    started = time.perf_counter()
    direct = squares_and_pids(values, rounds)
    sequential_seconds = time.perf_counter() - started

    started = time.perf_counter()
    split = divided(values, rounds)
    split_seconds = time.perf_counter() - started

    print(
        f"\n{len(values)} iterations, {workers} workers on {CPUS} CPUs: "
        f"sequential {sequential_seconds:.2f}s, split {split_seconds:.2f}s "
        f"({sequential_seconds / split_seconds:.2f}x)"
    )

    assert values_of(split) == values_of(direct) == [v * v for v in values]
    pids = pids_of(split)
    assert os.getpid() not in pids, "an iteration ran in the calling process"
    assert 2 <= len(pids) <= workers, pids
    # Ideal is 1/workers; anything short of 0.8 is real division of labour
    # and leaves room for a loaded machine.
    assert split_seconds < 0.8 * sequential_seconds, (
        f"dividing among {workers} workers took {split_seconds:.2f}s against "
        f"{sequential_seconds:.2f}s sequentially"
    )


@needs_two_cpus
def test_cores_bounds_the_workers_one_call_uses(rounds):
    """The shared pool may be wider than this call asked for."""
    shared_pool(CPUS)  # as wide as it will get, before asking for two

    result = cluster(cores=2)(squares_and_pids)(list(range(8)), rounds)

    assert values_of(result) == [v * v for v in range(8)]
    assert len(pids_of(result)) == 2 and os.getpid() not in pids_of(result)


@needs_two_cpus
def test_cores_minus_one_uses_the_cpus_this_process_may_use(rounds):
    values = list(range(4 * CPUS))

    result = cluster(cores=-1)(squares_and_pids)(values, rounds)

    assert values_of(result) == [v * v for v in values]
    pids = pids_of(result)
    assert os.getpid() not in pids
    assert 2 <= len(pids) <= CPUS, pids


@needs_two_cpus
def test_more_cores_than_cpus_is_capped_at_the_cpus(rounds):
    values = list(range(4 * CPUS))

    result = cluster(cores=4 * CPUS)(squares_and_pids)(values, rounds)

    assert values_of(result) == [v * v for v in values]
    assert len(pids_of(result)) <= CPUS


def test_one_core_runs_the_loop_in_this_process(rounds, caplog):
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=1)(squares_and_pids)([1, 2, 3], rounds // 50)

    assert values_of(result) == [1, 4, 9]
    assert pids_of(result) == {os.getpid()}
    assert not records(caplog, logging.WARNING, "has no effect here")


def test_parallel_false_runs_the_loop_in_this_process(rounds, caplog):
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=4, parallel=False)(squares_and_pids)(
            [1, 2, 3], rounds // 50
        )

    assert values_of(result) == [1, 4, 9]
    assert pids_of(result) == {os.getpid()}
    # Four workers were asked for and none will exist: that is said.
    assert len(records(caplog, logging.WARNING, "@cluster(cores=4)")) == 1


@needs_two_cpus
def test_a_loop_too_short_to_divide_is_run_by_one_worker(caplog):
    with caplog.at_level(logging.INFO, logger="clustrix.decorator"):
        result = cluster(cores=4)(squares_and_pids)(list(range(40)), 10)

    assert values_of(result) == [v * v for v in range(40)]
    said = records(caplog, logging.INFO, "too short to be worth dividing")
    assert len(said) == 1 and "squares_and_pids" in said[0], caplog.text
    assert len(pids_of(result)) == 1, "the message says one worker ran it"
    assert os.getpid() not in pids_of(result)


@needs_two_cpus
def test_the_threshold_is_about_the_whole_loop_not_one_iteration(rounds, caplog):
    """Each iteration is well under the threshold; forty of them are not."""
    per_iteration = rounds // 10  # about 12 ms
    with caplog.at_level(logging.INFO, logger="clustrix.decorator"):
        result = cluster(cores=2)(squares_and_pids)(list(range(40)), per_iteration)

    assert 0.012 * 40 > MIN_SECONDS_WORTH_DIVIDING
    assert values_of(result) == [v * v for v in range(40)]
    assert not records(caplog, logging.INFO, "too short"), caplog.text
    assert len(pids_of(result)) == 2


# --------------------------------------------------------------------------
# Loops the analysis refuses run here, unsplit
# --------------------------------------------------------------------------


def running_total(values):
    """Each iteration needs the one before it."""
    seen = []
    previous = 0
    for value in values:
        previous = previous + value
        seen.append((previous, os.getpid()))
    return seen


def first_negative(values):
    found = []
    for value in values:
        if value < 0:
            found.append((value, os.getpid()))
            break
    return found


AUDIT_TRAIL = []


def audited(values):
    out = []
    for value in values:
        AUDIT_TRAIL.append(value)
        out.append((value + 1, os.getpid()))
    return out


def noisy(values):
    out = []
    for value in values:
        out.append((value + random.random(), os.getpid()))
    return out


@pytest.mark.parametrize(
    "function, arguments, reason",
    [
        (running_total, ([1, 2, 3, 4],), "previous is read before the iteration"),
        (first_negative, ([3, -1, -2, 5],), "break stops the loop early"),
        (audited, ([1, 2, 3],), "AUDIT_TRAIL is a global the loop writes to"),
        (noisy, ([1, 2, 3],), "random.random() draws from a random generator"),
    ],
    ids=["carried-value", "break", "global-write", "unseeded-rng"],
)
def test_a_refused_loop_runs_here_and_says_once_that_cores_went_unused(
    function, arguments, reason, caplog
):
    random.seed(1234)
    AUDIT_TRAIL.clear()
    direct = function(*arguments)
    trail_after_direct = list(AUDIT_TRAIL)

    decorated = cluster(cores=4)(function)
    with caplog.at_level(logging.INFO, logger="clustrix.decorator"):
        results = []
        for _ in range(3):
            random.seed(1234)
            AUDIT_TRAIL.clear()
            results.append(decorated(*arguments))

    for result in results:
        assert values_of(result) == values_of(direct)
        assert pids_of(result) <= {os.getpid()}, "a refused loop left this process"
    assert AUDIT_TRAIL == trail_after_direct, "the global was not written here"

    # Three calls, one warning: the throttle of _warn_cores_unused (#152).
    warned = records(caplog, logging.WARNING, "@cluster(cores=4) has no effect here")
    assert len(warned) == 1, warned
    assert f"{function.__name__} has no loop that can be split" in warned[0]
    assert "clustrix.explain(function)" in warned[0]
    # ... and what it points at has the reason, on the wrapper too.
    assert reason in explain(decorated)
    assert reason in explain(function)
    # The decision itself is logged every time, as information.
    assert len(records(caplog, logging.INFO, "runs unsplit")) == 3


def test_declining_without_a_cores_request_is_a_decision_not_a_warning(caplog):
    """Nobody asked for workers, so there is nothing to be sorry about."""
    with caplog.at_level(logging.INFO, logger="clustrix.decorator"):
        result = cluster(running_total)([1, 2, 3])

    assert values_of(result) == [1, 3, 6]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], caplog.text
    said = records(caplog, logging.INFO, "running_total runs unsplit")
    assert len(said) == 1 and "carries a value from the previous iteration" in said[0]


def test_a_function_whose_source_is_gone_runs_here_and_says_why(caplog):
    namespace = {"os": os}
    exec(
        "def made_by_exec(values):\n"
        "    out = []\n"
        "    for value in values:\n"
        "        out.append((value * 3, os.getpid()))\n"
        "    return out\n",
        namespace,
    )
    decorated = cluster(cores=4)(namespace["made_by_exec"])

    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = decorated([1, 2, 3])

    assert values_of(result) == [3, 6, 9] and pids_of(result) == {os.getpid()}
    assert len(records(caplog, logging.WARNING, "made_by_exec has no loop")) == 1
    assert "its source is not available" in explain(decorated)


# --------------------------------------------------------------------------
# Checks that need real values: the split is abandoned, the answer is not
# --------------------------------------------------------------------------


def bump(counter, amount):
    """Changes its argument -- which no reading of the caller's source shows."""
    counter["total"] += amount
    return counter["total"]


def tallied(values, counter):
    out = []
    for value in values:
        out.append(bump(counter, value))
    return out


def collected_into(values, out, seen):
    for value in values:
        out.append(value + len(seen))
    return list(out)


def rows_extended(rows):
    lengths = []
    for row in rows:
        row.append(sum(row))
        lengths.append(len(row))
    return lengths


def _tallied_case():
    counter = {"total": 100}
    return (list(range(1, 9)), counter), counter


def _aliased_case():
    shared = [10]
    return ([1, 2, 3, 4], shared, shared), shared


def _rows_case():
    rows = [[1, 2], [3], [4, 5, 6], [7]]
    return (rows,), rows


@needs_two_cpus
@pytest.mark.parametrize(
    "function, case, reason",
    [
        (tallied, _tallied_case, "the loop changed counter"),
        (collected_into, _aliased_case, "seen can reach the same object as out"),
        (rows_extended, _rows_case, "the loop changed the items being looped over"),
    ],
    ids=["hidden-mutation", "aliased-collector", "items-mutated"],
)
def test_a_split_found_unsafe_at_run_time_is_abandoned_for_the_direct_answer(
    function, case, reason, caplog
):
    arguments, watched = case()
    direct = function(*arguments)
    state_after_direct = repr(watched)

    arguments, watched = case()
    with caplog.at_level(logging.WARNING, logger="clustrix.decorator"):
        result = cluster(cores=4)(function)(*arguments)

    assert result == direct
    assert (
        repr(watched) == state_after_direct
    ), "the caller's object is not as a direct call leaves it"
    said = records(caplog, logging.WARNING, f"{function.__name__}: not split")
    assert len(said) == 1 and reason in said[0], caplog.text


def printing_tally(values, counter):
    out = []
    for value in values:
        print(f"adding {value}")
        out.append(bump(counter, value))
    return out


@needs_two_cpus
def test_an_abandoned_split_does_not_print_the_loop_s_output_twice(capsys):
    """The chunks ran, and printed, before the split was found unsafe. The
    loop then runs again here and prints for itself; replaying what the
    workers captured as well showed every line twice."""
    result = cluster(cores=4)(printing_tally)([1, 2, 3, 4], {"total": 0})

    assert result == [1, 3, 6, 10]
    assert capsys.readouterr().out == "".join(f"adding {v}\n" for v in [1, 2, 3, 4])


# --------------------------------------------------------------------------
# Exceptions and output
# --------------------------------------------------------------------------


class Unlucky(Exception):
    pass


def trips_on(values, bad, rounds):
    out = []
    for value in values:
        spin(rounds)
        print(f"reached {value}")
        if value in bad:
            raise Unlucky(f"value {value} is not allowed")
        out.append(value)
    return out


@needs_two_cpus
def test_an_exception_in_an_iteration_arrives_as_itself_with_the_worker_s_traceback(
    rounds, capsys
):
    values = list(range(16))
    decorated = cluster(cores=4)(trips_on)

    with pytest.raises(Unlucky) as raised:
        decorated(values, {6, 13}, rounds // 4)

    # The one a sequential run would have hit first, not whichever chunk
    # happened to finish first.
    assert raised.value.args == ("value 6 is not allowed",)
    assert isinstance(raised.value.__cause__, RemoteTraceback)
    shown = "".join(traceback.format_exception(raised.value))
    assert "Unlucky: value 6 is not allowed" in shown
    # The worker's frame is the generated loop body, numbered as the user's
    # editor numbers the line that raised. (Its file name is not the user's:
    # cloudpickle replaces a shipped code object's with "<dynamic-code>".)
    lines, first = inspect.getsourcelines(trips_on)
    raising = first + next(i for i, line in enumerate(lines) if "raise" in line)
    assert f"line {raising}, in __cx_body" in str(raised.value.__cause__), shown

    # What the iterations before the failing one printed is not lost.
    printed = capsys.readouterr().out
    assert printed.startswith("".join(f"reached {v}\n" for v in range(7))), printed


def announces(values, rounds):
    out = []
    for value in values:
        spin(rounds)
        print(f"item {value}")
        out.append(value)
    print("after the loop")
    return out


@needs_two_cpus
def test_what_the_loop_prints_appears_once_in_iteration_order(rounds, capsys):
    values = list(range(16))

    assert cluster(cores=4)(announces)(values, rounds // 4) == values

    assert capsys.readouterr().out == (
        "".join(f"item {v}\n" for v in values) + "after the loop\n"
    )


# --------------------------------------------------------------------------
# The shapes a function comes in
# --------------------------------------------------------------------------


class Scaler:
    def __init__(self, factor):
        self.factor = factor

    @cluster(cores=4)
    def scale(self, values):
        out = []
        for value in values:
            out.append((self.factor * value, os.getpid()))
        return out

    def scale_directly(self, values):
        return [self.factor * value for value in values]


@cluster(cores=4)
def flexible(first, *rest, scale=2, offset=0, **named):
    out = []
    for value in (first, *rest):
        out.append((value * scale + offset + sum(named.values()), os.getpid()))
    return out


def make_scaled(multiplier):
    shift = multiplier + 1

    @cluster(cores=4)
    def scaled(values):
        out = []
        for value in values:
            out.append((multiplier * value + shift, os.getpid()))
        return out

    return scaled


def triple(value):
    return 3 * value


@cluster(cores=4)
def all_tripled(values):
    return [(triple(value), os.getpid()) for value in values]


@cluster(cores=4)
def tripled_by_key(values):
    return {value: (triple(value), os.getpid()) for value in values if value % 2}


@needs_two_cpus
def test_a_method_is_divided_and_sees_self():
    scaler = Scaler(7)

    result = scaler.scale([1, 2, 3, 4])

    assert values_of(result) == scaler.scale_directly([1, 2, 3, 4])
    assert os.getpid() not in pids_of(result)
    assert "SPLIT" in explain(Scaler.scale)


@needs_two_cpus
def test_star_args_keyword_only_defaults_and_double_star_are_bound_as_python_does():
    result = flexible(1, 2, 3, offset=10, extra=100, more=1000)
    assert values_of(result) == [v * 2 + 10 + 1100 for v in (1, 2, 3)]
    assert os.getpid() not in pids_of(result)

    assert values_of(flexible(5)) == [10]
    assert values_of(flexible(5, 6, scale=3)) == [15, 18]
    with pytest.raises(TypeError):
        flexible()  # the function's own signature still refuses this


@needs_two_cpus
def test_a_closure_defined_inside_another_function_keeps_its_outer_variables():
    result = make_scaled(5)([1, 2, 3])
    assert values_of(result) == [5 * v + 6 for v in (1, 2, 3)]
    assert os.getpid() not in pids_of(result)

    # Two closures over different values are two functions, not one plan.
    assert values_of(make_scaled(2)([1, 2, 3])) == [2 * v + 3 for v in (1, 2, 3)]


@needs_two_cpus
def test_a_body_that_is_only_a_comprehension_is_divided():
    result = all_tripled([1, 2, 3, 4, 5])
    assert values_of(result) == [3, 6, 9, 12, 15]
    assert os.getpid() not in pids_of(result)

    by_key = tripled_by_key([1, 2, 3, 4, 5])
    assert {key: value for key, (value, _) in by_key.items()} == {1: 3, 3: 9, 5: 15}
    assert list(by_key) == [1, 3, 5], "insertion order is the loop's order"
    assert os.getpid() not in {pid for _, pid in by_key.values()}


def test_an_empty_loop_returns_what_the_function_returns():
    assert cluster(cores=4)(squares_and_pids)([], 1) == []
    assert all_tripled([]) == []


def test_explain_works_on_the_decorated_function():
    decorated = cluster(cores=4)(squares_and_pids)

    account = explain(decorated)

    assert account == explain(squares_and_pids) == clustrix.explain(decorated)
    assert account.startswith("squares_and_pids:")
    assert "for value in values` -- SPLIT" in account
    assert "collected in order: out" in account
    assert "code before the loop runs in the caller" in account


# --------------------------------------------------------------------------
# What changes between calls
# --------------------------------------------------------------------------

FACTOR = 2


def uses_a_global(values):
    out = []
    for value in values:
        out.append((value * FACTOR, os.getpid()))
    return out


@needs_two_cpus
def test_configure_between_calls_is_honoured_by_an_already_decorated_function():
    """The plan is made once per function; where it runs is decided per call."""
    decorated = cluster(cores=4)(uses_a_global)
    here = {os.getpid()}

    assert os.getpid() not in pids_of(decorated([1, 2, 3]))

    configure(auto_parallel=False)
    assert pids_of(decorated([1, 2, 3])) == here

    configure(auto_parallel=True)
    assert os.getpid() not in pids_of(decorated([1, 2, 3]))

    configure(default_cores=1)  # the decorator's own cores=4 still wins
    assert os.getpid() not in pids_of(decorated([1, 2, 3]))

    bare = cluster(uses_a_global)  # ... and without one, the default decides
    assert pids_of(bare([1, 2, 3])) == here
    configure(default_cores=2)
    assert os.getpid() not in pids_of(bare([1, 2, 3]))


@needs_two_cpus
def test_a_global_rebound_between_calls_is_seen_by_the_workers(monkeypatch):
    decorated = cluster(cores=4)(uses_a_global)
    assert values_of(decorated([1, 2, 3])) == [2, 4, 6]

    monkeypatch.setattr(sys.modules[__name__], "FACTOR", 10)

    result = decorated([1, 2, 3])
    assert values_of(result) == [10, 20, 30] == values_of(uses_a_global([1, 2, 3]))
    assert os.getpid() not in pids_of(result)


# --------------------------------------------------------------------------
# Several callers at once, and a caller that is itself a worker
# --------------------------------------------------------------------------


@needs_two_cpus
def test_four_threads_calling_at_once_each_get_their_own_answer(rounds):
    decorated = cluster(cores=4)(squares_and_pids)
    decorated([0, 1], rounds // 20)  # the pool exists before the race starts
    answers, errors = {}, []

    def call(index):
        try:
            answers[index] = decorated(list(range(index, index + 12)), rounds // 20)
        except BaseException as error:  # noqa: BLE001 - asserted on below
            errors.append(repr(error))

    threads = [threading.Thread(target=call, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert sorted(answers) == [0, 1, 2, 3]
    for index, result in answers.items():
        assert values_of(result) == [v * v for v in range(index, index + 12)]
    everywhere = set().union(*(pids_of(result) for result in answers.values()))
    assert os.getpid() not in everywhere
    # Four callers share the workers; they do not each bring their own.
    assert len(everywhere) <= shared_pool(1).size, everywhere


@cluster(cores=4)
def inner_loop(values, rounds):
    out = []
    for value in values:
        spin(rounds)
        out.append((value, os.getpid()))
    return out


@cluster(cores=4)
def outer_loop(count, rounds):
    out = []
    for index in range(count):
        out.append((os.getpid(), inner_loop([index, index + 1, index + 2], rounds)))
    return out


@needs_two_cpus
def test_a_divided_loop_that_calls_a_decorated_function_does_not_divide_again(rounds):
    """Each worker would otherwise start ``cores`` workers of its own: this
    test's four-worker loop was measured running in twenty processes."""
    result = outer_loop(8, rounds // 3)

    assert [values_of(inner) for _, inner in result] == [
        [index, index + 1, index + 2] for index in range(8)
    ]
    outer_pids = {pid for pid, _ in result}
    assert os.getpid() not in outer_pids and len(outer_pids) >= 2
    for outer_pid, inner in result:
        assert pids_of(inner) == {
            outer_pid
        }, "the inner loop left the worker that called it: a pool inside a pool"
    assert len(outer_pids) <= min(4, CPUS)
