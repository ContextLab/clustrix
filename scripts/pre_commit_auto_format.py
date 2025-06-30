#!/usr/bin/env python3
"""
Pre-commit hook script that automatically handles formatting issues.

This script is designed to be used as a pre-commit hook that:
1. Runs black to check for formatting issues
2. If issues found, automatically fixes them
3. Stages the fixed files for the current commit
4. Allows the commit to proceed with properly formatted code

This eliminates the manual cycle of "commit fails -> run black -> stage -> commit again"
"""

import subprocess
import sys
import os
from pathlib import Path


def run_command(cmd, description="", check=True, capture_output=True):
    """Run a command and return result."""
    try:
        result = subprocess.run(
            cmd, 
            shell=isinstance(cmd, str),
            check=check, 
            capture_output=capture_output,
            text=True,
            args=cmd if isinstance(cmd, list) else None
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


def get_staged_python_files():
    """Get list of staged Python files."""
    result = run_command(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        "Getting staged files"
    )
    
    if result.returncode != 0:
        return []
    
    files = []
    for line in result.stdout.strip().split('\n'):
        if line and (line.endswith('.py') and (line.startswith('clustrix/') or line.startswith('tests/'))):
            files.append(line)
    
    return files


def main():
    """Main pre-commit hook function."""
    # Get the files that are staged for commit
    staged_files = get_staged_python_files()
    
    if not staged_files:
        print("ℹ️  No Python files in clustrix/ or tests/ staged for commit")
        sys.exit(0)
    
    print(f"🔍 Checking formatting for {len(staged_files)} staged Python files...")
    
    # Check if black would make changes to staged files
    black_check = run_command(
        ["black", "--check"] + staged_files,
        "Checking black formatting on staged files",
        check=False
    )
    
    if black_check.returncode == 0:
        print("✅ All staged files are properly formatted")
        sys.exit(0)
    
    print("⚠️  Formatting issues detected in staged files. Auto-correcting...")
    
    # Apply black formatting to staged files
    format_result = run_command(
        ["black"] + staged_files,
        "Applying black formatting to staged files"
    )
    
    if format_result.returncode != 0:
        print("❌ Failed to apply black formatting")
        sys.exit(1)
    
    # Re-stage the formatted files
    print("📝 Re-staging formatted files...")
    add_result = run_command(
        ["git", "add"] + staged_files,
        "Re-staging formatted files"
    )
    
    if add_result.returncode != 0:
        print("❌ Failed to re-stage formatted files")
        sys.exit(1)
    
    print("✅ Files formatted and re-staged successfully")
    print("💡 Your commit will now proceed with properly formatted code")
    
    # Exit with success to allow the commit to proceed
    sys.exit(0)


if __name__ == "__main__":
    main()