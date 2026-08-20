#!/usr/bin/env python3
"""
Real-world test runner for Clustrix.

This script demonstrates how to run real-world tests with different configurations.
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import Dict


def _report_failure(label: str, result: subprocess.CompletedProcess) -> None:
    """Print everything a failed pytest run produced.

    The four categories the pre-push hook runs all reported failure with an
    empty body, because only ``stdout`` was printed and a collection error
    goes to ``stderr``. An operator was told something failed and not what
    (issue #147).
    """
    print(f"\u274c {label} failed (exit {result.returncode})")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print("--- stderr ---")
        print(result.stderr)
    if not result.stdout and not result.stderr:
        print("(no output captured)")


class RealWorldTestRunner:
    """Runner for real-world tests with various configurations."""

    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.tests_dir = self.project_root / "tests"
        self.real_world_dir = self.tests_dir / "real_world"

    def check_dependencies(self) -> bool:
        """Check if required dependencies are installed."""
        try:

            print("✅ Required dependencies available")
            return True
        except ImportError as e:
            print(f"❌ Missing dependencies: {e}")
            print("Run: pip install -e '.[test]'")
            return False

    def check_credentials(self) -> Dict[str, bool]:
        """Check availability of credentials for different services."""
        try:
            # Add project root to path to ensure imports work
            import sys

            sys.path.insert(0, str(self.project_root))
            from tests.real_world.credential_manager import get_credential_manager

            manager = get_credential_manager()
            credentials = manager.get_credential_status()

            print("\n🔑 Credential Status:")
            print(
                f"  Environment: {'GitHub Actions' if manager.is_github_actions else 'Local Development'}"
            )

            service_names = {
                "ssh": "SSH",
                "slurm": "SLURM",
                "huggingface": "HuggingFace",
            }

            for service, available in credentials.items():
                display_name = service_names.get(service, service.upper())
                status = "✅" if available else "❌"
                print(f"  {status} {display_name}")

            return credentials

        except ImportError:
            # Fall back to environment variables
            credentials = {
                "aws": all(
                    [os.getenv("TEST_AWS_ACCESS_KEY"), os.getenv("TEST_AWS_SECRET_KEY")]
                ),
                "azure": bool(os.getenv("TEST_AZURE_SUBSCRIPTION_ID")),
                "gcp": bool(os.getenv("TEST_GCP_PROJECT_ID")),
                "ssh": bool(os.getenv("TEST_SSH_HOST", "localhost")),
            }

            print("\n🔑 Credential Status:")
            for service, available in credentials.items():
                status = "✅" if available else "❌"
                print(f"  {status} {service.upper()}")

            return credentials

    def run_unit_tests(self) -> bool:
        """Run traditional unit tests."""
        print("\n🧪 Running Unit Tests...")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(self.tests_dir),
            "-m",
            "not real_world and not expensive and not visual",
            "-v",
            "--tb=short",
            "--maxfail=5",
        ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ Unit tests passed")
                return True
            else:
                _report_failure("Unit tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running unit tests: {e}")
            return False

    def run_filesystem_tests(self) -> bool:
        """Run real-world filesystem tests."""
        print("\n📁 Running Filesystem Tests...")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(self.real_world_dir / "test_filesystem_real.py"),
            "-v",
            "--tb=short",
        ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ Filesystem tests passed")
                return True
            else:
                _report_failure("Filesystem tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running filesystem tests: {e}")
            return False

    def run_ssh_tests(self) -> bool:
        """Run real-world SSH tests."""
        print("\n🔐 Running SSH Tests...")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(self.real_world_dir / "test_ssh_real.py"),
            "-v",
            "--tb=short",
        ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ SSH tests passed")
                return True
            else:
                _report_failure("SSH tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running SSH tests: {e}")
            return False

    def run_api_tests(self, include_expensive: bool = False) -> bool:
        """Run real-world API tests."""
        print("\n🌐 Running API Tests...")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(self.real_world_dir / "test_cloud_apis_real.py"),
            "-v",
            "--tb=short",
        ]

        if include_expensive:
            cmd.append("--run-expensive")

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ API tests passed")
                return True
            else:
                _report_failure("API tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running API tests: {e}")
            return False

    def run_visual_tests(self) -> bool:
        """Run visual verification tests."""
        print("\n🎨 Running Visual Tests...")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(self.real_world_dir / "test_visual_verification.py"),
            "-v",
            "--tb=short",
            "--run-visual",
        ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ Visual tests passed")
                print(f"📸 Check screenshots in: {self.real_world_dir / 'screenshots'}")
                return True
            else:
                _report_failure("Visual tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running visual tests: {e}")
            return False

    def run_hybrid_tests(self) -> bool:
        """Run hybrid tests."""
        print("\n🔄 Running Hybrid Tests...")
        # Find hybrid test files
        hybrid_files = list(self.tests_dir.glob("test_*_hybrid.py"))
        if not hybrid_files:
            print("❌ No hybrid test files found")
            return False

        cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"] + [
            str(f) for f in hybrid_files
        ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ Hybrid tests passed")
                return True
            else:
                _report_failure("Hybrid tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running hybrid tests: {e}")
            return False

    def run_all_tests(
        self, include_expensive: bool = False, include_visual: bool = False
    ) -> bool:
        """Run all real-world tests."""
        print("\n🚀 Running All Real-World Tests...")

        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(self.real_world_dir),
            "-v",
            "--tb=short",
        ]

        if include_expensive:
            cmd.append("--run-expensive")

        if include_visual:
            cmd.append("--run-visual")

        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print("✅ All tests passed")
                return True
            else:
                _report_failure("Tests", result)
                return False
        except Exception as e:
            print(f"❌ Error running tests: {e}")
            return False

    def run_demo(self) -> None:
        """Run a demo of real-world testing capabilities."""
        print("🎭 Real-World Testing Demo")
        print("=" * 50)

        # Check system
        if not self.check_dependencies():
            return

        credentials = self.check_credentials()

        # Run tests progressively
        tests_to_run = [
            ("Unit Tests", self.run_unit_tests),
            ("Filesystem Tests", self.run_filesystem_tests),
            ("Hybrid Tests", self.run_hybrid_tests),
        ]

        if credentials.get("ssh", False):
            tests_to_run.append(("SSH Tests", self.run_ssh_tests))

        if any(credentials.values()):
            tests_to_run.append(
                ("API Tests", lambda: self.run_api_tests(include_expensive=False))
            )

        tests_to_run.append(("Visual Tests", self.run_visual_tests))

        # Run each test category
        results = []
        for test_name, test_func in tests_to_run:
            try:
                success = test_func()
                results.append((test_name, success))
            except Exception as e:
                print(f"❌ {test_name} failed with error: {e}")
                results.append((test_name, False))

        # Print summary
        print("\n📊 Test Results Summary:")
        print("=" * 50)
        for test_name, success in results:
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"  {status} {test_name}")

        passed = sum(1 for _, success in results if success)
        total = len(results)
        print(f"\nTotal: {passed}/{total} test categories passed")

        if passed == total:
            print("🎉 All real-world tests completed successfully!")
        else:
            print("⚠️  Some tests failed. Check logs for details.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run real-world tests for Clustrix")
    parser.add_argument("--unit", action="store_true", help="Run unit tests")
    parser.add_argument(
        "--filesystem", action="store_true", help="Run filesystem tests"
    )
    parser.add_argument("--ssh", action="store_true", help="Run SSH tests")
    parser.add_argument("--api", action="store_true", help="Run API tests")
    parser.add_argument("--visual", action="store_true", help="Run visual tests")
    parser.add_argument("--hybrid", action="store_true", help="Run hybrid tests")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument(
        "--expensive", action="store_true", help="Include expensive tests"
    )
    parser.add_argument("--demo", action="store_true", help="Run demo")
    parser.add_argument(
        "--check-creds", action="store_true", help="Check credentials only"
    )

    args = parser.parse_args()

    runner = RealWorldTestRunner()

    if args.check_creds:
        runner.check_credentials()
        return

    if args.demo:
        runner.run_demo()
        return

    # Every return value below is collected. Dropping them is what made the
    # pre-push hook incapable of blocking a push (issue #147): each category
    # printed "failed" and the script still exited 0, so the hook's
    # `if ! python scripts/run_real_world_tests.py --filesystem` never fired.
    outcomes = []

    if args.unit:
        outcomes.append(runner.run_unit_tests())

    if args.filesystem:
        outcomes.append(runner.run_filesystem_tests())

    if args.ssh:
        outcomes.append(runner.run_ssh_tests())

    if args.api:
        outcomes.append(runner.run_api_tests(include_expensive=args.expensive))

    if args.visual:
        outcomes.append(runner.run_visual_tests())

    if args.hybrid:
        outcomes.append(runner.run_hybrid_tests())

    if args.all:
        outcomes.append(
            runner.run_all_tests(include_expensive=args.expensive, include_visual=True)
        )

    if outcomes and not all(outcomes):
        sys.exit(1)

    if not any(
        [
            args.unit,
            args.filesystem,
            args.ssh,
            args.api,
            args.visual,
            args.hybrid,
            args.all,
        ]
    ):
        print("No specific test category selected. Running demo...")
        runner.run_demo()


if __name__ == "__main__":
    main()
