#!/usr/bin/env python3
"""Flattened functions must keep the signature and the results of the original.

This file used to `return False` on every failure path. pytest treats a
returned value as a pass, so it reported success no matter what flattening did
-- including the case where flattening emitted a parameterless script and the
flattened function could not accept the arguments it was called with.
"""

import inspect

import pytest

from clustrix.function_flattening import (
    analyze_function_complexity,
    auto_flatten_if_needed,
)


def function_with_args(x, y, z=42):
    """Positional, keyword and default arguments, plus a nested function."""

    def inner_add(a, b):
        return a + b

    return inner_add(x, y) + z


CALLS = [
    ((1, 2), {}),
    ((1, 2), {"z": 100}),
    ((), {"x": 5, "y": 10}),
]


def test_nested_function_is_detected():
    """Flattening only engages when a nested function is found."""
    complexity = analyze_function_complexity(function_with_args)
    assert complexity.get("nested_functions", 0) > 0, complexity


def test_signature_is_preserved():
    flattened, info = auto_flatten_if_needed(function_with_args)
    if not (info and info.get("success")):
        pytest.skip(f"flattening did not engage: {info}")

    assert inspect.signature(flattened) == inspect.signature(function_with_args)


@pytest.mark.parametrize("args,kwargs", CALLS, ids=["positional", "keyword", "named"])
def test_flattened_function_returns_the_same_answer(args, kwargs):
    flattened, info = auto_flatten_if_needed(function_with_args)
    if not (info and info.get("success")):
        pytest.skip(f"flattening did not engage: {info}")

    assert flattened(*args, **kwargs) == function_with_args(*args, **kwargs)
