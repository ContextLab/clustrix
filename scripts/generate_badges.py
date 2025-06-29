#!/usr/bin/env python3
"""
Generate badge URLs for README.md based on current project status.
This script is meant to be run locally or in CI without external services.
"""

import json
import subprocess
import sys
from pathlib import Path


def get_coverage_percentage():
    """Get coverage percentage from coverage.json if it exists."""
    coverage_file = Path("coverage.json")
    if coverage_file.exists():
        with open(coverage_file) as f:
            data = json.load(f)
            return int(data["totals"]["percent_covered"])
    return None


def get_coverage_color(percentage):
    """Determine badge color based on coverage percentage."""
    if percentage >= 80:
        return "brightgreen"
    elif percentage >= 60:
        return "green"
    elif percentage >= 40:
        return "yellow"
    elif percentage >= 20:
        return "orange"
    else:
        return "red"


def check_linting_status():
    """Check if code passes black and flake8."""
    black_result = subprocess.run(
        ["black", "--check", "clustrix/"], capture_output=True
    )
    flake8_result = subprocess.run(
        ["flake8", "clustrix/"], capture_output=True
    )
    return black_result.returncode == 0, flake8_result.returncode == 0


def check_mypy_status():
    """Check if code passes mypy type checking."""
    result = subprocess.run(
        ["mypy", "clustrix/", "--ignore-missing-imports"], capture_output=True
    )
    return result.returncode == 0


def generate_badge_urls():
    """Generate all badge URLs."""
    badges = []
    
    # Test status badge (from GitHub Actions)
    badges.append({
        "name": "Tests",
        "url": "https://github.com/ContextLab/clustrix/actions/workflows/tests.yml/badge.svg",
        "link": "https://github.com/ContextLab/clustrix/actions/workflows/tests.yml"
    })
    
    # Coverage badge
    coverage = get_coverage_percentage()
    if coverage is not None:
        color = get_coverage_color(coverage)
        badges.append({
            "name": "Coverage",
            "url": f"https://img.shields.io/badge/coverage-{coverage}%25-{color}.svg",
            "link": "https://github.com/ContextLab/clustrix/actions/workflows/tests.yml"
        })
    
    # Code style badges
    badges.append({
        "name": "Code style: black",
        "url": "https://img.shields.io/badge/code%20style-black-000000.svg",
        "link": "https://github.com/psf/black"
    })
    
    badges.append({
        "name": "Linting: flake8",
        "url": "https://img.shields.io/badge/linting-flake8-blue.svg",
        "link": "https://github.com/PyCQA/flake8"
    })
    
    # Type checking badge
    badges.append({
        "name": "Type Checking: mypy",
        "url": "https://img.shields.io/badge/mypy-checked-2a6db2.svg",
        "link": "https://mypy-lang.org/"
    })
    
    # PyPI badges
    badges.append({
        "name": "PyPI version",
        "url": "https://img.shields.io/pypi/v/clustrix.svg",
        "link": "https://pypi.org/project/clustrix/"
    })
    
    badges.append({
        "name": "Downloads",
        "url": "https://img.shields.io/pypi/dm/clustrix.svg",
        "link": "https://pypi.org/project/clustrix/"
    })
    
    # Documentation badge
    badges.append({
        "name": "Documentation",
        "url": "https://readthedocs.org/projects/clustrix/badge/?version=latest",
        "link": "https://clustrix.readthedocs.io/en/latest/?badge=latest"
    })
    
    # Python version badge
    badges.append({
        "name": "Python 3.8+",
        "url": "https://img.shields.io/badge/python-3.8+-blue.svg",
        "link": "https://www.python.org/downloads/"
    })
    
    # License badge
    badges.append({
        "name": "License: MIT",
        "url": "https://img.shields.io/badge/License-MIT-yellow.svg",
        "link": "https://opensource.org/licenses/MIT"
    })
    
    return badges


def generate_markdown(badges):
    """Generate markdown for badges."""
    lines = []
    for badge in badges:
        line = f"[![{badge['name']}]({badge['url']})]({badge['link']})"
        lines.append(line)
    return "\n".join(lines)


def update_readme_badges():
    """Update badges in README.md."""
    readme_path = Path("README.md")
    if not readme_path.exists():
        print("README.md not found")
        return False
    
    with open(readme_path, "r") as f:
        content = f.read()
    
    # Find the badge section (between # Clustrix and the first blank line after badges)
    lines = content.split("\n")
    start_idx = None
    end_idx = None
    
    for i, line in enumerate(lines):
        if line.strip() == "# Clustrix":
            start_idx = i + 1
        elif start_idx is not None and line.strip() == "" and i > start_idx + 1:
            end_idx = i
            break
    
    if start_idx is None or end_idx is None:
        print("Could not find badge section in README.md")
        return False
    
    # Generate new badges
    badges = generate_badge_urls()
    badge_markdown = generate_markdown(badges)
    
    # Replace the badge section
    new_lines = lines[:start_idx + 1] + badge_markdown.split("\n") + lines[end_idx:]
    
    with open(readme_path, "w") as f:
        f.write("\n".join(new_lines))
    
    print(f"Updated {len(badges)} badges in README.md")
    return True


if __name__ == "__main__":
    # Run coverage if requested
    if "--run-coverage" in sys.argv:
        print("Running tests with coverage...")
        subprocess.run([
            "pytest", "tests/", "--cov=clustrix", 
            "--cov-report=json", "--cov-report=term"
        ])
    
    # Check linting if requested
    if "--check-linting" in sys.argv:
        black_ok, flake8_ok = check_linting_status()
        mypy_ok = check_mypy_status()
        print(f"Black: {'✓' if black_ok else '✗'}")
        print(f"Flake8: {'✓' if flake8_ok else '✗'}")
        print(f"Mypy: {'✓' if mypy_ok else '✗'}")
    
    # Update README badges
    update_readme_badges()