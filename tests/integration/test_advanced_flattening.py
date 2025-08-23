#!/usr/bin/env python3
"""
Test the new advanced function flattening with dependency resolution.
"""


from clustrix.function_flattening import (
    AdvancedFunctionFlattener,
    auto_flatten_if_needed,
    analyze_function_complexity,
)
from clustrix.dependency_resolution import FunctionDependencyAnalyzer
import logging

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO)


def test_dependency_analyzer():
    """Test the dependency analyzer on its own."""
    print("🧪 Testing dependency analyzer...")

    def test_function_with_nested():
        """Test function with nested function."""

        def inner_func(x):
            return x * 2

        result = inner_func(5)
        return result + 1

    try:
        analyzer = FunctionDependencyAnalyzer(root_dir="/Users/jmanning/clustrix")
        dep_info = analyzer.analyze_function_dependencies(test_function_with_nested)

        print(f"✅ Dependency analysis successful:")
        print(f"   Main function: {dep_info.main_function.name}")
        print(f"   Is local: {dep_info.main_function.is_local}")
        print(f"   Dependencies: {len(dep_info.dependencies)}")
        print(f"   External modules: {dep_info.modules_to_import}")
        print(f"   Circular deps: {dep_info.circular_dependencies}")
        print(f"   Function dependencies: {dep_info.main_function.dependencies}")

        return True

    except Exception as e:
        print(f"❌ Dependency analysis failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_advanced_flattener():
    """Test the advanced flattener directly."""
    print("\n🧪 Testing advanced flattener...")

    def test_function_with_nested():
        """Test function with nested function."""

        def inner_func(x):
            return x * 2

        result = inner_func(5)
        return result + 1

    try:
        flattener = AdvancedFunctionFlattener(root_dir="/Users/jmanning/clustrix")
        result = flattener.flatten_with_dependencies(test_function_with_nested)

        if result.get("success"):
            print(f"✅ Advanced flattening successful:")
            print(f"   Dependencies found: {result['dependencies_count']}")
            print(f"   Hoisted functions: {result['hoisted_functions']}")
            print(f"   External modules: {result['external_modules']}")

            print(f"\n🔍 Generated flattened code:")
            print("=" * 50)
            print(result["flattened_function"])
            print("=" * 50)

            return True
        else:
            print(f"❌ Advanced flattening failed: {result.get('error')}")
            return False

    except Exception as e:
        print(f"❌ Advanced flattener crashed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_auto_flatten_with_nested():
    """Test auto_flatten_if_needed with nested functions."""
    print("\n🧪 Testing auto_flatten_if_needed with nested functions...")

    def outer_function(x, y):
        """Function with nested function."""

        def inner_add(a, b):
            return a + b

        result = inner_add(x, y)
        return result * 2

    try:
        # First check complexity
        complexity = analyze_function_complexity(outer_function)
        print(f"Complexity analysis: {complexity}")

        # Now test flattening
        flattened_func, flattening_info = auto_flatten_if_needed(outer_function)

        if flattening_info:
            print(f"Flattening attempted: {flattening_info.get('success', False)}")

            if flattening_info.get("success"):
                print("✅ Function was flattened successfully")

                # Test that the flattened function works
                try:
                    original_result = outer_function(3, 4)
                    flattened_result = flattened_func(3, 4)

                    print(f"Original result: {original_result}")
                    print(f"Flattened result: {flattened_result}")

                    if original_result == flattened_result:
                        print("✅ Results match - flattening preserves behavior")
                        return True
                    else:
                        print("❌ Results don't match")
                        return False

                except Exception as e:
                    print(f"❌ Error testing flattened function: {e}")
                    return False
            else:
                print(f"❌ Flattening failed: {flattening_info.get('error')}")
                return False
        else:
            print("ℹ️  Function was not considered complex enough for flattening")
            return True

    except Exception as e:
        print(f"❌ Test crashed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_closure_variables():
    """Test handling of closure variables."""
    print("\n🧪 Testing closure variable handling...")

    def outer_with_closure(multiplier):
        """Function with closure variables."""
        base_value = 10

        def inner_multiply(x):
            # Uses closure variables: multiplier, base_value
            return x * multiplier + base_value

        return inner_multiply(5)

    try:
        flattener = AdvancedFunctionFlattener(root_dir="/Users/jmanning/clustrix")
        result = flattener.flatten_with_dependencies(outer_with_closure)

        if result.get("success"):
            print(f"✅ Closure handling successful:")
            print(f"   Flattened code:")
            print("=" * 50)
            print(result["flattened_function"])
            print("=" * 50)

            return True
        else:
            print(f"❌ Closure handling failed: {result.get('error')}")
            return False

    except Exception as e:
        print(f"❌ Closure test crashed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_external_vs_local_detection():
    """Test detection of external vs local functions."""
    print("\n🧪 Testing external vs local function detection...")

    def test_function_with_imports():
        """Function that uses both external and potentially local functions."""
        import os
        import sys

        # External function calls
        current_dir = os.getcwd()
        python_version = sys.version

        # Simulated local function call (won't actually exist)
        # This tests the detection logic

        return {"dir": current_dir, "version": python_version}

    try:
        analyzer = FunctionDependencyAnalyzer(root_dir="/Users/jmanning/clustrix")

        # Test external function detection
        import os

        is_external = analyzer.is_external_function(os.getcwd)
        print(f"os.getcwd is external: {is_external}")

        # Test analysis of function with mixed dependencies
        dep_info = analyzer.analyze_function_dependencies(test_function_with_imports)

        print(f"✅ External/local detection results:")
        print(f"   Main function is local: {dep_info.main_function.is_local}")
        print(f"   External modules to import: {dep_info.modules_to_import}")
        print(f"   Local dependencies: {len(dep_info.dependencies)}")

        return True

    except Exception as e:
        print(f"❌ External/local detection failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🚀 Advanced Function Flattening Test Suite")
    print("=" * 60)

    tests = [
        test_dependency_analyzer,
        test_advanced_flattener,
        test_auto_flatten_with_nested,
        test_closure_variables,
        test_external_vs_local_detection,
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
    print("📊 Advanced Flattening Test Results:")
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {i+1}. {test.__name__}: {status}")

    passed = sum(results)
    total = len(results)
    print(f"\nOverall: {passed}/{total} tests passed")

    if passed < total:
        print("\n⚠️  Advanced function flattening needs more work!")
    else:
        print("\n🎉 All advanced flattening tests passed!")
