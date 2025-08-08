#!/usr/bin/env python3
"""Test runner script for field mapping validation tests.

This script runs the comprehensive field mapping validation tests with
real cloud provider APIs to verify that Issue #59 fixes are working correctly.

Usage:
    python scripts/test_field_mapping_fixes.py [--provider PROVIDER] [--verbose]
    
Examples:
    python scripts/test_field_mapping_fixes.py                    # Run all tests
    python scripts/test_field_mapping_fixes.py --provider aws     # Test only AWS
    python scripts/test_field_mapping_fixes.py --verbose          # Verbose output
"""

import argparse
import os
import sys
import subprocess
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_credentials():
    """Check which cloud provider credentials are available."""
    available = {}
    
    # Check AWS
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        available["AWS"] = True
        logger.info("✅ AWS credentials available")
    else:
        available["AWS"] = False
        logger.warning("⚠️  AWS credentials not found (set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)")
    
    # Check Azure
    azure_vars = ["AZURE_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"]
    if all(os.environ.get(var) for var in azure_vars):
        available["Azure"] = True
        logger.info("✅ Azure credentials available")
    else:
        available["Azure"] = False
        logger.warning(f"⚠️  Azure credentials not found (set {', '.join(azure_vars)})")
    
    # Check GCP
    if os.environ.get("GCP_PROJECT_ID") and os.environ.get("GCP_SERVICE_ACCOUNT_KEY"):
        available["GCP"] = True
        logger.info("✅ GCP credentials available")
    else:
        available["GCP"] = False
        logger.warning("⚠️  GCP credentials not found (set GCP_PROJECT_ID, GCP_SERVICE_ACCOUNT_KEY)")
    
    # Check HuggingFace
    if os.environ.get("HF_TOKEN"):
        available["HuggingFace"] = True
        logger.info("✅ HuggingFace credentials available")
    else:
        available["HuggingFace"] = False
        logger.warning("⚠️  HuggingFace credentials not found (set HF_TOKEN)")
    
    return available


def run_field_mapping_tests(provider_filter=None, verbose=False):
    """Run the field mapping validation tests."""
    # Get project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    test_file = project_root / "tests" / "real_world" / "test_field_mapping_validation.py"
    
    if not test_file.exists():
        logger.error(f"Test file not found: {test_file}")
        return False
    
    # Build pytest command
    cmd = ["python", "-m", "pytest", str(test_file), "-m", "real_world"]
    
    if verbose:
        cmd.extend(["-v", "-s"])
    else:
        cmd.append("-q")
    
    # Add specific test filter if requested
    if provider_filter:
        provider_lower = provider_filter.lower()
        if provider_lower == "aws":
            cmd.append("-k")
            cmd.append("aws")
        elif provider_lower == "azure":
            cmd.append("-k")
            cmd.append("azure")
        elif provider_lower == "gcp":
            cmd.append("-k")
            cmd.append("gcp")
        elif provider_lower == "huggingface" or provider_lower == "hf":
            cmd.append("-k")
            cmd.append("huggingface")
        elif provider_lower == "lambda":
            cmd.append("-k")
            cmd.append("lambda")
        else:
            logger.error(f"Unknown provider: {provider_filter}")
            return False
    
    logger.info(f"Running command: {' '.join(cmd)}")
    
    # Run tests
    try:
        result = subprocess.run(cmd, cwd=project_root, capture_output=False)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Failed to run tests: {e}")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test field mapping validation fixes for Issue #59",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                         # Run all field mapping tests
  %(prog)s --provider aws          # Test only AWS field mapping
  %(prog)s --provider azure        # Test only Azure field mapping
  %(prog)s --provider gcp          # Test only GCP field mapping
  %(prog)s --provider huggingface  # Test only HuggingFace field mapping
  %(prog)s --verbose               # Run with verbose output
        """)
    
    parser.add_argument(
        "--provider",
        help="Test only specified provider (aws, azure, gcp, huggingface, lambda)",
        choices=["aws", "azure", "gcp", "huggingface", "hf", "lambda"]
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose test output"
    )
    
    args = parser.parse_args()
    
    logger.info("🧪 Field Mapping Validation Test Runner")
    logger.info("=" * 50)
    
    # Check credentials
    logger.info("Checking available credentials...")
    available_creds = check_credentials()
    
    available_count = sum(1 for available in available_creds.values() if available)
    if available_count == 0:
        logger.error("❌ No cloud provider credentials found!")
        logger.error("Please set credentials for at least one provider to run tests.")
        logger.error("See test file docstring for required environment variables.")
        return 1
    
    logger.info(f"Found credentials for {available_count} provider(s)")
    logger.info("")
    
    # Run tests
    logger.info("Running field mapping validation tests...")
    success = run_field_mapping_tests(args.provider, args.verbose)
    
    if success:
        logger.info("🎉 All field mapping tests passed!")
        logger.info("Issue #59 field mapping fixes are working correctly.")
        return 0
    else:
        logger.error("❌ Some field mapping tests failed!")
        logger.error("Check the output above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())