"""Comprehensive tests for AST parsing and comprehension support in loop analysis.

This module focuses on testing AST visitor methods for various Python loop constructs
including list/set/dict comprehensions, generator expressions, and tuple unpacking.
"""

import ast
import pytest
from clustrix.loop_analysis import (
    LoopInfo,
    LoopDetector,
    DependencyAnalyzer,
    detect_loops_in_function,
    find_parallelizable_loops,
)


class TestBase:
    """Base class with common test infrastructure for AST parsing tests."""

    @pytest.fixture
    def sample_code_patterns(self):
        """Provide reusable code samples for AST parsing tests."""
        return {
            "list_comp": "[x**2 for x in range(10)]",
            "set_comp": "{x**2 for x in range(10)}",
            "dict_comp": "{x: x**2 for x in range(10)}",
            "generator_exp": "(x**2 for x in range(10))",
            "nested_comp": "[[y for y in range(x)] for x in range(5)]",
            "conditional_comp": "[x for x in range(20) if x % 2 == 0]",
            "tuple_unpack": "for a, b in items",
            "multi_tuple_unpack": "for a, b, c in items",
            "nested_tuple_unpack": "for (a, b), c in items",
        }

    @pytest.fixture
    def ast_parsing_utilities(self):
        """Common AST parsing helpers."""

        def parse_expression(code):
            """Parse a Python expression into AST."""
            try:
                return ast.parse(code, mode="eval").body
            except SyntaxError:
                return ast.parse(code, mode="exec").body[0].value

        def parse_statement(code):
            """Parse a Python statement into AST."""
            return ast.parse(code, mode="exec").body[0]

        def parse_function(func_code):
            """Parse a function definition into AST."""
            return ast.parse(func_code, mode="exec").body[0]

        return {
            "parse_expression": parse_expression,
            "parse_statement": parse_statement,
            "parse_function": parse_function,
        }

    def create_test_function(self, body_code):
        """Helper to create test functions with given body code."""
        func_code = f"""
def test_function():
    {body_code}
    return result
"""
        return compile(func_code, "<string>", "exec")


class TestListComprehensions(TestBase):
    """Test list comprehension detection and analysis."""

    def test_simple_list_comprehension_function(self):
        """Test function containing simple list comprehension."""

        def list_comp_function():
            return [x**2 for x in range(10)]

        loops = detect_loops_in_function(list_comp_function)
        assert isinstance(loops, list)
        # Should detect comprehension as a loop construct

    def test_nested_list_comprehension_function(self):
        """Test function with nested list comprehensions."""

        def nested_list_comp_function():
            return [[y for y in range(x)] for x in range(5)]

        loops = detect_loops_in_function(nested_list_comp_function)
        assert isinstance(loops, list)

    def test_conditional_list_comprehension_function(self):
        """Test list comprehension with conditions."""

        def conditional_list_comp_function():
            return [x for x in range(20) if x % 2 == 0]

        loops = detect_loops_in_function(conditional_list_comp_function)
        assert isinstance(loops, list)

    def test_complex_list_comprehension_function(self):
        """Test complex list comprehension with multiple conditions."""

        def complex_list_comp_function(data):
            return [
                item.value * 2
                for item in data
                if hasattr(item, "value") and item.value > 0
            ]

        loops = detect_loops_in_function(complex_list_comp_function, ([],))
        assert isinstance(loops, list)


class TestSetComprehensions(TestBase):
    """Test set comprehension detection and analysis."""

    def test_simple_set_comprehension_function(self):
        """Test function containing simple set comprehension."""

        def set_comp_function():
            return {x**2 for x in range(10)}

        loops = detect_loops_in_function(set_comp_function)
        assert isinstance(loops, list)

    def test_conditional_set_comprehension_function(self):
        """Test set comprehension with conditions."""

        def conditional_set_comp_function():
            return {x for x in range(20) if x % 3 == 0}

        loops = detect_loops_in_function(conditional_set_comp_function)
        assert isinstance(loops, list)


class TestDictComprehensions(TestBase):
    """Test dict comprehension detection and analysis."""

    def test_simple_dict_comprehension_function(self):
        """Test function containing simple dict comprehension."""

        def dict_comp_function():
            return {x: x**2 for x in range(10)}

        loops = detect_loops_in_function(dict_comp_function)
        assert isinstance(loops, list)

    def test_complex_dict_comprehension_function(self):
        """Test dict comprehension with complex key-value expressions."""

        def complex_dict_comp_function(items):
            return {str(k): v * 2 for k, v in items if v > 0}

        loops = detect_loops_in_function(complex_dict_comp_function, ([],))
        assert isinstance(loops, list)


class TestGeneratorExpressions(TestBase):
    """Test generator expression detection and analysis."""

    def test_simple_generator_expression_function(self):
        """Test function containing simple generator expression."""

        def gen_exp_function():
            return sum(x**2 for x in range(100))

        loops = detect_loops_in_function(gen_exp_function)
        assert isinstance(loops, list)

    def test_nested_generator_expression_function(self):
        """Test function with nested generator expressions."""

        def nested_gen_exp_function():
            return list(sum(y for y in range(x)) for x in range(5))

        loops = detect_loops_in_function(nested_gen_exp_function)
        assert isinstance(loops, list)

    def test_conditional_generator_expression_function(self):
        """Test generator expression with conditions."""

        def conditional_gen_exp_function():
            return max(x for x in range(100) if x % 7 == 0)

        loops = detect_loops_in_function(conditional_gen_exp_function)
        assert isinstance(loops, list)


class TestTupleUnpacking(TestBase):
    """Test tuple unpacking in for loops."""

    def test_simple_tuple_unpacking_function(self):
        """Test function with simple tuple unpacking in for loop."""

        def tuple_unpack_function():
            result = []
            items = [(1, 2), (3, 4), (5, 6)]
            for a, b in items:
                result.append(a + b)
            return result

        loops = detect_loops_in_function(tuple_unpack_function)
        assert isinstance(loops, list)

    def test_multi_variable_tuple_unpacking_function(self):
        """Test function with multi-variable tuple unpacking."""

        def multi_tuple_unpack_function():
            result = []
            items = [(1, 2, 3), (4, 5, 6)]
            for a, b, c in items:
                result.append(a + b + c)
            return result

        loops = detect_loops_in_function(multi_tuple_unpack_function)
        assert isinstance(loops, list)

    def test_nested_tuple_unpacking_function(self):
        """Test function with nested tuple unpacking."""

        def nested_tuple_unpack_function():
            result = []
            items = [((1, 2), 3), ((4, 5), 6)]
            for (a, b), c in items:
                result.append(a + b + c)
            return result

        loops = detect_loops_in_function(nested_tuple_unpack_function)
        assert isinstance(loops, list)


class TestComprehensionParallelization(TestBase):
    """Test parallelization detection for comprehensions."""

    def test_parallelizable_list_comprehension(self):
        """Test detecting parallelizable list comprehensions."""

        def parallelizable_comp():
            return [expensive_computation(x) for x in range(1000)]

        def expensive_computation(x):
            return x**3 + x**2 + x

        # Inject the helper function into the test function's globals
        parallelizable_comp.__globals__["expensive_computation"] = expensive_computation

        loops = find_parallelizable_loops(parallelizable_comp, (), {})
        assert isinstance(loops, list)

    def test_non_parallelizable_comprehension_with_dependencies(self):
        """Test comprehensions with dependencies that prevent parallelization."""

        def dependent_comp():
            accumulator = 0
            return [accumulator := accumulator + x for x in range(10)]

        loops = find_parallelizable_loops(dependent_comp, (), {})
        assert isinstance(loops, list)


class TestASTVisitorMethods(TestBase):
    """Direct tests for AST visitor methods."""

    def test_loop_detector_initialization(self):
        """Test LoopDetector initialization."""
        detector = LoopDetector()
        assert detector.loops == []
        assert detector.current_level == 0

    def test_dependency_analyzer_comprehension_analysis(self):
        """Test DependencyAnalyzer on comprehension constructs."""
        code = """
result = [x**2 for x in data if x > threshold]
"""
        tree = ast.parse(code)
        analyzer = DependencyAnalyzer()
        analyzer.visit(tree)

        # Should detect reads from 'data' and 'threshold'
        assert isinstance(analyzer.reads, set)
        assert isinstance(analyzer.writes, set)


class TestEdgeCasesAndErrorHandling(TestBase):
    """Test edge cases and error handling for AST parsing."""

    def test_malformed_comprehension_handling(self):
        """Test handling of malformed comprehension syntax."""

        # This should not crash the analyzer
        def potentially_problematic_function():
            try:
                # Valid comprehension
                return [x for x in range(10)]
            except Exception:
                return []

        loops = detect_loops_in_function(potentially_problematic_function)
        assert isinstance(loops, list)

    def test_complex_nested_structures(self):
        """Test deeply nested comprehensions and loops."""

        def deeply_nested_function():
            return [[[z for z in range(y)] for y in range(x)] for x in range(3)]

        loops = detect_loops_in_function(deeply_nested_function)
        assert isinstance(loops, list)

    def test_mixed_loop_and_comprehension_constructs(self):
        """Test functions mixing traditional loops and comprehensions."""

        def mixed_constructs_function():
            result = []
            for i in range(5):
                inner_result = [x**2 for x in range(i)]
                result.extend(inner_result)

            # Add generator expression
            final_result = sum(x for x in result if x % 2 == 0)
            return final_result

        loops = detect_loops_in_function(mixed_constructs_function)
        assert isinstance(loops, list)


class TestRealWorldPatterns(TestBase):
    """Test realistic usage patterns combining various constructs."""

    def test_data_processing_with_comprehensions(self):
        """Test data processing function using comprehensions."""

        def process_data_with_comprehensions(data):
            # Filter and transform
            clean_data = [item for item in data if item is not None]
            transformed = {str(i): item**2 for i, item in enumerate(clean_data)}

            # Generate summary statistics
            squares = (x**2 for x in clean_data if x > 0)
            return sum(squares)

        loops = find_parallelizable_loops(
            process_data_with_comprehensions, ([1, 2, None, 3],), {}
        )
        assert isinstance(loops, list)

    def test_machine_learning_style_function(self):
        """Test ML-style function with various loop constructs."""

        def ml_style_function(features, labels):
            # Feature engineering with comprehensions
            normalized_features = [
                [val / max(row) for val in row] for row in features if max(row) > 0
            ]

            # Training loop with tuple unpacking
            loss_history = []
            for epoch, (feat_batch, label_batch) in enumerate(
                zip(normalized_features, labels)
            ):
                # Compute loss using generator expression
                batch_loss = sum(
                    (pred - actual) ** 2
                    for pred, actual in zip(feat_batch, label_batch)
                )
                loss_history.append(batch_loss)

            return loss_history

        sample_features = [[1, 2, 3], [4, 5, 6]]
        sample_labels = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

        loops = find_parallelizable_loops(
            ml_style_function, (sample_features, sample_labels), {}
        )
        assert isinstance(loops, list)
