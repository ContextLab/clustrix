#!/usr/bin/env python3
"""
Real-world test runner for Clustrix.

Runs the suites under ``tests/real_world`` one category at a time and exits
non-zero when any category fails. Every category names files that exist:

``--filesystem``
    ``test_filesystem_real.py`` -- the filesystem helpers on this machine.
``--ssh``
    ``test_local_sshd_slurm.py`` -- ``@cluster`` against a real sshd and a
    real SLURM controller. Skipped by pytest, with the reason, unless
    ``CLUSTRIX_TEST_SSH_HOST``, ``CLUSTRIX_TEST_SSH_USER`` and
    ``CLUSTRIX_TEST_SSH_KEY`` name one (``scripts/setup_local_slurm.sh``
    creates it).
``--all``
    everything under ``tests/real_world``.
``--unit``
    everything outside ``tests/real_world``; what CI runs.
"""

import sys
import argparse
import subprocess
from pathlib import Path
from typing import Dict, List


def _report_failure(label: str, result: subprocess.CompletedProcess) -> None:
    """Print everything a failed pytest run produced.

    The four categories the pre-push hook runs all reported failure with an
    empty body, because only ``stdout`` was printed and a collection error
    goes to ``stderr``. An operator was told something failed and not what
    (issue #147).
    """
    print(f"❌ {label} failed (exit {result.returncode})")
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

    def check_credentials(self) -> Dict[str, bool]:
        """Report which credentials the real-world suite can find."""
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

    def _run_pytest(self, label: str, pytest_args: List[str]) -> bool:
        """Run pytest with `pytest_args`; report and return whether it passed."""
        cmd = [sys.executable, "-m", "pytest", *pytest_args, "-v", "--tb=short"]
        try:
            result = subprocess.run(
                cmd, cwd=self.project_root, capture_output=True, text=True
            )
        except Exception as e:
            print(f"❌ Error running {label}: {e}")
            return False
        if result.returncode == 0:
            print(f"✅ {label} passed")
            return True
        _report_failure(label, result)
        return False

    def run_unit_tests(self) -> bool:
        """Run everything outside tests/real_world."""
        print("\n🧪 Running Unit Tests...")
        return self._run_pytest(
            "Unit tests",
            [
                str(self.tests_dir),
                "-m",
                "not real_world",
                f"--ignore={self.real_world_dir}",
                "--maxfail=5",
            ],
        )

    def run_filesystem_tests(self) -> bool:
        """Run real-world filesystem tests."""
        print("\n📁 Running Filesystem Tests...")
        return self._run_pytest(
            "Filesystem tests", [str(self.real_world_dir / "test_filesystem_real.py")]
        )

    def run_ssh_tests(self) -> bool:
        """Run @cluster against the real sshd and SLURM the environment names."""
        print("\n🔐 Running SSH Tests...")
        return self._run_pytest(
            "SSH tests", [str(self.real_world_dir / "test_local_sshd_slurm.py")]
        )

    def run_all_tests(self) -> bool:
        """Run all real-world tests."""
        print("\n🚀 Running All Real-World Tests...")
        return self._run_pytest("All tests", [str(self.real_world_dir)])


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run real-world tests for Clustrix")
    parser.add_argument("--unit", action="store_true", help="Run unit tests")
    parser.add_argument(
        "--filesystem", action="store_true", help="Run filesystem tests"
    )
    parser.add_argument("--ssh", action="store_true", help="Run SSH and SLURM tests")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument(
        "--check-creds", action="store_true", help="Check credentials only"
    )

    args = parser.parse_args()

    runner = RealWorldTestRunner()

    if args.check_creds:
        runner.check_credentials()
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

    if args.all:
        outcomes.append(runner.run_all_tests())

    if outcomes and not all(outcomes):
        sys.exit(1)

    if not outcomes:
        parser.print_help()


if __name__ == "__main__":
    main()
