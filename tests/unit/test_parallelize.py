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


def test_the_caller_s_arguments_are_not_modified_by_a_refused_attempt():
    def mutates_its_input(xs, state):
        out = []
        for x in xs:
            out.append(state.setdefault("first", x))
        return out

    state = {}
    plan = parallelize.plan(mutates_its_input)
    assert plan.parallelizable  # nothing in the source proves the mutation

    with pytest.raises(parallelize.SharedStateModified) as refused:
        parallelize.run(
            plan, mutates_its_input, ([1, 2, 3, 4], state), {}, in_process, 2
        )

    assert refused.value.names == ["state"]
    assert state == {}, "the attempt ran on copies; the real object is untouched"
    # ...so running it for real afterwards gives the true sequential answer.
    assert mutates_its_input([1, 2, 3, 4], state) == [1, 1, 1, 1]


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
