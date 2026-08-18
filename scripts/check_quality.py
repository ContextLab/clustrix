#!/usr/bin/env python3
"""
Check code quality metrics and display results.
"""

import subprocess
import json
from pathlib import Path


def check_tests():
    """Run tests and return pass/fail status."""
    print("🧪 Running tests...")
    result = subprocess.run(["pytest", "tests/", "-q"], capture_output=True, text=True)
    passed = result.returncode == 0
    if passed:
        # Extract test counts from output
        lines = result.stdout.strip().split("\n")
        for line in lines:
            if " passed" in line:
                print(f"✅ Tests: {line}")
                return True
    else:
        print("❌ Tests failed")
        return False


def check_coverage():
    """Check test coverage."""
    print("\n📊 Checking coverage...")
    coverage_file = Path("coverage.json")
    if coverage_file.exists():
        with open(coverage_file) as f:
            data = json.load(f)
            coverage = int(data["totals"]["percent_covered"])
            print(f"✅ Coverage: {coverage}%")
            return coverage
    else:
        print("❌ No coverage data found. Run: pytest --cov=clustrix --cov-report=json")
        return None


def check_black():
    """Check if code is formatted with black."""
    print("\n🎨 Checking code formatting (black)...")
    result = subprocess.run(
        ["black", "--check", "clustrix/"], capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✅ Code is properly formatted")
        return True
    else:
        print("❌ Code needs formatting. Run: black clustrix/")
        return False


def check_flake8():
    """Check if code passes flake8 linting."""
    print("\n🔍 Checking linting (flake8)...")
    result = subprocess.run(
        ["flake8", "clustrix/", "--count"], capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✅ No linting errors")
        return True
    else:
        error_count = result.stdout.strip()
        print(f"❌ Found {error_count} linting errors. Run: flake8 clustrix/")
        return False


def check_mypy():
    """Check if code passes mypy type checking."""
    print("\n🔤 Checking type annotations (mypy)...")
    result = subprocess.run(
        ["mypy", "clustrix/", "--ignore-missing-imports"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("✅ Type checking passed")
        return True
    else:
        print("❌ Type checking failed. Run: mypy clustrix/")
        return False


def main():
    """Run all checks and display summary."""
    print("🚀 Clustrix Code Quality Check\n" + "=" * 40)

    results = {
        "tests": check_tests(),
        "coverage": check_coverage(),
        "black": check_black(),
        "flake8": check_flake8(),
        "mypy": check_mypy(),
    }

    print("\n" + "=" * 40)
    print("📋 Summary:")

    all_passed = all(v for v in results.values() if v is not None)

    if all_passed:
        print("✅ All checks passed! 🎉")
    else:
        print("❌ Some checks failed. Please fix the issues above.")

    # Display badge URLs
    if results["coverage"] is not None:
        print("\n🏷️  Coverage badge URL:")
        color = (
            "brightgreen"
            if results["coverage"] >= 80
            else (
                "green"
                if results["coverage"] >= 60
                else (
                    "yellow"
                    if results["coverage"] >= 40
                    else "orange" if results["coverage"] >= 20 else "red"
                )
            )
        )
        print(
            f"   https://img.shields.io/badge/coverage-{results['coverage']}%25-{color}.svg"
        )


if __name__ == "__main__":
    main()
