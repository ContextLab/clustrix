"""Splitting a loop must never change what a function returns.

Every function here is run twice -- directly, and through
``clustrix.parallelize`` with its loop cut into chunks -- and the two results
are compared. Nothing is mocked: the split functions are real compiled code,
and the process-pool tests ship them to real worker processes with
cloudpickle, the way a backend would.

The second half is the other side of the contract: loops whose iterations
are *not* independent must be refused, with a reason that names the line.
"""

import copy
import math
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

import cloudpickle
import pytest

from clustrix import parallelize

LEDGER = []


def in_process(body, env, chunks):
    """Run every chunk here, each on its own copy of the environment.

    The copy is what makes this an honest stand-in for separate jobs: a chunk
    that mutates shared state must not be able to pass it to the next one.
    It is made the way a backend makes it, by serializing.
    """
    shipped = cloudpickle.dumps(env)
    return [body(cloudpickle.loads(shipped), chunk) for chunk in chunks]


def _run_pickled(payload):
    body, env, chunk = cloudpickle.loads(payload)
    return body(env, chunk)


def in_worker_processes(body, env, chunks):
    """Ship each chunk to a separate, freshly spawned Python process."""
    payloads = [cloudpickle.dumps((body, env, chunk)) for chunk in chunks]
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        return list(pool.map(_run_pickled, payloads))


def split_run(func, *args, jobs=3, backend=in_process, **kwargs):
    plan = parallelize.plan(func)
    assert plan.parallelizable, plan.explain()
    return parallelize.run(plan, func, args, kwargs, backend, jobs=jobs)


# -- functions whose loops are independent -----------------------------------


def squares(n):
    out = []
    for i in range(n):
        out.append(i * i)
    return out


def filtered_comprehension(xs, k):
    return [x * k for x in xs if x % 2]


def dict_comprehension(xs):
    table = {x: x**2 for x in xs}
    return table


def set_comprehension(words):
    return {w.lower() for w in words}


def float_sum(xs):
    total = 0.0
    for x in xs:
        total += math.sqrt(x) / 7
    return total


def several_accumulators(pairs):
    kept = {}
    count = 0
    names = []
    for i, (key, value) in enumerate(pairs):
        doubled = value * 2
        if doubled > 4:
            continue
        kept[i] = (key, doubled)
        names.append(key)
        count += 1
    return kept, count, names


def writes_into_a_grid(n):
    grid = [[0] * 3 for _ in range(n)]
    for i in range(n):
        for j in range(3):
            grid[i][j] = i * j
    return grid


def returns_before_the_loop(xs):
    if not xs:
        return "empty"
    out = []
    for x in xs:
        out.append(-x)
    return out


def non_commutative_concatenation(words):
    text = ""
    for word in words:
        text = text + word.upper()
    return text


def prepends(words):
    text = ""
    for word in words:
        text = word + text
    return text


def code_after_the_loop(xs, scale):
    values = []
    for x in xs:
        values.append(x * scale)
    best = max(values)
    return {"best": best, "n": len(values), "scale": scale}


def uses_helpers_and_closures(xs):
    offset = 10

    def shift(value):
        return value + offset

    out = []
    for x in xs:
        out.append(shift(x))
    return out


def reuses_a_name_as_a_temporary(xs):
    tmp = "set before the loop"
    out = [tmp]
    for x in xs:
        tmp = x * 3
        out.append(tmp)
    return out


def try_inside_the_body(xs):
    out, failures = [], []
    for x in xs:
        try:
            value = 10 // x
        except ZeroDivisionError as error:
            failures.append(str(error))
            continue
        out.append(value)
    return out, failures


def slice_assignment(n):
    flat = [0] * (2 * n)
    for i in range(n):
        flat[2 * i : 2 * i + 2] = [i, -i]
    return flat


def nested_accumulator(keys):
    buckets = {"even": [], "odd": []}
    for k in keys:
        buckets["even" if k % 2 == 0 else "odd"].append(k)
    return buckets


def method_with_keywords(rows):
    table = []
    for row in rows:
        table.insert(0, row)
    table.sort(key=len)
    return table


def keyword_and_default_arguments(xs, *, power=2, **extra):
    out = []
    for x in xs:
        out.append((x**power, sorted(extra)))
    return out


def seeds_its_own_generator(n):
    import random

    out = []
    for i in range(n):
        generator = random.Random(i)
        out.append(generator.random())
    return out


def seeds_the_global_generator_each_iteration(n):
    import random

    out = []
    for i in range(n):
        random.seed(i)
        out.append(random.random())
    return out


def imports_inside_and_calls_into_the_module(n):
    import math as m

    out = []
    for i in range(n):
        m.floor(i)
        out.append(m.sqrt(i))
    return out


INDEPENDENT = [
    (squares, (10,), {}),
    (squares, (0,), {}),
    (squares, (1,), {}),
    (filtered_comprehension, ([1, 2, 3, 4, 5], 3), {}),
    (dict_comprehension, ([3, 1, 2],), {}),
    (set_comprehension, (["A", "b", "B"],), {}),
    (float_sum, (list(range(1, 200)),), {}),
    (several_accumulators, ([("a", 1), ("b", 2), ("c", 5), ("d", 0)],), {}),
    (writes_into_a_grid, (5,), {}),
    (returns_before_the_loop, ([],), {}),
    (returns_before_the_loop, ([1, 2, 3],), {}),
    (non_commutative_concatenation, (["a", "b", "c", "d"],), {}),
    (prepends, (["a", "b", "c", "d"],), {}),
    (code_after_the_loop, ([4, 9, 2], 1.5), {}),
    (uses_helpers_and_closures, ([1, 2, 3],), {}),
    (reuses_a_name_as_a_temporary, ([1, 2, 3],), {}),
    (try_inside_the_body, ([1, 0, 5, 0, 2],), {}),
    (slice_assignment, (4,), {}),
    (nested_accumulator, (list(range(9)),), {}),
    (method_with_keywords, (["ccc", "a", "bb", "dddd"],), {}),
    (keyword_and_default_arguments, ([1, 2, 3],), {"power": 3, "tag": 1, "b": 2}),
    (seeds_its_own_generator, (6,), {}),
    (seeds_the_global_generator_each_iteration, (6,), {}),
    (imports_inside_and_calls_into_the_module, (6,), {}),
]


@pytest.mark.parametrize(
    "func,args,kwargs", INDEPENDENT, ids=lambda value: getattr(value, "__name__", None)
)
@pytest.mark.parametrize("jobs", [1, 2, 3, 50])
def test_a_split_run_returns_exactly_what_a_direct_call_does(func, args, kwargs, jobs):
    expected = func(*copy.deepcopy(args), **copy.deepcopy(kwargs))

    got = split_run(func, *copy.deepcopy(args), jobs=jobs, **copy.deepcopy(kwargs))

    assert got == expected
    assert type(got) is type(expected)


def test_a_float_sum_is_bit_identical_not_merely_close():
    """Partial sums merged per chunk would reorder the additions. The log is
    replayed in iteration order instead, so not one bit differs."""
    values = [0.1 * i for i in range(1, 2000)]

    assert split_run(float_sum, values, jobs=7).hex() == float_sum(values).hex()


@pytest.mark.parametrize(
    "func,args",
    [
        (squares, (23,)),
        (several_accumulators, ([("a", 1), ("b", 2), ("c", 5), ("d", 0)],)),
        (uses_helpers_and_closures, ([1, 2, 3, 4, 5],)),
        (code_after_the_loop, ([4, 9, 2, 7], 2)),
        (seeds_its_own_generator, (8,)),
    ],
    ids=lambda value: getattr(value, "__name__", None),
)
def test_the_split_survives_real_worker_processes(func, args):
    """The chunks cross a process boundary, pickled, as a backend sends them."""
    expected = func(*copy.deepcopy(args))

    assert split_run(func, *args, jobs=4, backend=in_worker_processes) == expected


def test_chunks_cover_every_item_once_in_order():
    for total in range(0, 25):
        for jobs in range(1, 9):
            chunks = parallelize.chunk(list(range(total)), jobs)
            assert [x for c in chunks for x in c] == list(range(total))
            assert len(chunks) <= max(1, jobs)
            if total:
                sizes = [len(c) for c in chunks]
                assert max(sizes) - min(sizes) <= 1 and min(sizes) >= 1


def test_a_range_is_chunked_without_being_materialised():
    chunks = parallelize.chunk(range(10**12), 4)

    assert all(isinstance(c, range) for c in chunks)
    assert sum(len(c) for c in chunks) == 10**12


def test_an_exception_in_an_iteration_propagates_with_its_type():
    def divides(xs):
        out = []
        for x in xs:
            out.append(1 // x)
        return out

    with pytest.raises(ZeroDivisionError):
        split_run(divides, [1, 2, 0, 4], jobs=2)


# -- what only real values can show ------------------------------------------
#
# Each of these loops *looks* independent: nothing in the source proves
# otherwise. The split is attempted, a run-time check catches the problem,
# and the original loop runs instead -- in order, once, on the real objects.


def run_with_fallback(func, *args, jobs=2, backend=in_process):
    plan = parallelize.plan(func)
    assert plan.parallelizable, plan.explain()
    reasons = []
    result = parallelize.run(
        plan, func, args, {}, backend, jobs, on_fallback=reasons.append
    )
    return result, reasons


def mutates_what_it_reads(xs, state):
    out = []
    for x in xs:
        out.append(state.setdefault("first", x))
    return out


def test_a_loop_that_mutates_shared_state_falls_back_to_the_true_answer():
    expected_state = {}
    expected = mutates_what_it_reads([1, 2, 3, 4], expected_state)

    state = {}
    result, reasons = run_with_fallback(mutates_what_it_reads, [1, 2, 3, 4], state)

    assert result == expected == [1, 1, 1, 1]
    assert (
        state == expected_state
    ), "the caller's object ends as a direct call leaves it"
    assert len(reasons) == 1 and "state" in reasons[0]


def remember(seen, value):
    """A helper that reads -- and so depends on -- what was collected so far."""
    return value + 10 * len(seen)


def collects_into_an_aliased_list(xs):
    out = []
    seen = out  # the same list under another name
    for x in xs:
        out.append(remember(seen, x))
    return out


def test_a_collector_reachable_through_another_name_is_caught_before_any_chunk():
    """The research prototype of this design returned [11, 22, 31, 42] here
    where a direct call gives [1, 12, 23, 34]: the helper sees the list grow
    in a sequential run and sees a stale copy in a split one."""
    chunks_run = []

    def counting_backend(body, env, chunks):
        chunks_run.append(len(chunks))
        return in_process(body, env, chunks)

    plan = parallelize.plan(collects_into_an_aliased_list)
    assert plan.parallelizable, plan.explain()
    reasons = []

    result = parallelize.run(
        plan,
        collects_into_an_aliased_list,
        ([1, 2, 3, 4],),
        {},
        counting_backend,
        2,
        on_fallback=reasons.append,
    )

    assert result == collects_into_an_aliased_list([1, 2, 3, 4]) == [1, 12, 23, 34]
    assert chunks_run == [], "refused before a single chunk was sent anywhere"
    assert "seen" in reasons[0] and "out" in reasons[0]


def collects_inside_a_container_that_is_read(xs):
    out = []
    box = {"results": out}
    for x in xs:
        out.append(x + len(box["results"]))
    return out


def test_an_alias_buried_in_a_container_is_found_too():
    result, reasons = run_with_fallback(
        collects_inside_a_container_that_is_read, [5, 5, 5]
    )

    assert result == collects_inside_a_container_that_is_read([5, 5, 5]) == [5, 6, 7]
    assert "box" in reasons[0]


def marks_each_item(records):
    done = []
    for record in records:
        record["seen"] = True
        done.append(record["id"])
    return done


def test_items_modified_in_place_are_modified_for_the_caller_too():
    """A worker would mark its own copies and the caller's would stay as
    they were. The chunk reports that its items changed."""
    records = [{"id": i} for i in range(4)]

    result, reasons = run_with_fallback(marks_each_item, records)

    assert result == [0, 1, 2, 3]
    assert all(record.get("seen") for record in records)
    assert "items being looped over" in reasons[0]


def draw():
    import random

    return random.random()


def draws_through_a_helper(n):
    out = []
    for _ in range(n):
        out.append(draw())
    return out


def test_a_helper_that_uses_the_global_generator_is_caught_at_run_time():
    """The loop body never mentions ``random``; only running it shows that
    the shared generator moved. The chunks run in other processes, as they do
    under a real backend, so this process's generator is where a direct call
    would find it when the original loop takes over."""
    import random

    random.seed(1234)
    expected = draws_through_a_helper(6)

    random.seed(1234)
    result, reasons = run_with_fallback(
        draws_through_a_helper, 6, jobs=3, backend=in_worker_processes
    )

    assert result == expected
    assert "random number generator" in reasons[0]


def test_the_code_before_the_loop_runs_once_even_when_the_split_is_abandoned():
    calls = []

    def expensive_setup():
        calls.append("setup")
        return {}

    def setup_then_mutate(xs):
        state = expensive_setup()
        out = []
        for x in xs:
            out.append(state.setdefault("first", x))
        return out

    result, reasons = run_with_fallback(setup_then_mutate, [7, 8, 9])

    assert result == [7, 7, 7]
    assert reasons and calls == ["setup"]


def test_no_fallback_is_reported_when_the_split_goes_through():
    result, reasons = run_with_fallback(squares, 9, jobs=4)

    assert result == squares(9) and reasons == []


def test_the_code_around_the_loop_goes_through_invoke_unless_it_is_cheap():
    """``invoke`` is how a backend runs ``pre`` and ``post`` as jobs."""

    def work_around(n):
        base = sum(k for k in range(n))
        out = []
        for i in range(n):
            out.append(base + i)
        return squares(len(out)) + out

    invoked = []

    def recording_invoke(function, *arguments):
        invoked.append(function.__name__)
        return function(*arguments)

    for func, expected_calls in (
        (work_around, ["__cx_pre", "__cx_post"]),
        (squares, []),
    ):
        invoked.clear()
        plan = parallelize.plan(func)
        got = parallelize.run(
            plan, func, (5,), {}, in_process, 2, invoke=recording_invoke
        )
        assert got == func(5)
        assert invoked == expected_calls


# -- loops that must be refused ----------------------------------------------


def running_total(xs):
    out, previous = [], 0
    for x in xs:
        previous = previous + x
        out.append(previous)
    return out


def stops_early(xs):
    out = []
    for x in xs:
        if x > 3:
            break
        out.append(x)
    return out


def reads_what_it_collects(xs):
    out = []
    for x in xs:
        out.append(x + len(out))
    return out


def writes_a_global(xs):
    for x in xs:
        LEDGER.append(x)
    return len(LEDGER)


def uses_the_last_value(xs):
    for x in xs:
        doubled = x * 2
    return doubled


def unseeded_global_random(n):
    import random

    out = []
    for _ in range(n):
        out.append(random.random())
    return out


def returns_from_the_loop(xs):
    for x in xs:
        if x < 0:
            return x
    return None


def conditional_carry(xs):
    out = []
    for x in xs:
        if x > 0:
            last_positive = x
        out.append(last_positive)
    return out


def loop_with_else(xs):
    out = []
    for x in xs:
        out.append(x)
    else:
        out.append("done")
    return out


def augmented_and_tested(xs):
    total = 0
    out = []
    for x in xs:
        total += x
        if total > 5:
            out.append(x)
    return out


def assigns_a_declared_global(xs):
    global LEDGER
    for x in xs:
        LEDGER = [x]
    return LEDGER


def yields(xs):
    for x in xs:
        yield x


REFUSED = [
    (running_total, "previous", "previous iteration"),
    (stops_early, "break", "break"),
    (reads_what_it_collects, "out", "also read"),
    (writes_a_global, "LEDGER", "global"),
    (uses_the_last_value, "doubled", "after it"),
    (unseeded_global_random, "random.random", "random generator"),
    (returns_from_the_loop, "returns", "returns"),
    (conditional_carry, "last_positive", "previous iteration"),
    (loop_with_else, "else", "else"),
    (augmented_and_tested, "total", "previous iteration"),
    (assigns_a_declared_global, "LEDGER", "global"),
]


@pytest.mark.parametrize(
    "func,names,because", REFUSED, ids=lambda value: getattr(value, "__name__", None)
)
def test_a_dependent_loop_is_refused_and_the_reason_names_the_cause(
    func, names, because
):
    plan = parallelize.plan(func)

    assert not plan.parallelizable
    reasons = " | ".join(b.reason for loop in plan.loops for b in loop.blockers)
    assert names in reasons and because in reasons, reasons
    with pytest.raises(ValueError):
        parallelize.bind(plan, func)


def test_a_generator_is_not_analysed_at_all():
    plan = parallelize.plan(yields)

    assert not plan.parallelizable
    assert "generators" in plan.unavailable


def test_a_function_without_source_is_declined_not_crashed_on():
    namespace = {}
    exec("def made_up(xs):\n    return [x for x in xs]\n", namespace)

    plan = parallelize.plan(namespace["made_up"])

    assert not plan.parallelizable
    assert "source is not available" in plan.unavailable


def test_blockers_report_the_line_in_the_file_not_in_the_function():
    import inspect

    plan = parallelize.plan(stops_early)
    first_line = inspect.getsourcelines(stops_early)[1]

    (blocker,) = plan.loops[0].blockers
    assert blocker.line == first_line + 4  # the `break` statement itself


def test_explain_says_what_was_decided_in_words():
    split = parallelize.explain(several_accumulators)
    refused = parallelize.explain(running_total)

    assert "SPLIT" in split
    assert "collected in order: count, kept, names" in split
    assert "private to each iteration: doubled" in split
    assert "not split" in refused and "previous" in refused


def test_the_first_splittable_loop_is_chosen_and_the_rest_still_run():
    def two_loops(xs):
        firsts = []
        for x in xs:
            firsts.append(x + 1)
        seconds = []
        for y in firsts:
            seconds.append(y * 2)
        return seconds

    plan = parallelize.plan(two_loops)

    assert [loop.parallelizable for loop in plan.loops] == [True, True]
    assert plan.chosen is plan.loops[0]
    assert split_run(two_loops, [1, 2, 3]) == two_loops([1, 2, 3])


def test_a_later_loop_is_split_when_an_earlier_one_cannot_be():
    def carried_then_free(xs):
        running, previous = [], 0
        for x in xs:
            previous = previous + x
            running.append(previous)
        out = []
        for r in running:
            out.append(r * r)
        return out

    plan = parallelize.plan(carried_then_free)

    assert [loop.parallelizable for loop in plan.loops] == [False, True]
    assert split_run(carried_then_free, [1, 2, 3, 4]) == carried_then_free([1, 2, 3, 4])


def test_where_the_surrounding_code_may_run_is_decided_from_what_it_does():
    """Unpacking arguments can happen in the caller; real work is a job."""

    def trivial_around(xs):
        out = []
        for x in xs:
            out.append(x)
        return out

    def work_before(path):
        rows = open(path).read().split()
        out = []
        for row in rows:
            out.append(row)
        return out

    cheap, costly = parallelize.plan(trivial_around), parallelize.plan(work_before)

    assert cheap.pre_is_cheap and cheap.post_is_cheap
    assert not costly.pre_is_cheap and costly.post_is_cheap


def test_a_global_rebound_after_planning_is_seen_by_the_split():
    """The plan is compiled once; what the function reads is looked up per call."""
    namespace = {"FACTOR": 2}
    source = "def scaled(xs):\n    return [x * FACTOR for x in xs]\n"
    import linecache

    filename = "<scaled-for-test>"
    linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
    exec(compile(source, filename, "exec"), namespace)
    scaled = namespace["scaled"]
    plan = parallelize.plan(scaled)
    assert plan.parallelizable, plan.explain()

    assert parallelize.run(plan, scaled, ([1, 2],), {}, in_process, 2) == [2, 4]
    namespace["FACTOR"] = 10
    assert parallelize.run(plan, scaled, ([1, 2],), {}, in_process, 2) == [10, 20]


# -- declining a split that is safe but not worth making ---------------------


def tally_into(xs, seen):
    """Mutates an argument through an accumulator: a direct call leaves
    ``seen`` extended, and so must any way of running this."""
    for x in xs:
        seen.append(x * 2)
    return len(seen)


def run_recording(func, *args, should_split, jobs=3):
    """Run through the engine and report everything the hooks were told."""
    told = {"chunks": 0, "asked": [], "unsplit": [], "fallback": []}

    def counting_backend(body, env, chunks):
        told["chunks"] += len(chunks)
        return in_process(body, env, chunks)

    def ask(count):
        told["asked"].append(count)
        return should_split

    result = parallelize.run(
        parallelize.plan(func),
        func,
        args,
        {},
        counting_backend,
        jobs,
        on_fallback=told["fallback"].append,
        should_split=ask,
        on_unsplit=lambda count, seconds: told["unsplit"].append((count, seconds)),
    )
    return result, told


def test_a_declined_split_runs_the_original_loop_on_the_real_objects():
    mine, reference = [0], [0]
    expected = tally_into([1, 2, 3, 4, 5], reference)

    result, told = run_recording(tally_into, [1, 2, 3, 4, 5], mine, should_split=False)

    assert result == expected
    assert mine == reference == [0, 2, 4, 6, 8, 10]
    assert told["asked"] == [5]
    assert told["chunks"] == 0, "the backend was used for a split nobody wanted"
    assert told["fallback"] == [], "declining is a choice, not a failed check"
    ((count, seconds),) = told["unsplit"]
    assert count == 5 and seconds > 0


def test_an_accepted_split_is_the_split_it_always_was():
    result, told = run_recording(squares, 9, should_split=True)

    assert result == squares(9)
    assert told["asked"] == [9] and told["chunks"] == 3
    assert told["unsplit"] == [] and told["fallback"] == []


def returns_before_its_loop(xs):
    if not xs:
        return "nothing to do"
    out = []
    for x in xs:
        out.append(x)
    return out


def test_nobody_is_asked_about_a_loop_that_has_nothing_to_run():
    result, told = run_recording(squares, 0, should_split=False)
    assert result == [] and told["asked"] == [] and told["unsplit"] == []

    result, told = run_recording(returns_before_its_loop, [], should_split=False)
    assert result == "nothing to do" and told["asked"] == []


def test_a_split_abandoned_for_aliasing_is_not_asked_about_either():
    """The aliasing check comes first: there is no split left to decline."""

    def collect(xs, out, seen):
        for x in xs:
            out.append(x + len(seen))
        return list(out)

    shared, reference = [1], [1]
    expected = collect([1, 2], reference, reference)

    result, told = run_recording(collect, [1, 2], shared, shared, should_split=False)

    assert result == expected == [1, 2, 4]
    assert told["asked"] == [] and len(told["fallback"]) == 1


# -- what may run in the caller ----------------------------------------------


def test_imports_and_empty_containers_before_a_loop_do_not_cost_a_job():
    def only_imports(xs):
        import os
        from math import sqrt

        out = []
        for x in xs:
            out.append(sqrt(x) + len(os.sep))
        return out

    def unpacked_containers(xs):
        finals, pids = {}, set()
        [first, second] = (0, [])
        for x in xs:
            finals[x] = x
            pids.add(x)
        return finals, pids, first, second

    def loads_first(xs):
        table = load_table()  # noqa: F821 - never run, only analysed
        out = []
        for x in xs:
            out.append(table[x])
        return out

    def imports_then_reads(path):
        import os

        rows = open(os.path.expanduser(path)).read().split()
        out = []
        for row in rows:
            out.append(row)
        return out

    def assigns_into_an_object(xs, holder):
        holder.started = True
        out = []
        for x in xs:
            out.append(x)
        return out

    cheap = {
        f.__name__: parallelize.plan(f).pre_is_cheap
        for f in (
            only_imports,
            unpacked_containers,
            loads_first,
            imports_then_reads,
            assigns_into_an_object,
        )
    }

    assert cheap == {
        "only_imports": True,
        "unpacked_containers": True,
        "loads_first": False,
        "imports_then_reads": False,
        "assigns_into_an_object": False,
    }
    assert split_run(only_imports, [1, 4, 9]) == only_imports([1, 4, 9])
    assert split_run(unpacked_containers, [3, 1]) == unpacked_containers([3, 1])


def test_an_import_that_fails_in_the_caller_is_retried_where_the_work_runs():
    """``pre`` runs in the caller when it only imports -- but the module may
    be installed on the cluster and not here. That must not become an
    ImportError the user would never have seen from a single remote job."""
    import sys
    import types

    def needs_a_remote_library(xs):
        import only_on_the_cluster

        out = []
        for x in xs:
            out.append(only_on_the_cluster.double(x))
        return out

    plan = parallelize.plan(needs_a_remote_library)
    assert plan.pre_is_cheap
    invoked = []

    def on_the_cluster(function, *arguments):
        invoked.append(function.__name__)
        library = types.ModuleType("only_on_the_cluster")
        library.double = lambda x: 2 * x
        sys.modules["only_on_the_cluster"] = library
        try:
            return function(*arguments)
        finally:
            del sys.modules["only_on_the_cluster"]

    def chunks_on_the_cluster(body, env, chunks):
        return [on_the_cluster(body, dict(env), chunk) for chunk in chunks]

    result = parallelize.run(
        plan,
        needs_a_remote_library,
        ([1, 2, 3],),
        {},
        chunks_on_the_cluster,
        2,
        invoke=on_the_cluster,
    )

    assert result == [2, 4, 6]
    assert invoked[0] == "__cx_pre", invoked

    # With nowhere else to run it, the ImportError is the user's to see.
    with pytest.raises(ImportError):
        parallelize.run(plan, needs_a_remote_library, ([1],), {}, in_process, 2)


# -- defects found while writing the documentation ----------------------------


def test_a_loop_variable_reused_by_a_later_loop_does_not_block_the_first():
    """``i`` after the first loop is not the value it left: the second loop
    assigns it before anything reads it."""

    def two_passes(xs, ys):
        firsts = []
        for i in xs:
            firsts.append(i * 2)
        seconds = []
        for i in ys:
            seconds.append(i + 1)
        return firsts, seconds

    plan = parallelize.plan(two_passes)

    assert plan.loops[0].parallelizable, plan.explain()
    assert split_run(two_passes, [1, 2, 3], [7, 8]) == two_passes([1, 2, 3], [7, 8])


def test_a_value_genuinely_used_after_the_loop_still_blocks_it():
    def reads_the_last(xs):
        for x in xs:
            last = x
        print(last)
        for x in xs:
            pass
        return last

    plan = parallelize.plan(reads_the_last)

    assert not plan.loops[0].parallelizable
    assert "last" in plan.loops[0].blockers[0].reason


def test_source_that_belongs_to_another_function_is_not_used():
    """A function compiled from a string keeps a file name and line number
    that ``inspect`` may resolve to unrelated text. ``plan`` must decline --
    not raise (``tokenize.TokenError`` escaped here), and not split one
    function by another's source."""
    namespace = {}
    code = compile(
        "def squares(different, names):\n    return [n for n in names]\n",
        __file__,  # a real file: inspect will happily read *this* module
        "exec",
    )
    exec(code, namespace)

    plan = parallelize.plan(namespace["squares"])

    assert not plan.parallelizable
    assert plan.unavailable and "source" in plan.unavailable


def test_explain_names_a_comprehension_s_variables_as_the_source_does():
    report = parallelize.explain(filtered_comprehension)

    assert "__cx_" not in report
    assert "for x in xs" in report
