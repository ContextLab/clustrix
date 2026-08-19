"""The loop range decides how work is split, so guessing it changes the answer.

`detect_loops` used to fall back to `range(10)` whenever it could not evaluate
a loop's range expression. A function looping over `range(1000)` whose bounds
could not be read was then chunked as ten iterations, and the caller got a
tenth of the work back with no error and no warning -- the same silent
wrong-answer shape as the fabricated-result bugs in `@cluster`.

It also reached that value by calling `eval()` on text sliced out of the
user's source, under a comment admitting the approach was dangerous.

These tests use real functions defined in this file, because `detect_loops`
reads source with `inspect.getsource` and gets nothing from a function built
by `exec`.
"""

from clustrix.utils import _evaluate_literal_range, detect_loops


def loop_over_a_literal_range():
    for i in range(1000):
        print(i)


def loop_over_a_variable_range(n):
    for i in range(n):
        print(i)


def loop_over_a_computed_range(data):
    for i in range(len(data)):
        print(i)


class TestLiteralRangeEvaluation:
    def test_a_single_bound(self):
        assert _evaluate_literal_range("range(1000)") == range(1000)

    def test_start_and_stop(self):
        assert _evaluate_literal_range("range(2, 20)") == range(2, 20)

    def test_start_stop_and_step(self):
        assert _evaluate_literal_range("range(0, 100, 5)") == range(0, 100, 5)

    def test_a_variable_bound_is_not_guessed(self):
        """The whole point: unknown must not become ten."""
        for expression in ("range(n)", "range(len(data))", "range(cfg.count)"):
            try:
                result = _evaluate_literal_range(expression)
            except Exception:
                result = None
            assert result != range(10), (
                f"{expression!r} evaluated to range(10) -- that is the "
                f"fabrication this test exists to prevent"
            )
            assert result is None or not isinstance(result, range)

    def test_no_arguments_is_refused(self):
        assert _evaluate_literal_range("range()") is None

    def test_too_many_arguments_is_refused(self):
        assert _evaluate_literal_range("range(1, 2, 3, 4)") is None

    def test_it_is_not_a_general_evaluator(self):
        """It must not run arbitrary expressions from the user's source."""
        for expression in (
            "__import__('os').system('true')",
            "print('side effect')",
            "[x for x in range(3)]",
        ):
            try:
                result = _evaluate_literal_range(expression)
            except Exception:
                result = None
            assert result is None


class TestDetectLoopsRefusesRatherThanGuessing:
    def test_a_variable_range_is_never_reported_as_range_ten(self):
        detected = detect_loops(loop_over_a_variable_range, (1000,), {})
        if detected is not None:
            assert detected.get("range") != range(10), (
                "detect_loops reported range(10) for a loop over range(n); "
                "chunking on that silently runs ten iterations instead of n"
            )

    def test_a_computed_range_is_never_reported_as_range_ten(self):
        detected = detect_loops(loop_over_a_computed_range, ([0] * 1000,), {})
        if detected is not None:
            assert detected.get("range") != range(10)

    def test_a_literal_range_is_reported_exactly_when_reported_at_all(self):
        """If it does report a range, it must be the real one."""
        detected = detect_loops(loop_over_a_literal_range, (), {})
        if detected is not None and "range" in detected:
            assert detected["range"] == range(1000)
