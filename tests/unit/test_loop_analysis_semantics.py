"""Behavioural correctness tests for clustrix.loop_analysis.

Issue #106 asked for real semantic coverage of ``find_parallelizable_loops``
rather than tests that only assert "it returned a list". Issue #131 asked
that anything salvaged from the closed test-coverage epic (PR #128, branch
``epic/test-coverage-90-percent``) be checked against master's actual
behaviour rather than the branch's unreviewed 1,712-line rewrite of
``loop_analysis.py``.

That branch's ``tests/test_loop_analysis_ast.py`` and
``tests/test_loop_analysis_advanced.py`` were reviewed and NOT ported:

* ``test_loop_analysis_advanced.py`` calls ``LoopInfo.enhanced_dependency_analysis``,
  ``LoopInfo.reduction_pattern_detection``, ``LoopInfo.parallelization_suggestions``,
  ``LoopInfo._classify_reduction_pattern``, ``LoopInfo._suggest_alternatives``, and the
  module-level ``integrate_with_decorator`` / ``validate_analysis_results`` -- none of
  which exist on master. Porting it means porting that unreviewed API surface too,
  which is exactly the scope expansion #131/#132 warn against.
* ``test_loop_analysis_ast.py`` is real AST parsing with zero mocks (the thing #131
  singles out as worth having), but almost every assertion is
  ``assert isinstance(loops, list)`` -- true whether or not anything meaningful was
  detected, and true on master today for reasons that have nothing to do with the
  branch's added comprehension/tuple-unpacking support (``visit_ListComp``,
  ``visit_SetComp``, ``visit_DictComp``, ``visit_GeneratorExp`` -- see #132). That is
  a cheater test: it can't distinguish "detected and handled correctly" from
  "silently ignored".

This file replaces that salvage attempt with tests that assert on the actual
values master's analyzer produces, using real functions (no mocks), including
tests that pin down the *documented gaps* (comprehensions and tuple-unpacking
targets are invisible to detection -- not "handled", just never seen) so a
future PR can't quietly reintroduce comprehension auto-parallelization without
a test noticing.
"""

import ast

from clustrix.loop_analysis import (
    DependencyAnalyzer,
    LoopDetector,
    detect_loops_in_function,
    find_parallelizable_loops,
)


# ---------------------------------------------------------------------------
# Regression test for the AugAssign loop-carried-dependency bug fixed
# alongside this test file (see clustrix/loop_analysis.py DependencyAnalyzer
# .visit_AugAssign). Before the fix, `total += i` was invisible to the
# dependency check: AugAssign's target has ctx=Store in the AST even though
# `x += y` semantically reads x's prior value, so `total` never landed in
# `reads`, `dependencies` came out empty, and the loop was marked
# is_parallelizable=True despite each iteration depending on the last.
# clustrix/decorator.py's local-parallel path chunks a loop's range and runs
# each chunk independently, combining results by list concatenation -- which
# is silently wrong for a reduction accumulator (you'd get several partial
# sums, or only one chunk's total, never the real total).
# ---------------------------------------------------------------------------
class TestAugAssignLoopCarriedDependency:
    """A `+=`/`*=`/etc. accumulator is a real cross-iteration dependency."""

    def test_sum_accumulator_is_not_parallelizable(self):
        def sum_accumulator(n):
            total = 0
            for i in range(n):
                total += i
            return total

        loops = detect_loops_in_function(sum_accumulator, (20,))
        assert len(loops) == 1
        loop = loops[0]
        assert "total" in loop.dependencies
        assert loop.is_parallelizable is False

    def test_product_accumulator_is_not_parallelizable(self):
        def product_accumulator(n):
            product = 1
            for i in range(1, n):
                product *= i
            return product

        loops = detect_loops_in_function(product_accumulator, (10,))
        assert len(loops) == 1
        assert "product" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_accumulator_loop_excluded_from_parallelizable_loops(self):
        def sum_accumulator(n):
            total = 0
            for i in range(n):
                total += i
            return total

        assert find_parallelizable_loops(sum_accumulator, (20,), {}) == []

    def test_non_reduction_augassign_also_flagged(self):
        """`-=` is not in the reduction whitelist; confirm it's still caught."""

        def running_difference(n):
            remaining = 100
            for i in range(n):
                remaining -= i
            return remaining

        loops = detect_loops_in_function(running_difference, (10,))
        assert len(loops) == 1
        assert "remaining" in loops[0].dependencies
        assert loops[0].is_parallelizable is False


# ---------------------------------------------------------------------------
# Mutation of shared state (lists/dicts read from an enclosing scope)
# ---------------------------------------------------------------------------
class TestSharedStateMutation:
    def test_append_to_shared_list_marks_dependency(self):
        def build_results(data):
            results = []
            for x in data:
                results.append(x * 2)
            return results

        loops = detect_loops_in_function(build_results, ([1, 2, 3],))
        assert len(loops) == 1
        assert "results" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_write_to_shared_dict_marks_dependency(self):
        def build_lookup(data):
            lookup = {}
            for x in data:
                lookup[x] = x * x
            return lookup

        loops = detect_loops_in_function(build_lookup, ([1, 2, 3],))
        assert len(loops) == 1
        assert "lookup" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_self_referential_array_write_is_loop_carried(self):
        """results[i] = results[i-1] + 1 is a genuine loop-carried dependency
        via the array itself (each write depends on a prior write)."""

        def cumulative(n):
            results = [0] * n
            for i in range(1, n):
                results[i] = results[i - 1] + 1
            return results

        loops = detect_loops_in_function(cumulative, (10,))
        assert len(loops) == 1
        assert "loop_carried_dependency" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_disjoint_array_write_is_still_conservatively_rejected(self):
        """results[i] = data[i] * 2 has no real cross-iteration dependency,
        but the analyzer's dependency check is "any name read in the body",
        not "any name read AND written across iterations" -- so this
        currently-safe, genuinely-parallel pattern is also rejected. This is
        a known false negative (documented, not fixed here): it costs a
        missed optimization, not a wrong answer, which is the safe side to
        err on. See #106 report for discussion."""

        def elementwise_double(n, data):
            results = [0] * n
            for i in range(n):
                results[i] = data[i] * 2
            return results

        loops = detect_loops_in_function(elementwise_double, (5, [1, 2, 3, 4, 5]))
        assert len(loops) == 1
        assert loops[0].is_parallelizable is False
        assert "data" in loops[0].dependencies
        assert "results" in loops[0].dependencies


# ---------------------------------------------------------------------------
# break / continue / return / for-else
# ---------------------------------------------------------------------------
class TestControlFlow:
    def test_break_marks_non_parallelizable(self):
        def find_first_match(items, target):
            found_index = -1
            for i in range(len(items)):
                if items[i] == target:
                    found_index = i
                    break
            return found_index

        loops = detect_loops_in_function(find_first_match, ([1, 2, 3], 2))
        assert len(loops) == 1
        assert "loop_carried_dependency" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_continue_marks_non_parallelizable(self):
        def sum_even(n):
            total = 0
            for i in range(n):
                if i % 2 != 0:
                    continue
                total += i
            return total

        loops = detect_loops_in_function(sum_even, (20,))
        assert len(loops) == 1
        assert "loop_carried_dependency" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_return_inside_loop_marks_non_parallelizable(self):
        def first_negative(data):
            for x in data:
                if x < 0:
                    return x
            return None

        loops = detect_loops_in_function(first_negative, ([1, 2, -3, 4],))
        assert len(loops) == 1
        assert "loop_carried_dependency" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_for_else_body_still_analyzed(self):
        """The `else` clause of a for-loop is not part of `node.body`, so it
        should not affect detection of the loop itself; the loop's own break
        is what matters."""

        def search_with_else(items, target):
            for item in items:
                if item == target:
                    break
            else:
                pass
            return None

        loops = detect_loops_in_function(search_with_else, ([1, 2, 3], 5))
        assert len(loops) == 1
        assert loops[0].is_parallelizable is False


# ---------------------------------------------------------------------------
# Nested loops
# ---------------------------------------------------------------------------
class TestNestedLoops:
    def test_nesting_levels_assigned_correctly(self):
        def triple_nested():
            total = 0
            for i in range(3):
                for j in range(3):
                    for k in range(3):
                        total = i + j + k
            return total

        loops = detect_loops_in_function(triple_nested)
        assert len(loops) == 3
        levels = sorted(loop.nested_level for loop in loops)
        assert levels == [0, 1, 2]

    def test_find_parallelizable_loops_respects_max_nesting_level(self):
        """Loops detected via ast.walk are found in the same relative nesting
        order regardless of `func`'s own nesting; find_parallelizable_loops
        filters out anything deeper than max_nesting_level (default 1)."""

        def deeply_nested():
            for i in range(5):
                for j in range(5):
                    for k in range(5):
                        pass

        loops = detect_loops_in_function(deeply_nested)
        assert [loop.nested_level for loop in loops] == [0, 1, 2]
        # All three are independently parallelizable (no shared state, no
        # control flow) -- the level-2 loop must still be excluded because
        # it is deeper than the default max_nesting_level=1.
        parallelizable = find_parallelizable_loops(deeply_nested, (), {})
        assert all(loop.nested_level <= 1 for loop in parallelizable)
        assert not any(loop.nested_level == 2 for loop in parallelizable)


# ---------------------------------------------------------------------------
# enumerate / zip / dict.items() -- tuple-unpacking targets
#
# `_analyze_for_loop` requires `isinstance(node.target, ast.Name)` and
# returns None otherwise. enumerate()/zip()/dict.items() loops almost always
# unpack into a tuple target (`for i, x in enumerate(items)`), so these are
# not merely "conservatively rejected" the way a shared-list append is --
# they are never even constructed as a LoopInfo. detect_loops_in_function
# silently returns fewer loops than exist in the source. This is a real gap
# (not something this task fixes -- extending LoopInfo/DependencyAnalyzer to
# carry multiple loop variables is a real feature, not a bug fix), pinned
# down here so it's a documented, tested limitation rather than a surprise.
# ---------------------------------------------------------------------------
class TestTupleUnpackingTargetsAreInvisible:
    def test_enumerate_loop_is_not_detected(self):
        def with_enumerate(items):
            out = []
            for i, item in enumerate(items):
                out.append(i)
            return out

        loops = detect_loops_in_function(with_enumerate, ([1, 2, 3],))
        assert loops == []

    def test_zip_loop_is_not_detected(self):
        def with_zip(a, b):
            out = []
            for x, y in zip(a, b):
                out.append(x + y)
            return out

        loops = detect_loops_in_function(with_zip, ([1, 2], [3, 4]))
        assert loops == []

    def test_dict_items_loop_is_not_detected(self):
        def with_dict_items(mapping):
            out = []
            for key, value in mapping.items():
                out.append((key, value))
            return out

        loops = detect_loops_in_function(with_dict_items, ({"a": 1},))
        assert loops == []

    def test_plain_range_loop_is_still_detected(self):
        """Sanity check that the gap above is specific to tuple targets, not
        a general regression in detection."""

        def plain_range(n):
            for i in range(n):
                pass

        loops = detect_loops_in_function(plain_range, (10,))
        assert len(loops) == 1
        assert loops[0].variable == "i"


# ---------------------------------------------------------------------------
# range() variants
# ---------------------------------------------------------------------------
class TestRangeVariants:
    def test_range_one_arg(self):
        def f(n):
            for i in range(n):
                pass

        loops = detect_loops_in_function(f, (10,))
        assert loops[0].range_info == {"start": 0, "stop": 10, "step": 1}

    def test_range_two_args_literal(self):
        def f():
            for i in range(2, 8):
                pass

        loops = detect_loops_in_function(f)
        assert loops[0].range_info == {"start": 2, "stop": 8, "step": 1}

    def test_range_three_args_negative_step(self):
        def f():
            for i in range(10, 0, -1):
                pass

        loops = detect_loops_in_function(f)
        assert loops[0].range_info == {"start": 10, "stop": 0, "step": -1}
        assert loops[0].is_parallelizable is True

    def test_range_with_non_literal_call_arg_is_not_statically_evaluable(self):
        """range(len(data)) can't be safely evaluated without executing
        len(data); SafeRangeEvaluator only handles Constant/Name/BinOp, so
        range_info should come back None rather than something wrong."""

        def f(data):
            for i in range(len(data)):
                pass

        loops = detect_loops_in_function(f, ([1, 2, 3],))
        assert len(loops) == 1
        assert loops[0].range_info is None

    def test_range_zero_step_does_not_crash_and_is_not_parallelizable(self):
        def f():
            for i in range(10, 0, 0):  # pragma: no branch - never executed
                pass

        loops = detect_loops_in_function(f)
        assert len(loops) == 1
        assert loops[0].is_parallelizable is False


# ---------------------------------------------------------------------------
# Generators and comprehensions: confirm master does NOT auto-parallelize
# them. This is the regression guard for issue #132 -- the closed branch
# added visit_ListComp/visit_SetComp/visit_DictComp/visit_GeneratorExp to
# LoopDetector, making comprehensions parallelization candidates, and that
# change was never reviewed. It must not come back silently.
# ---------------------------------------------------------------------------
class TestComprehensionsAreNotLoops:
    def test_list_comprehension_produces_no_loop(self):
        def f():
            return [x**2 for x in range(10)]

        assert detect_loops_in_function(f) == []

    def test_set_comprehension_produces_no_loop(self):
        def f():
            return {x**2 for x in range(10)}

        assert detect_loops_in_function(f) == []

    def test_dict_comprehension_produces_no_loop(self):
        def f():
            return {x: x**2 for x in range(10)}

        assert detect_loops_in_function(f) == []

    def test_generator_expression_produces_no_loop(self):
        def f():
            return sum(x**2 for x in range(100))

        assert detect_loops_in_function(f) == []

    def test_loop_detector_has_no_comprehension_visitors(self):
        """Belt and suspenders: assert the visitor methods themselves are
        absent, not just that they happen not to fire for these inputs."""
        assert not hasattr(LoopDetector, "visit_ListComp")
        assert not hasattr(LoopDetector, "visit_SetComp")
        assert not hasattr(LoopDetector, "visit_DictComp")
        assert not hasattr(LoopDetector, "visit_GeneratorExp")

    def test_mixed_for_loop_and_comprehension_only_detects_the_for_loop(self):
        def f(n):
            result = []
            for i in range(n):
                inner = [x**2 for x in range(i)]
                result.extend(inner)
            return result

        loops = detect_loops_in_function(f, (5,))
        # Only the real `for` statement is found; the comprehension inside
        # its body contributes no separate LoopInfo.
        assert len(loops) == 1
        assert loops[0].variable == "i"


# ---------------------------------------------------------------------------
# Side-effecting bodies (I/O, calls to named functions)
# ---------------------------------------------------------------------------
class TestSideEffectingBody:
    def test_call_to_named_function_marks_dependency(self):
        """Calling any bare-name function (not a method call) puts that
        name in `reads` via the Call node's own `func` child, which is
        indistinguishable here from reading a real shared variable. This is
        conservative-but-safe: it can reject loops that would have been
        fine, but it never approves one that mutates state through the
        call."""

        def with_print(n):
            for i in range(n):
                print(i)

        loops = detect_loops_in_function(with_print, (10,))
        assert len(loops) == 1
        assert "print" in loops[0].dependencies
        assert loops[0].is_parallelizable is False

    def test_file_write_in_loop_marks_dependency(self):
        def write_lines(handle, lines):
            for line in lines:
                handle.write(line)

        loops = detect_loops_in_function(write_lines, (None, ["a", "b"]))
        assert len(loops) == 1
        assert "handle" in loops[0].dependencies
        assert loops[0].is_parallelizable is False


# ---------------------------------------------------------------------------
# DependencyAnalyzer unit-level checks that back the above (real AST, not
# hand-built nodes standing in for real source).
# ---------------------------------------------------------------------------
class TestDependencyAnalyzerOnRealSource:
    def test_augassign_target_is_both_read_and_written(self):
        source = """
def f(n):
    total = 0
    for i in range(n):
        total += i
    return total
"""
        tree = ast.parse(source)
        for_node = next(n for n in ast.walk(tree) if isinstance(n, ast.For))
        analyzer = DependencyAnalyzer()
        analyzer.loop_var = "i"
        for stmt in for_node.body:
            analyzer.visit(stmt)

        assert "total" in analyzer.reads
        assert "total" in analyzer.writes
        assert analyzer.has_dependencies() is True

    def test_comprehension_names_are_still_visible_to_generic_traversal(self):
        """LoopDetector never builds a LoopInfo for a comprehension, but
        DependencyAnalyzer used directly (no visit_ListComp override, so
        the default generic_visit recurses into it) still sees the names
        inside one. This documents that the "comprehensions are invisible"
        property is specific to LoopDetector's loop construction, not a
        blanket AST blindness in DependencyAnalyzer."""

        source = "result = [x * 2 for x in data if x > threshold]"
        tree = ast.parse(source)
        analyzer = DependencyAnalyzer()
        analyzer.visit(tree)

        assert {"x", "data", "threshold"} <= analyzer.reads
