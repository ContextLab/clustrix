#!/usr/bin/env python3
"""
Comprehensive test runner for real cluster job submission tests.

This script runs all cluster job submission tests across different cluster types
and provides detailed reporting and validation.
"""

import os
import sys
import argparse
import subprocess
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.real_world.credential_manager import get_credential_manager
from tests.real_world.cluster_job_validator import create_validator, ClusterType


class ClusterJobTestRunner:
    """Comprehensive test runner for cluster job submission tests."""

    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.tests_dir = self.project_root / "tests" / "real_world"
        self.results_dir = self.project_root / "test_results"
        self.results_dir.mkdir(exist_ok=True)

        # Test session ID
        self.session_id = uuid.uuid4().hex[:8]
        self.session_start_time = time.time()

        # Results tracking
        self.test_results = []
        self.cluster_validators = {}

        # Credential manager
        self.credential_manager = get_credential_manager()

    def check_dependencies(self) -> bool:
        """Check if required dependencies are installed."""
        try:
            import pytest
            import clustrix

            print("✅ Required dependencies available")
            return True
        except ImportError as e:
            print(f"❌ Missing dependencies: {e}")
            print("Run: pip install -e '.[test]'")
            return False

    def check_cluster_availability(self) -> Dict[str, bool]:
        """Check availability of different cluster types."""
        print("\n🔍 Checking Cluster Availability:")

        availability = {}

        # Check SLURM
        slurm_creds = self.credential_manager.get_slurm_credentials()
        availability["slurm"] = slurm_creds is not None
        status = "✅" if availability["slurm"] else "❌"
        print(
            f"  {status} SLURM: {'Available' if availability['slurm'] else 'Not available'}"
        )

        # Check PBS (use SSH credentials)
        ssh_creds = self.credential_manager.get_ssh_credentials()
        availability["pbs"] = ssh_creds is not None
        status = "✅" if availability["pbs"] else "❌"
        print(
            f"  {status} PBS: {'Available' if availability['pbs'] else 'Not available'}"
        )

        # Check SGE (use SSH credentials)
        availability["sge"] = ssh_creds is not None
        status = "✅" if availability["sge"] else "❌"
        print(
            f"  {status} SGE: {'Available' if availability['sge'] else 'Not available'}"
        )

        # Check Kubernetes
        try:
            result = subprocess.run(
                ["kubectl", "cluster-info"], capture_output=True, text=True, timeout=10
            )
            availability["kubernetes"] = result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            availability["kubernetes"] = False

        status = "✅" if availability["kubernetes"] else "❌"
        print(
            f"  {status} Kubernetes: {'Available' if availability['kubernetes'] else 'Not available'}"
        )

        # Check SSH
        availability["ssh"] = ssh_creds is not None
        status = "✅" if availability["ssh"] else "❌"
        print(
            f"  {status} SSH: {'Available' if availability['ssh'] else 'Not available'}"
        )

        return availability

    def run_cluster_tests(
        self, cluster_type: str, test_selection: str = "basic", timeout: int = 300
    ) -> Dict[str, Any]:
        """Run tests for a specific cluster type."""
        print(f"\n🚀 Running {cluster_type.upper()} Tests ({test_selection}):")

        # Determine test file
        if cluster_type == "ssh":
            test_file = self.tests_dir / f"test_{cluster_type}_job_execution_real.py"
        else:
            test_file = self.tests_dir / f"test_{cluster_type}_job_submission_real.py"

        if not test_file.exists():
            return {
                "cluster_type": cluster_type,
                "status": "failed",
                "error": f"Test file not found: {test_file}",
                "tests_run": 0,
                "tests_passed": 0,
                "tests_failed": 0,
            }

        # Build pytest command
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-v",
            "--tb=short",
            "--maxfail=5",
            "-m",
            "real_world",
        ]

        # Add test selection markers
        if test_selection == "basic":
            cmd.extend(["-m", "not expensive"])
        elif test_selection == "expensive":
            cmd.extend(["-m", "expensive"])
        elif test_selection == "all":
            pass  # Run all tests

        # Add timeout
        cmd.extend(["--timeout", str(timeout)])

        # Run tests
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout + 60,  # Add buffer for pytest overhead
            )

            duration = time.time() - start_time

            # Parse results
            test_results = self._parse_pytest_output(result.stdout, result.stderr)

            return {
                "cluster_type": cluster_type,
                "status": "completed",
                "return_code": result.returncode,
                "duration": duration,
                "tests_run": test_results["tests_run"],
                "tests_passed": test_results["tests_passed"],
                "tests_failed": test_results["tests_failed"],
                "tests_skipped": test_results["tests_skipped"],
                "stdout": result.stdout,
                "stderr": result.stderr,
                "test_details": test_results["test_details"],
            }

        except subprocess.TimeoutExpired:
            return {
                "cluster_type": cluster_type,
                "status": "timeout",
                "error": f"Tests timed out after {timeout + 60} seconds",
                "duration": timeout + 60,
                "tests_run": 0,
                "tests_passed": 0,
                "tests_failed": 0,
            }
        except Exception as e:
            return {
                "cluster_type": cluster_type,
                "status": "error",
                "error": str(e),
                "duration": time.time() - start_time,
                "tests_run": 0,
                "tests_passed": 0,
                "tests_failed": 0,
            }

    def _parse_pytest_output(self, stdout: str, stderr: str) -> Dict[str, Any]:
        """Parse pytest output to extract test results."""
        results = {
            "tests_run": 0,
            "tests_passed": 0,
            "tests_failed": 0,
            "tests_skipped": 0,
            "test_details": [],
        }

        # Look for pytest summary line
        lines = stdout.split("\n")
        for line in lines:
            if "passed" in line and "failed" in line:
                # Parse line like: "2 failed, 8 passed, 3 skipped in 45.67s"
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "passed":
                        results["tests_passed"] = int(parts[i - 1])
                    elif part == "failed":
                        results["tests_failed"] = int(parts[i - 1])
                    elif part == "skipped":
                        results["tests_skipped"] = int(parts[i - 1])

                results["tests_run"] = (
                    results["tests_passed"]
                    + results["tests_failed"]
                    + results["tests_skipped"]
                )
                break
            elif "passed" in line and "failed" not in line:
                # Parse line like: "8 passed in 45.67s"
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == "passed":
                        results["tests_passed"] = int(parts[i - 1])
                        results["tests_run"] = results["tests_passed"]
                        break

        # Extract individual test results
        test_lines = [
            line
            for line in lines
            if "::" in line
            and ("PASSED" in line or "FAILED" in line or "SKIPPED" in line)
        ]
        for line in test_lines:
            parts = line.split("::")
            if len(parts) >= 2:
                test_name = parts[1].split()[0]
                if "PASSED" in line:
                    status = "passed"
                elif "FAILED" in line:
                    status = "failed"
                elif "SKIPPED" in line:
                    status = "skipped"
                else:
                    status = "unknown"

                results["test_details"].append(
                    {"name": test_name, "status": status, "full_line": line.strip()}
                )

        return results

    def run_all_available_tests(
        self, test_selection: str = "basic", timeout: int = 300
    ) -> Dict[str, Any]:
        """Run tests for all available cluster types."""
        print(f"\n🎯 Running All Available Cluster Tests ({test_selection}):")

        # Check cluster availability
        availability = self.check_cluster_availability()

        # Run tests for each available cluster type
        all_results = {}
        total_tests = 0
        total_passed = 0
        total_failed = 0
        total_skipped = 0

        for cluster_type, available in availability.items():
            if available:
                print(f"\n{'='*60}")
                print(f"Testing {cluster_type.upper()} Cluster")
                print(f"{'='*60}")

                result = self.run_cluster_tests(cluster_type, test_selection, timeout)
                all_results[cluster_type] = result

                total_tests += result.get("tests_run", 0)
                total_passed += result.get("tests_passed", 0)
                total_failed += result.get("tests_failed", 0)
                total_skipped += result.get("tests_skipped", 0)

                # Print results
                if result["status"] == "completed":
                    print(
                        f"✅ {cluster_type.upper()}: {result['tests_passed']} passed, {result['tests_failed']} failed, {result['tests_skipped']} skipped"
                    )
                else:
                    print(
                        f"❌ {cluster_type.upper()}: {result['status']} - {result.get('error', 'Unknown error')}"
                    )
            else:
                print(f"⏭️  Skipping {cluster_type.upper()} (not available)")
                all_results[cluster_type] = {
                    "cluster_type": cluster_type,
                    "status": "skipped",
                    "reason": "cluster not available",
                }

        # Generate summary
        summary = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "test_selection": test_selection,
            "timeout": timeout,
            "total_tests": total_tests,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_skipped": total_skipped,
            "success_rate": (
                (total_passed / total_tests * 100) if total_tests > 0 else 0
            ),
            "cluster_results": all_results,
            "cluster_availability": availability,
        }

        return summary

    def validate_job_execution(
        self,
        cluster_type: str,
        job_id: str,
        function_name: str,
        expected_output: Any = None,
    ) -> Dict[str, Any]:
        """Validate job execution using the cluster job validator."""
        if cluster_type not in self.cluster_validators:
            try:
                self.cluster_validators[cluster_type] = create_validator(cluster_type)
            except Exception as e:
                return {"status": "error", "error": f"Failed to create validator: {e}"}

        validator = self.cluster_validators[cluster_type]

        # Validate job submission
        submission_result = validator.validate_job_submission(
            job_id, function_name, "", {}
        )

        if not submission_result.success:
            return {
                "status": "failed",
                "stage": "submission",
                "result": submission_result,
            }

        # Monitor job execution
        execution_result = validator.monitor_job_execution(job_id, 300)

        if not execution_result.success:
            return {
                "status": "failed",
                "stage": "execution",
                "result": execution_result,
            }

        # Validate output
        output_result = validator.validate_job_output(job_id, expected_output)

        return {
            "status": "completed",
            "stage": "completed",
            "submission_result": submission_result,
            "execution_result": execution_result,
            "output_result": output_result,
        }

    def generate_report(
        self, results: Dict[str, Any], output_file: Optional[str] = None
    ) -> str:
        """Generate a comprehensive test report."""
        if output_file is None:
            output_file = (
                self.results_dir / f"cluster_job_test_report_{self.session_id}.json"
            )

        # Add session metadata
        report = {
            "session_info": {
                "session_id": self.session_id,
                "start_time": self.session_start_time,
                "end_time": time.time(),
                "duration": time.time() - self.session_start_time,
                "environment": {
                    "python_version": sys.version,
                    "platform": sys.platform,
                    "user": os.getenv("USER", "unknown"),
                },
            },
            "results": results,
            "credential_status": self.credential_manager.get_credential_status(),
        }

        # Write report
        with open(output_file, "w") as f:
            json.dump(report, f, indent=2, default=str)

        return str(output_file)

    def print_summary(self, results: Dict[str, Any]):
        """Print a summary of test results."""
        print("\n" + "=" * 80)
        print("CLUSTER JOB SUBMISSION TEST SUMMARY")
        print("=" * 80)

        print(f"Session ID: {results['session_id']}")
        print(f"Test Selection: {results['test_selection']}")
        print(f"Timestamp: {results['timestamp']}")

        print(f"\n📊 Overall Results:")
        print(f"  Total Tests: {results['total_tests']}")
        print(f"  Passed: {results['total_passed']}")
        print(f"  Failed: {results['total_failed']}")
        print(f"  Skipped: {results['total_skipped']}")
        print(f"  Success Rate: {results['success_rate']:.1f}%")

        print(f"\n🎯 Cluster Results:")
        for cluster_type, result in results["cluster_results"].items():
            if result["status"] == "completed":
                print(
                    f"  {cluster_type.upper()}: {result['tests_passed']} passed, {result['tests_failed']} failed, {result['tests_skipped']} skipped"
                )
            elif result["status"] == "skipped":
                print(
                    f"  {cluster_type.upper()}: Skipped ({result.get('reason', 'unknown reason')})"
                )
            else:
                print(
                    f"  {cluster_type.upper()}: {result['status']} - {result.get('error', 'Unknown error')}"
                )

        print(f"\n🔑 Cluster Availability:")
        for cluster_type, available in results["cluster_availability"].items():
            status = "✅" if available else "❌"
            print(f"  {status} {cluster_type.upper()}")

        if results["total_tests"] > 0:
            if results["success_rate"] == 100:
                print(
                    f"\n🎉 All tests passed! Cluster job submission is working correctly."
                )
            elif results["success_rate"] >= 80:
                print(f"\n✅ Most tests passed. Check individual results for details.")
            else:
                print(f"\n⚠️  Many tests failed. Review the detailed results and logs.")
        else:
            print(
                f"\n❌ No tests were run. Check cluster availability and credentials."
            )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run cluster job submission tests")
    parser.add_argument(
        "--cluster",
        choices=["slurm", "pbs", "sge", "kubernetes", "ssh", "all"],
        default="all",
        help="Cluster type to test",
    )
    parser.add_argument(
        "--tests",
        choices=["basic", "expensive", "all"],
        default="basic",
        help="Test selection",
    )
    parser.add_argument(
        "--timeout", type=int, default=300, help="Timeout per test in seconds"
    )
    parser.add_argument("--output", type=str, help="Output file for results")
    parser.add_argument(
        "--validate", action="store_true", help="Enable additional job validation"
    )
    parser.add_argument(
        "--check-only", action="store_true", help="Only check cluster availability"
    )

    args = parser.parse_args()

    # Create test runner
    runner = ClusterJobTestRunner()

    # Check dependencies
    if not runner.check_dependencies():
        sys.exit(1)

    # Check cluster availability
    availability = runner.check_cluster_availability()

    if args.check_only:
        print("\nCluster availability check completed.")
        return

    # Run tests
    if args.cluster == "all":
        results = runner.run_all_available_tests(args.tests, args.timeout)
    else:
        if not availability.get(args.cluster, False):
            print(f"❌ {args.cluster.upper()} cluster is not available")
            sys.exit(1)

        cluster_result = runner.run_cluster_tests(
            args.cluster, args.tests, args.timeout
        )
        results = {
            "session_id": runner.session_id,
            "timestamp": datetime.now().isoformat(),
            "test_selection": args.tests,
            "timeout": args.timeout,
            "total_tests": cluster_result.get("tests_run", 0),
            "total_passed": cluster_result.get("tests_passed", 0),
            "total_failed": cluster_result.get("tests_failed", 0),
            "total_skipped": cluster_result.get("tests_skipped", 0),
            "success_rate": (
                (
                    cluster_result.get("tests_passed", 0)
                    / cluster_result.get("tests_run", 1)
                    * 100
                )
                if cluster_result.get("tests_run", 0) > 0
                else 0
            ),
            "cluster_results": {args.cluster: cluster_result},
            "cluster_availability": availability,
        }

    # Generate report
    report_file = runner.generate_report(results, args.output)
    print(f"\n📄 Detailed report saved to: {report_file}")

    # Print summary
    runner.print_summary(results)

    # Exit with appropriate code
    if results["total_failed"] > 0:
        sys.exit(1)
    elif results["total_tests"] == 0:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
