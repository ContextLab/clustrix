#!/usr/bin/env python3
"""
Automated formatting and quality check script with auto-correction.

This script:
1. Runs black to check for formatting issues
2. If formatting issues found, automatically fixes them and commits
3. Runs all other quality checks (flake8, mypy, pytest)
4. Provides clear feedback on what was done

Usage:
    python scripts/auto_format_and_check.py
"""

import subprocess
import sys
import os
from pathlib import Path


def run_command(cmd, description="", check=True, capture_output=True):
    """Run a command and return result."""
    print(f"🔍 {description}")
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            check=check, 
            capture_output=capture_output,
            text=True
        )
        return result
    except subprocess.CalledProcessError as e:
        if not check:
            return e
        print(f"❌ {description} failed:")
        print(f"   Command: {cmd}")
        print(f"   Exit code: {e.returncode}")
        if e.stdout:
            print(f"   Stdout: {e.stdout}")
        if e.stderr:
            print(f"   Stderr: {e.stderr}")
        return e


def check_git_status():
    """Check if there are uncommitted changes."""
    result = run_command("git status --porcelain", "Checking git status")
    return len(result.stdout.strip()) > 0


def auto_format_and_commit():
    """Auto-format code and commit if changes were made."""
    print("\n🎨 STEP 1: Checking and fixing code formatting")
    print("=" * 50)
    
    # First, check if black would make changes
    black_check = run_command(
        "black --check clustrix/ tests/", 
        "Checking black formatting",
        check=False
    )
    
    if black_check.returncode == 0:
        print("✅ Code is already properly formatted")
        return True
    
    print("⚠️  Formatting issues detected. Auto-correcting...")
    
    # Apply black formatting
    format_result = run_command(
        "black clustrix/ tests/", 
        "Applying black formatting",
        capture_output=False
    )
    
    if format_result.returncode != 0:
        print("❌ Failed to apply black formatting")
        return False
    
    # Check if files were actually changed
    if not check_git_status():
        print("ℹ️  No files were changed by black")
        return True
    
    # Stage and commit the formatting changes
    print("📝 Committing formatting changes...")
    
    run_command("git add -A", "Staging formatted files")
    
    commit_msg = """Auto-fix: Apply black formatting

Automatically applied black formatting to resolve CI linting issues.
This ensures consistent code formatting across all environments.

🤖 Generated with [Claude Code](https://claude.ai/code)

Co-Authored-By: Claude <noreply@anthropic.com>"""
    
    commit_result = run_command(
        f'git commit -m "{commit_msg}"',
        "Committing formatting changes"
    )
    
    if commit_result.returncode == 0:
        print("✅ Formatting changes committed successfully")
        
        # Push the changes
        push_result = run_command("git push", "Pushing formatting changes")
        if push_result.returncode == 0:
            print("✅ Formatting changes pushed to remote")
        else:
            print("⚠️  Failed to push formatting changes (will need manual push)")
            
        return True
    else:
        print("❌ Failed to commit formatting changes")
        return False


def run_quality_checks():
    """Run all quality checks after formatting is resolved."""
    print("\n🔍 STEP 2: Running quality checks")
    print("=" * 50)
    
    checks = [
        ("black --check clustrix/ tests/", "Black formatting check"),
        ("flake8 clustrix/ tests/", "Flake8 linting"),
        ("mypy clustrix/", "MyPy type checking"),
        ("python -m pytest --tb=short", "Running tests"),
    ]
    
    all_passed = True
    results = {}
    
    for cmd, description in checks:
        result = run_command(cmd, description, check=False)
        results[description] = result.returncode == 0
        
        if result.returncode == 0:
            print(f"✅ {description} passed")
        else:
            print(f"❌ {description} failed")
            all_passed = False
    
    return all_passed, results


def main():
    """Main execution function."""
    print("🚀 Automated Code Quality Check with Auto-Formatting")
    print("=" * 60)
    
    # Ensure we're in the right directory
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent
    os.chdir(project_dir)
    
    print(f"📁 Working directory: {project_dir}")
    
    # Step 1: Auto-format and commit if needed
    format_success = auto_format_and_commit()
    if not format_success:
        print("\n❌ FAILED: Could not resolve formatting issues")
        sys.exit(1)
    
    # Step 2: Run all quality checks
    checks_passed, results = run_quality_checks()
    
    # Summary
    print("\n📊 SUMMARY")
    print("=" * 30)
    
    if checks_passed:
        print("🎉 ALL CHECKS PASSED!")
        print("\n✅ Code is ready for CI/CD pipeline")
        sys.exit(0)
    else:
        print("❌ SOME CHECKS FAILED")
        print("\nFailed checks:")
        for check, passed in results.items():
            if not passed:
                print(f"   • {check}")
        
        print("\n💡 Please fix the remaining issues and run again")
        sys.exit(1)


if __name__ == "__main__":
    main()