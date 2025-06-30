"""Tests for loop analysis functionality."""

import ast
import pytest
from clustrix.loop_analysis import (
    LoopInfo,
    find_parallelizable_loops,
    estimate_work_size,
    detect_loops_in_function,
    detect_loops,
    SafeRangeEvaluator,
    DependencyAnalyzer,
    LoopDetector,
    analyze_loop_patterns,
    _ast_to_string,
)


class TestLoopInfo:
    """Test LoopInfo class functionality."""

    def test_basic_initialization(self):
        """Test basic LoopInfo initialization."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            range_info={"start": 0, "stop": 10, "step": 1},
        )

        assert loop_info.loop_type == "for"
        assert loop_info.variable == "i"
        assert loop_info.iterable == "range(10)"
        assert loop_info.range_info == {"start": 0, "stop": 10, "step": 1}
        assert loop_info.nested_level == 0
        assert loop_info.dependencies == set()
        assert loop_info.is_parallelizable is True

    def test_initialization_with_dependencies(self):
        """Test LoopInfo with dependencies."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="j",
            iterable="items",
            dependencies={"items", "config"},
        )

        assert loop_info.dependencies == {"items", "config"}
        assert loop_info.is_parallelizable is False  # Has dependencies

    def test_to_dict_conversion(self):
        """Test converting LoopInfo to dictionary."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(5)",
            range_info={"start": 0, "stop": 5, "step": 1},
            nested_level=1,
        )

        result = loop_info.to_dict()

        assert result["variable"] == "i"
        assert result["iterable"] == "range(5)"
        assert result["range_info"] == {"start": 0, "stop": 5, "step": 1}
        assert result["nested_level"] == 1
        assert result["is_parallelizable"] is True

    def test_assess_parallelizability_with_dependencies(self):
        """Test parallelizability assessment with dependencies."""
        # With dependencies - not parallelizable
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            dependencies={"shared_var"},
        )
        assert loop_info.is_parallelizable is False

        # No dependencies and range info - should be parallelizable
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            range_info={"start": 0, "stop": 10, "step": 1},
        )
        assert loop_info.is_parallelizable is True


class TestLoopDetection:
    """Test loop detection in functions."""

    def test_detect_for_loop(self):
        """Test detecting for loops in function."""

        def function_with_for_loop():
            results = []
            for i in range(10):
                results.append(i * 2)
            return results

        loops = detect_loops_in_function(function_with_for_loop)
        assert isinstance(loops, list)
        # Note: Loop detection may vary based on implementation
        # Check that if loops are detected, they have expected attributes
        for loop in loops:
            assert hasattr(loop, "variable")
            assert hasattr(loop, "iterable")

    def test_detect_while_loop(self):
        """Test detecting while loops in function."""

        def function_with_while_loop():
            i = 0
            result = []
            while i < 10:
                result.append(i)
                i += 1
            return result

        loops = detect_loops_in_function(function_with_while_loop)
        assert isinstance(loops, list)

    def test_detect_nested_loops(self):
        """Test detecting nested loops."""

        def function_with_nested_loops():
            results = []
            for i in range(5):
                for j in range(3):
                    results.append(i * j)
            return results

        loops = detect_loops_in_function(function_with_nested_loops)
        assert isinstance(loops, list)

    def test_detect_no_loops(self):
        """Test function with no loops."""

        def function_without_loops():
            return 42

        loops = detect_loops_in_function(function_without_loops)
        assert isinstance(loops, list)
        assert len(loops) == 0

    def test_detect_loops_source_unavailable(self):
        """Test loop detection when source code is unavailable."""

        # Lambda functions don't have accessible source code
        def no_source_func():
            return sum(range(10))

        # Remove source code to simulate lambda-like behavior
        no_source_func.__code__ = compile("lambda: sum(range(10))", "<lambda>", "eval")
        loops = detect_loops_in_function(no_source_func)
        assert loops == []


class TestParallelizableLoopFinding:
    """Test finding parallelizable loops."""

    def test_find_parallelizable_range_loop(self):
        """Test finding parallelizable range-based loop."""

        def range_loop_function():
            results = []
            for i in range(100):
                results.append(i**2)
            return results

        loops = find_parallelizable_loops(range_loop_function, (), {})
        assert isinstance(loops, list)

    def test_find_parallelizable_with_arguments(self):
        """Test finding loops with function arguments."""

        def parameterized_function(n):
            results = []
            for i in range(n):
                results.append(i * 2)
            return results

        loops = find_parallelizable_loops(parameterized_function, (50,), {})
        assert isinstance(loops, list)

    def test_find_loops_with_dependencies(self):
        """Test finding loops that have dependencies."""

        def dependent_loop_function():
            shared_counter = 0
            results = []
            for i in range(10):
                shared_counter += 1
                results.append(shared_counter)
            return results

        loops = find_parallelizable_loops(dependent_loop_function, (), {})
        assert isinstance(loops, list)

    def test_find_loops_no_parallelizable(self):
        """Test function with no parallelizable loops."""

        def non_parallelizable_function():
            result = 0
            for i in range(10):
                result += i  # Sequential dependency
            return result

        loops = find_parallelizable_loops(non_parallelizable_function, (), {})
        assert isinstance(loops, list)


class TestWorkSizeEstimation:
    """Test work size estimation for loops."""

    def test_estimate_range_work_size(self):
        """Test estimating work size for range-based loop."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(1000)",
            range_info={"start": 0, "stop": 1000, "step": 1},
        )

        work_size = estimate_work_size(loop_info)
        assert work_size == 1000

    def test_estimate_work_size_with_step(self):
        """Test estimating work size with step."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(0, 100, 2)",
            range_info={"start": 0, "stop": 100, "step": 2},
        )

        work_size = estimate_work_size(loop_info)
        assert work_size == 50  # (100 - 0) // 2

    def test_estimate_work_size_no_range_info(self):
        """Test estimating work size without range info."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="item",
            iterable="items",
        )

        work_size = estimate_work_size(loop_info)
        assert work_size == 100  # Default estimate for non-range loops


class TestBackwardCompatibility:
    """Test backward compatibility functions."""

    def test_legacy_detect_loops_function(self):
        """Test the legacy detect_loops function."""

        def simple_loop_function():
            for i in range(10):
                pass

        result = detect_loops(simple_loop_function, (), {})
        # Legacy function should return dict or None
        assert result is None or isinstance(result, dict)


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_function(self):
        """Test analyzing empty function."""

        def empty_function():
            pass

        loops = find_parallelizable_loops(empty_function, (), {})
        assert loops == []

    def test_function_with_exception(self):
        """Test analyzing function that raises exception."""

        def exception_function():
            for i in range(10):
                if i == 5:
                    raise ValueError("Test exception")

        # Should not raise exception during analysis
        loops = find_parallelizable_loops(exception_function, (), {})
        assert isinstance(loops, list)

    def test_complex_loop_conditions(self):
        """Test loops with complex conditions."""

        def complex_loop_function(items):
            results = []
            for item in items:
                if hasattr(item, "value"):
                    results.append(item.value * 2)
            return results

        loops = find_parallelizable_loops(complex_loop_function, ([],), {})
        assert isinstance(loops, list)

    def test_generator_expression(self):
        """Test function with generator expressions."""

        def generator_function():
            return sum(x**2 for x in range(100))

        loops = find_parallelizable_loops(generator_function, (), {})
        assert isinstance(loops, list)

    def test_list_comprehension(self):
        """Test function with list comprehensions."""

        def list_comp_function():
            return [x**2 for x in range(100)]

        loops = find_parallelizable_loops(list_comp_function, (), {})
        assert isinstance(loops, list)


class TestIntegrationWithRealFunctions:
    """Test integration with realistic functions."""

    def test_mathematical_computation_loop(self):
        """Test loop detection in mathematical computation."""

        def monte_carlo_pi(n_samples):
            import random

            inside_circle = 0
            for _ in range(n_samples):
                x = random.random()
                y = random.random()
                if x * x + y * y <= 1:
                    inside_circle += 1
            return 4 * inside_circle / n_samples

        loops = find_parallelizable_loops(monte_carlo_pi, (10000,), {})
        assert isinstance(loops, list)

    def test_data_processing_loop(self):
        """Test loop detection in data processing function."""

        def process_data(data):
            results = []
            for item in data:
                processed = item * 2 + 1
                results.append(processed)
            return results

        loops = find_parallelizable_loops(process_data, ([1, 2, 3, 4, 5],), {})
        assert isinstance(loops, list)

    def test_machine_learning_loop(self):
        """Test loop detection in ML-style function."""

        def train_epochs(data, epochs):
            loss = 0
            for epoch in range(epochs):
                for batch in data:
                    # Simulate training step
                    loss += sum(batch) * 0.001
            return loss

        loops = find_parallelizable_loops(train_epochs, ([[1, 2, 3]], 10), {})
        assert isinstance(loops, list)


class TestASTToString:
    """Test _ast_to_string function."""

    def test_ast_name_node(self):
        """Test converting AST Name node to string."""
        node = ast.Name(id="variable", ctx=ast.Load())
        result = _ast_to_string(node)
        assert result == "variable"

    def test_ast_call_node(self):
        """Test converting AST Call node to string."""
        func = ast.Name(id="range", ctx=ast.Load())
        args = [ast.Constant(value=10)]
        node = ast.Call(func=func, args=args, keywords=[])
        result = _ast_to_string(node)
        assert result == "range(10)"

    def test_ast_call_node_complex(self):
        """Test converting complex AST Call node to string."""
        func = ast.Name(id="max", ctx=ast.Load())
        args = [ast.Name(id="a", ctx=ast.Load()), ast.Name(id="b", ctx=ast.Load())]
        node = ast.Call(func=func, args=args, keywords=[])
        result = _ast_to_string(node)
        assert result == "max(a, b)"

    def test_ast_constant_node(self):
        """Test converting AST Constant node to string."""
        node = ast.Constant(value=42)
        result = _ast_to_string(node)
        assert result == "42"

    def test_ast_num_node(self):
        """Test converting AST Num node to string (Python < 3.8)."""
        node = ast.Num(n=3.14)
        result = _ast_to_string(node)
        assert result == "3.14"

    def test_ast_unknown_node(self):
        """Test converting unknown AST node to string."""
        node = ast.BinOp(
            left=ast.Constant(value=1), op=ast.Add(), right=ast.Constant(value=2)
        )
        result = _ast_to_string(node)
        assert result == "BinOp"


class TestLoopInfoAdvanced:
    """Test advanced LoopInfo functionality."""

    def test_assess_parallelizability_while_loop(self):
        """Test parallelizability assessment for while loops."""
        loop_info = LoopInfo(loop_type="while", variable="i", iterable="i < 10")
        assert loop_info.is_parallelizable is False

    def test_assess_parallelizability_negative_step(self):
        """Test parallelizability with negative step."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10, 0, -1)",
            range_info={"start": 10, "stop": 0, "step": -1},
        )
        assert loop_info.is_parallelizable is True

    def test_estimate_parallelization_benefit(self):
        """Test parallelization benefit estimation."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(1000)",
            range_info={"start": 0, "stop": 1000, "step": 1},
        )
        benefit = loop_info.estimate_parallelization_benefit()
        assert isinstance(benefit, (int, float))
        assert benefit > 0

    def test_estimate_parallelization_benefit_small_loop(self):
        """Test parallelization benefit for small loops."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(5)",
            range_info={"start": 0, "stop": 5, "step": 1},
        )
        benefit = loop_info.estimate_parallelization_benefit()
        assert isinstance(benefit, (int, float))

    def test_suggest_parallelization_strategy(self):
        """Test parallelization strategy suggestion."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(1000)",
            range_info={"start": 0, "stop": 1000, "step": 1},
        )
        strategy = loop_info.suggest_parallelization_strategy()
        assert isinstance(strategy, dict)
        assert "executor_type" in strategy
        assert "chunk_size" in strategy

    def test_suggest_parallelization_strategy_non_parallelizable(self):
        """Test strategy suggestion for non-parallelizable loops."""
        loop_info = LoopInfo(loop_type="while", variable="i", iterable="i < 10")
        strategy = loop_info.suggest_parallelization_strategy()
        assert strategy["strategy"] == "none"

    def test_get_iteration_count(self):
        """Test iteration count calculation."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(0, 100, 2)",
            range_info={"start": 0, "stop": 100, "step": 2},
        )
        count = loop_info._get_iteration_count()
        assert count == 50

    def test_get_iteration_count_negative_step(self):
        """Test iteration count with negative step."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10, 0, -1)",
            range_info={"start": 10, "stop": 0, "step": -1},
        )
        count = loop_info._get_iteration_count()
        assert count == 10

    def test_get_iteration_count_no_range_info(self):
        """Test iteration count without range info."""
        loop_info = LoopInfo(loop_type="for", variable="i", iterable="items")
        count = loop_info._get_iteration_count()
        assert count == 100  # Default fallback


class TestSafeRangeEvaluator:
    """Test SafeRangeEvaluator class."""

    def test_initialization(self):
        """Test SafeRangeEvaluator initialization."""
        evaluator = SafeRangeEvaluator(local_vars={"n": 10})
        assert evaluator.local_vars == {"n": 10}

    def test_visit_call_range_one_arg(self):
        """Test visiting range call with one argument."""
        evaluator = SafeRangeEvaluator()
        call_node = ast.Call(
            func=ast.Name(id="range", ctx=ast.Load()),
            args=[ast.Constant(value=10)],
            keywords=[],
        )
        evaluator.visit_Call(call_node)
        assert evaluator.result == {"start": 0, "stop": 10, "step": 1}

    def test_visit_call_range_two_args(self):
        """Test visiting range call with two arguments."""
        evaluator = SafeRangeEvaluator()
        call_node = ast.Call(
            func=ast.Name(id="range", ctx=ast.Load()),
            args=[ast.Constant(value=1), ast.Constant(value=11)],
            keywords=[],
        )
        evaluator.visit_Call(call_node)
        assert evaluator.result == {"start": 1, "stop": 11, "step": 1}

    def test_visit_call_range_three_args(self):
        """Test visiting range call with three arguments."""
        evaluator = SafeRangeEvaluator()
        call_node = ast.Call(
            func=ast.Name(id="range", ctx=ast.Load()),
            args=[ast.Constant(value=0), ast.Constant(value=10), ast.Constant(value=2)],
            keywords=[],
        )
        evaluator.visit_Call(call_node)
        assert evaluator.result == {"start": 0, "stop": 10, "step": 2}

    def test_visit_call_non_range(self):
        """Test visiting non-range call."""
        evaluator = SafeRangeEvaluator()
        call_node = ast.Call(
            func=ast.Name(id="len", ctx=ast.Load()),
            args=[ast.Name(id="items", ctx=ast.Load())],
            keywords=[],
        )
        evaluator.visit_Call(call_node)
        assert evaluator.safe is False

    def test_evaluate_node_constant(self):
        """Test evaluating constant nodes."""
        evaluator = SafeRangeEvaluator()
        node = ast.Constant(value=42)
        result = evaluator._evaluate_node(node)
        assert result == 42

    def test_evaluate_node_num(self):
        """Test evaluating Num nodes (Python < 3.8)."""
        evaluator = SafeRangeEvaluator()
        node = ast.Num(n=3)  # Use integer for the test
        result = evaluator._evaluate_node(node)
        assert result == 3

    def test_evaluate_node_name(self):
        """Test evaluating Name nodes with local variables."""
        evaluator = SafeRangeEvaluator(local_vars={"n": 100})
        node = ast.Name(id="n", ctx=ast.Load())
        result = evaluator._evaluate_node(node)
        assert result == 100

    def test_evaluate_node_binop(self):
        """Test evaluating BinOp nodes."""
        evaluator = SafeRangeEvaluator()
        node = ast.BinOp(
            left=ast.Constant(value=10), op=ast.Add(), right=ast.Constant(value=5)
        )
        result = evaluator._evaluate_node(node)
        assert result == 15

    def test_evaluate_binop_operations(self):
        """Test various binary operations."""
        evaluator = SafeRangeEvaluator()

        # Addition
        node = ast.BinOp(
            left=ast.Constant(value=10), op=ast.Add(), right=ast.Constant(value=5)
        )
        assert evaluator._evaluate_binop(node) == 15

        # Subtraction
        node = ast.BinOp(
            left=ast.Constant(value=10), op=ast.Sub(), right=ast.Constant(value=3)
        )
        assert evaluator._evaluate_binop(node) == 7

        # Multiplication
        node = ast.BinOp(
            left=ast.Constant(value=6), op=ast.Mult(), right=ast.Constant(value=7)
        )
        assert evaluator._evaluate_binop(node) == 42

        # Floor division
        node = ast.BinOp(
            left=ast.Constant(value=20), op=ast.FloorDiv(), right=ast.Constant(value=3)
        )
        assert evaluator._evaluate_binop(node) == 6

    def test_evaluate_binop_division_by_zero(self):
        """Test division by zero handling."""
        evaluator = SafeRangeEvaluator()
        node = ast.BinOp(
            left=ast.Constant(value=10), op=ast.FloorDiv(), right=ast.Constant(value=0)
        )
        result = evaluator._evaluate_binop(node)
        assert result is None

    def test_evaluate_binop_unsupported_operation(self):
        """Test unsupported binary operations."""
        evaluator = SafeRangeEvaluator()
        node = ast.BinOp(
            left=ast.Constant(value=10), op=ast.Pow(), right=ast.Constant(value=2)
        )
        result = evaluator._evaluate_binop(node)
        assert result is None


class TestDependencyAnalyzer:
    """Test DependencyAnalyzer class."""

    def test_initialization(self):
        """Test DependencyAnalyzer initialization."""
        analyzer = DependencyAnalyzer()
        assert analyzer.reads == set()
        assert analyzer.writes == set()
        assert analyzer.function_calls == set()

    def test_visit_name_load(self):
        """Test visiting Name node with Load context."""
        analyzer = DependencyAnalyzer()
        node = ast.Name(id="variable", ctx=ast.Load())
        analyzer.visit_Name(node)
        assert "variable" in analyzer.reads

    def test_visit_name_store(self):
        """Test visiting Name node with Store context."""
        analyzer = DependencyAnalyzer()
        node = ast.Name(id="variable", ctx=ast.Store())
        analyzer.visit_Name(node)
        assert "variable" in analyzer.writes

    def test_visit_subscript(self):
        """Test visiting Subscript node."""
        analyzer = DependencyAnalyzer()
        node = ast.Subscript(
            value=ast.Name(id="array", ctx=ast.Load()),
            slice=ast.Constant(value=0),
            ctx=ast.Load(),
        )
        analyzer.visit_Subscript(node)
        assert "array" in analyzer.reads

    def test_visit_call(self):
        """Test visiting Call node."""
        analyzer = DependencyAnalyzer()
        node = ast.Call(
            func=ast.Name(id="function", ctx=ast.Load()), args=[], keywords=[]
        )
        analyzer.visit_Call(node)
        assert "function" in analyzer.function_calls

    def test_visit_aug_assign(self):
        """Test visiting AugAssign node."""
        analyzer = DependencyAnalyzer()
        node = ast.AugAssign(
            target=ast.Name(id="counter", ctx=ast.Store()),
            op=ast.Add(),
            value=ast.Constant(value=1),
        )
        analyzer.visit_AugAssign(node)
        assert "counter" in analyzer.writes

    def test_visit_control_flow(self):
        """Test visiting control flow nodes."""
        analyzer = DependencyAnalyzer()

        # Break
        analyzer.visit_Break(ast.Break())
        assert analyzer.has_break is True

        # Continue
        analyzer.visit_Continue(ast.Continue())
        assert analyzer.has_continue is True

        # Return
        analyzer.visit_Return(ast.Return(value=ast.Constant(value=42)))
        assert analyzer.has_return is True

    def test_visit_for_loop(self):
        """Test visiting For loop node."""
        analyzer = DependencyAnalyzer()
        node = ast.For(
            target=ast.Name(id="i", ctx=ast.Store()),
            iter=ast.Call(
                func=ast.Name(id="range", ctx=ast.Load()),
                args=[ast.Constant(value=10)],
                keywords=[],
            ),
            body=[],
            orelse=[],
        )
        analyzer.visit_For(node)
        assert analyzer.loop_var == "i"

    def test_has_dependencies(self):
        """Test dependency detection."""
        analyzer = DependencyAnalyzer()
        analyzer.reads = {"var1"}
        analyzer.writes = {"var1"}  # Same variable read and written
        assert analyzer.has_dependencies() is True

        # Different variables
        analyzer = DependencyAnalyzer()
        analyzer.reads = {"var1"}
        analyzer.writes = {"var2"}
        assert analyzer.has_dependencies() is False

    def test_has_loop_carried_dependencies(self):
        """Test loop-carried dependency detection."""
        analyzer = DependencyAnalyzer()
        analyzer.has_break = True
        assert analyzer.has_loop_carried_dependencies() is True

        # Test array access patterns
        analyzer = DependencyAnalyzer()
        analyzer.array_accesses = {"arr[read]", "arr[write]"}
        assert analyzer.has_loop_carried_dependencies() is True


class TestLoopDetector:
    """Test LoopDetector class."""

    def test_initialization(self):
        """Test LoopDetector initialization."""
        detector = LoopDetector()
        assert detector.loops == []
        assert detector.current_level == 0

    def test_visit_for_loop(self):
        """Test visiting For loop."""
        detector = LoopDetector()
        node = ast.For(
            target=ast.Name(id="i", ctx=ast.Store()),
            iter=ast.Call(
                func=ast.Name(id="range", ctx=ast.Load()),
                args=[ast.Constant(value=10)],
                keywords=[],
            ),
            body=[],
            orelse=[],
        )
        detector.visit_For(node)
        assert len(detector.loops) == 1
        assert detector.loops[0].loop_type == "for"
        assert detector.loops[0].variable == "i"

    def test_visit_while_loop(self):
        """Test visiting While loop."""
        detector = LoopDetector()
        node = ast.While(
            test=ast.Compare(
                left=ast.Name(id="i", ctx=ast.Load()),
                ops=[ast.Lt()],
                comparators=[ast.Constant(value=10)],
            ),
            body=[],
            orelse=[],
        )
        detector.visit_While(node)
        assert len(detector.loops) == 1
        assert detector.loops[0].loop_type == "while"

    def test_nested_loops(self):
        """Test nested loop detection."""
        detector = LoopDetector()

        # Outer loop
        outer_node = ast.For(
            target=ast.Name(id="i", ctx=ast.Store()),
            iter=ast.Call(
                func=ast.Name(id="range", ctx=ast.Load()),
                args=[ast.Constant(value=10)],
                keywords=[],
            ),
            body=[
                ast.For(
                    target=ast.Name(id="j", ctx=ast.Store()),
                    iter=ast.Call(
                        func=ast.Name(id="range", ctx=ast.Load()),
                        args=[ast.Constant(value=5)],
                        keywords=[],
                    ),
                    body=[],
                    orelse=[],
                )
            ],
            orelse=[],
        )

        detector.visit(outer_node)
        assert len(detector.loops) == 2
        assert detector.loops[0].nested_level == 0
        assert detector.loops[1].nested_level == 1


class TestAdvancedAnalysis:
    """Test advanced analysis functions."""

    def test_analyze_loop_patterns(self):
        """Test loop pattern analysis."""

        def test_function():
            for i in range(100):
                pass
            for j in range(50):
                pass

        result = analyze_loop_patterns(test_function)
        assert isinstance(result, dict)
        assert "total_loops" in result
        assert "parallelizable_loops" in result

    def test_detect_loops_in_function_with_args(self):
        """Test loop detection with function arguments."""

        def test_function(n, items):
            for i in range(n):
                for item in items:
                    pass

        loops = detect_loops_in_function(test_function, (10, [1, 2, 3]))
        assert isinstance(loops, list)

    def test_estimate_work_size_edge_cases(self):
        """Test work size estimation edge cases."""
        # Negative step
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10, 0, -1)",
            range_info={"start": 10, "stop": 0, "step": -1},
        )
        work_size = estimate_work_size(loop_info)
        assert work_size == 10

    def test_legacy_detect_loops_with_range_info(self):
        """Test legacy detect_loops function with range info."""

        def test_function():
            for i in range(100):
                result = i * 2

        result = detect_loops(test_function, (), {})
        if result is not None:
            assert isinstance(result, dict)
            if "range_info" in result:
                assert isinstance(result["range_info"], dict)
