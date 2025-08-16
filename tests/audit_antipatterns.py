#!/usr/bin/env python3
"""
Audit script to identify anti-patterns in existing tests.
This will help us understand the scope of refactoring needed.
"""

import os
import ast
import json
from pathlib import Path
from typing import Dict, List, Set


class TestAuditAnalyzer(ast.NodeVisitor):
    """Analyze test files for anti-patterns."""

    def __init__(self):
        self.anti_patterns = {
            "mock_usage": [],
            "patch_decorators": [],
            "exec_usage": [],
            "string_functions": [],
            "magic_mock": [],
            "trivial_computations": [],
            "missing_cluster_decorator": [],
        }
        self.good_patterns = {
            "real_cluster_decorator": [],
            "real_config": [],
            "meaningful_computation": [],
            "real_world_marked": [],
        }

    def visit_ImportFrom(self, node):
        """Check for mock imports."""
        if node.module and "mock" in node.module.lower():
            for name in node.names:
                self.anti_patterns["mock_usage"].append(
                    {
                        "line": node.lineno,
                        "import": f"from {node.module} import {name.name}",
                    }
                )
        self.generic_visit(node)

    def visit_Name(self, node):
        """Check for Mock and MagicMock usage."""
        if node.id in ["Mock", "MagicMock", "patch"]:
            self.anti_patterns["magic_mock"].append(
                {"line": node.lineno, "usage": node.id}
            )
        self.generic_visit(node)

    def visit_Call(self, node):
        """Check for exec() calls and patch decorators."""
        if isinstance(node.func, ast.Name):
            if node.func.id == "exec":
                self.anti_patterns["exec_usage"].append(
                    {"line": node.lineno, "call": "exec()"}
                )
            elif node.func.id == "patch":
                self.anti_patterns["patch_decorators"].append(
                    {"line": node.lineno, "call": "patch()"}
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        """Check for test patterns."""
        if node.name.startswith("test_"):
            # Check for @cluster decorator
            has_cluster = False
            has_real_world = False
            has_patch = False

            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name):
                    if decorator.id == "cluster":
                        has_cluster = True
                        self.good_patterns["real_cluster_decorator"].append(
                            {"line": node.lineno, "function": node.name}
                        )
                elif isinstance(decorator, ast.Attribute):
                    if decorator.attr == "real_world":
                        has_real_world = True
                        self.good_patterns["real_world_marked"].append(
                            {"line": node.lineno, "function": node.name}
                        )
                    elif decorator.attr == "patch":
                        has_patch = True
                        self.anti_patterns["patch_decorators"].append(
                            {"line": node.lineno, "function": node.name}
                        )

            # Check for trivial computations
            if self._is_trivial_computation(node):
                self.anti_patterns["trivial_computations"].append(
                    {"line": node.lineno, "function": node.name}
                )

        self.generic_visit(node)

    def _is_trivial_computation(self, func_node):
        """Check if function has trivial computation like x + y."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Return):
                # Check for simple arithmetic
                if isinstance(node.value, ast.BinOp):
                    if isinstance(node.value.op, (ast.Add, ast.Mult, ast.Sub)):
                        left = node.value.left
                        right = node.value.right
                        if isinstance(left, ast.Name) and isinstance(right, ast.Name):
                            return True
        return False


def audit_test_file(filepath: Path) -> Dict:
    """Audit a single test file for anti-patterns."""
    with open(filepath, "r") as f:
        content = f.read()

    try:
        tree = ast.parse(content)
        analyzer = TestAuditAnalyzer()
        analyzer.visit(tree)

        # Count anti-patterns
        anti_pattern_count = sum(len(v) for v in analyzer.anti_patterns.values())
        good_pattern_count = sum(len(v) for v in analyzer.good_patterns.values())

        return {
            "file": str(filepath.relative_to(Path("/Users/jmanning/clustrix"))),
            "anti_patterns": analyzer.anti_patterns,
            "good_patterns": analyzer.good_patterns,
            "anti_pattern_count": anti_pattern_count,
            "good_pattern_count": good_pattern_count,
            "needs_refactoring": anti_pattern_count > 0,
            "priority": (
                "high"
                if anti_pattern_count > 5
                else "medium" if anti_pattern_count > 0 else "low"
            ),
        }
    except Exception as e:
        return {
            "file": str(filepath.relative_to(Path("/Users/jmanning/clustrix"))),
            "error": str(e),
            "needs_refactoring": True,
            "priority": "medium",
        }


def audit_all_tests():
    """Audit all test files in the tests directory."""
    test_dir = Path("/Users/jmanning/clustrix/tests")
    results = []

    # Find all Python test files
    test_files = list(test_dir.glob("**/*.py"))
    test_files = [
        f for f in test_files if f.name.startswith("test_") or "test" in f.name
    ]

    print(f"Auditing {len(test_files)} test files...")

    for filepath in test_files:
        print(f"  Analyzing {filepath.name}...")
        result = audit_test_file(filepath)
        results.append(result)

    # Generate summary
    total_files = len(results)
    files_with_antipatterns = sum(1 for r in results if r.get("needs_refactoring"))
    high_priority = sum(1 for r in results if r.get("priority") == "high")
    medium_priority = sum(1 for r in results if r.get("priority") == "medium")

    # Count specific anti-patterns
    total_mocks = sum(
        len(r.get("anti_patterns", {}).get("mock_usage", [])) for r in results
    )
    total_patches = sum(
        len(r.get("anti_patterns", {}).get("patch_decorators", [])) for r in results
    )
    total_execs = sum(
        len(r.get("anti_patterns", {}).get("exec_usage", [])) for r in results
    )

    summary = {
        "total_files": total_files,
        "files_needing_refactoring": files_with_antipatterns,
        "refactoring_percentage": (
            (files_with_antipatterns / total_files * 100) if total_files > 0 else 0
        ),
        "priority_breakdown": {
            "high": high_priority,
            "medium": medium_priority,
            "low": total_files - high_priority - medium_priority,
        },
        "anti_pattern_totals": {
            "mock_usage": total_mocks,
            "patch_decorators": total_patches,
            "exec_usage": total_execs,
        },
        "files": results,
    }

    return summary


if __name__ == "__main__":
    print("=" * 70)
    print("CLUSTRIX TEST AUDIT - ANTI-PATTERN ANALYSIS")
    print("=" * 70)

    audit_results = audit_all_tests()

    # Save detailed results
    output_file = Path("/Users/jmanning/clustrix/tests/audit_results.json")
    with open(output_file, "w") as f:
        json.dump(audit_results, f, indent=2)

    # Print summary
    print(f"\n📊 AUDIT SUMMARY")
    print(f"  Total test files: {audit_results['total_files']}")
    print(
        f"  Files needing refactoring: {audit_results['files_needing_refactoring']} ({audit_results['refactoring_percentage']:.1f}%)"
    )
    print(f"\n📈 PRIORITY BREAKDOWN")
    print(f"  High priority: {audit_results['priority_breakdown']['high']}")
    print(f"  Medium priority: {audit_results['priority_breakdown']['medium']}")
    print(f"  Low priority: {audit_results['priority_breakdown']['low']}")
    print(f"\n⚠️  ANTI-PATTERN TOTALS")
    for pattern, count in audit_results["anti_pattern_totals"].items():
        print(f"  {pattern}: {count}")

    print(f"\n✅ Detailed results saved to: {output_file}")

    # List high-priority files
    print(f"\n🚨 HIGH PRIORITY FILES FOR REFACTORING:")
    for result in audit_results["files"]:
        if result.get("priority") == "high":
            print(
                f"  - {result['file']} ({result.get('anti_pattern_count', 0)} anti-patterns)"
            )
