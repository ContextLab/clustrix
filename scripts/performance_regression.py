#!/usr/bin/env python3
"""
Performance Regression Detection and Alerting System

This script monitors test execution performance, detects regressions,
and provides alerting for performance issues. It maintains historical
performance data and provides actionable insights.

Features:
- Performance baseline establishment and maintenance
- Regression detection with statistical analysis
- Test categorization and performance profiling
- Automated alerting for performance issues
- Historical performance tracking and reporting
- Actionable recommendations for optimization

Usage:
    python scripts/performance_regression.py --baseline
    python scripts/performance_regression.py --check
    python scripts/performance_regression.py --profile
    python scripts/performance_regression.py --alert
"""

import json
import subprocess
import sys
import time
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import argparse
import re


@dataclass
class PerformanceMetric:
    """Single performance measurement."""
    name: str
    value: float
    unit: str  # "seconds", "tests/second", "MB", etc.
    category: str  # "duration", "throughput", "memory", etc.
    timestamp: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceBaseline:
    """Performance baseline data."""
    metric_name: str
    mean: float
    std_dev: float
    samples: List[float] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)
    sample_count: int = 0


@dataclass
class RegressionAlert:
    """Performance regression alert."""
    metric_name: str
    severity: str  # "info", "warning", "error", "critical"
    current_value: float
    baseline_mean: float
    deviation_percent: float
    message: str
    recommendations: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class PerformanceRegressionDetector:
    """Comprehensive performance regression detection system."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.cache_dir = project_root / ".pytest_cache"
        self.baselines_file = self.cache_dir / "performance_baselines.json"
        self.history_file = self.cache_dir / "performance_history.json"
        
        # Ensure cache directory exists
        self.cache_dir.mkdir(exist_ok=True)
        
        # Regression thresholds (percentage increase from baseline)
        self.thresholds = {
            "warning": 20.0,    # 20% increase
            "error": 50.0,      # 50% increase  
            "critical": 100.0   # 100% increase (2x slower)
        }
        
        # Performance targets (absolute values)
        self.targets = {
            "unit_test_suite": 120.0,      # 2 minutes
            "integration_test_suite": 300.0, # 5 minutes
            "full_test_suite": 900.0,      # 15 minutes
            "single_test_max": 30.0,       # 30 seconds
            "test_discovery": 10.0,        # 10 seconds
            "coverage_overhead": 0.3       # 30% overhead max
        }
    
    def establish_baseline(self, force: bool = False) -> Dict[str, PerformanceBaseline]:
        """Establish or update performance baselines."""
        print("📊 Establishing performance baselines...")
        
        # Load existing baselines
        baselines = self._load_baselines()
        
        # Run performance measurements
        metrics = self._collect_performance_metrics()
        
        # Update baselines
        for metric in metrics:
            baseline_key = metric.name
            
            if baseline_key not in baselines or force:
                # Create new baseline
                baselines[baseline_key] = PerformanceBaseline(
                    metric_name=metric.name,
                    mean=metric.value,
                    std_dev=0.0,
                    samples=[metric.value],
                    sample_count=1
                )
                print(f"✨ New baseline: {metric.name} = {metric.value:.2f} {metric.unit}")
            else:
                # Update existing baseline
                baseline = baselines[baseline_key]
                baseline.samples.append(metric.value)
                
                # Keep only last 50 samples for rolling baseline
                baseline.samples = baseline.samples[-50:]
                baseline.sample_count = len(baseline.samples)
                
                # Recalculate statistics
                baseline.mean = statistics.mean(baseline.samples)
                baseline.std_dev = statistics.stdev(baseline.samples) if len(baseline.samples) > 1 else 0.0
                baseline.last_updated = time.time()
                
                print(f"📈 Updated baseline: {metric.name} = {baseline.mean:.2f}±{baseline.std_dev:.2f} {metric.unit}")
        
        # Save updated baselines
        self._save_baselines(baselines)
        
        return baselines
    
    def check_regressions(self) -> List[RegressionAlert]:
        """Check for performance regressions against baselines."""
        print("🔍 Checking for performance regressions...")
        
        # Load baselines
        baselines = self._load_baselines()
        if not baselines:
            return [RegressionAlert(
                metric_name="baseline_availability",
                severity="error",
                current_value=0.0,
                baseline_mean=0.0,
                deviation_percent=0.0,
                message="No performance baselines available - run with --baseline first",
                recommendations=["Run: python scripts/performance_regression.py --baseline"]
            )]
        
        # Collect current metrics
        current_metrics = self._collect_performance_metrics()
        
        alerts = []
        
        for metric in current_metrics:
            if metric.name in baselines:
                alert = self._analyze_metric_regression(metric, baselines[metric.name])
                if alert:
                    alerts.append(alert)
            else:
                # New metric - create info alert
                alerts.append(RegressionAlert(
                    metric_name=metric.name,
                    severity="info",
                    current_value=metric.value,
                    baseline_mean=0.0,
                    deviation_percent=0.0,
                    message=f"New metric detected: {metric.name} = {metric.value:.2f} {metric.unit}",
                    recommendations=["Consider adding to baseline with --baseline"]
                ))
        
        return alerts
    
    def profile_performance(self) -> Dict[str, Any]:
        """Profile test performance to identify slow tests and bottlenecks."""
        print("🔬 Profiling test performance...")
        
        profile_data = {
            "slow_tests": [],
            "test_categories": {},
            "bottlenecks": [],
            "recommendations": [],
            "timestamp": time.time()
        }
        
        # Run tests with detailed timing
        timing_data = self._profile_test_execution()
        
        # Analyze slow individual tests
        slow_tests = [test for test in timing_data.get("test_durations", []) 
                     if test["duration"] > self.targets["single_test_max"]]
        profile_data["slow_tests"] = sorted(slow_tests, key=lambda x: x["duration"], reverse=True)
        
        # Categorize performance by test type
        categories = ["unit", "integration", "real_world", "slow"]
        for category in categories:
            category_tests = [test for test in timing_data.get("test_durations", [])
                            if category in test.get("markers", [])]
            
            if category_tests:
                durations = [test["duration"] for test in category_tests]
                profile_data["test_categories"][category] = {
                    "count": len(category_tests),
                    "total_duration": sum(durations),
                    "avg_duration": statistics.mean(durations),
                    "max_duration": max(durations),
                    "slowest_test": max(category_tests, key=lambda x: x["duration"])["name"]
                }
        
        # Identify bottlenecks
        bottlenecks = self._identify_bottlenecks(timing_data, profile_data)
        profile_data["bottlenecks"] = bottlenecks
        
        # Generate recommendations
        recommendations = self._generate_performance_recommendations(profile_data)
        profile_data["recommendations"] = recommendations
        
        return profile_data
    
    def _collect_performance_metrics(self) -> List[PerformanceMetric]:
        """Collect comprehensive performance metrics."""
        metrics = []
        
        # Test suite duration metrics
        suite_metrics = self._measure_test_suite_performance()
        metrics.extend(suite_metrics)
        
        # Test discovery performance
        discovery_metrics = self._measure_test_discovery_performance()
        metrics.extend(discovery_metrics)
        
        # Coverage overhead
        coverage_metrics = self._measure_coverage_overhead()
        metrics.extend(coverage_metrics)
        
        # Infrastructure performance
        infrastructure_metrics = self._measure_infrastructure_performance()
        metrics.extend(infrastructure_metrics)
        
        return metrics
    
    def _measure_test_suite_performance(self) -> List[PerformanceMetric]:
        """Measure test suite execution performance."""
        metrics = []
        
        test_suites = [
            ("unit_test_suite", ["tests/unit/", "-m", "not real_world"]),
            ("integration_test_suite", ["tests/integration/", "-m", "integration"]),
            ("full_test_suite", ["tests/", "-m", "not real_world"]),
        ]
        
        for suite_name, pytest_args in test_suites:
            try:
                start_time = time.time()
                result = subprocess.run(
                    [sys.executable, "-m", "pytest"] + pytest_args + ["--tb=no", "-q"],
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=1200  # 20 minutes max
                )
                duration = time.time() - start_time
                
                # Parse test count from output
                test_count = self._extract_test_count(result.stdout.decode())
                
                metrics.append(PerformanceMetric(
                    name=suite_name,
                    value=duration,
                    unit="seconds",
                    category="duration",
                    context={
                        "test_count": test_count,
                        "success": result.returncode == 0,
                        "args": pytest_args
                    }
                ))
                
                if test_count > 0:
                    metrics.append(PerformanceMetric(
                        name=f"{suite_name}_throughput",
                        value=test_count / duration if duration > 0 else 0,
                        unit="tests/second",
                        category="throughput",
                        context={"test_count": test_count, "duration": duration}
                    ))
            
            except subprocess.TimeoutExpired:
                metrics.append(PerformanceMetric(
                    name=suite_name,
                    value=1200.0,  # Timeout value
                    unit="seconds",
                    category="duration",
                    context={"timeout": True}
                ))
            except Exception as e:
                print(f"Warning: Failed to measure {suite_name}: {e}")
        
        return metrics
    
    def _measure_test_discovery_performance(self) -> List[PerformanceMetric]:
        """Measure test discovery performance."""
        metrics = []
        
        try:
            # Measure pytest collection time
            start_time = time.time()
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q"],
                cwd=self.project_root,
                capture_output=True,
                timeout=60
            )
            duration = time.time() - start_time
            
            # Count collected items
            items_count = result.stdout.decode().count(" ") if result.returncode == 0 else 0
            
            metrics.append(PerformanceMetric(
                name="test_discovery",
                value=duration,
                unit="seconds",
                category="duration",
                context={"items_collected": items_count}
            ))
            
            if items_count > 0:
                metrics.append(PerformanceMetric(
                    name="test_discovery_throughput",
                    value=items_count / duration if duration > 0 else 0,
                    unit="items/second",
                    category="throughput"
                ))
        
        except Exception as e:
            print(f"Warning: Failed to measure test discovery: {e}")
        
        return metrics
    
    def _measure_coverage_overhead(self) -> List[PerformanceMetric]:
        """Measure coverage collection overhead."""
        metrics = []
        
        try:
            # Run tests without coverage
            start_time = time.time()
            result_no_cov = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/unit/", "-x", "-q", "--tb=no"],
                cwd=self.project_root,
                capture_output=True,
                timeout=300
            )
            duration_no_cov = time.time() - start_time
            
            # Run same tests with coverage
            start_time = time.time()
            result_with_cov = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/unit/", "-x", "-q", "--tb=no", "--cov=clustrix"],
                cwd=self.project_root,
                capture_output=True,
                timeout=300
            )
            duration_with_cov = time.time() - start_time
            
            if result_no_cov.returncode == 0 and result_with_cov.returncode == 0:
                overhead_percent = ((duration_with_cov - duration_no_cov) / duration_no_cov) * 100
                
                metrics.append(PerformanceMetric(
                    name="coverage_overhead",
                    value=overhead_percent,
                    unit="percent",
                    category="overhead",
                    context={
                        "duration_no_cov": duration_no_cov,
                        "duration_with_cov": duration_with_cov
                    }
                ))
        
        except Exception as e:
            print(f"Warning: Failed to measure coverage overhead: {e}")
        
        return metrics
    
    def _measure_infrastructure_performance(self) -> List[PerformanceMetric]:
        """Measure infrastructure performance metrics."""
        metrics = []
        
        try:
            # Measure import time for key modules
            start_time = time.time()
            import clustrix  # noqa
            import_duration = time.time() - start_time
            
            metrics.append(PerformanceMetric(
                name="module_import_time",
                value=import_duration,
                unit="seconds",
                category="startup"
            ))
            
            # Measure file system scan time
            start_time = time.time()
            test_files = list(self.project_root.rglob("test_*.py"))
            scan_duration = time.time() - start_time
            
            metrics.append(PerformanceMetric(
                name="file_scan_time",
                value=scan_duration,
                unit="seconds",
                category="io",
                context={"files_found": len(test_files)}
            ))
        
        except Exception as e:
            print(f"Warning: Failed to measure infrastructure performance: {e}")
        
        return metrics
    
    def _profile_test_execution(self) -> Dict[str, Any]:
        """Profile detailed test execution to identify bottlenecks."""
        timing_data = {"test_durations": [], "setup_teardown": []}
        
        try:
            # Run pytest with detailed timing output
            result = subprocess.run([
                sys.executable, "-m", "pytest", 
                "tests/unit/", "-v", "--durations=0", "--tb=no"
            ], 
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=300
            )
            
            if result.returncode == 0:
                # Parse pytest durations output
                timing_data["test_durations"] = self._parse_pytest_durations(result.stdout)
        
        except Exception as e:
            print(f"Warning: Failed to profile test execution: {e}")
        
        return timing_data
    
    def _parse_pytest_durations(self, pytest_output: str) -> List[Dict[str, Any]]:
        """Parse pytest --durations output."""
        durations = []
        
        # Look for duration lines like "0.15s call     tests/unit/test_example.py::test_function"
        duration_pattern = r'(\d+\.?\d*s)\s+(\w+)\s+(.+)'
        
        for line in pytest_output.split('\n'):
            match = re.search(duration_pattern, line)
            if match:
                duration_str, phase, test_name = match.groups()
                duration_val = float(duration_str[:-1])  # Remove 's' suffix
                
                durations.append({
                    "name": test_name,
                    "duration": duration_val,
                    "phase": phase,
                    "markers": self._extract_test_markers(test_name)
                })
        
        return durations
    
    def _extract_test_markers(self, test_name: str) -> List[str]:
        """Extract test markers from test name (simplified)."""
        markers = []
        
        # Simple heuristics based on path and name
        if "/unit/" in test_name:
            markers.append("unit")
        elif "/integration/" in test_name:
            markers.append("integration")
        elif "/real_world/" in test_name:
            markers.append("real_world")
        
        if "slow" in test_name.lower():
            markers.append("slow")
        
        return markers
    
    def _extract_test_count(self, pytest_output: str) -> int:
        """Extract test count from pytest output."""
        # Look for patterns like "5 passed" or "10 failed, 2 passed"
        import re
        
        # Common patterns in pytest output
        patterns = [
            r'(\d+) passed',
            r'(\d+) failed',
            r'(\d+) error',
            r'(\d+) skipped'
        ]
        
        total_count = 0
        for pattern in patterns:
            matches = re.findall(pattern, pytest_output)
            for match in matches:
                total_count += int(match)
        
        return total_count
    
    def _analyze_metric_regression(self, current_metric: PerformanceMetric, 
                                 baseline: PerformanceBaseline) -> Optional[RegressionAlert]:
        """Analyze a metric for performance regression."""
        if baseline.mean == 0:
            return None
        
        deviation_percent = ((current_metric.value - baseline.mean) / baseline.mean) * 100
        
        # Determine severity based on deviation
        severity = "info"
        if deviation_percent > self.thresholds["critical"]:
            severity = "critical"
        elif deviation_percent > self.thresholds["error"]:
            severity = "error"
        elif deviation_percent > self.thresholds["warning"]:
            severity = "warning"
        elif deviation_percent > 5:  # Minor increase
            severity = "info"
        else:
            return None  # No significant change
        
        # Generate appropriate message
        if deviation_percent > 0:
            message = f"Performance regression detected: {current_metric.name} increased by {deviation_percent:.1f}%"
        else:
            message = f"Performance improvement: {current_metric.name} improved by {abs(deviation_percent):.1f}%"
            severity = "info"  # Improvements are always info level
        
        # Generate recommendations
        recommendations = self._generate_regression_recommendations(current_metric, baseline, deviation_percent)
        
        return RegressionAlert(
            metric_name=current_metric.name,
            severity=severity,
            current_value=current_metric.value,
            baseline_mean=baseline.mean,
            deviation_percent=deviation_percent,
            message=message,
            recommendations=recommendations
        )
    
    def _generate_regression_recommendations(self, metric: PerformanceMetric, 
                                           baseline: PerformanceBaseline, 
                                           deviation_percent: float) -> List[str]:
        """Generate recommendations for performance regression."""
        recommendations = []
        
        if deviation_percent > 50:  # Significant regression
            recommendations.append("Critical performance regression - investigate immediately")
            
            if "test_suite" in metric.name:
                recommendations.extend([
                    "Check for new slow tests or increased test complexity",
                    "Consider parallel test execution with pytest-xdist",
                    "Profile individual test execution times"
                ])
            
            if "discovery" in metric.name:
                recommendations.extend([
                    "Check for filesystem issues or excessive test files",
                    "Optimize test file organization and naming"
                ])
        
        elif deviation_percent > 20:  # Moderate regression
            recommendations.append("Performance regression detected - monitoring recommended")
            
            if metric.category == "duration":
                recommendations.append("Review recent code changes for performance impact")
            
            if metric.category == "throughput":
                recommendations.append("Check system resources and test environment")
        
        # Metric-specific recommendations
        if metric.name == "coverage_overhead" and metric.value > 30:
            recommendations.append("Coverage overhead is high - consider optimizing coverage collection")
        
        if "throughput" in metric.name and deviation_percent > 30:
            recommendations.append("Test throughput decreased significantly - check test parallelization")
        
        return recommendations
    
    def _identify_bottlenecks(self, timing_data: Dict[str, Any], 
                            profile_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Identify performance bottlenecks."""
        bottlenecks = []
        
        # Find slowest tests
        test_durations = timing_data.get("test_durations", [])
        if test_durations:
            slowest_tests = sorted(test_durations, key=lambda x: x["duration"], reverse=True)[:5]
            
            for test in slowest_tests:
                if test["duration"] > self.targets["single_test_max"]:
                    bottlenecks.append({
                        "type": "slow_test",
                        "name": test["name"],
                        "duration": test["duration"],
                        "impact": "high" if test["duration"] > 60 else "medium",
                        "recommendation": "Optimize or mark as @pytest.mark.slow"
                    })
        
        # Check test category performance
        for category, stats in profile_data.get("test_categories", {}).items():
            target_key = f"{category}_test_suite"
            if target_key in self.targets:
                if stats["total_duration"] > self.targets[target_key]:
                    bottlenecks.append({
                        "type": "slow_category",
                        "name": f"{category}_tests",
                        "duration": stats["total_duration"],
                        "impact": "high",
                        "recommendation": f"Optimize {category} test execution or implement parallel execution"
                    })
        
        return bottlenecks
    
    def _generate_performance_recommendations(self, profile_data: Dict[str, Any]) -> List[str]:
        """Generate actionable performance recommendations."""
        recommendations = []
        
        slow_tests = profile_data.get("slow_tests", [])
        if len(slow_tests) > 5:
            recommendations.append(f"Found {len(slow_tests)} slow tests - consider optimization or @pytest.mark.slow")
        
        bottlenecks = profile_data.get("bottlenecks", [])
        high_impact_bottlenecks = [b for b in bottlenecks if b.get("impact") == "high"]
        
        if high_impact_bottlenecks:
            recommendations.append("High-impact bottlenecks found - prioritize optimization")
        
        # Category-specific recommendations
        test_categories = profile_data.get("test_categories", {})
        
        if "unit" in test_categories:
            unit_stats = test_categories["unit"]
            if unit_stats["avg_duration"] > 1.0:  # Unit tests should be fast
                recommendations.append("Unit tests are slow - ensure no external dependencies")
        
        if "integration" in test_categories:
            integration_stats = test_categories["integration"]
            if integration_stats["total_duration"] > 300:  # 5 minutes
                recommendations.append("Integration tests are slow - consider mocking or parallel execution")
        
        return recommendations
    
    def _load_baselines(self) -> Dict[str, PerformanceBaseline]:
        """Load performance baselines from cache."""
        try:
            if self.baselines_file.exists():
                with open(self.baselines_file, 'r') as f:
                    data = json.load(f)
                
                baselines = {}
                for name, baseline_data in data.items():
                    baselines[name] = PerformanceBaseline(
                        metric_name=baseline_data["metric_name"],
                        mean=baseline_data["mean"],
                        std_dev=baseline_data["std_dev"],
                        samples=baseline_data.get("samples", []),
                        last_updated=baseline_data.get("last_updated", time.time()),
                        sample_count=baseline_data.get("sample_count", len(baseline_data.get("samples", [])))
                    )
                
                return baselines
        
        except Exception as e:
            print(f"Warning: Failed to load baselines: {e}")
        
        return {}
    
    def _save_baselines(self, baselines: Dict[str, PerformanceBaseline]):
        """Save performance baselines to cache."""
        try:
            data = {}
            for name, baseline in baselines.items():
                data[name] = {
                    "metric_name": baseline.metric_name,
                    "mean": baseline.mean,
                    "std_dev": baseline.std_dev,
                    "samples": baseline.samples,
                    "last_updated": baseline.last_updated,
                    "sample_count": baseline.sample_count
                }
            
            with open(self.baselines_file, 'w') as f:
                json.dump(data, f, indent=2)
        
        except Exception as e:
            print(f"Warning: Failed to save baselines: {e}")
    
    def save_performance_history(self, metrics: List[PerformanceMetric]):
        """Save performance metrics to history."""
        try:
            # Load existing history
            history = []
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    history = json.load(f)
            
            # Add new metrics
            entry = {
                "timestamp": time.time(),
                "metrics": [
                    {
                        "name": metric.name,
                        "value": metric.value,
                        "unit": metric.unit,
                        "category": metric.category,
                        "context": metric.context
                    } for metric in metrics
                ]
            }
            
            history.append(entry)
            
            # Keep only last 100 entries
            history = history[-100:]
            
            with open(self.history_file, 'w') as f:
                json.dump(history, f, indent=2)
        
        except Exception as e:
            print(f"Warning: Failed to save performance history: {e}")
    
    def generate_alert_report(self, alerts: List[RegressionAlert]) -> str:
        """Generate formatted alert report."""
        if not alerts:
            return "✅ No performance regressions detected"
        
        lines = [
            "# Performance Regression Alert Report",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"## Summary: {len(alerts)} alerts found",
            ""
        ]
        
        # Group by severity
        severity_groups = {"critical": [], "error": [], "warning": [], "info": []}
        for alert in alerts:
            severity_groups[alert.severity].append(alert)
        
        for severity, severity_alerts in severity_groups.items():
            if severity_alerts:
                icon = {"critical": "🚨", "error": "❌", "warning": "⚠️", "info": "ℹ️"}[severity]
                lines.append(f"## {icon} {severity.upper()} ({len(severity_alerts)})")
                
                for alert in severity_alerts:
                    lines.extend([
                        f"### {alert.metric_name}",
                        f"- **Current**: {alert.current_value:.2f}",
                        f"- **Baseline**: {alert.baseline_mean:.2f}",
                        f"- **Change**: {alert.deviation_percent:+.1f}%",
                        f"- **Message**: {alert.message}",
                        ""
                    ])
                    
                    if alert.recommendations:
                        lines.append("**Recommendations:**")
                        for rec in alert.recommendations:
                            lines.append(f"- {rec}")
                        lines.append("")
        
        return "\n".join(lines)


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(description="Performance Regression Detection")
    parser.add_argument("--baseline", action="store_true", help="Establish performance baseline")
    parser.add_argument("--check", action="store_true", help="Check for regressions")
    parser.add_argument("--profile", action="store_true", help="Profile test performance")
    parser.add_argument("--alert", action="store_true", help="Generate alerts report")
    parser.add_argument("--force", action="store_true", help="Force baseline update")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    
    args = parser.parse_args()
    
    if not any([args.baseline, args.check, args.profile, args.alert]):
        args.check = True  # Default action
    
    project_root = Path(__file__).parent.parent
    detector = PerformanceRegressionDetector(project_root)
    
    if args.baseline:
        print("📊 Establishing performance baselines...")
        baselines = detector.establish_baseline(force=args.force)
        print(f"✅ Established {len(baselines)} baselines")
    
    if args.check or args.alert:
        print("🔍 Checking for performance regressions...")
        alerts = detector.check_regressions()
        
        if args.json:
            output = [
                {
                    "metric_name": alert.metric_name,
                    "severity": alert.severity,
                    "current_value": alert.current_value,
                    "baseline_mean": alert.baseline_mean,
                    "deviation_percent": alert.deviation_percent,
                    "message": alert.message,
                    "recommendations": alert.recommendations,
                    "timestamp": alert.timestamp
                } for alert in alerts
            ]
            print(json.dumps(output, indent=2))
        else:
            report = detector.generate_alert_report(alerts)
            print("\n" + "="*60)
            print(report)
            print("="*60)
        
        # Exit with appropriate code for CI
        critical_alerts = [a for a in alerts if a.severity == "critical"]
        error_alerts = [a for a in alerts if a.severity == "error"]
        
        if critical_alerts:
            print(f"\n🚨 CRITICAL performance regressions detected: {len(critical_alerts)}")
            sys.exit(2)
        elif error_alerts:
            print(f"\n❌ Performance regressions detected: {len(error_alerts)}")
            sys.exit(1)
        else:
            print(f"\n✅ Performance check completed")
            sys.exit(0)
    
    if args.profile:
        print("🔬 Profiling test performance...")
        profile_data = detector.profile_performance()
        
        if args.json:
            print(json.dumps(profile_data, indent=2, default=str))
        else:
            print(f"\n📊 Performance Profile Results:")
            print(f"- Slow tests found: {len(profile_data.get('slow_tests', []))}")
            print(f"- Bottlenecks identified: {len(profile_data.get('bottlenecks', []))}")
            print(f"- Categories profiled: {len(profile_data.get('test_categories', {}))}")
            
            if profile_data.get("recommendations"):
                print("\n💡 Recommendations:")
                for rec in profile_data["recommendations"]:
                    print(f"  - {rec}")


if __name__ == "__main__":
    main()