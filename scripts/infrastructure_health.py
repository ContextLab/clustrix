#!/usr/bin/env python3
"""
Infrastructure Health Monitoring System

This script monitors the health of the test infrastructure independently from
test execution results. It focuses on ensuring the testing infrastructure
itself is reliable and performs well, minimizing false positive rates.

Features:
- CI/CD pipeline health monitoring
- Test configuration validation
- Dependency health checks
- Resource utilization monitoring
- Infrastructure performance metrics
- False positive rate tracking

Usage:
    python scripts/infrastructure_health.py --check
    python scripts/infrastructure_health.py --monitor
    python scripts/infrastructure_health.py --report
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import argparse


@dataclass
class HealthCheck:
    """Represents a single health check result."""
    name: str
    status: str  # "healthy", "degraded", "unhealthy"
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    severity: str = "info"  # "info", "warning", "error", "critical"


@dataclass
class InfrastructureMetrics:
    """Infrastructure performance and health metrics."""
    health_checks: List[HealthCheck] = field(default_factory=list)
    performance_metrics: Dict[str, float] = field(default_factory=dict)
    false_positive_rate: float = 0.0
    reliability_score: float = 100.0
    last_updated: float = field(default_factory=time.time)


class InfrastructureHealthMonitor:
    """Comprehensive infrastructure health monitoring system."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.health_history_file = project_root / ".pytest_cache" / "infrastructure_health.json"
        self.performance_baseline_file = project_root / ".pytest_cache" / "performance_baseline.json"
        
        # Ensure cache directory exists
        self.health_history_file.parent.mkdir(exist_ok=True)
    
    def run_comprehensive_health_check(self) -> InfrastructureMetrics:
        """Run all infrastructure health checks."""
        metrics = InfrastructureMetrics()
        
        # Core infrastructure checks
        metrics.health_checks.extend([
            self._check_python_environment(),
            self._check_dependency_health(),
            self._check_test_configuration(),
            self._check_ci_workflow_health(),
            self._check_file_system_health(),
            self._check_git_repository_health(),
            self._check_coverage_infrastructure(),
            self._check_test_discovery_infrastructure()
        ])
        
        # Performance checks
        performance_data = self._measure_infrastructure_performance()
        metrics.performance_metrics = performance_data
        
        # Calculate overall health scores
        metrics.reliability_score = self._calculate_reliability_score(metrics.health_checks)
        metrics.false_positive_rate = self._calculate_false_positive_rate(metrics.health_checks)
        
        return metrics
    
    def _check_python_environment(self) -> HealthCheck:
        """Check Python environment health."""
        try:
            # Check Python version
            python_version = sys.version_info
            if python_version < (3, 8):
                return HealthCheck(
                    name="python_environment",
                    status="unhealthy",
                    message=f"Python version {python_version} is too old",
                    severity="critical"
                )
            
            # Check virtual environment
            in_venv = hasattr(sys, 'real_prefix') or (
                hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
            )
            
            details = {
                "python_version": f"{python_version.major}.{python_version.minor}.{python_version.micro}",
                "virtual_environment": in_venv,
                "executable": sys.executable
            }
            
            status = "healthy" if in_venv else "degraded"
            message = "Python environment healthy" if in_venv else "Not using virtual environment"
            severity = "info" if in_venv else "warning"
            
            return HealthCheck(
                name="python_environment",
                status=status,
                message=message,
                details=details,
                severity=severity
            )
        
        except Exception as e:
            return HealthCheck(
                name="python_environment",
                status="unhealthy",
                message=f"Failed to check Python environment: {e}",
                severity="error"
            )
    
    def _check_dependency_health(self) -> HealthCheck:
        """Check health of critical dependencies."""
        critical_deps = [
            "pytest", "pytest-cov", "black", "flake8", "mypy", 
            "paramiko", "pyyaml", "cloudpickle"
        ]
        
        try:
            import pkg_resources
            
            missing_deps = []
            outdated_deps = []
            
            for dep in critical_deps:
                try:
                    pkg_resources.get_distribution(dep)
                except pkg_resources.DistributionNotFound:
                    missing_deps.append(dep)
                except Exception:
                    outdated_deps.append(dep)
            
            if missing_deps:
                return HealthCheck(
                    name="dependency_health",
                    status="unhealthy",
                    message=f"Missing critical dependencies: {', '.join(missing_deps)}",
                    details={"missing": missing_deps, "outdated": outdated_deps},
                    severity="critical"
                )
            
            elif outdated_deps:
                return HealthCheck(
                    name="dependency_health",
                    status="degraded",
                    message=f"Potentially outdated dependencies: {', '.join(outdated_deps)}",
                    details={"missing": missing_deps, "outdated": outdated_deps},
                    severity="warning"
                )
            
            else:
                return HealthCheck(
                    name="dependency_health",
                    status="healthy",
                    message="All critical dependencies available",
                    details={"missing": missing_deps, "outdated": outdated_deps},
                    severity="info"
                )
        
        except Exception as e:
            return HealthCheck(
                name="dependency_health",
                status="unhealthy",
                message=f"Failed to check dependencies: {e}",
                severity="error"
            )
    
    def _check_test_configuration(self) -> HealthCheck:
        """Check test configuration consistency and validity."""
        issues = []
        warnings = []
        
        # Check for configuration files
        pytest_ini = self.project_root / "pytest.ini"
        pyproject_toml = self.project_root / "pyproject.toml"
        setup_cfg = self.project_root / "setup.cfg"
        
        config_files = []
        if pytest_ini.exists():
            config_files.append("pytest.ini")
        if pyproject_toml.exists() and self._has_pytest_config(pyproject_toml):
            config_files.append("pyproject.toml")
        if setup_cfg.exists():
            config_files.append("setup.cfg")
        
        if len(config_files) > 1:
            warnings.append(f"Multiple pytest configuration files found: {', '.join(config_files)}")
        
        if not config_files:
            issues.append("No pytest configuration file found")
        
        # Check test directory structure
        expected_dirs = ["tests", "tests/unit", "tests/integration"]
        for dir_name in expected_dirs:
            if not (self.project_root / dir_name).exists():
                issues.append(f"Missing test directory: {dir_name}")
        
        # Check for conftest.py
        if not (self.project_root / "tests" / "conftest.py").exists():
            warnings.append("No conftest.py found in tests directory")
        
        # Determine status
        if issues:
            status = "unhealthy"
            message = f"Configuration issues found: {'; '.join(issues)}"
            severity = "error"
        elif warnings:
            status = "degraded"
            message = f"Configuration warnings: {'; '.join(warnings)}"
            severity = "warning"
        else:
            status = "healthy"
            message = "Test configuration is valid"
            severity = "info"
        
        return HealthCheck(
            name="test_configuration",
            status=status,
            message=message,
            details={"config_files": config_files, "issues": issues, "warnings": warnings},
            severity=severity
        )
    
    def _has_pytest_config(self, pyproject_path: Path) -> bool:
        """Check if pyproject.toml contains pytest configuration."""
        try:
            with open(pyproject_path, 'r') as f:
                content = f.read()
                return '[tool.pytest' in content
        except Exception:
            return False
    
    def _check_ci_workflow_health(self) -> HealthCheck:
        """Check CI/CD workflow configuration and health."""
        github_dir = self.project_root / ".github"
        workflows_dir = github_dir / "workflows"
        
        if not workflows_dir.exists():
            return HealthCheck(
                name="ci_workflow_health",
                status="degraded",
                message="No GitHub workflows directory found",
                severity="warning"
            )
        
        workflow_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
        
        if not workflow_files:
            return HealthCheck(
                name="ci_workflow_health",
                status="unhealthy",
                message="No CI workflow files found",
                severity="error"
            )
        
        # Analyze workflow configurations
        issues = []
        warnings = []
        
        for workflow_file in workflow_files:
            try:
                with open(workflow_file, 'r') as f:
                    content = f.read()
                
                # Check for essential elements
                if "python" not in content.lower():
                    warnings.append(f"{workflow_file.name}: No Python setup detected")
                
                if "pytest" not in content.lower():
                    warnings.append(f"{workflow_file.name}: No pytest execution detected")
                
                if "timeout-minutes" not in content:
                    warnings.append(f"{workflow_file.name}: No timeout specified")
            
            except Exception as e:
                issues.append(f"Failed to read {workflow_file.name}: {e}")
        
        # Determine status
        if issues:
            status = "unhealthy"
            message = f"CI workflow issues: {'; '.join(issues[:3])}"  # Limit to first 3
            severity = "error"
        elif warnings:
            status = "degraded"
            message = f"CI workflow warnings: {len(warnings)} found"
            severity = "warning"
        else:
            status = "healthy"
            message = f"CI workflows healthy ({len(workflow_files)} files)"
            severity = "info"
        
        return HealthCheck(
            name="ci_workflow_health",
            status=status,
            message=message,
            details={"workflow_files": [f.name for f in workflow_files], "issues": issues, "warnings": warnings},
            severity=severity
        )
    
    def _check_file_system_health(self) -> HealthCheck:
        """Check file system health for test operations."""
        try:
            # Check write permissions
            test_file = self.project_root / ".health_check_test"
            try:
                test_file.write_text("health check")
                test_file.unlink()
                write_permission = True
            except Exception:
                write_permission = False
            
            # Check disk space
            try:
                import shutil
                total, used, free = shutil.disk_usage(self.project_root)
                free_gb = free // (1024**3)
                free_percent = (free / total) * 100
            except Exception:
                free_gb = 0
                free_percent = 0
            
            # Check test cache directory
            cache_dir = self.project_root / ".pytest_cache"
            cache_writable = cache_dir.exists() and os.access(cache_dir, os.W_OK)
            
            issues = []
            warnings = []
            
            if not write_permission:
                issues.append("No write permission in project directory")
            
            if free_percent < 5:
                issues.append(f"Low disk space: {free_percent:.1f}% free")
            elif free_percent < 10:
                warnings.append(f"Disk space warning: {free_percent:.1f}% free")
            
            if not cache_writable:
                warnings.append("Pytest cache directory not writable")
            
            if issues:
                status = "unhealthy"
                severity = "error"
                message = f"File system issues: {'; '.join(issues)}"
            elif warnings:
                status = "degraded"
                severity = "warning"
                message = f"File system warnings: {'; '.join(warnings)}"
            else:
                status = "healthy"
                severity = "info"
                message = f"File system healthy ({free_gb} GB free)"
            
            return HealthCheck(
                name="file_system_health",
                status=status,
                message=message,
                details={
                    "write_permission": write_permission,
                    "free_space_gb": free_gb,
                    "free_space_percent": free_percent,
                    "cache_writable": cache_writable
                },
                severity=severity
            )
        
        except Exception as e:
            return HealthCheck(
                name="file_system_health",
                status="unhealthy",
                message=f"Failed to check file system: {e}",
                severity="error"
            )
    
    def _check_git_repository_health(self) -> HealthCheck:
        """Check Git repository health."""
        try:
            # Check if we're in a git repository
            git_dir = self.project_root / ".git"
            if not git_dir.exists():
                return HealthCheck(
                    name="git_repository_health",
                    status="degraded",
                    message="Not a Git repository",
                    severity="warning"
                )
            
            # Check git status
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return HealthCheck(
                    name="git_repository_health",
                    status="unhealthy",
                    message="Git status command failed",
                    severity="error"
                )
            
            # Check for uncommitted changes (informational)
            uncommitted_files = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
            
            # Check if we can get current branch
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"
            
            return HealthCheck(
                name="git_repository_health",
                status="healthy",
                message=f"Git repository healthy (branch: {current_branch})",
                details={
                    "current_branch": current_branch,
                    "uncommitted_files": uncommitted_files
                },
                severity="info"
            )
        
        except subprocess.TimeoutExpired:
            return HealthCheck(
                name="git_repository_health",
                status="degraded",
                message="Git commands timed out",
                severity="warning"
            )
        except Exception as e:
            return HealthCheck(
                name="git_repository_health",
                status="degraded",
                message=f"Git check failed: {e}",
                severity="warning"
            )
    
    def _check_coverage_infrastructure(self) -> HealthCheck:
        """Check coverage reporting infrastructure."""
        try:
            # Check if coverage is available
            try:
                import coverage
                coverage_available = True
            except ImportError:
                coverage_available = False
            
            # Check coverage configuration
            config_files = []
            if (self.project_root / ".coveragerc").exists():
                config_files.append(".coveragerc")
            
            pyproject_toml = self.project_root / "pyproject.toml"
            if pyproject_toml.exists():
                with open(pyproject_toml, 'r') as f:
                    if '[tool.coverage' in f.read():
                        config_files.append("pyproject.toml")
            
            # Check for coverage badge script
            badge_script = self.project_root / ".github" / "scripts" / "update_coverage_badge.py"
            badge_script_exists = badge_script.exists()
            
            issues = []
            warnings = []
            
            if not coverage_available:
                issues.append("Coverage.py not available")
            
            if not config_files:
                warnings.append("No coverage configuration found")
            
            if not badge_script_exists:
                warnings.append("Coverage badge update script not found")
            
            if issues:
                status = "unhealthy"
                severity = "error"
                message = f"Coverage infrastructure issues: {'; '.join(issues)}"
            elif warnings:
                status = "degraded"
                severity = "warning"
                message = f"Coverage infrastructure warnings: {'; '.join(warnings)}"
            else:
                status = "healthy"
                severity = "info"
                message = "Coverage infrastructure healthy"
            
            return HealthCheck(
                name="coverage_infrastructure",
                status=status,
                message=message,
                details={
                    "coverage_available": coverage_available,
                    "config_files": config_files,
                    "badge_script_exists": badge_script_exists
                },
                severity=severity
            )
        
        except Exception as e:
            return HealthCheck(
                name="coverage_infrastructure",
                status="unhealthy",
                message=f"Failed to check coverage infrastructure: {e}",
                severity="error"
            )
    
    def _check_test_discovery_infrastructure(self) -> HealthCheck:
        """Check test discovery infrastructure health."""
        try:
            # Count test files
            test_files = []
            test_dirs = ["tests", "tests/unit", "tests/integration", "tests/real_world"]
            
            for test_dir in test_dirs:
                test_path = self.project_root / test_dir
                if test_path.exists():
                    test_files.extend(list(test_path.rglob("test_*.py")))
            
            # Check for test discovery script
            discovery_script = self.project_root / "scripts" / "test_discovery.py"
            discovery_script_exists = discovery_script.exists()
            
            # Check pytest collection works
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "--collect-only", "-q"],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                collection_works = result.returncode == 0
                collected_items = result.stdout.count(" ") if collection_works else 0
            except subprocess.TimeoutExpired:
                collection_works = False
                collected_items = 0
            except Exception:
                collection_works = False
                collected_items = 0
            
            warnings = []
            issues = []
            
            if not test_files:
                issues.append("No test files found")
            
            if not collection_works:
                issues.append("Pytest collection failed")
            
            if not discovery_script_exists:
                warnings.append("Test discovery script not found")
            
            if issues:
                status = "unhealthy"
                severity = "error"
                message = f"Test discovery issues: {'; '.join(issues)}"
            elif warnings:
                status = "degraded"
                severity = "warning"
                message = f"Test discovery warnings: {'; '.join(warnings)}"
            else:
                status = "healthy"
                severity = "info"
                message = f"Test discovery healthy ({len(test_files)} test files)"
            
            return HealthCheck(
                name="test_discovery_infrastructure",
                status=status,
                message=message,
                details={
                    "test_files_found": len(test_files),
                    "collection_works": collection_works,
                    "collected_items": collected_items,
                    "discovery_script_exists": discovery_script_exists
                },
                severity=severity
            )
        
        except Exception as e:
            return HealthCheck(
                name="test_discovery_infrastructure",
                status="unhealthy",
                message=f"Failed to check test discovery: {e}",
                severity="error"
            )
    
    def _measure_infrastructure_performance(self) -> Dict[str, float]:
        """Measure infrastructure performance metrics."""
        metrics = {}
        
        try:
            # Measure test collection time
            start_time = time.time()
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q"],
                cwd=self.project_root,
                capture_output=True,
                timeout=60
            )
            collection_time = time.time() - start_time
            metrics["test_collection_time"] = collection_time
            
            # Measure dependency import time
            start_time = time.time()
            import pytest  # noqa
            import coverage  # noqa
            import black  # noqa
            import flake8  # noqa
            import mypy  # noqa
            import_time = time.time() - start_time
            metrics["dependency_import_time"] = import_time
            
            # Measure file system performance
            start_time = time.time()
            test_files = list(self.project_root.rglob("test_*.py"))
            file_scan_time = time.time() - start_time
            metrics["file_scan_time"] = file_scan_time
            metrics["test_files_count"] = len(test_files)
            
        except Exception as e:
            print(f"Warning: Failed to measure some performance metrics: {e}")
        
        return metrics
    
    def _calculate_reliability_score(self, health_checks: List[HealthCheck]) -> float:
        """Calculate overall infrastructure reliability score (0-100)."""
        if not health_checks:
            return 0.0
        
        total_weight = 0
        weighted_score = 0
        
        weights = {
            "healthy": 100,
            "degraded": 75,
            "unhealthy": 25
        }
        
        severity_multipliers = {
            "info": 1.0,
            "warning": 0.9,
            "error": 0.7,
            "critical": 0.5
        }
        
        for check in health_checks:
            base_score = weights.get(check.status, 0)
            multiplier = severity_multipliers.get(check.severity, 0.5)
            check_score = base_score * multiplier
            
            weighted_score += check_score
            total_weight += 100  # Each check has max weight of 100
        
        return weighted_score / total_weight if total_weight > 0 else 0.0
    
    def _calculate_false_positive_rate(self, health_checks: List[HealthCheck]) -> float:
        """Calculate estimated false positive rate based on infrastructure health."""
        base_rate = 0.01  # 1% baseline
        
        # Increase rate for each unhealthy check
        unhealthy_checks = sum(1 for check in health_checks if check.status == "unhealthy")
        degraded_checks = sum(1 for check in health_checks if check.status == "degraded")
        
        # Each unhealthy check adds 2% false positive rate
        # Each degraded check adds 0.5% false positive rate
        additional_rate = (unhealthy_checks * 0.02) + (degraded_checks * 0.005)
        
        # Cap at 20% maximum
        return min(base_rate + additional_rate, 0.20)
    
    def save_health_history(self, metrics: InfrastructureMetrics):
        """Save health metrics to history file."""
        try:
            # Load existing history
            history = []
            if self.health_history_file.exists():
                with open(self.health_history_file, 'r') as f:
                    history = json.load(f)
            
            # Add new metrics (keep last 100 records)
            history.append({
                "timestamp": metrics.last_updated,
                "reliability_score": metrics.reliability_score,
                "false_positive_rate": metrics.false_positive_rate,
                "health_checks": [
                    {
                        "name": check.name,
                        "status": check.status,
                        "severity": check.severity
                    } for check in metrics.health_checks
                ]
            })
            
            # Keep only last 100 records
            history = history[-100:]
            
            # Save back to file
            with open(self.health_history_file, 'w') as f:
                json.dump(history, f, indent=2)
        
        except Exception as e:
            print(f"Warning: Failed to save health history: {e}")
    
    def generate_health_report(self, metrics: InfrastructureMetrics) -> str:
        """Generate human-readable health report."""
        report_lines = [
            "# Infrastructure Health Report",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(metrics.last_updated))}",
            "",
            "## Overall Health",
            f"- Reliability Score: {metrics.reliability_score:.1f}/100.0",
            f"- False Positive Rate: {metrics.false_positive_rate*100:.2f}%",
            "",
            "## Infrastructure Checks"
        ]
        
        # Group checks by status
        status_groups = {"healthy": [], "degraded": [], "unhealthy": []}
        for check in metrics.health_checks:
            status_groups[check.status].append(check)
        
        for status, checks in status_groups.items():
            if checks:
                icon = {"healthy": "✅", "degraded": "⚠️", "unhealthy": "❌"}[status]
                report_lines.append(f"### {icon} {status.upper()} ({len(checks)})")
                
                for check in checks:
                    severity_icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}[check.severity]
                    report_lines.append(f"- {severity_icon} **{check.name}**: {check.message}")
        
        # Performance metrics
        if metrics.performance_metrics:
            report_lines.extend(["", "## Performance Metrics"])
            for metric_name, value in metrics.performance_metrics.items():
                if metric_name.endswith("_time"):
                    report_lines.append(f"- {metric_name}: {value:.3f}s")
                else:
                    report_lines.append(f"- {metric_name}: {value}")
        
        return "\n".join(report_lines)


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(description="Infrastructure Health Monitoring")
    parser.add_argument("--check", action="store_true", help="Run health checks")
    parser.add_argument("--monitor", action="store_true", help="Run continuous monitoring")
    parser.add_argument("--report", action="store_true", help="Generate health report")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--save", action="store_true", help="Save to history")
    
    args = parser.parse_args()
    
    if not any([args.check, args.monitor, args.report]):
        args.check = True  # Default action
    
    project_root = Path(__file__).parent.parent
    monitor = InfrastructureHealthMonitor(project_root)
    
    if args.check or args.monitor or args.report:
        print("🏥 Running infrastructure health checks...")
        metrics = monitor.run_comprehensive_health_check()
        
        if args.save:
            monitor.save_health_history(metrics)
            print("💾 Health history saved")
        
        if args.json:
            # Output JSON format
            output = {
                "reliability_score": metrics.reliability_score,
                "false_positive_rate": metrics.false_positive_rate,
                "health_checks": [
                    {
                        "name": check.name,
                        "status": check.status,
                        "message": check.message,
                        "severity": check.severity,
                        "details": check.details
                    } for check in metrics.health_checks
                ],
                "performance_metrics": metrics.performance_metrics,
                "timestamp": metrics.last_updated
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            # Human-readable report
            report = monitor.generate_health_report(metrics)
            print("\n" + "="*60)
            print(report)
            print("="*60)
        
        # Exit with appropriate code
        unhealthy_checks = sum(1 for check in metrics.health_checks if check.status == "unhealthy")
        if unhealthy_checks > 0:
            print(f"\n❌ Infrastructure health check failed ({unhealthy_checks} unhealthy checks)")
            sys.exit(1)
        else:
            print("\n✅ Infrastructure health check passed")
            sys.exit(0)


if __name__ == "__main__":
    main()