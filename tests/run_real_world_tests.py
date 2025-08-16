#!/usr/bin/env python3
"""
Runner for real-world tests using actual infrastructure.

This script runs tests against real infrastructure (local or cloud)
and validates actual functionality without mocks.
"""

import sys
import os
import subprocess
import json
import time
from pathlib import Path
from typing import List, Dict, Optional
import argparse


class RealWorldTestRunner:
    """Run real-world tests with actual infrastructure."""

    def __init__(self, config_file: Optional[str] = None):
        self.test_dir = Path(__file__).parent
        self.infrastructure_dir = self.test_dir / "infrastructure"
        self.config = self.load_config(config_file)
        self.results = []

    def load_config(self, config_file: Optional[str]) -> Dict:
        """Load test configuration."""
        if config_file and Path(config_file).exists():
            with open(config_file, "r") as f:
                return json.load(f)

        # Default configuration
        default_config = self.infrastructure_dir / "test_infrastructure.json"
        if default_config.exists():
            with open(default_config, "r") as f:
                return json.load(f)

        # Fallback to environment variables
        return {
            "infrastructure": {
                "kubernetes": {"available": os.getenv("KUBECONFIG") is not None},
                "ssh": {"available": os.getenv("TEST_SSH_HOST") is not None},
                "cloud": {
                    "aws": os.getenv("AWS_ACCESS_KEY_ID") is not None,
                    "gcp": os.getenv("GOOGLE_APPLICATION_CREDENTIALS") is not None,
                    "azure": os.getenv("AZURE_SUBSCRIPTION_ID") is not None,
                },
            }
        }

    def check_infrastructure(self) -> Dict[str, bool]:
        """Check which infrastructure is available."""
        available = {}

        # Check Kubernetes
        try:
            result = subprocess.run(
                ["kubectl", "cluster-info"], capture_output=True, timeout=5
            )
            available["kubernetes"] = result.returncode == 0
        except:
            available["kubernetes"] = False

        # Check SSH
        if self.config.get("infrastructure", {}).get("ssh"):
            ssh_config = self.config["infrastructure"]["ssh"]
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-p",
                        str(ssh_config.get("port", 22)),
                        "-o",
                        "ConnectTimeout=5",
                        "-o",
                        "StrictHostKeyChecking=no",
                        f"{ssh_config['username']}@{ssh_config['host']}",
                        "echo",
                        "test",
                    ],
                    capture_output=True,
                    timeout=10,
                )
                available["ssh"] = result.returncode == 0
            except:
                available["ssh"] = False
        else:
            available["ssh"] = False

        # Check cloud providers
        available["aws"] = bool(os.getenv("AWS_ACCESS_KEY_ID"))
        available["gcp"] = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        available["azure"] = bool(os.getenv("AZURE_SUBSCRIPTION_ID"))

        # Check Docker
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            available["docker"] = result.returncode == 0
        except:
            available["docker"] = False

        return available

    def run_test_category(self, category: str, tests: List[str]) -> Dict:
        """Run a category of tests."""
        print(f"\n{'='*60}")
        print(f"Running {category} tests")
        print("=" * 60)

        category_results = {
            "category": category,
            "total": len(tests),
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "tests": [],
        }

        for test in tests:
            test_path = self.test_dir / test

            if not test_path.exists():
                print(f"⚠️  Test file not found: {test}")
                category_results["skipped"] += 1
                continue

            print(f"\n📋 Running: {test}")

            start_time = time.time()

            try:
                # Run pytest with real_world marker
                result = subprocess.run(
                    [
                        "pytest",
                        str(test_path),
                        "-v",
                        "-m",
                        "real_world",
                        "--tb=short",
                        "--no-header",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout per test file
                )

                duration = time.time() - start_time

                # Parse results
                if result.returncode == 0:
                    print(f"  ✅ PASSED ({duration:.2f}s)")
                    category_results["passed"] += 1
                    status = "passed"
                elif "SKIPPED" in result.stdout or result.returncode == 5:
                    print(f"  ⏭️  SKIPPED ({duration:.2f}s)")
                    category_results["skipped"] += 1
                    status = "skipped"
                else:
                    print(f"  ❌ FAILED ({duration:.2f}s)")
                    category_results["failed"] += 1
                    status = "failed"

                    # Show failure details
                    if result.stdout:
                        print("\n--- Test Output ---")
                        print(result.stdout[-2000:])  # Last 2000 chars
                    if result.stderr:
                        print("\n--- Error Output ---")
                        print(result.stderr[-1000:])  # Last 1000 chars

                category_results["tests"].append(
                    {"test": test, "status": status, "duration": duration}
                )

            except subprocess.TimeoutExpired:
                print(f"  ⏱️  TIMEOUT (>300s)")
                category_results["failed"] += 1
                category_results["tests"].append(
                    {"test": test, "status": "timeout", "duration": 300}
                )
            except Exception as e:
                print(f"  ❌ ERROR: {e}")
                category_results["failed"] += 1
                category_results["tests"].append(
                    {"test": test, "status": "error", "error": str(e)}
                )

        return category_results

    def run_all_tests(self, categories: Optional[List[str]] = None):
        """Run all real-world tests."""
        print("🚀 Starting Real-World Test Suite")
        print("=" * 60)

        # Check infrastructure
        print("🔍 Checking available infrastructure...")
        available = self.check_infrastructure()

        for service, is_available in available.items():
            status = "✅" if is_available else "❌"
            print(f"  {status} {service}")

        # Define test categories
        all_categories = {
            "executor": ["test_executor_real.py", "test_executor_real_standalone.py"],
            "decorator": ["test_decorator_real.py"],
            "config": ["test_config_real.py"],
            "credentials": [
                "test_secure_credentials_real.py",
                "test_auth_fallbacks_real.py",
            ],
            "cloud_providers": [
                "test_cloud_providers_gcp_real.py",
                "test_cloud_providers_aws_real.py",
                "test_cloud_providers_azure_real.py",
            ],
            "kubernetes": [
                "real_world/test_kubernetes_end_to_end_execution.py",
                "real_world/test_kubernetes_local_execution.py",
            ],
            "ssh": ["real_world/test_ssh_job_execution_real.py"],
            "notebook": ["test_notebook_magic_real.py"],
        }

        # Filter categories if specified
        if categories:
            test_categories = {
                k: v for k, v in all_categories.items() if k in categories
            }
        else:
            test_categories = all_categories

        # Run tests by category
        start_time = time.time()

        for category, tests in test_categories.items():
            # Skip cloud tests if no credentials
            if category == "cloud_providers":
                if not any([available["aws"], available["gcp"], available["azure"]]):
                    print(f"\n⏭️  Skipping {category} tests (no cloud credentials)")
                    continue

            # Skip Kubernetes tests if not available
            if category == "kubernetes" and not available["kubernetes"]:
                print(f"\n⏭️  Skipping {category} tests (Kubernetes not available)")
                continue

            # Skip SSH tests if not available
            if category == "ssh" and not available["ssh"]:
                print(f"\n⏭️  Skipping {category} tests (SSH not available)")
                continue

            # Run category tests
            results = self.run_test_category(category, tests)
            self.results.append(results)

        total_duration = time.time() - start_time

        # Generate summary
        self.print_summary(total_duration)

        # Save results
        self.save_results()

        # Return exit code
        total_failed = sum(r["failed"] for r in self.results)
        return 0 if total_failed == 0 else 1

    def print_summary(self, duration: float):
        """Print test summary."""
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)

        total_passed = sum(r["passed"] for r in self.results)
        total_failed = sum(r["failed"] for r in self.results)
        total_skipped = sum(r["skipped"] for r in self.results)
        total_tests = sum(r["total"] for r in self.results)

        print(f"\n📊 Overall Results:")
        print(f"  ✅ Passed:  {total_passed}/{total_tests}")
        print(f"  ❌ Failed:  {total_failed}/{total_tests}")
        print(f"  ⏭️  Skipped: {total_skipped}/{total_tests}")
        print(f"  ⏱️  Duration: {duration:.2f}s")

        if total_tests > 0:
            success_rate = (
                total_passed / (total_passed + total_failed) * 100
                if total_passed + total_failed > 0
                else 0
            )
            print(f"  📈 Success Rate: {success_rate:.1f}%")

        # Category breakdown
        print(f"\n📋 Category Breakdown:")
        for result in self.results:
            status = "✅" if result["failed"] == 0 else "❌"
            print(
                f"  {status} {result['category']}: "
                f"{result['passed']}/{result['total']} passed"
            )

        # Failed tests
        if total_failed > 0:
            print(f"\n❌ Failed Tests:")
            for result in self.results:
                for test in result["tests"]:
                    if test["status"] == "failed":
                        print(f"  • {test['test']}")

    def save_results(self):
        """Save test results to file."""
        results_file = self.test_dir / "real_world_test_results.json"

        with open(results_file, "w") as f:
            json.dump(
                {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "results": self.results,
                },
                f,
                indent=2,
            )

        print(f"\n💾 Results saved to {results_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run real-world tests for Clustrix")
    parser.add_argument("--config", help="Path to test configuration file")
    parser.add_argument(
        "--category",
        nargs="+",
        choices=[
            "executor",
            "decorator",
            "config",
            "credentials",
            "cloud_providers",
            "kubernetes",
            "ssh",
            "notebook",
        ],
        help="Test categories to run",
    )
    parser.add_argument(
        "--setup-infrastructure",
        action="store_true",
        help="Setup test infrastructure before running tests",
    )
    parser.add_argument(
        "--teardown-infrastructure",
        action="store_true",
        help="Teardown test infrastructure after tests",
    )

    args = parser.parse_args()

    # Setup infrastructure if requested
    if args.setup_infrastructure:
        print("🔧 Setting up test infrastructure...")
        setup_script = (
            Path(__file__).parent / "infrastructure" / "setup_test_infrastructure.py"
        )
        result = subprocess.run([sys.executable, str(setup_script), "setup"])
        if result.returncode != 0:
            print("❌ Infrastructure setup failed")
            sys.exit(1)

    # Run tests
    runner = RealWorldTestRunner(config_file=args.config)
    exit_code = runner.run_all_tests(categories=args.category)

    # Teardown infrastructure if requested
    if args.teardown_infrastructure:
        print("\n🧹 Tearing down test infrastructure...")
        setup_script = (
            Path(__file__).parent / "infrastructure" / "setup_test_infrastructure.py"
        )
        subprocess.run([sys.executable, str(setup_script), "teardown"])

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
