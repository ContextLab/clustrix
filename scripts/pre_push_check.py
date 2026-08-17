#!/usr/bin/env python3
"""
Quick pre-push verification script.
Run this before pushing to ensure GitHub Actions won't fail.
"""

import subprocess
import sys
from pathlib import Path


def run_command(cmd, description):
    """Run a command and return success status."""
    print(f"Running {description}...")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        if result.returncode != 0:
            print(f"❌ {description} failed:")
            print(result.stdout)
            print(result.stderr)
            return False
        else:
            print(f"✅ {description} passed")
            return True
    except Exception as e:
        print(f"❌ {description} error: {e}")
        return False


def main():
    """Run all pre-push checks repeatedly until they all pass."""
    max_attempts = 5
    attempt = 1

    while attempt <= max_attempts:
        print(f"🔍 Pre-push quality checks (attempt {attempt}/{max_attempts})...")

        checks = [
            ("black clustrix/ tests/", "Black formatting"),  # Format, don't just check
            ("flake8 clustrix/ tests/ --max-line-length=88 --extend-ignore=E203,W503,F401,E722,F541,F841,F811,E731,E501,W291,W293,F824", "Flake8 linting"),
            ("mypy clustrix/", "MyPy type checking"),
            # Must mirror what GitHub Actions actually runs (see
            # .github/workflows/tests.yml), or this script cannot deliver on
            # the promise in its own docstring.
            #
            # This was a bare `pytest`, which was harmless only for as long as
            # the project had no effective pytest config: pytest.ini shadowed
            # pyproject.toml and neither applied, so `testpaths` did nothing
            # (see #130). With the config live, a bare `pytest` resolves to
            # testpaths and pulls in all 1670 tests -- including the 224 in
            # tests/real_world/, which make live network and cloud calls and do
            # not terminate in a reasonable time. Real-world tests are run
            # deliberately via scripts/run_real_world_tests.py, not here.
            ('pytest tests/unit/ -m "not real_world"', "Tests"),
        ]

        all_passed = True
        for cmd, desc in checks:
            if not run_command(cmd, desc):
                all_passed = False

        if all_passed:
            print(f"\n🎉 All checks passed on attempt {attempt}! Safe to push.")
            return 0
        else:
            print(f"\n⚠️  Some checks failed on attempt {attempt}.")
            if attempt < max_attempts:
                print("Fixes may have been auto-applied. Retrying...\n")
                attempt += 1
            else:
                print("💥 Maximum attempts reached. Manual fixes required.")
                return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
