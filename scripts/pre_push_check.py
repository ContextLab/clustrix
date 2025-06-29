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
            ("black clustrix/", "Black formatting"),  # Format, don't just check
            ("flake8 clustrix/", "Flake8 linting"),
            ("mypy clustrix/", "MyPy type checking"),
            ("pytest", "Tests"),
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
