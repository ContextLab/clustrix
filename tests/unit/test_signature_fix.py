#!/usr/bin/env python3
"""
Test the signature fix for flattened functions.
"""


from clustrix.function_flattening import (
    analyze_function_complexity,
    auto_flatten_if_needed,
)
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)


def test_signature_preservation():
    """Test that flattened functions preserve original signatures."""
    print("🧪 Testing signature preservation...")

    def test_function_with_args(x, y, z=42):
        """Function with positional and keyword arguments."""

        def inner_add(a, b):
            return a + b

        result = inner_add(x, y)
        return result + z

    # Test complexity analysis
    complexity = analyze_function_complexity(test_function_with_args)
    print(f"Complexity: {complexity}")

    if complexity.get("nested_functions", 0) > 0:
        print("✅ Nested function detected")

        # Test flattening
        flattened_func, flattening_info = auto_flatten_if_needed(
            test_function_with_args
        )

        if flattening_info and flattening_info.get("success"):
            print("✅ Flattening successful")

            # Test that signatures match
            import inspect

            original_sig = inspect.signature(test_function_with_args)
            flattened_sig = inspect.signature(flattened_func)

            print(f"Original signature: {original_sig}")
            print(f"Flattened signature: {flattened_sig}")

            # Test execution with different argument patterns
            try:
                # Test with positional args
                original_result1 = test_function_with_args(1, 2)
                flattened_result1 = flattened_func(1, 2)
                print(
                    f"Positional args - Original: {original_result1}, Flattened: {flattened_result1}"
                )

                # Test with keyword args
                original_result2 = test_function_with_args(1, 2, z=100)
                flattened_result2 = flattened_func(1, 2, z=100)
                print(
                    f"Keyword args - Original: {original_result2}, Flattened: {flattened_result2}"
                )

                # Test with mixed args
                original_result3 = test_function_with_args(x=5, y=10)
                flattened_result3 = flattened_func(x=5, y=10)
                print(
                    f"Mixed args - Original: {original_result3}, Flattened: {flattened_result3}"
                )

                # Check if results match
                if (
                    original_result1 == flattened_result1
                    and original_result2 == flattened_result2
                    and original_result3 == flattened_result3
                ):
                    print("✅ All signature tests passed!")
                    return True
                else:
                    print("❌ Results don't match")
                    return False

            except Exception as e:
                print(f"❌ Execution failed: {e}")
                import traceback

                traceback.print_exc()
                return False
        else:
            print(f"❌ Flattening failed: {flattening_info}")
            return False
    else:
        print("❌ Nested function not detected")
        return False


if __name__ == "__main__":
    print("🚀 Testing Function Signature Preservation")
    print("=" * 50)

    success = test_signature_preservation()

    if success:
        print("\n🎉 Signature preservation test passed!")
    else:
        print("\n❌ Signature preservation test failed!")
