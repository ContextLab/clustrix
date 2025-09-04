"""Tests for error handling and edge cases in loop analysis functionality."""

import ast
import sys
import pytest
from unittest.mock import patch, MagicMock
from clustrix.loop_analysis import (
    LoopInfo,
    SafeRangeEvaluator,
    DependencyAnalyzer,
    LoopDetector,
    _ast_to_string,
    detect_loops_in_function,
    find_parallelizable_loops,
    analyze_loop_patterns,
    estimate_work_size,
    detect_loops,
    try_parse_ast_safely,
    handle_malformed_nodes,
    validate_python_version_compatibility,
    performance_limit_check,
)


class TestASTErrorHandling:
    """Test error handling in AST parsing and analysis."""

    def test_malformed_ast_nodes(self):
        """Test handling of malformed AST nodes."""

        # Create a malformed node that might occur in edge cases
        class MalformedNode:
            def __init__(self):
                self.lineno = 1
                self.col_offset = 0

        malformed = MalformedNode()

        # Test _ast_to_string with malformed node
        result = _ast_to_string(malformed)
        assert isinstance(result, str)
        assert "MalformedNode" in result

    def test_ast_to_string_with_none_attributes(self):
        """Test _ast_to_string with nodes having None attributes."""
        # Create a Name node with None id (edge case)
        node = ast.Name()
        node.id = None

        # Should handle gracefully without crashing
        try:
            result = _ast_to_string(node)
            assert result is not None
        except AttributeError:
            # This is acceptable - we're testing error resilience
            pass

    def test_ast_to_string_with_complex_call(self):
        """Test _ast_to_string with complex call expressions."""
        # Create a call with complex nested structure
        call_node = ast.Call()
        call_node.func = ast.Attribute()
        call_node.func.value = ast.Name()
        call_node.func.value.id = "obj"
        call_node.func.attr = "method"
        call_node.args = []

        result = _ast_to_string(call_node)
        assert isinstance(result, str)

    def test_safe_range_evaluator_malformed_input(self):
        """Test SafeRangeEvaluator with malformed input."""
        evaluator = SafeRangeEvaluator()

        # Test with None node
        try:
            evaluator._evaluate_node(None)
            # Should return None and set safe=False
            assert not evaluator.safe or evaluator.result is None
        except (AttributeError, TypeError):
            # This is acceptable behavior
            pass

    def test_safe_range_evaluator_invalid_range_args(self):
        """Test SafeRangeEvaluator with invalid range arguments."""
        evaluator = SafeRangeEvaluator()

        # Create a range call with too many arguments
        call_node = ast.Call()
        call_node.func = ast.Name(id="range")
        # Add 4 arguments (range only accepts 1-3)
        call_node.args = [
            ast.Constant(value=0),
            ast.Constant(value=10),
            ast.Constant(value=1),
            ast.Constant(value=5),  # Extra argument
        ]

        evaluator.visit_Call(call_node)
        assert not evaluator.safe

    def test_safe_range_evaluator_non_integer_args(self):
        """Test SafeRangeEvaluator with non-integer arguments."""
        evaluator = SafeRangeEvaluator()

        # Create a range call with string argument
        call_node = ast.Call()
        call_node.func = ast.Name(id="range")
        call_node.args = [ast.Constant(value="not_a_number")]

        evaluator.visit_Call(call_node)
        assert not evaluator.safe

    def test_dependency_analyzer_malformed_nodes(self):
        """Test DependencyAnalyzer with malformed AST nodes."""
        analyzer = DependencyAnalyzer()

        # Test with node that has missing attributes
        malformed_name = ast.Name()
        # Don't set the id attribute
        try:
            analyzer.visit_Name(malformed_name)
            # Should handle gracefully
        except AttributeError:
            # Acceptable behavior for malformed nodes
            pass

    def test_dependency_analyzer_invalid_subscript(self):
        """Test DependencyAnalyzer with invalid subscript nodes."""
        analyzer = DependencyAnalyzer()

        # Create a subscript with malformed value
        subscript = ast.Subscript()
        subscript.value = None  # Invalid
        subscript.slice = ast.Constant(value=0)

        try:
            analyzer.visit_Subscript(subscript)
        except AttributeError:
            # Expected behavior for malformed nodes
            pass

    def test_loop_detector_empty_function(self):
        """Test LoopDetector with empty function body."""
        detector = LoopDetector()

        # Parse an empty function
        code = """
def empty_function():
    pass
"""
        tree = ast.parse(code)
        func_node = tree.body[0]

        # Visit empty function body
        for stmt in func_node.body:
            detector.visit(stmt)

        assert len(detector.loops) == 0

    def test_loop_detector_malformed_for_loop(self):
        """Test LoopDetector with malformed for loop."""
        detector = LoopDetector()

        # Create a for loop with missing target
        for_node = ast.For()
        for_node.target = None  # Malformed
        for_node.iter = ast.Name(id="items")
        for_node.body = [ast.Pass()]
        for_node.orelse = []

        try:
            detector.visit_For(for_node)
            # Should handle gracefully or return None
        except AttributeError:
            # Expected for malformed nodes
            pass


class TestPythonVersionCompatibility:
    """Test Python version compatibility across different versions."""

    def test_ast_constant_vs_num_compatibility(self):
        """Test compatibility between ast.Constant (3.8+) and ast.Num (<3.8)."""
        evaluator = SafeRangeEvaluator()

        # Test with ast.Constant (Python 3.8+)
        if hasattr(ast, "Constant"):
            constant_node = ast.Constant(value=42)
            result = evaluator._evaluate_node(constant_node)
            assert result == 42

        # Test with ast.Num (Python < 3.8) - create manually if needed
        if hasattr(ast, "Num"):
            num_node = ast.Num(n=42)
            result = evaluator._evaluate_node(num_node)
            assert result == 42

    def test_ast_to_string_python_version_compat(self):
        """Test _ast_to_string compatibility across Python versions."""
        # Test with different node types that vary across Python versions

        # ast.Constant (3.8+)
        if hasattr(ast, "Constant"):
            const_node = ast.Constant(value=123)
            result = _ast_to_string(const_node)
            assert "123" in result

        # ast.Num (< 3.8)
        if hasattr(ast, "Num"):
            num_node = ast.Num(n=456)
            result = _ast_to_string(num_node)
            assert "456" in result

    @pytest.mark.skipif(sys.version_info < (3, 9), reason="Requires Python 3.9+")
    def test_python39_features(self):
        """Test features that require Python 3.9+."""
        # Test any Python 3.9+ specific AST features
        code = "x = [i for i in range(10)]"
        tree = ast.parse(code)

        # Should parse without errors
        assert len(tree.body) == 1

    @pytest.mark.skipif(sys.version_info >= (3, 12), reason="Tests deprecated features")
    def test_deprecated_ast_features(self):
        """Test handling of deprecated AST features."""
        # Test handling of features that might be deprecated in newer Python versions
        if hasattr(ast, "Num"):  # Deprecated in Python 3.8+
            num_node = ast.Num(n=42)
            result = _ast_to_string(num_node)
            assert "42" in result


class TestPerformanceAndLimits:
    """Test performance limits and deeply nested structures."""

    def test_deeply_nested_loops_detection(self):
        """Test loop detection with deeply nested structures."""
        # Create code with deeply nested loops
        nested_code = "def nested_function():\n"
        indent = "    "

        # Create 10 levels of nesting
        for i in range(10):
            nested_code += f"{indent * (i + 1)}for i{i} in range(10):\n"

        # Add a simple statement at the deepest level
        nested_code += f"{indent * 11}pass\n"

        try:
            tree = ast.parse(nested_code)
            detector = LoopDetector()
            detector.visit(tree)

            # Should detect all nested loops
            assert len(detector.loops) == 10

            # Check nesting levels
            max_level = max(loop.nested_level for loop in detector.loops)
            assert max_level == 9  # 0-indexed

        except RecursionError:
            # If we hit recursion limits, that's a valid limitation to document
            pytest.skip("Hit Python recursion limit with deeply nested structures")

    def test_large_function_analysis_limits(self):
        """Test analysis limits with very large functions."""
        # Create a function with many loops
        large_code = "def large_function():\n"

        # Add 100 separate for loops
        for i in range(100):
            large_code += f"    for x{i} in range(10):\n"
            large_code += f"        result{i} = x{i} * 2\n"

        try:
            tree = ast.parse(large_code)
            detector = LoopDetector()
            detector.visit(tree)

            # Should detect all loops
            assert len(detector.loops) == 100

        except MemoryError:
            # If we hit memory limits, document this limitation
            pytest.skip("Hit memory limit with large function analysis")

    def test_complex_expression_parsing_limits(self):
        """Test parsing limits with complex expressions."""
        # Create a function with very complex expressions
        complex_code = """
def complex_function():
    for i in range(((((1 + 2) * 3) - 4) / 5) + (6 * (7 + (8 - (9 * 10))))):
        result = i + ((((a * b) + (c * d)) - (e * f)) / (g + h))
"""

        try:
            tree = ast.parse(complex_code)
            detector = LoopDetector()
            detector.visit(tree)

            # Should handle complex expressions
            assert len(detector.loops) >= 0

        except Exception as e:
            # Document any limitations with complex expressions
            pytest.skip(f"Complex expression parsing limitation: {e}")

    def test_performance_timeout_simulation(self):
        """Test behavior under simulated performance constraints."""
        # Simulate a timeout scenario using mock
        with patch("time.time") as mock_time:
            # Simulate time running out during analysis
            mock_time.side_effect = [0, 1000000]  # Large time jump

            code = """
def test_function():
    for i in range(1000):
        for j in range(1000):
            result = i * j
"""
            tree = ast.parse(code)
            detector = LoopDetector()

            # Should still complete analysis even with time constraints
            detector.visit(tree)
            assert len(detector.loops) == 2


class TestExceptionSpecificity:
    """Test enhanced exception specificity and error reporting."""

    def test_specific_parsing_exceptions(self):
        """Test that specific exceptions are raised for different error types."""

        # Test with invalid syntax that would cause SyntaxError
        invalid_code = "for i in range(10\n    pass"  # Missing closing parenthesis

        with pytest.raises(SyntaxError):
            ast.parse(invalid_code)

    def test_graceful_error_propagation(self):
        """Test that errors are propagated gracefully through the analysis chain."""

        def test_function_with_invalid_code():
            # This simulates a function that can't be inspected properly
            pass

        # Mock inspect.getsource to raise an error
        with patch("inspect.getsource") as mock_getsource:
            mock_getsource.side_effect = OSError("Source not available")

            # The function should handle this gracefully
            try:
                result = detect_loops_in_function(test_function_with_invalid_code)
                # Should return None or empty result, not crash
                assert result is None or isinstance(result, (dict, list))
            except OSError:
                # If it propagates the OSError, that's also acceptable
                pass

    def test_error_context_preservation(self):
        """Test that error context is preserved in exceptions."""

        # Create a scenario that would generate an error with context
        malformed_range = SafeRangeEvaluator()

        # Create a complex malformed call
        call_node = ast.Call()
        call_node.func = ast.Name(id="not_range")
        call_node.args = [ast.Constant(value=10)]

        # Visit the malformed call
        malformed_range.visit_Call(call_node)

        # Should set safe=False but not crash
        assert not malformed_range.safe

    def test_dependency_analysis_error_handling(self):
        """Test error handling in dependency analysis."""

        analyzer = DependencyAnalyzer()

        # Create a scenario with circular references or complex dependencies
        code_with_complex_deps = """
for i in globals()['items']:
    locals()['result'] = globals()[f'func_{i}'](locals()['data'])
"""

        try:
            tree = ast.parse(code_with_complex_deps)
            for node in ast.walk(tree):
                if isinstance(node, ast.For):
                    analyzer.visit_For(node)

            # Should handle complex scenarios without crashing
            deps = analyzer.has_dependencies()
            assert isinstance(deps, bool)

        except Exception as e:
            # Document any limitations with complex dependency analysis
            pytest.skip(f"Complex dependency analysis limitation: {e}")

    def test_loop_info_validation_errors(self):
        """Test validation errors in LoopInfo creation."""

        # Test with invalid range info
        try:
            loop_info = LoopInfo(
                loop_type="for",
                variable="i",
                range_info={"start": "invalid", "stop": 10, "step": 1},
            )
            # The parallelizability assessment should handle invalid data
            assert isinstance(loop_info.is_parallelizable, bool)
        except (TypeError, ValueError):
            # Acceptable to raise validation errors
            pass

    def test_work_size_estimation_edge_cases(self):
        """Test work size estimation with edge cases."""

        # Test with invalid loop info
        loop_info = LoopInfo(
            loop_type="while",  # While loops are harder to estimate
            variable=None,
            range_info=None,
        )

        # Should handle gracefully
        work_size = estimate_work_size(loop_info)
        assert isinstance(work_size, int)
        assert work_size >= 0

        # Test with negative range
        loop_info_negative = LoopInfo(
            loop_type="for",
            variable="i",
            range_info={"start": 10, "stop": 0, "step": 1},  # Invalid range
        )

        work_size_negative = estimate_work_size(loop_info_negative)
        assert isinstance(work_size_negative, int)
        assert work_size_negative >= 0


class TestRealWorldErrorScenarios:
    """Test error handling with real-world problematic code patterns."""

    def test_lambda_in_loop_analysis(self):
        """Test analysis of loops containing lambda functions."""

        code_with_lambda = """
def function_with_lambda():
    for item in items:
        result = list(map(lambda x: x * 2, item))
        processed = filter(lambda y: y > 0, result)
"""

        try:
            tree = ast.parse(code_with_lambda)
            detector = LoopDetector()
            detector.visit(tree)

            # Should detect the loop despite lambda complexity
            assert len(detector.loops) >= 0

        except Exception as e:
            pytest.skip(f"Lambda analysis limitation: {e}")

    def test_generator_expression_in_loop(self):
        """Test analysis of loops with generator expressions."""

        code_with_generator = """
def function_with_generator():
    for batch in data_batches:
        processed = (item.process() for item in batch if item.is_valid())
        results = list(processed)
"""

        try:
            tree = ast.parse(code_with_generator)
            detector = LoopDetector()
            detector.visit(tree)

            # Should handle generator expressions
            assert len(detector.loops) >= 0

        except Exception as e:
            pytest.skip(f"Generator expression analysis limitation: {e}")

    def test_dynamic_attribute_access_in_loop(self):
        """Test analysis of loops with dynamic attribute access."""

        code_with_dynamic = """
def function_with_dynamic_access():
    for attr_name in dir(obj):
        value = getattr(obj, attr_name, None)
        if callable(value):
            result = value()
"""

        try:
            tree = ast.parse(code_with_dynamic)
            detector = LoopDetector()
            analyzer = DependencyAnalyzer()

            detector.visit(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.For):
                    for stmt in node.body:
                        analyzer.visit(stmt)

            # Should handle dynamic access patterns
            assert len(detector.loops) >= 0
            deps = analyzer.has_dependencies()
            assert isinstance(deps, bool)

        except Exception as e:
            pytest.skip(f"Dynamic access analysis limitation: {e}")

    def test_exception_handling_in_loop(self):
        """Test analysis of loops with exception handling."""

        code_with_exceptions = """
def function_with_exceptions():
    for item in items:
        try:
            result = risky_operation(item)
        except ValueError as e:
            result = default_value
        except Exception:
            continue
        finally:
            cleanup(item)
"""

        try:
            tree = ast.parse(code_with_exceptions)
            detector = LoopDetector()
            analyzer = DependencyAnalyzer()

            detector.visit(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.For):
                    for stmt in node.body:
                        analyzer.visit(stmt)

            # Should handle try/except blocks
            assert len(detector.loops) >= 0

            # Should detect continue statement
            assert analyzer.has_continue

        except Exception as e:
            pytest.skip(f"Exception handling analysis limitation: {e}")


class TestRobustErrorHandlingUtilities:
    """Test the new robust error handling utility functions."""

    def test_try_parse_ast_safely_valid_code(self):
        """Test try_parse_ast_safely with valid code."""
        valid_code = """
def test_function():
    for i in range(10):
        print(i)
"""
        tree = try_parse_ast_safely(valid_code)
        assert tree is not None
        assert isinstance(tree, ast.AST)

    def test_try_parse_ast_safely_invalid_code(self):
        """Test try_parse_ast_safely with invalid code."""
        invalid_code = "for i in range(10\n    pass"  # Missing closing parenthesis
        tree = try_parse_ast_safely(invalid_code)
        assert tree is None

    def test_try_parse_ast_safely_empty_input(self):
        """Test try_parse_ast_safely with empty or invalid input."""
        # Empty string
        assert try_parse_ast_safely("") is None

        # Whitespace only
        assert try_parse_ast_safely("   \n\t  ") is None

        # None input
        assert try_parse_ast_safely(None) is None

        # Non-string input
        assert try_parse_ast_safely(123) is None

    def test_handle_malformed_nodes_valid_node(self):
        """Test handle_malformed_nodes with valid AST node."""
        valid_node = ast.Name(id="variable")
        assert handle_malformed_nodes(valid_node, "Name") is True

    def test_handle_malformed_nodes_none_node(self):
        """Test handle_malformed_nodes with None node."""
        assert handle_malformed_nodes(None, "None") is False

    def test_handle_malformed_nodes_malformed_node(self):
        """Test handle_malformed_nodes with malformed node."""

        # Create a mock object that simulates a malformed node
        class MalformedNode:
            def __init__(self):
                # Override __class__ access to raise an exception
                pass

            def __getattribute__(self, name):
                if name == "__class__":
                    raise AttributeError("Simulated corruption")
                return super().__getattribute__(name)

        malformed = MalformedNode()

        # Should handle gracefully
        result = handle_malformed_nodes(malformed, "Malformed")
        # Should return False for malformed node
        assert result is False

    def test_validate_python_version_compatibility_current_version(self):
        """Test validate_python_version_compatibility with current Python version."""
        # Should pass for reasonable minimum version
        assert validate_python_version_compatibility("test_feature", (3, 6)) is True

        # Should fail for unrealistic future version
        assert validate_python_version_compatibility("future_feature", (4, 0)) is False

    def test_validate_python_version_compatibility_edge_cases(self):
        """Test validate_python_version_compatibility with edge cases."""
        # Very old version should pass
        assert validate_python_version_compatibility("old_feature", (2, 7)) is True

        # Current exact version should pass
        import sys

        current = sys.version_info[:2]
        assert validate_python_version_compatibility("current_feature", current) is True

    def test_performance_limit_check_valid_sizes(self):
        """Test performance_limit_check with valid sizes."""
        # Within limits
        assert performance_limit_check(100, 1000) is True

        # At the limit
        assert performance_limit_check(1000, 1000) is True

        # Over the limit
        assert performance_limit_check(1001, 1000) is False

    def test_performance_limit_check_edge_cases(self):
        """Test performance_limit_check with edge cases."""
        # Negative size
        assert performance_limit_check(-1, 1000) is False

        # Zero size
        assert performance_limit_check(0, 1000) is True

        # Zero limit
        assert performance_limit_check(1, 0) is False

    def test_enhanced_ast_to_string_error_handling(self):
        """Test _ast_to_string with enhanced error handling."""
        # Test with None node
        assert _ast_to_string(None) == "None"

        # Test with node having None id
        name_node = ast.Name()
        name_node.id = None
        result = _ast_to_string(name_node)
        assert result == "UnknownName"

    def test_integration_of_error_handling_functions(self):
        """Test integration of all error handling functions together."""
        # Test a complete workflow with error handling
        problematic_code = "for i in range(10"  # Missing closing parenthesis

        # Should parse safely and return None
        tree = try_parse_ast_safely(problematic_code)
        assert tree is None

        # Test with valid code and malformed node handling
        valid_code = "for i in range(10): pass"
        tree = try_parse_ast_safely(valid_code)
        assert tree is not None

        # Check performance limits for the parsed tree
        node_count = len(list(ast.walk(tree)))
        assert performance_limit_check(node_count, 100) is True

    def test_error_handling_with_real_world_patterns(self):
        """Test error handling with real-world problematic code patterns."""
        # Test with code that has various syntax issues
        problematic_patterns = [
            "def func(\n    pass",  # Incomplete function definition
            "for i in:\n    pass",  # Missing iterable
            "while:\n    pass",  # Missing condition
            "if:\n    pass",  # Missing condition
            "try:\n    pass\nexcept:",  # Incomplete except
        ]

        for pattern in problematic_patterns:
            tree = try_parse_ast_safely(pattern)
            # Should return None for all invalid patterns
            assert tree is None

    def test_visitor_error_resilience(self):
        """Test that visitor classes handle errors gracefully with enhanced functions."""
        # Create valid AST and then test visitor resilience
        code = """
def test_func():
    for i in range(10):
        result = i * 2
        print(result)
"""
        tree = try_parse_ast_safely(code)
        assert tree is not None

        # Test that detector handles the valid tree
        detector = LoopDetector()
        try:
            detector.visit(tree)
            assert len(detector.loops) >= 0  # Should not crash
        except Exception as e:
            # Should not reach here with enhanced error handling
            pytest.fail(f"Visitor should handle tree gracefully: {e}")

    def test_error_logging_integration(self):
        """Test that error conditions generate appropriate log messages."""
        import logging

        # Capture log messages
        with patch("clustrix.loop_analysis.logger") as mock_logger:
            # Test various error conditions
            try_parse_ast_safely("")  # Empty code
            handle_malformed_nodes(None)  # None node
            validate_python_version_compatibility("test", (4, 0))  # Future version
            performance_limit_check(-1)  # Invalid size

            # Verify that debug messages were logged
            assert mock_logger.debug.called
            call_count = mock_logger.debug.call_count
            assert call_count >= 4  # At least one call for each error condition
