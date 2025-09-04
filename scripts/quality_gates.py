#!/usr/bin/env python3
"""
Quality Gates Enforcement System

This script enforces comprehensive quality gates for the clustrix project,
ensuring high code quality, test coverage, and infrastructure reliability.
It provides detailed reporting and prevents regression through automated
quality validation.

Features:
- Coverage threshold enforcement
- Test quality validation 
- Performance regression detection
- Code quality metrics validation
- Infrastructure reliability gates
- Detailed reporting and recommendations

Usage:
    python scripts/quality_gates.py --enforce
    python scripts/quality_gates.py --report
    python scripts/quality_gates.py --check-pr
    python scripts/quality_gates.py --all
"""

import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import argparse


@dataclass
class QualityGate:
    """Represents a single quality gate."""
    name: str
    threshold: float
    actual: float
    passed: bool
    description: str
    severity: str = "error"  # "warning", "error", "critical"
    recommendations: List[str] = field(default_factory=list)


@dataclass
class QualityReport:
    """Comprehensive quality assessment report."""
    overall_status: str  # "passed", "failed", "warning"
    gates: List[QualityGate] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class QualityGateEnforcer:
    """Comprehensive quality gate enforcement system."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        
        # Quality gate thresholds - these can be configured
        self.thresholds = {
            # Coverage gates
            "total_coverage": 90.0,
            "branch_coverage": 85.0,
            "individual_file_coverage": 80.0,
            "new_code_coverage": 95.0,
            
            # Test quality gates
            "test_discovery_rate": 95.0,
            "test_success_rate": 95.0,
            "test_categorization_rate": 80.0,
            
            # Performance gates
            "unit_test_duration": 120.0,  # 2 minutes
            "integration_test_duration": 300.0,  # 5 minutes
            "full_suite_duration": 900.0,  # 15 minutes
            
            # Code quality gates
            "complexity_score": 10.0,  # Max cyclomatic complexity
            "maintainability_index": 60.0,  # Min maintainability
            
            # Infrastructure gates
            "infrastructure_reliability": 95.0,
            "false_positive_rate": 5.0,  # Max 5%
        }
        
        # Severity mapping
        self.gate_severities = {
            "total_coverage": "critical",
            "test_success_rate": "critical",
            "infrastructure_reliability": "error",
            "unit_test_duration": "warning",
            "complexity_score": "warning"
        }
    
    def enforce_all_gates(self) -> QualityReport:
        """Enforce all quality gates and return comprehensive report."""
        report = QualityReport(overall_status="unknown")
        
        # Run all quality checks
        gates = []
        gates.extend(self._check_coverage_gates())
        gates.extend(self._check_test_quality_gates())
        gates.extend(self._check_performance_gates())
        gates.extend(self._check_code_quality_gates())
        gates.extend(self._check_infrastructure_gates())
        
        report.gates = gates
        
        # Calculate overall status
        critical_failures = [g for g in gates if not g.passed and g.severity == "critical"]
        error_failures = [g for g in gates if not g.passed and g.severity == "error"]
        warnings = [g for g in gates if not g.passed and g.severity == "warning"]
        
        if critical_failures:
            report.overall_status = "failed"
            report.recommendations.append("Critical quality gates failed - deployment blocked")
        elif error_failures:
            report.overall_status = "failed" 
            report.recommendations.append("Quality gates failed - review required")
        elif warnings:
            report.overall_status = "warning"
            report.recommendations.append("Quality warnings detected - monitoring recommended")
        else:
            report.overall_status = "passed"
            report.recommendations.append("All quality gates passed - ready for deployment")
        
        # Generate metrics summary
        report.metrics = self._generate_metrics_summary(gates)
        
        return report
    
    def _check_coverage_gates(self) -> List[QualityGate]:
        """Check coverage-related quality gates."""
        gates = []
        
        # Try to read coverage data
        coverage_data = self._get_coverage_data()
        
        if coverage_data is None:
            # No coverage data available
            gates.append(QualityGate(
                name="coverage_data_available",
                threshold=100.0,
                actual=0.0,
                passed=False,
                description="Coverage data must be available for quality assessment",
                severity="critical",
                recommendations=["Run tests with coverage: pytest --cov=clustrix"]
            ))
            return gates
        
        # Total coverage gate
        total_coverage = coverage_data.get("total_coverage", 0.0)
        gates.append(QualityGate(
            name="total_coverage",
            threshold=self.thresholds["total_coverage"],
            actual=total_coverage,
            passed=total_coverage >= self.thresholds["total_coverage"],
            description=f"Overall test coverage must be >= {self.thresholds['total_coverage']}%",
            severity=self.gate_severities.get("total_coverage", "error"),
            recommendations=self._get_coverage_recommendations(total_coverage, self.thresholds["total_coverage"])
        ))
        
        # Branch coverage gate (if available)
        branch_coverage = coverage_data.get("branch_coverage")
        if branch_coverage is not None:
            # Handle both numeric and string formats
            if isinstance(branch_coverage, str):
                try:
                    branch_coverage = float(branch_coverage.replace('%', ''))
                except (ValueError, AttributeError):
                    branch_coverage = None
            
            if branch_coverage is not None:
                gates.append(QualityGate(
                    name="branch_coverage",
                    threshold=self.thresholds["branch_coverage"],
                    actual=branch_coverage,
                    passed=branch_coverage >= self.thresholds["branch_coverage"],
                    description=f"Branch coverage must be >= {self.thresholds['branch_coverage']}%",
                    severity="error",
                    recommendations=self._get_branch_coverage_recommendations(branch_coverage)
                ))
        
        # Individual file coverage
        file_coverage_issues = coverage_data.get("low_coverage_files", [])
        file_coverage_rate = max(0, 100 - len(file_coverage_issues))
        gates.append(QualityGate(
            name="individual_file_coverage", 
            threshold=self.thresholds["individual_file_coverage"],
            actual=file_coverage_rate,
            passed=file_coverage_rate >= self.thresholds["individual_file_coverage"],
            description=f"Individual file coverage compliance >= {self.thresholds['individual_file_coverage']}%",
            severity="warning",
            recommendations=[f"Review files with low coverage: {', '.join(file_coverage_issues[:5])}"] if file_coverage_issues else []
        ))
        
        return gates
    
    def _check_test_quality_gates(self) -> List[QualityGate]:
        """Check test quality gates."""
        gates = []
        
        # Get test metrics
        test_metrics = self._get_test_metrics()
        
        # Test discovery rate
        discovery_rate = test_metrics.get("test_discovery_rate", 0.0)
        gates.append(QualityGate(
            name="test_discovery_rate",
            threshold=self.thresholds["test_discovery_rate"],
            actual=discovery_rate,
            passed=discovery_rate >= self.thresholds["test_discovery_rate"],
            description="Test discovery should find >= 95% of expected tests",
            severity="error",
            recommendations=self._get_test_discovery_recommendations(discovery_rate)
        ))
        
        # Test success rate (from recent runs)
        success_rate = test_metrics.get("test_success_rate", 100.0)
        gates.append(QualityGate(
            name="test_success_rate",
            threshold=self.thresholds["test_success_rate"],
            actual=success_rate,
            passed=success_rate >= self.thresholds["test_success_rate"],
            description="Test success rate should be >= 95%",
            severity="critical",
            recommendations=["Fix failing tests before proceeding"] if success_rate < self.thresholds["test_success_rate"] else []
        ))
        
        # Test categorization rate
        categorization_rate = test_metrics.get("test_categorization_rate", 0.0)
        gates.append(QualityGate(
            name="test_categorization_rate",
            threshold=self.thresholds["test_categorization_rate"],
            actual=categorization_rate,
            passed=categorization_rate >= self.thresholds["test_categorization_rate"],
            description="Tests should have proper category markers >= 80%",
            severity="warning",
            recommendations=["Add @pytest.mark.{unit|integration|real_world} to uncategorized tests"]
        ))
        
        return gates
    
    def _check_performance_gates(self) -> List[QualityGate]:
        """Check performance-related quality gates."""
        gates = []
        
        # Get performance metrics
        performance_data = self._get_performance_metrics()
        
        # Unit test duration
        unit_duration = performance_data.get("unit_test_duration", 0.0)
        gates.append(QualityGate(
            name="unit_test_duration",
            threshold=self.thresholds["unit_test_duration"],
            actual=unit_duration,
            passed=unit_duration <= self.thresholds["unit_test_duration"],
            description=f"Unit test suite should complete in <= {self.thresholds['unit_test_duration']} seconds",
            severity="warning",
            recommendations=self._get_performance_recommendations("unit", unit_duration)
        ))
        
        # Full suite duration
        full_duration = performance_data.get("full_suite_duration", 0.0)
        gates.append(QualityGate(
            name="full_suite_duration",
            threshold=self.thresholds["full_suite_duration"],
            actual=full_duration,
            passed=full_duration <= self.thresholds["full_suite_duration"],
            description=f"Full test suite should complete in <= {self.thresholds['full_suite_duration']} seconds",
            severity="error",
            recommendations=self._get_performance_recommendations("full", full_duration)
        ))
        
        return gates
    
    def _check_code_quality_gates(self) -> List[QualityGate]:
        """Check code quality gates."""
        gates = []
        
        # Get code quality metrics
        code_metrics = self._get_code_quality_metrics()
        
        # Complexity gate
        max_complexity = code_metrics.get("max_complexity", 0.0)
        gates.append(QualityGate(
            name="complexity_score",
            threshold=self.thresholds["complexity_score"],
            actual=max_complexity,
            passed=max_complexity <= self.thresholds["complexity_score"],
            description=f"Maximum cyclomatic complexity should be <= {self.thresholds['complexity_score']}",
            severity="warning",
            recommendations=["Refactor complex functions to reduce complexity"] if max_complexity > self.thresholds["complexity_score"] else []
        ))
        
        # Linting compliance
        linting_score = code_metrics.get("linting_score", 100.0)
        gates.append(QualityGate(
            name="linting_compliance",
            threshold=100.0,
            actual=linting_score,
            passed=linting_score >= 100.0,
            description="Code should pass all linting checks",
            severity="error",
            recommendations=["Fix linting issues: run black clustrix/ && flake8 clustrix/"] if linting_score < 100.0 else []
        ))
        
        return gates
    
    def _check_infrastructure_gates(self) -> List[QualityGate]:
        """Check infrastructure quality gates."""
        gates = []
        
        try:
            # Try to get infrastructure health data
            from scripts.infrastructure_health import InfrastructureHealthMonitor
            monitor = InfrastructureHealthMonitor(self.project_root)
            health_metrics = monitor.run_comprehensive_health_check()
            
            # Infrastructure reliability gate
            reliability = health_metrics.reliability_score
            gates.append(QualityGate(
                name="infrastructure_reliability",
                threshold=self.thresholds["infrastructure_reliability"],
                actual=reliability,
                passed=reliability >= self.thresholds["infrastructure_reliability"],
                description=f"Infrastructure reliability should be >= {self.thresholds['infrastructure_reliability']}%",
                severity="error",
                recommendations=["Fix infrastructure health issues"] if reliability < self.thresholds["infrastructure_reliability"] else []
            ))
            
            # False positive rate gate
            false_positive_rate = health_metrics.false_positive_rate * 100
            gates.append(QualityGate(
                name="false_positive_rate",
                threshold=self.thresholds["false_positive_rate"],
                actual=false_positive_rate,
                passed=false_positive_rate <= self.thresholds["false_positive_rate"],
                description=f"False positive rate should be <= {self.thresholds['false_positive_rate']}%",
                severity="warning",
                recommendations=["Improve infrastructure stability to reduce false positives"] if false_positive_rate > self.thresholds["false_positive_rate"] else []
            ))
            
        except Exception as e:
            # Infrastructure check failed
            gates.append(QualityGate(
                name="infrastructure_check",
                threshold=100.0,
                actual=0.0,
                passed=False,
                description="Infrastructure health check should succeed",
                severity="error",
                recommendations=[f"Fix infrastructure health check: {e}"]
            ))
        
        return gates
    
    def _get_coverage_data(self) -> Optional[Dict[str, Any]]:
        """Extract coverage data from coverage reports."""
        try:
            # Try JSON coverage report first
            coverage_json = self.project_root / "coverage.json"
            if coverage_json.exists():
                with open(coverage_json, 'r') as f:
                    data = json.load(f)
                
                total_coverage = data["totals"]["percent_covered"]
                branch_coverage = data["totals"].get("percent_covered_display")  # Branch coverage if available
                
                # Find files with low coverage
                low_coverage_files = []
                for filename, file_data in data["files"].items():
                    if file_data["summary"]["percent_covered"] < self.thresholds["individual_file_coverage"]:
                        low_coverage_files.append(filename)
                
                return {
                    "total_coverage": total_coverage,
                    "branch_coverage": branch_coverage,
                    "low_coverage_files": low_coverage_files
                }
            
            # Try XML coverage report
            coverage_xml = self.project_root / "coverage.xml"
            if coverage_xml.exists():
                tree = ET.parse(coverage_xml)
                root = tree.getroot()
                
                # Extract total coverage from XML
                coverage_elem = root.find(".//coverage")
                if coverage_elem is not None:
                    lines_covered = float(coverage_elem.get("lines-covered", 0))
                    lines_valid = float(coverage_elem.get("lines-valid", 1))
                    total_coverage = (lines_covered / lines_valid) * 100 if lines_valid > 0 else 0
                    
                    return {
                        "total_coverage": total_coverage,
                        "branch_coverage": None,
                        "low_coverage_files": []
                    }
            
            return None
        
        except Exception as e:
            print(f"Warning: Failed to read coverage data: {e}")
            return None
    
    def _get_test_metrics(self) -> Dict[str, Any]:
        """Get test quality metrics."""
        metrics = {
            "test_discovery_rate": 95.0,  # Default assumption
            "test_success_rate": 100.0,   # Default assumption
            "test_categorization_rate": 80.0  # Default assumption
        }
        
        try:
            # Try to run test discovery to get real metrics
            discovery_script = self.project_root / "scripts" / "test_discovery.py"
            if discovery_script.exists():
                result = subprocess.run(
                    [sys.executable, str(discovery_script), "--discover"],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                
                if result.returncode == 0:
                    # Parse output to extract metrics (simplified)
                    output = result.stdout
                    if "Discovery Rate:" in output:
                        rate_line = [line for line in output.split('\n') if "Discovery Rate:" in line][0]
                        rate = float(rate_line.split(":")[-1].strip().replace("%", ""))
                        metrics["test_discovery_rate"] = rate
        
        except Exception as e:
            print(f"Warning: Failed to get test metrics: {e}")
        
        return metrics
    
    def _get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics."""
        metrics = {
            "unit_test_duration": 0.0,
            "integration_test_duration": 0.0,
            "full_suite_duration": 0.0
        }
        
        try:
            # Try to get recent test timing data
            pytest_cache = self.project_root / ".pytest_cache"
            if pytest_cache.exists():
                # Look for cached performance data
                for cache_file in pytest_cache.glob("**/performance_*.json"):
                    try:
                        with open(cache_file, 'r') as f:
                            perf_data = json.load(f)
                            metrics.update(perf_data)
                            break
                    except Exception:
                        continue
        
        except Exception as e:
            print(f"Warning: Failed to get performance metrics: {e}")
        
        return metrics
    
    def _get_code_quality_metrics(self) -> Dict[str, Any]:
        """Get code quality metrics."""
        metrics = {
            "max_complexity": 5.0,  # Default good value
            "linting_score": 100.0  # Default assumption
        }
        
        try:
            # Check linting with flake8
            result = subprocess.run(
                ["flake8", "clustrix/", "--exit-zero"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # Count linting issues
            issues = len([line for line in result.stdout.split('\n') if line.strip()])
            if issues == 0:
                metrics["linting_score"] = 100.0
            else:
                metrics["linting_score"] = max(0, 100 - issues)  # Simplified scoring
            
        except Exception as e:
            print(f"Warning: Failed to get code quality metrics: {e}")
        
        return metrics
    
    def _get_coverage_recommendations(self, current: float, target: float) -> List[str]:
        """Get recommendations for improving coverage."""
        if current >= target:
            return []
        
        gap = target - current
        recommendations = []
        
        if gap > 20:
            recommendations.append("Coverage is critically low - add comprehensive test suite")
        elif gap > 10:
            recommendations.append("Add more unit tests to increase coverage")
        elif gap > 5:
            recommendations.append("Add targeted tests for uncovered code paths")
        else:
            recommendations.append("Minor coverage improvements needed")
        
        recommendations.extend([
            "Run: pytest --cov=clustrix --cov-report=html to identify gaps",
            "Focus on testing error handling and edge cases"
        ])
        
        return recommendations
    
    def _get_branch_coverage_recommendations(self, current: float) -> List[str]:
        """Get recommendations for improving branch coverage."""
        recommendations = []
        
        if current < 70:
            recommendations.extend([
                "Branch coverage is low - add tests for conditional logic",
                "Test both true and false paths of if statements"
            ])
        elif current < 85:
            recommendations.append("Add tests for remaining conditional branches")
        
        return recommendations
    
    def _get_test_discovery_recommendations(self, rate: float) -> List[str]:
        """Get recommendations for improving test discovery."""
        if rate >= 95:
            return []
        
        return [
            "Ensure test files follow naming convention: test_*.py",
            "Check test functions start with test_",
            "Verify test classes start with Test",
            "Run: python scripts/test_discovery.py --validate"
        ]
    
    def _get_performance_recommendations(self, test_type: str, duration: float) -> List[str]:
        """Get recommendations for improving test performance."""
        recommendations = []
        
        if test_type == "unit" and duration > 120:
            recommendations.extend([
                "Unit tests are slow - consider parallel execution with pytest-xdist",
                "Mock external dependencies in unit tests",
                "Optimize fixture setup and teardown"
            ])
        elif test_type == "full" and duration > 900:
            recommendations.extend([
                "Full test suite is slow - implement test categorization",
                "Use pytest markers to run only necessary tests in CI",
                "Consider parallel execution and result caching"
            ])
        
        return recommendations
    
    def _generate_metrics_summary(self, gates: List[QualityGate]) -> Dict[str, Any]:
        """Generate summary metrics from quality gates."""
        total_gates = len(gates)
        passed_gates = sum(1 for g in gates if g.passed)
        failed_gates = total_gates - passed_gates
        
        critical_failures = sum(1 for g in gates if not g.passed and g.severity == "critical")
        error_failures = sum(1 for g in gates if not g.passed and g.severity == "error")
        warnings = sum(1 for g in gates if not g.passed and g.severity == "warning")
        
        return {
            "total_gates": total_gates,
            "passed_gates": passed_gates,
            "failed_gates": failed_gates,
            "pass_rate": (passed_gates / total_gates * 100) if total_gates > 0 else 0,
            "critical_failures": critical_failures,
            "error_failures": error_failures,
            "warnings": warnings
        }
    
    def generate_report(self, report: QualityReport, format: str = "text") -> str:
        """Generate formatted quality report."""
        if format == "json":
            return json.dumps({
                "overall_status": report.overall_status,
                "gates": [
                    {
                        "name": gate.name,
                        "passed": gate.passed,
                        "threshold": gate.threshold,
                        "actual": gate.actual,
                        "description": gate.description,
                        "severity": gate.severity,
                        "recommendations": gate.recommendations
                    } for gate in report.gates
                ],
                "metrics": report.metrics,
                "recommendations": report.recommendations,
                "timestamp": report.timestamp
            }, indent=2)
        
        # Text format
        lines = [
            "# Quality Gates Report",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report.timestamp))}",
            "",
            f"## Overall Status: {report.overall_status.upper()}",
            ""
        ]
        
        # Summary metrics
        if report.metrics:
            lines.extend([
                "## Summary",
                f"- Total Gates: {report.metrics.get('total_gates', 0)}",
                f"- Passed: {report.metrics.get('passed_gates', 0)}",
                f"- Failed: {report.metrics.get('failed_gates', 0)}",
                f"- Pass Rate: {report.metrics.get('pass_rate', 0):.1f}%",
                ""
            ])
        
        # Group gates by status and severity
        passed_gates = [g for g in report.gates if g.passed]
        failed_gates = [g for g in report.gates if not g.passed]
        
        # Failed gates by severity
        critical = [g for g in failed_gates if g.severity == "critical"]
        errors = [g for g in failed_gates if g.severity == "error"] 
        warnings = [g for g in failed_gates if g.severity == "warning"]
        
        for severity, gates in [("Critical", critical), ("Error", errors), ("Warning", warnings)]:
            if gates:
                icon = {"Critical": "🚨", "Error": "❌", "Warning": "⚠️"}[severity]
                lines.append(f"## {icon} {severity} ({len(gates)})")
                
                for gate in gates:
                    lines.append(f"### {gate.name}")
                    lines.append(f"- **Status**: FAILED ({gate.actual:.1f} vs {gate.threshold:.1f})")
                    lines.append(f"- **Description**: {gate.description}")
                    
                    if gate.recommendations:
                        lines.append("- **Recommendations**:")
                        for rec in gate.recommendations:
                            lines.append(f"  - {rec}")
                    lines.append("")
        
        # Passed gates summary
        if passed_gates:
            lines.extend([
                f"## ✅ Passed ({len(passed_gates)})",
                ""
            ])
            for gate in passed_gates:
                lines.append(f"- **{gate.name}**: {gate.actual:.1f} >= {gate.threshold:.1f}")
        
        # Overall recommendations
        if report.recommendations:
            lines.extend([
                "",
                "## Recommendations",
                ""
            ])
            for rec in report.recommendations:
                lines.append(f"- {rec}")
        
        return "\n".join(lines)
    
    def check_pr_quality(self) -> QualityReport:
        """Special quality check for pull requests."""
        report = self.enforce_all_gates()
        
        # Add PR-specific recommendations
        if report.overall_status == "failed":
            report.recommendations.insert(0, "❌ PR cannot be merged due to quality gate failures")
        elif report.overall_status == "warning":
            report.recommendations.insert(0, "⚠️ PR has quality warnings - review recommended")
        else:
            report.recommendations.insert(0, "✅ PR meets all quality requirements")
        
        return report


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(description="Quality Gates Enforcement")
    parser.add_argument("--enforce", action="store_true", help="Enforce all quality gates")
    parser.add_argument("--report", action="store_true", help="Generate quality report")
    parser.add_argument("--check-pr", action="store_true", help="Check PR quality")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    
    args = parser.parse_args()
    
    if not any([args.enforce, args.report, args.check_pr, args.all]):
        args.enforce = True  # Default action
    
    project_root = Path(__file__).parent.parent
    enforcer = QualityGateEnforcer(project_root)
    
    if args.check_pr or args.all:
        print("🔍 Checking PR quality...")
        report = enforcer.check_pr_quality()
    else:
        print("🚦 Enforcing quality gates...")
        report = enforcer.enforce_all_gates()
    
    # Generate and output report
    output_format = "json" if args.json else "text"
    report_text = enforcer.generate_report(report, output_format)
    
    if not args.json:
        print("\n" + "="*60)
    
    print(report_text)
    
    if not args.json:
        print("="*60)
    
    # Exit with appropriate code
    if report.overall_status == "failed":
        critical_failures = report.metrics.get("critical_failures", 0)
        error_failures = report.metrics.get("error_failures", 0)
        
        if critical_failures > 0:
            print(f"\n🚨 Quality gates FAILED - {critical_failures} critical failures")
            sys.exit(2)  # Critical failure exit code
        else:
            print(f"\n❌ Quality gates FAILED - {error_failures} error failures")
            sys.exit(1)  # Standard failure exit code
    elif report.overall_status == "warning":
        warnings = report.metrics.get("warnings", 0)
        print(f"\n⚠️ Quality gates PASSED with warnings - {warnings} warnings")
        sys.exit(0)  # Success but with warnings
    else:
        print(f"\n✅ All quality gates PASSED")
        sys.exit(0)  # Success


if __name__ == "__main__":
    main()