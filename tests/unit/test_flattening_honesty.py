#!/usr/bin/env python3
"""The flattening machinery must not lie about what it did.

Three separate lies used to live here, and each one was load-bearing for the
silent-wrong-answer bug in ``clustrix.decorator._execute_single``:

1. ``analyze_function_complexity`` returned ``complexity_score: 999,
   is_complex: True`` from its except branch. That branch runs when
   ``inspect.getsource`` fails -- so a function whose source cannot be read was
   reported as maximally complex, and the source-rewriting flattener was
   invoked on exactly the input it cannot possibly process.

2. ``auto_flatten_if_needed`` returned ``success: True`` while returning the
   *original* function, because the flag was passed through from the flattener
   where it only meant "the AST analysis stage did not raise". Callers had no
   way to tell "flattened" from "not flattened".

3. The advanced flattener picked its result out of the exec namespace with
   ``callable(obj) and func.__name__ in name``. Hoisted helpers are named
   ``{parent}_{nested}_hoisted``, which contains the parent's name -- so for a
   function with a nested helper, the *helper* matched first and was returned
   as the successfully flattened main function.

Nothing here is mocked; every assertion runs real functions and compares real
values.
"""

import inspect

import pytest

from clustrix.function_flattening import (
    analyze_function_complexity,
    auto_flatten_if_needed,
)

# ---------------------------------------------------------------------------
# Real functions, spanning the categories the analyser treats differently.
# ---------------------------------------------------------------------------


def plain_arithmetic(a, b):
    return a + b


def loop_only(n):
    total = 0
    for i in range(n):
        total += i * i
    return total


def one_nested_helper(x, y, z=42):
    def inner_add(a, b):
        return a + b

    return inner_add(x, y) + z


def nested_helper_and_loop(n):
    def square(v):
        return v * v

    total = 0
    for i in range(n):
        total += square(i)
    return total


def nested_helper_with_closure(n, scale):
    def scaled(v):
        # `scale` is captured from the enclosing scope: the case the hoisting
        # rewriter cannot pass through (#90).
        return v * scale

    return sum(scaled(i) for i in range(n))


def make_source_less_function():
    """A function ``inspect.getsource`` cannot recover -- as in a REPL."""
    namespace = {}
    exec("def add(a, b):\n    return a + b\n", namespace)
    return namespace["add"]


SOURCE_LESS = make_source_less_function()

REAL_FUNCTIONS = [
    ("plain_arithmetic", plain_arithmetic, (2, 3), {}),
    ("loop_only", loop_only, (5,), {}),
    ("one_nested_helper", one_nested_helper, (1, 2), {}),
    ("one_nested_helper_kwargs", one_nested_helper, (1, 2), {"z": 100}),
    ("nested_helper_and_loop", nested_helper_and_loop, (5,), {}),
    ("nested_helper_with_closure", nested_helper_with_closure, (5, 3), {}),
]
REAL_IDS = [case[0] for case in REAL_FUNCTIONS]


# ---------------------------------------------------------------------------
# Lie 1: "I could not analyse this" must be distinguishable from "it is complex"
# ---------------------------------------------------------------------------


def test_source_available_is_true_when_the_source_can_be_read():
    info = analyze_function_complexity(one_nested_helper)
    assert info["source_available"] is True
    assert isinstance(info["complexity_score"], int)


def test_unreadable_source_is_reported_as_unanalysed_not_as_complex():
    """The exact reproduction: a source-less function is not "complex"."""
    info = analyze_function_complexity(SOURCE_LESS)

    assert info["source_available"] is False, info
    assert info["is_complex"] is False, (
        "an unanalysable function must not be reported as complex -- that is "
        f"what invoked the source rewriter on it: {info}"
    )
    assert (
        info["complexity_score"] is None
    ), f"no score was measured, so none may be reported: {info}"
    assert info["complexity_score"] != 999
    assert info["estimated_risk"] == "unknown"
    assert info["analysis_error"]


def test_the_two_states_are_distinguishable():
    """`is_complex: False` alone must not be readable as "measured and simple"."""
    measured_simple = analyze_function_complexity(plain_arithmetic)
    unmeasured = analyze_function_complexity(SOURCE_LESS)

    assert measured_simple["is_complex"] is False
    assert unmeasured["is_complex"] is False
    # Same is_complex, different provenance -- and the provenance is reported.
    assert measured_simple["source_available"] is True
    assert unmeasured["source_available"] is False


def test_a_real_nested_function_is_still_measured_as_complex():
    """The honest branch keeps working -- this is not a blanket 'never complex'."""
    info = analyze_function_complexity(one_nested_helper)
    assert info["is_complex"] is True
    assert info["nested_functions"] == 1


# ---------------------------------------------------------------------------
# Lie 2: the success flag
# ---------------------------------------------------------------------------


def test_no_flattening_is_attempted_without_source():
    """Nothing to rewrite means no attempt, and the caller is told so."""
    returned, info = auto_flatten_if_needed(SOURCE_LESS)

    assert returned is SOURCE_LESS
    assert info is None, f"an attempt was reported where none is possible: {info}"
    # And the function still works, untouched.
    assert returned(2, 3) == 5


def test_simple_function_is_returned_untouched():
    returned, info = auto_flatten_if_needed(plain_arithmetic)
    assert returned is plain_arithmetic
    assert info is None


@pytest.mark.parametrize("label,func,args,kwargs", REAL_FUNCTIONS, ids=REAL_IDS)
def test_success_is_never_true_while_returning_the_original(label, func, args, kwargs):
    """``success``/``flattened`` must describe what was actually returned."""
    returned, info = auto_flatten_if_needed(func)

    if info is None:
        assert returned is func, f"{label}: no info, but a substitute was returned"
        return

    assert (
        info["flattened"] == info["success"]
    ), f"{label}: the two flags disagree: {info}"

    if returned is func:
        assert info["success"] is False, (
            f"{label}: reported success while handing back the original "
            f"function: {info}"
        )
        assert info["reason"], f"{label}: no reason given for not flattening: {info}"
    else:
        assert (
            info["success"] is True
        ), f"{label}: returned a substitute but reported failure: {info}"
        assert info["strategy"] in ("advanced", "basic"), info


@pytest.mark.parametrize("label,func,args,kwargs", REAL_FUNCTIONS, ids=REAL_IDS)
def test_a_claimed_flattening_must_compute_the_same_answer(label, func, args, kwargs):
    """If the flag says flattened, the replacement is held to the real answer.

    Both branches assert. Today every input takes the "not flattened" branch --
    the generators emit code that does not compile -- and that branch checks the
    original function is returned unchanged and still correct. If a generator is
    ever fixed, the other branch checks the replacement against real output
    rather than trusting the flag.
    """
    expected = func(*args, **kwargs)

    returned, info = auto_flatten_if_needed(func)

    if info is not None and info["flattened"]:
        assert returned is not func
        assert inspect.signature(returned) == inspect.signature(
            func
        ), f"{label}: flattened signature differs from the original"
        assert returned(*args, **kwargs) == expected, (
            f"{label}: flattening changed the answer: "
            f"{returned(*args, **kwargs)!r} != {expected!r}"
        )
    else:
        assert returned is func
        assert returned(*args, **kwargs) == expected


# ---------------------------------------------------------------------------
# Lie 3: the substring match that returned a hoisted helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,func,args,kwargs", REAL_FUNCTIONS, ids=REAL_IDS)
def test_a_hoisted_helper_is_never_returned_as_the_main_function(
    label, func, args, kwargs
):
    """A helper named ``<parent>_<nested>_hoisted`` must never come back."""
    returned, _info = auto_flatten_if_needed(func)

    name = getattr(returned, "__name__", "")
    assert not name.endswith("_hoisted"), (
        f"{label}: a hoisted helper ({name!r}) was returned in place of the "
        f"function the caller asked for"
    )


def test_returned_callable_always_accepts_the_original_call():
    """Whatever comes back is callable exactly as the original was.

    A replacement with a different arity was the other way flattening produced
    a wrong outcome: the basic flattener emitted a parameterless script.
    """
    for label, func, args, kwargs in REAL_FUNCTIONS:
        returned, _info = auto_flatten_if_needed(func)
        # Raises TypeError if the signature does not accept this call.
        inspect.signature(returned).bind(*args, **kwargs)
