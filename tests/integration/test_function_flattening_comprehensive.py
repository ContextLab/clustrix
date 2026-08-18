#!/usr/bin/env python3
"""
Comprehensive test suite for function flattening capabilities.
Tests nested functions, inline functions, closures, and edge cases.
"""

from clustrix.function_flattening import (
    auto_flatten_if_needed,
    analyze_function_complexity,
)
from clustrix import cluster
import inspect


def test_basic_nested_function():
    """Test basic nested function flattening."""
    print("🧪 Testing basic nested function...")

    def outer_function(x, y):
        """Function with a simple nested function."""

        def inner_add(a, b):
            return a + b

        result = inner_add(x, y)
        return result * 2

    # Test complexity analysis
    complexity = analyze_function_complexity(outer_function)
    print(f"Complexity: {complexity}")

    # Test flattening
    flattened_func, flattening_info = auto_flatten_if_needed(outer_function)
    print(
        f"Flattening successful: {flattening_info.get('success', False) if flattening_info else 'No flattening needed'}"
    )

    if flattening_info and flattening_info.get("success"):
        print("✅ Flattened function created")
        # Test that flattened function works
        try:
            original_result = outer_function(3, 4)
            flattened_result = flattened_func(3, 4)
            print(f"Original result: {original_result}")
            print(f"Flattened result: {flattened_result}")

            if original_result == flattened_result:
                print("✅ Results match")
                return True
            else:
                print("❌ Results don't match")
                return False
        except Exception as e:
            print(f"❌ Flattened function execution failed: {e}")
            return False
    else:
        print("ℹ️  Function not considered complex enough for flattening")
        return True


def test_nested_function_with_closure():
    """Test nested function that captures variables from outer scope."""
    print("\n🧪 Testing nested function with closure...")

    def outer_with_closure(multiplier):
        """Function with nested function that uses closure."""
        base_value = 10

        def inner_multiply(x):
            # Uses both parameter and closure variables
            return x * multiplier + base_value

        results = []
        for i in range(3):
            results.append(inner_multiply(i))

        return results

    complexity = analyze_function_complexity(outer_with_closure)
    print(f"Complexity: {complexity}")

    flattened_func, flattening_info = auto_flatten_if_needed(outer_with_closure)

    if flattening_info and flattening_info.get("success"):
        try:
            original_result = outer_with_closure(5)
            flattened_result = flattened_func(5)
            print(f"Original result: {original_result}")
            print(f"Flattened result: {flattened_result}")

            if original_result == flattened_result:
                print("✅ Closure flattening successful")
                return True
            else:
                print("❌ Closure flattening failed - results don't match")
                return False
        except Exception as e:
            print(f"❌ Closure flattening execution failed: {e}")
            return False
    else:
        print("ℹ️  Function not flattened")
        return True


def test_multiple_nested_functions():
    """Test function with multiple nested functions."""
    print("\n🧪 Testing multiple nested functions...")

    def outer_multiple_nested(data):
        """Function with multiple nested functions."""

        def process_item(item):
            return item * 2

        def filter_item(item):
            return item > 5

        def summarize(items):
            return sum(items) / len(items) if items else 0

        processed = [process_item(x) for x in data]
        filtered = [x for x in processed if filter_item(x)]
        summary = summarize(filtered)

        return {"processed": processed, "filtered": filtered, "summary": summary}

    complexity = analyze_function_complexity(outer_multiple_nested)
    print(f"Complexity: {complexity}")

    flattened_func, flattening_info = auto_flatten_if_needed(outer_multiple_nested)

    if flattening_info and flattening_info.get("success"):
        try:
            test_data = [1, 2, 3, 4, 5, 6]
            original_result = outer_multiple_nested(test_data)
            flattened_result = flattened_func(test_data)

            print(f"Original result: {original_result}")
            print(f"Flattened result: {flattened_result}")

            if original_result == flattened_result:
                print("✅ Multiple nested functions flattened successfully")
                return True
            else:
                print("❌ Multiple nested flattening failed")
                return False
        except Exception as e:
            print(f"❌ Multiple nested execution failed: {e}")
            return False
    else:
        print("ℹ️  Function not flattened")
        return True


def test_inline_function_from_test_file():
    """Test the exact pattern from our failing test case."""
    print("\n🧪 Testing inline function pattern from actual test case...")

    def test_function_container():
        """Container function similar to our test case."""

        def simple_gpu_matrix_mult():
            """Simple GPU matrix multiplication - inline function."""
            # Simulate the torch operations without actually importing torch
            return {
                "success": True,
                "device": "cuda:0",
                "result_shape": [100, 100],
                "result_mean": 0.021056,
                "result_std": 9.934280,
                "cuda_available": True,
                "gpu_count": 8,
            }

        # Execute the inline function
        result = simple_gpu_matrix_mult()
        return result

    complexity = analyze_function_complexity(test_function_container)
    print(f"Complexity: {complexity}")

    flattened_func, flattening_info = auto_flatten_if_needed(test_function_container)

    if flattening_info:
        print(f"Flattening attempt made: {flattening_info}")

        if flattening_info.get("success"):
            try:
                original_result = test_function_container()
                flattened_result = flattened_func()

                print(f"Original result: {original_result}")
                print(f"Flattened result: {flattened_result}")

                if original_result == flattened_result:
                    print("✅ Inline function flattened successfully")
                    return True
                else:
                    print("❌ Inline function flattening failed")
                    return False
            except Exception as e:
                print(f"❌ Inline function execution failed: {e}")
                import traceback

                traceback.print_exc()
                return False
        else:
            print(
                f"❌ Flattening failed: {flattening_info.get('error', 'Unknown error')}"
            )
            return False
    else:
        print("ℹ️  Function not flattened")
        return True


def test_deeply_nested_functions():
    """Test functions nested multiple levels deep."""
    print("\n🧪 Testing deeply nested functions...")

    def level1(x):
        """Level 1 function."""

        def level2(y):
            """Level 2 nested function."""

            def level3(z):
                """Level 3 nested function."""
                return z**2

            return level3(y) + 1

        return level2(x) * 2

    complexity = analyze_function_complexity(level1)
    print(f"Complexity: {complexity}")

    flattened_func, flattening_info = auto_flatten_if_needed(level1)

    if flattening_info and flattening_info.get("success"):
        try:
            original_result = level1(3)
            flattened_result = flattened_func(3)

            print(f"Original result: {original_result}")
            print(f"Flattened result: {flattened_result}")

            if original_result == flattened_result:
                print("✅ Deeply nested functions flattened successfully")
                return True
            else:
                print("❌ Deep nesting flattening failed")
                return False
        except Exception as e:
            print(f"❌ Deep nesting execution failed: {e}")
            return False
    else:
        print("ℹ️  Function not flattened")
        return True


def test_current_flattening_capabilities():
    """Test what the current flattening system can actually handle."""
    print("\n🧪 Testing current flattening capabilities...")

    # Create a function that should trigger flattening based on complexity
    def complex_function_for_testing():
        """A deliberately complex function to test flattening."""
        import subprocess
        import os
        import json
        import time

        results = []

        # Add complexity through multiple operations
        for i in range(5):
            for j in range(3):
                if i > 0:
                    # Subprocess call (high complexity)
                    result = subprocess.run(
                        ["echo", f"test_{i}_{j}"], capture_output=True, text=True
                    )

                    if result.returncode == 0:
                        output = result.stdout.strip()
                        parsed = {"i": i, "j": j, "output": output}
                        results.append(parsed)

                # Additional complexity
                time.sleep(0.01)

        # More complex operations
        final_result = {
            "results": results,
            "total_count": len(results),
            "processed_at": time.time(),
        }

        return final_result

    complexity = analyze_function_complexity(complex_function_for_testing)
    print(f"Complex function complexity: {complexity}")

    flattened_func, flattening_info = auto_flatten_if_needed(
        complex_function_for_testing
    )

    if flattening_info:
        print(f"Flattening info: {flattening_info}")

        if flattening_info.get("success"):
            print("✅ Current flattening system created a flattened function")

            # Check if we can execute it
            try:
                print("Testing flattened function execution...")
                result = flattened_func()
                print(f"Flattened function result: {type(result)}")
                print("✅ Flattened function executed successfully")
                return True
            except Exception as e:
                print(f"❌ Flattened function execution failed: {e}")
                return False
        else:
            print(f"❌ Flattening failed: {flattening_info.get('error')}")
            return False
    else:
        print("ℹ️  Function not considered for flattening")
        return True


if __name__ == "__main__":
    print("🚀 Comprehensive Function Flattening Test Suite")
    print("=" * 60)

    tests = [
        test_basic_nested_function,
        test_nested_function_with_closure,
        test_multiple_nested_functions,
        test_inline_function_from_test_file,
        test_deeply_nested_functions,
        test_current_flattening_capabilities,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
            results.append(False)

    print("\n" + "=" * 60)
    print("📊 Test Results Summary:")
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {i+1}. {test.__name__}: {status}")

    passed = sum(results)
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")

    if passed < total:
        print(
            "\n⚠️  Function flattening needs improvements for nested/inline functions!"
        )
    else:
        print("\n🎉 All function flattening tests passed!")
