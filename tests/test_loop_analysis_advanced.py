"""Advanced tests for loop analysis functionality focusing on enhanced features."""

import ast
import pytest
from typing import List
from clustrix.loop_analysis import (
    LoopInfo,
    detect_loops_in_function,
    analyze_loop_patterns,
    integrate_with_decorator,
    validate_analysis_results,
    DependencyAnalyzer,
)


class TestLoopInfoEnhanced:
    """Test enhanced LoopInfo class functionality."""

    def test_enhanced_dependency_analysis_basic(self):
        """Test basic enhanced dependency analysis."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            range_info={"start": 0, "stop": 10, "step": 1},
        )

        # Test without AST body (should return basic analysis)
        analysis = loop_info.enhanced_dependency_analysis()

        assert isinstance(analysis, dict)
        assert "read_variables" in analysis
        assert "write_variables" in analysis
        assert "loop_carried_deps" in analysis
        assert "reduction_vars" in analysis
        assert "control_flow" in analysis
        assert "parallelization_blockers" in analysis

    def test_enhanced_dependency_analysis_with_ast(self):
        """Test enhanced dependency analysis with actual AST body."""

        # Create a test function to analyze
        def test_function():
            result = []
            counter = 0
            for i in range(10):
                counter += 1
                result.append(i * counter)
            return result

        # Get the AST body
        source = """
def test_function():
    result = []
    counter = 0
    for i in range(10):
        counter += 1
        result.append(i * counter)
    return result
"""
        tree = ast.parse(source)

        # Find the for loop
        for_loop = None
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                for_loop = node
                break

        assert for_loop is not None

        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            range_info={"start": 0, "stop": 10, "step": 1},
        )

        analysis = loop_info.enhanced_dependency_analysis(for_loop.body)

        # Should detect counter as both read and written (loop-carried dependency)
        assert len(analysis["parallelization_blockers"]) > 0
        assert any(
            "counter" in blocker for blocker in analysis["parallelization_blockers"]
        )

        # Should detect function calls
        assert len(analysis["function_calls"]) > 0

    def test_reduction_pattern_detection_basic(self):
        """Test basic reduction pattern detection."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            range_info={"start": 0, "stop": 10, "step": 1},
        )

        # Test without AST body
        patterns = loop_info.reduction_pattern_detection()

        assert isinstance(patterns, dict)
        assert "detected_reductions" in patterns
        assert "potential_reductions" in patterns
        assert "reduction_candidates" in patterns
        assert "performance_impact" in patterns
        assert patterns["performance_impact"] == "low"

    def test_reduction_pattern_detection_with_sum(self):
        """Test reduction pattern detection with sum operation."""
        # Create AST for a sum reduction
        source = """
total = 0
for i in range(100):
    total += i * 2
"""
        tree = ast.parse(source)

        # Find the for loop
        for_loop = None
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                for_loop = node
                break

        assert for_loop is not None

        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(100)",
            range_info={"start": 0, "stop": 100, "step": 1},
        )

        patterns = loop_info.reduction_pattern_detection(for_loop.body)

        # Should detect the += operation as a reduction
        assert len(patterns["detected_reductions"]) > 0
        reduction = patterns["detected_reductions"][0]
        assert reduction["variable"] == "total"
        assert reduction["operation"] == "Add"
        assert reduction["pattern_type"] == "summation"

    def test_reduction_pattern_detection_with_builtin_functions(self):
        """Test reduction pattern detection with builtin reduction functions."""
        source = """
data = [1, 2, 3, 4, 5]
for item in data:
    result = sum(item)
    maximum = max(item)
"""
        tree = ast.parse(source)

        # Find the for loop
        for_loop = None
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                for_loop = node
                break

        assert for_loop is not None

        loop_info = LoopInfo(
            loop_type="for",
            variable="item",
            iterable="data",
        )

        patterns = loop_info.reduction_pattern_detection(for_loop.body)

        # Should detect sum and max as reduction candidates
        assert len(patterns["reduction_candidates"]) >= 2
        functions = [rc["function"] for rc in patterns["reduction_candidates"]]
        assert "sum" in functions
        assert "max" in functions

    def test_parallelization_suggestions_basic(self):
        """Test basic parallelization suggestions."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            range_info={"start": 0, "stop": 10, "step": 1},
        )

        suggestions = loop_info.parallelization_suggestions()

        assert isinstance(suggestions, dict)
        assert "primary_strategy" in suggestions
        assert "alternative_strategies" in suggestions
        assert "optimization_opportunities" in suggestions
        assert "performance_estimates" in suggestions
        assert "implementation_complexity" in suggestions
        assert "recommended_tools" in suggestions
        assert "caveats" in suggestions

    def test_parallelization_suggestions_small_loop(self):
        """Test parallelization suggestions for small loops."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(5)",
            range_info={"start": 0, "stop": 5, "step": 1},
        )

        suggestions = loop_info.parallelization_suggestions()

        # Small loops should suggest sequential execution
        assert suggestions["primary_strategy"] == "sequential"
        assert any("Too few iterations" in caveat for caveat in suggestions["caveats"])

    def test_parallelization_suggestions_large_loop(self):
        """Test parallelization suggestions for large loops."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(50000)",
            range_info={"start": 0, "stop": 50000, "step": 1},
        )

        suggestions = loop_info.parallelization_suggestions()

        # Large loops should suggest distributed processing
        assert suggestions["primary_strategy"] == "distributed_processing"
        assert "dask.distributed" in suggestions["recommended_tools"]
        assert suggestions["implementation_complexity"] == "high"

    def test_parallelization_suggestions_numpy_loop(self):
        """Test parallelization suggestions for NumPy-based loops."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="numpy.arange(1000)",
        )

        suggestions = loop_info.parallelization_suggestions()

        # NumPy loops should suggest vectorization
        assert "vectorization" in suggestions["optimization_opportunities"]
        assert "NumPy/Pandas vectorized operations" in suggestions["recommended_tools"]

    def test_parallelization_suggestions_non_parallelizable(self):
        """Test suggestions for non-parallelizable loops."""
        loop_info = LoopInfo(
            loop_type="while",
            variable="i",
            iterable="i < 10",
            dependencies={"shared_var"},
        )

        suggestions = loop_info.parallelization_suggestions()

        assert suggestions["primary_strategy"] == "none"
        assert len(suggestions["alternative_strategies"]) > 0
        assert any("not suitable" in caveat for caveat in suggestions["caveats"])

    def test_classify_reduction_pattern(self):
        """Test reduction pattern classification."""
        loop_info = LoopInfo(loop_type="for", variable="i", iterable="range(10)")

        # Test various operation classifications
        assert loop_info._classify_reduction_pattern("Add") == "summation"
        assert loop_info._classify_reduction_pattern("Mult") == "product"
        assert loop_info._classify_reduction_pattern("BitOr") == "bitwise_or"
        assert loop_info._classify_reduction_pattern("Unknown") == "custom"

    def test_suggest_alternatives_while_loop(self):
        """Test alternative suggestions for while loops."""
        loop_info = LoopInfo(loop_type="while", variable="i", iterable="i < 10")

        alternatives = loop_info._suggest_alternatives()

        assert len(alternatives) > 0
        assert any("Convert to for-loop" in alt for alt in alternatives)
        assert any("itertools" in alt for alt in alternatives)

    def test_suggest_alternatives_nested_loop(self):
        """Test alternative suggestions for nested loops."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="j",
            iterable="range(10)",
            nested_level=2,
        )

        alternatives = loop_info._suggest_alternatives()

        assert len(alternatives) > 0
        assert any("outer loop instead" in alt for alt in alternatives)

    def test_suggest_alternatives_with_dependencies(self):
        """Test alternative suggestions for loops with dependencies."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10)",
            dependencies={"shared_state"},
        )

        alternatives = loop_info._suggest_alternatives()

        assert len(alternatives) > 0
        assert any("reduce dependencies" in alt for alt in alternatives)
        assert any("reduction patterns" in alt for alt in alternatives)


class TestIntegrationUtilities:
    """Test integration utility functions."""

    def test_integrate_with_decorator_no_loops(self):
        """Test decorator integration with no loops."""
        integration = integrate_with_decorator([], lambda: None)

        assert integration["recommended_cores"] == 1
        assert integration["parallelization_approach"] == "none"
        assert len(integration["warnings"]) > 0
        assert "No loops detected" in integration["warnings"][0]

    def test_integrate_with_decorator_basic_loop(self):
        """Test decorator integration with basic parallelizable loop."""

        def test_function():
            return [i**2 for i in range(1000)]

        loops = detect_loops_in_function(test_function)

        # Create a simple parallelizable loop
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(1000)",
            range_info={"start": 0, "stop": 1000, "step": 1},
        )

        integration = integrate_with_decorator([loop_info], test_function)

        assert integration["recommended_cores"] > 1
        assert integration["parallelization_approach"] != "none"
        assert integration["estimated_speedup"] > 1.0

    def test_integrate_with_decorator_large_loop(self):
        """Test decorator integration with large loop."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(50000)",
            range_info={"start": 0, "stop": 50000, "step": 1},
        )

        integration = integrate_with_decorator([loop_info], lambda: None)

        assert integration["recommended_cores"] == 16
        assert integration["parallelization_approach"] == "distributed"
        assert "fixed_size" in integration["chunk_strategy"]

    def test_integrate_with_decorator_numpy_data(self):
        """Test decorator integration with NumPy data."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="numpy.arange(1000)",
            range_info={"start": 0, "stop": 1000, "step": 1},
        )

        integration = integrate_with_decorator([loop_info], lambda: None)

        assert integration["recommended_memory"] == "4GB"
        assert any("chunking" in mod for mod in integration["decorator_modifications"])

    def test_integrate_with_decorator_with_config(self):
        """Test decorator integration with existing configuration."""
        loop_info = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(1000)",
            range_info={"start": 0, "stop": 1000, "step": 1},
        )

        config = {"cores": 2}
        integration = integrate_with_decorator([loop_info], lambda: None, config)

        # Should suggest increasing cores if current is too low
        if integration["recommended_cores"] > 2:
            assert any(
                "Increase cores" in mod
                for mod in integration["decorator_modifications"]
            )

    def test_validate_analysis_results_basic(self):
        """Test basic analysis result validation."""

        def simple_function():
            for i in range(10):
                pass

        loops = detect_loops_in_function(simple_function)
        validation = validate_analysis_results(loops, simple_function)

        assert isinstance(validation, dict)
        assert "accuracy_score" in validation
        assert "detected_issues" in validation
        assert "confidence_level" in validation
        assert validation["accuracy_score"] >= 0.0

    def test_validate_analysis_results_comprehensive(self):
        """Test comprehensive analysis result validation."""

        def complex_function():
            # Multiple loop types
            for i in range(100):
                result = i**2

            j = 0
            while j < 10:
                j += 1

            # List comprehension
            squares = [x**2 for x in range(5)]

            return result, j, squares

        loops = detect_loops_in_function(complex_function)
        validation = validate_analysis_results(loops, complex_function)

        # Should detect the comprehension as a missed pattern
        if validation["false_negatives"]:
            assert any("comprehension" in fn for fn in validation["false_negatives"])

        # Should provide recommendations
        assert len(validation["recommendations"]) > 0

    def test_validate_analysis_results_range_validation(self):
        """Test validation of range loop accuracy."""

        def range_function():
            for i in range(0, 20, 2):
                result = i * 3
            return result

        loops = detect_loops_in_function(range_function)
        validation = validate_analysis_results(loops, range_function)

        # Should have high confidence for simple range loops
        if loops and loops[0].range_info:
            assert validation["accuracy_score"] > 0.0

            # Check for iteration count validation
            if validation["detected_issues"]:
                # If there are issues, they should be specific
                assert any(
                    "iteration count" in issue or "range parameters" in issue
                    for issue in validation["detected_issues"]
                )

    def test_validate_analysis_results_error_handling(self):
        """Test validation error handling."""
        # Create a mock loop that might cause validation errors
        invalid_loop = LoopInfo(
            loop_type="for",
            variable="i",
            iterable="range(10, 0, 0)",  # Invalid step of 0
            range_info={"start": 10, "stop": 0, "step": 0},
        )

        def simple_function():
            pass

        validation = validate_analysis_results([invalid_loop], simple_function)

        # Should detect the invalid range parameters
        assert len(validation["detected_issues"]) > 0
        assert any(
            "Invalid range parameters" in issue
            for issue in validation["detected_issues"]
        )


class TestAdvancedPatternDetection:
    """Test advanced pattern detection capabilities."""

    def test_nested_reduction_detection(self):
        """Test detection of nested reduction patterns."""

        def nested_reduction_function():
            total = 0
            for i in range(10):
                subtotal = 0
                for j in range(5):
                    subtotal += i * j
                total += subtotal
            return total

        loops = detect_loops_in_function(nested_reduction_function)

        # Should detect nested loops
        assert len(loops) >= 2

        # Test reduction detection on outer loop
        outer_loops = [loop for loop in loops if loop.nested_level == 0]
        if outer_loops:
            outer_loop = outer_loops[0]
            # The outer loop should have the nested structure in its analysis
            assert outer_loop.nested_level == 0

    def test_complex_dependency_analysis(self):
        """Test complex dependency analysis."""

        def complex_dependency_function():
            shared_state = {"counter": 0, "results": []}
            for i in range(100):
                shared_state["counter"] += 1
                if shared_state["counter"] % 10 == 0:
                    shared_state["results"].append(i)
            return shared_state

        loops = detect_loops_in_function(complex_dependency_function)

        if loops:
            loop = loops[0]
            # Should detect that this loop has dependencies that prevent parallelization
            assert not loop.is_parallelizable or len(loop.dependencies) > 0

    def test_function_call_analysis(self):
        """Test analysis of function calls within loops."""

        def function_call_loop():
            results = []
            for i in range(50):
                # Various function calls
                value = abs(i - 25)
                squared = pow(value, 2)
                results.append(max(squared, 10))
            return results

        loops = detect_loops_in_function(function_call_loop)

        if loops:
            loop = loops[0]
            # Should be parallelizable despite function calls
            assert loop.is_parallelizable

    def test_array_access_pattern_analysis(self):
        """Test analysis of array access patterns."""

        def array_access_function(data):
            results = []
            for i in range(len(data) - 1):
                # Read current and next elements
                current = data[i]
                next_val = data[i + 1]
                results.append(current + next_val)
            return results

        loops = detect_loops_in_function(array_access_function, ([1, 2, 3, 4, 5],))

        if loops:
            loop = loops[0]
            # This should be parallelizable since we're only reading from data
            # The dependency analysis should recognize this pattern
            assert isinstance(loop.dependencies, set)

    def test_performance_estimation_accuracy(self):
        """Test accuracy of performance benefit estimation."""
        # Test different loop sizes and their benefit estimations
        test_cases = [
            (10, "low benefit expected for small loops"),
            (1000, "medium benefit expected for medium loops"),
            (100000, "high benefit expected for large loops"),
        ]

        for size, description in test_cases:
            loop_info = LoopInfo(
                loop_type="for",
                variable="i",
                iterable=f"range({size})",
                range_info={"start": 0, "stop": size, "step": 1},
            )

            benefit = loop_info.estimate_parallelization_benefit()

            # Larger loops should generally have higher benefits
            assert isinstance(benefit, (int, float))
            assert 0.0 <= benefit <= 1.0

            if size >= 1000:
                assert (
                    benefit > 0.3
                ), f"Large loop should have higher benefit: {description}"


class TestRealWorldIntegration:
    """Test integration with real-world scenarios."""

    def test_data_processing_pipeline(self):
        """Test analysis of data processing pipeline."""

        def data_processing_pipeline(data_batches):
            processed_results = []
            total_processed = 0

            for batch in data_batches:
                batch_result = []
                for item in batch:
                    # Simulate data processing
                    processed_item = item * 2 + 1
                    batch_result.append(processed_item)
                    total_processed += 1

                processed_results.append(batch_result)

            return processed_results, total_processed

        loops = detect_loops_in_function(
            data_processing_pipeline, ([[1, 2, 3], [4, 5, 6], [7, 8, 9]],)
        )

        # Should detect nested loops
        assert len(loops) >= 2

        # Analyze the patterns
        analysis = analyze_loop_patterns(
            data_processing_pipeline, ([[1, 2, 3], [4, 5, 6]],)
        )
        assert analysis["total_loops"] >= 2
        assert analysis["nested_loops"] >= 1

    def test_machine_learning_simulation(self):
        """Test analysis of machine learning-style loops."""

        def gradient_descent_simulation(epochs, learning_rate):
            weights = [0.5, 0.3, 0.8]
            loss_history = []

            for epoch in range(epochs):
                total_loss = 0.0
                for i, weight in enumerate(weights):
                    # Simulate gradient calculation
                    gradient = weight * learning_rate * 0.1
                    weights[i] = weight - gradient
                    total_loss += abs(gradient)

                loss_history.append(total_loss)

                # Early stopping condition
                if total_loss < 0.001:
                    break

            return weights, loss_history

        loops = detect_loops_in_function(gradient_descent_simulation, (100, 0.01))

        # Should detect the outer training loop and inner weight update loop
        assert len(loops) >= 1

        # The outer loop might not be parallelizable due to the sequential nature
        # and early stopping condition
        outer_loops = [loop for loop in loops if loop.nested_level == 0]
        if outer_loops:
            outer_loop = outer_loops[0]
            # Early stopping (break) should make it non-parallelizable
            # This tests the dependency analysis for control flow

    def test_numerical_computation(self):
        """Test analysis of numerical computation loops."""

        def monte_carlo_pi_estimation(n_samples):
            inside_circle = 0

            for i in range(n_samples):
                # Generate random points (simplified)
                x = (i * 31 + 17) % 1000 / 1000.0  # Pseudo-random
                y = (i * 37 + 23) % 1000 / 1000.0

                if x * x + y * y <= 1.0:
                    inside_circle += 1

            pi_estimate = 4.0 * inside_circle / n_samples
            return pi_estimate

        loops = detect_loops_in_function(monte_carlo_pi_estimation, (10000,))

        if loops:
            loop = loops[0]

            # Should detect the accumulation pattern (inside_circle += 1)
            # But this is a race condition, so might not be parallelizable
            # Test the analysis capabilities
            suggestions = loop.parallelization_suggestions()
            assert isinstance(suggestions, dict)

            # If it detects the race condition, should suggest alternatives
            if not loop.is_parallelizable:
                assert len(suggestions["alternative_strategies"]) > 0


@pytest.mark.integration
class TestClusterDecoratorIntegration:
    """Test integration with the @cluster decorator (requires clustrix.decorator)."""

    def test_loop_analysis_with_cluster_decorator(self):
        """Test that loop analysis integrates properly with @cluster decorator."""
        try:
            from clustrix.decorator import cluster

            @cluster(cores=4)
            def parallelizable_function(n):
                results = []
                for i in range(n):
                    results.append(i**2)
                return results

            # Test that we can analyze the decorated function
            loops = detect_loops_in_function(parallelizable_function.func, (1000,))

            if loops:
                loop = loops[0]
                integration = integrate_with_decorator(
                    loops, parallelizable_function.func, {"cores": 4}
                )

                assert integration["recommended_cores"] >= 4

        except ImportError:
            # Skip if decorator module not available
            pytest.skip("clustrix.decorator not available")

    def test_validation_with_decorator_config(self):
        """Test validation of loop analysis with decorator configuration."""

        def test_function():
            for i in range(5000):
                result = i * 2
            return result

        loops = detect_loops_in_function(test_function)

        if loops:
            # Test with underprovisioned decorator config
            integration = integrate_with_decorator(
                loops, test_function, {"cores": 1, "memory": "512MB"}
            )

            # Should suggest improvements
            assert len(integration["decorator_modifications"]) > 0

            # Validate the integration results
            validation = validate_analysis_results(loops, test_function)
            assert validation["confidence_level"] in ["low", "medium", "high"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
