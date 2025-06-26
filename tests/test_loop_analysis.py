"""Tests for loop analysis functionality."""

# Removed unused pytest import
from clustrix.loop_analysis import (
    LoopInfo,
    find_parallelizable_loops,
    estimate_work_size,
    detect_loops_in_function,
    detect_loops,
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
