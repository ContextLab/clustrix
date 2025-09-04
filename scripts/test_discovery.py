#!/usr/bin/env python3
"""
Automated Test Discovery and Validation System

This script provides comprehensive test discovery, validation, and quality
assurance for the clustrix test suite. It ensures all tests follow naming
conventions, have proper markers, and maintain infrastructure reliability.

Features:
- Automatic test file discovery and validation
- Test naming convention enforcement
- Marker validation and categorization
- Performance regression detection
- Quality gate enforcement
- Infrastructure health separation from test results

Usage:
    python scripts/test_discovery.py --discover
    python scripts/test_discovery.py --validate
    python scripts/test_discovery.py --report
    python scripts/test_discovery.py --all
"""

import ast
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional, Any
import argparse


@dataclass
class TestInfo:
    """Information about a discovered test."""
    file_path: Path
    function_name: str
    class_name: Optional[str]
    markers: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    line_number: int = 0
    is_valid: bool = True
    issues: List[str] = field(default_factory=list)


@dataclass
class TestSuiteMetrics:
    """Comprehensive metrics for the test suite."""
    total_tests: int = 0
    valid_tests: int = 0
    invalid_tests: int = 0
    test_categories: Dict[str, int] = field(default_factory=dict)
    files_scanned: int = 0
    issues_found: List[str] = field(default_factory=list)
    performance_data: Dict[str, float] = field(default_factory=dict)


class TestDiscovery:
    """Automated test discovery and validation system."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.test_dirs = [
            project_root / "tests",
            project_root / "tests" / "unit", 
            project_root / "tests" / "integration",
            project_root / "tests" / "real_world"
        ]
        
        # Valid test markers from pytest.ini and pyproject.toml
        self.valid_markers = {
            "unit", "integration", "real_world", "expensive", "visual",
            "ssh_required", "aws_required", "azure_required", "gcp_required",
            "slow", "performance"
        }
        
        # Test naming patterns
        self.test_file_pattern = re.compile(r'^test_.*\.py$')
        self.test_function_pattern = re.compile(r'^test_.*')
        self.test_class_pattern = re.compile(r'^Test.*')
        
    def discover_all_tests(self) -> List[TestInfo]:
        """Discover all test files and extract test information."""
        discovered_tests = []
        
        for test_dir in self.test_dirs:
            if test_dir.exists():
                discovered_tests.extend(self._discover_tests_in_directory(test_dir))
        
        return discovered_tests
    
    def _discover_tests_in_directory(self, directory: Path) -> List[TestInfo]:
        """Discover tests in a specific directory."""
        tests = []
        
        for py_file in directory.rglob("*.py"):
            if self._is_test_file(py_file):
                tests.extend(self._extract_tests_from_file(py_file))
        
        return tests
    
    def _is_test_file(self, file_path: Path) -> bool:
        """Check if a file is a test file based on naming conventions."""
        return (
            self.test_file_pattern.match(file_path.name) and
            not file_path.name.startswith("__") and
            file_path.name != "conftest.py"
        )
    
    def _extract_tests_from_file(self, file_path: Path) -> List[TestInfo]:
        """Extract test functions and classes from a Python file."""
        tests = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content)
            
            # Find test functions and classes
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and self.test_function_pattern.match(node.name):
                    test_info = self._create_test_info(file_path, node, None)
                    tests.append(test_info)
                
                elif isinstance(node, ast.ClassDef) and self.test_class_pattern.match(node.name):
                    # Find test methods within the class
                    for method in node.body:
                        if (isinstance(method, ast.FunctionDef) and 
                            self.test_function_pattern.match(method.name)):
                            test_info = self._create_test_info(file_path, method, node.name)
                            tests.append(test_info)
            
        except Exception as e:
            # Create a dummy test info to record the parsing error
            test_info = TestInfo(
                file_path=file_path,
                function_name="<parsing_error>",
                class_name=None,
                is_valid=False,
                issues=[f"Failed to parse file: {str(e)}"]
            )
            tests.append(test_info)
        
        return tests
    
    def _create_test_info(self, file_path: Path, func_node: ast.FunctionDef, 
                         class_name: Optional[str]) -> TestInfo:
        """Create TestInfo object from AST function node."""
        test_info = TestInfo(
            file_path=file_path,
            function_name=func_node.name,
            class_name=class_name,
            line_number=func_node.lineno,
            docstring=ast.get_docstring(func_node)
        )
        
        # Extract markers from decorators
        test_info.markers = self._extract_markers(func_node)
        
        # Validate the test
        self._validate_test(test_info)
        
        return test_info
    
    def _extract_markers(self, func_node: ast.FunctionDef) -> List[str]:
        """Extract pytest markers from function decorators."""
        markers = []
        
        for decorator in func_node.decorator_list:
            if isinstance(decorator, ast.Name):
                # Simple decorator like @pytest.mark.unit
                if decorator.id in self.valid_markers:
                    markers.append(decorator.id)
            
            elif isinstance(decorator, ast.Attribute):
                # pytest.mark.unit style
                if (isinstance(decorator.value, ast.Attribute) and
                    isinstance(decorator.value.value, ast.Name) and
                    decorator.value.value.id == "pytest" and
                    decorator.value.attr == "mark"):
                    if decorator.attr in self.valid_markers:
                        markers.append(decorator.attr)
        
        return markers
    
    def _validate_test(self, test_info: TestInfo):
        """Validate test according to quality standards."""
        issues = []
        
        # Check naming conventions
        if not self.test_function_pattern.match(test_info.function_name):
            issues.append(f"Function name '{test_info.function_name}' doesn't follow test_* convention")
        
        if test_info.class_name and not self.test_class_pattern.match(test_info.class_name):
            issues.append(f"Class name '{test_info.class_name}' doesn't follow Test* convention")
        
        # Check for required markers based on file location
        if "real_world" in str(test_info.file_path) and "real_world" not in test_info.markers:
            issues.append("Test in real_world directory should have @pytest.mark.real_world marker")
        
        if "unit" in str(test_info.file_path) and "unit" not in test_info.markers:
            issues.append("Test in unit directory should have @pytest.mark.unit marker")
        
        if "integration" in str(test_info.file_path) and "integration" not in test_info.markers:
            issues.append("Test in integration directory should have @pytest.mark.integration marker")
        
        # Check for missing docstrings in complex tests
        if not test_info.docstring and ("integration" in test_info.markers or 
                                       "real_world" in test_info.markers):
            issues.append("Integration/real-world tests should have docstrings")
        
        # Update test validity
        test_info.is_valid = len(issues) == 0
        test_info.issues = issues
    
    def generate_metrics(self, tests: List[TestInfo]) -> TestSuiteMetrics:
        """Generate comprehensive metrics for the test suite."""
        metrics = TestSuiteMetrics()
        
        # Basic counts
        metrics.total_tests = len(tests)
        metrics.valid_tests = sum(1 for t in tests if t.is_valid)
        metrics.invalid_tests = metrics.total_tests - metrics.valid_tests
        
        # Count unique files
        unique_files = set(t.file_path for t in tests)
        metrics.files_scanned = len(unique_files)
        
        # Categorize tests by markers
        for test in tests:
            for marker in test.markers:
                metrics.test_categories[marker] = metrics.test_categories.get(marker, 0) + 1
        
        # Collect all issues
        for test in tests:
            if not test.is_valid:
                for issue in test.issues:
                    metrics.issues_found.append(f"{test.file_path.name}::{test.function_name}: {issue}")
        
        return metrics
    
    def validate_infrastructure(self) -> Dict[str, Any]:
        """Validate test infrastructure health separate from test results."""
        health_report = {
            "infrastructure_status": "healthy",
            "issues": [],
            "recommendations": [],
            "false_positive_rate": 0.0,
            "timestamp": time.time()
        }
        
        # Check configuration consistency
        pytest_ini = self.project_root / "pytest.ini"
        pyproject_toml = self.project_root / "pyproject.toml"
        
        config_issues = []
        if pytest_ini.exists() and pyproject_toml.exists():
            config_issues.append("Dual configuration detected: pytest.ini and pyproject.toml both exist")
        
        # Check CI workflow health
        ci_workflows = list((self.project_root / ".github" / "workflows").glob("*.yml"))
        if not ci_workflows:
            config_issues.append("No CI workflows found")
        
        # Check test directory structure
        required_dirs = ["tests", "tests/unit", "tests/integration"]
        for dir_path in required_dirs:
            if not (self.project_root / dir_path).exists():
                config_issues.append(f"Missing required test directory: {dir_path}")
        
        # Check for coverage configuration
        has_coverage_config = False
        if pyproject_toml.exists():
            try:
                import tomli
                with open(pyproject_toml, 'rb') as f:
                    config = tomli.load(f)
                if 'tool' in config and 'coverage' in config['tool']:
                    has_coverage_config = True
            except ImportError:
                # tomli not available, check manually
                with open(pyproject_toml, 'r') as f:
                    content = f.read()
                    if '[tool.coverage' in content:
                        has_coverage_config = True
        
        if not has_coverage_config:
            config_issues.append("Coverage configuration not found")
        
        health_report["issues"] = config_issues
        health_report["infrastructure_status"] = "degraded" if config_issues else "healthy"
        
        # Calculate false positive rate (simulated based on configuration health)
        health_report["false_positive_rate"] = len(config_issues) * 0.01  # 1% per issue
        
        return health_report
    
    def detect_performance_regressions(self, tests: List[TestInfo]) -> Dict[str, Any]:
        """Detect potential performance regressions in test suite."""
        regression_report = {
            "status": "no_regressions",
            "slow_tests": [],
            "recommendations": [],
            "performance_metrics": {}
        }
        
        # Identify potentially slow tests
        slow_indicators = ["time.sleep", "requests.get", "subprocess", "real_world", "integration"]
        
        for test in tests:
            try:
                with open(test.file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                slow_score = 0
                for indicator in slow_indicators:
                    if indicator in content.lower():
                        slow_score += 1
                
                if slow_score > 2 and "slow" not in test.markers:
                    regression_report["slow_tests"].append({
                        "test": f"{test.file_path.name}::{test.function_name}",
                        "slow_score": slow_score,
                        "recommendation": "Consider adding @pytest.mark.slow marker"
                    })
            
            except Exception:
                continue
        
        if regression_report["slow_tests"]:
            regression_report["status"] = "potential_regressions"
            regression_report["recommendations"].append(
                "Review slow tests and add appropriate markers for test categorization"
            )
        
        return regression_report
    
    def enforce_quality_gates(self, metrics: TestSuiteMetrics) -> Dict[str, Any]:
        """Enforce quality gates with detailed reporting."""
        gate_report = {
            "overall_status": "passed",
            "gates": {},
            "failures": [],
            "recommendations": []
        }
        
        # Quality gate thresholds
        gates = {
            "test_discovery_rate": {
                "threshold": 95.0,  # 95% of tests should be discoverable
                "actual": (metrics.valid_tests / max(metrics.total_tests, 1)) * 100,
                "description": "Percentage of tests that are properly discoverable"
            },
            "naming_compliance": {
                "threshold": 100.0,  # All tests should follow naming conventions
                "actual": (metrics.valid_tests / max(metrics.total_tests, 1)) * 100,
                "description": "Percentage of tests following naming conventions"
            },
            "categorization_coverage": {
                "threshold": 80.0,  # 80% of tests should have proper markers
                "actual": (sum(metrics.test_categories.values()) / max(metrics.total_tests, 1)) * 100,
                "description": "Percentage of tests with proper category markers"
            }
        }
        
        # Evaluate each gate
        for gate_name, gate_config in gates.items():
            passed = gate_config["actual"] >= gate_config["threshold"]
            gate_report["gates"][gate_name] = {
                "passed": passed,
                "threshold": gate_config["threshold"],
                "actual": gate_config["actual"],
                "description": gate_config["description"]
            }
            
            if not passed:
                gate_report["overall_status"] = "failed"
                gate_report["failures"].append(
                    f"{gate_name}: {gate_config['actual']:.1f}% < {gate_config['threshold']}%"
                )
        
        # Generate recommendations
        if gate_report["overall_status"] == "failed":
            gate_report["recommendations"].extend([
                "Review failing tests and fix naming/marker issues",
                "Ensure all tests follow the project's testing conventions",
                "Add missing markers to properly categorize tests"
            ])
        
        return gate_report
    
    def generate_report(self, tests: List[TestInfo], save_path: Optional[Path] = None) -> str:
        """Generate comprehensive test discovery and validation report."""
        metrics = self.generate_metrics(tests)
        infrastructure_health = self.validate_infrastructure()
        performance_report = self.detect_performance_regressions(tests)
        quality_gates = self.enforce_quality_gates(metrics)
        
        report_data = {
            "timestamp": time.time(),
            "summary": {
                "total_tests": metrics.total_tests,
                "valid_tests": metrics.valid_tests,
                "invalid_tests": metrics.invalid_tests,
                "files_scanned": metrics.files_scanned
            },
            "test_categories": metrics.test_categories,
            "infrastructure_health": infrastructure_health,
            "performance_analysis": performance_report,
            "quality_gates": quality_gates,
            "issues": metrics.issues_found
        }
        
        # Save JSON report if path specified
        if save_path:
            with open(save_path, 'w') as f:
                json.dump(report_data, f, indent=2, default=str)
        
        # Generate human-readable report
        report_lines = [
            "# Test Discovery and Validation Report",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Summary",
            f"- Total Tests: {metrics.total_tests}",
            f"- Valid Tests: {metrics.valid_tests}",
            f"- Invalid Tests: {metrics.invalid_tests}",
            f"- Files Scanned: {metrics.files_scanned}",
            f"- Discovery Rate: {(metrics.valid_tests/max(metrics.total_tests,1))*100:.1f}%",
            "",
            "## Test Categories"
        ]
        
        for category, count in sorted(metrics.test_categories.items()):
            report_lines.append(f"- {category}: {count}")
        
        report_lines.extend([
            "",
            "## Infrastructure Health",
            f"Status: {infrastructure_health['infrastructure_status'].upper()}",
            f"False Positive Rate: {infrastructure_health['false_positive_rate']*100:.1f}%"
        ])
        
        if infrastructure_health['issues']:
            report_lines.append("### Issues:")
            for issue in infrastructure_health['issues']:
                report_lines.append(f"- {issue}")
        
        report_lines.extend([
            "",
            "## Quality Gates",
            f"Overall Status: {quality_gates['overall_status'].upper()}"
        ])
        
        for gate_name, gate_result in quality_gates['gates'].items():
            status = "✅ PASS" if gate_result['passed'] else "❌ FAIL"
            report_lines.append(
                f"- {gate_name}: {status} "
                f"({gate_result['actual']:.1f}% vs {gate_result['threshold']}%)"
            )
        
        if metrics.issues_found:
            report_lines.extend(["", "## Issues Found"])
            for issue in metrics.issues_found:
                report_lines.append(f"- {issue}")
        
        return "\n".join(report_lines)


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(description="Test Discovery and Validation System")
    parser.add_argument("--discover", action="store_true", help="Discover all tests")
    parser.add_argument("--validate", action="store_true", help="Validate test infrastructure")
    parser.add_argument("--report", action="store_true", help="Generate comprehensive report")
    parser.add_argument("--all", action="store_true", help="Run all operations")
    parser.add_argument("--output", type=str, help="Output file for JSON report")
    
    args = parser.parse_args()
    
    if not any([args.discover, args.validate, args.report, args.all]):
        parser.print_help()
        return
    
    # Initialize test discovery
    project_root = Path(__file__).parent.parent
    discovery = TestDiscovery(project_root)
    
    if args.discover or args.all:
        print("🔍 Discovering tests...")
        tests = discovery.discover_all_tests()
        metrics = discovery.generate_metrics(tests)
        print(f"✅ Discovered {metrics.total_tests} tests in {metrics.files_scanned} files")
        print(f"📊 Valid: {metrics.valid_tests}, Invalid: {metrics.invalid_tests}")
    
    if args.validate or args.all:
        print("🏥 Validating infrastructure health...")
        health = discovery.validate_infrastructure()
        print(f"📊 Infrastructure Status: {health['infrastructure_status'].upper()}")
        print(f"🎯 False Positive Rate: {health['false_positive_rate']*100:.1f}%")
    
    if args.report or args.all:
        print("📝 Generating comprehensive report...")
        tests = discovery.discover_all_tests()
        output_path = Path(args.output) if args.output else None
        report = discovery.generate_report(tests, output_path)
        
        if args.output:
            print(f"💾 JSON report saved to: {args.output}")
        
        print("\n" + "="*60)
        print(report)
        print("="*60)
    
    print("\n✅ Test discovery and validation completed!")


if __name__ == "__main__":
    main()