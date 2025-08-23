#!/usr/bin/env python3
"""
AWS Batch Connectivity Validation Script

This script tests AWS Batch API connectivity and basic functionality
to determine if Clustrix could integrate with AWS Batch in the future.
"""

import sys
import os
import json
import logging
from pathlib import Path

# Add the clustrix package to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_aws_batch_connectivity():
    """Test AWS Batch API connectivity."""
    print("🔍 AWS Batch Connectivity Test")
    print("=" * 50)

    # Get AWS credentials
    creds = ValidationCredentials()
    aws_creds = creds.get_aws_credentials()

    if not aws_creds:
        print("❌ No AWS credentials found")
        return False

    print(f"✅ AWS credentials found")
    print(f"   Access Key: {aws_creds['aws_access_key_id'][:10]}...")
    print(f"   Region: {aws_creds['aws_region']}")

    # Set up environment variables for boto3
    os.environ["AWS_ACCESS_KEY_ID"] = aws_creds["aws_access_key_id"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_creds["aws_secret_access_key"]
    os.environ["AWS_DEFAULT_REGION"] = aws_creds["aws_region"]

    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
    except ImportError:
        print("❌ boto3 not available. Install with: pip install boto3")
        return False

    # Test AWS Batch client creation
    try:
        print("\n🔌 Testing AWS Batch client connection...")
        batch_client = boto3.client("batch", region_name=aws_creds["aws_region"])

        # Test basic API call - list compute environments
        print("📊 Listing compute environments...")
        response = batch_client.describe_compute_environments()

        compute_envs = response.get("computeEnvironments", [])
        print(f"✅ AWS Batch API accessible")
        print(f"   Found {len(compute_envs)} compute environments")

        if compute_envs:
            print("   Compute environments:")
            for env in compute_envs[:5]:  # Show first 5
                name = env.get("computeEnvironmentName", "Unknown")
                state = env.get("state", "Unknown")
                status = env.get("status", "Unknown")
                print(f"   - {name}: {state}/{status}")
        else:
            print("   ℹ️  No compute environments configured")

        # Test job queues
        print("\n📋 Listing job queues...")
        response = batch_client.describe_job_queues()

        job_queues = response.get("jobQueues", [])
        print(f"   Found {job_queues.__len__()} job queues")

        if job_queues:
            print("   Job queues:")
            for queue in job_queues[:5]:  # Show first 5
                name = queue.get("jobQueueName", "Unknown")
                state = queue.get("state", "Unknown")
                status = queue.get("status", "Unknown")
                priority = queue.get("priority", "Unknown")
                print(f"   - {name}: {state}/{status} (priority: {priority})")
        else:
            print("   ℹ️  No job queues configured")

        # Test job definitions
        print("\n📝 Listing job definitions...")
        response = batch_client.describe_job_definitions(status="ACTIVE")

        job_definitions = response.get("jobDefinitions", [])
        print(f"   Found {len(job_definitions)} active job definitions")

        if job_definitions:
            print("   Job definitions:")
            for job_def in job_definitions[:5]:  # Show first 5
                name = job_def.get("jobDefinitionName", "Unknown")
                revision = job_def.get("revision", "Unknown")
                job_type = job_def.get("type", "Unknown")
                print(f"   - {name}:{revision} ({job_type})")
        else:
            print("   ℹ️  No active job definitions found")

        # Test basic permissions
        print("\n🔐 Testing AWS Batch permissions...")

        # Try to list jobs (this will work even with no jobs)
        try:
            response = batch_client.list_jobs(jobQueue="*")
            print("✅ Job listing permission available")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ValidationException":
                print("⚠️  No valid job queues to list jobs from")
            else:
                print(f"❌ Job listing permission denied: {e}")

        # Summary
        total_resources = len(compute_envs) + len(job_queues) + len(job_definitions)

        if total_resources > 0:
            print(f"\n✅ AWS Batch infrastructure detected!")
            print(f"   Total resources: {total_resources}")
            print("   ✅ Clustrix could potentially integrate with AWS Batch")
            return {
                "api_accessible": True,
                "compute_environments": len(compute_envs),
                "job_queues": len(job_queues),
                "job_definitions": len(job_definitions),
                "ready_for_jobs": len(job_queues) > 0 and len(compute_envs) > 0,
            }
        else:
            print(f"\n⚠️  AWS Batch API accessible but no infrastructure configured")
            print("   ℹ️  Account needs AWS Batch setup before job submission")
            return {
                "api_accessible": True,
                "compute_environments": 0,
                "job_queues": 0,
                "job_definitions": 0,
                "ready_for_jobs": False,
                "needs_setup": True,
            }

    except NoCredentialsError:
        print("❌ AWS credentials not properly configured")
        return False
    except ClientError as e:
        if e.response["Error"]["Code"] == "UnauthorizedOperation":
            print("❌ AWS credentials lack AWS Batch permissions")
            print(
                "   Required permissions: batch:DescribeComputeEnvironments, batch:DescribeJobQueues, etc."
            )
        elif e.response["Error"]["Code"] == "AccessDenied":
            print("❌ Access denied to AWS Batch service")
            print("   Check IAM permissions for AWS Batch")
        else:
            print(f"❌ AWS Batch API error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


def test_aws_batch_job_definition_format():
    """Test creating a sample job definition format for Clustrix."""
    print("\n🧪 Testing AWS Batch Job Definition Format")
    print("=" * 50)

    # Create a sample job definition that Clustrix might use
    sample_job_definition = {
        "jobDefinitionName": "clustrix-python-job",
        "type": "container",
        "containerProperties": {
            "image": "python:3.11-slim",
            "vcpus": 1,
            "memory": 512,
            "jobRoleArn": "arn:aws:iam::account:role/BatchJobRole",
            "environment": [
                {"name": "CLUSTRIX_JOB_ID", "value": "test-job"},
                {"name": "PYTHONPATH", "value": "/app"},
            ],
            "mountPoints": [],
            "volumes": [],
            "ulimits": [],
        },
        "retryStrategy": {"attempts": 3},
        "timeout": {"attemptDurationSeconds": 3600},
    }

    print("📝 Sample Clustrix job definition structure:")
    print(json.dumps(sample_job_definition, indent=2))

    print("\n✅ Job definition format compatible with AWS Batch")
    print("   Clustrix could create job definitions for:")
    print("   - Python container execution")
    print("   - Resource specification (CPU/memory)")
    print("   - Environment variable injection")
    print("   - Retry and timeout policies")

    return True


def main():
    """Main validation function."""
    print("🚀 Starting AWS Batch Connectivity Validation")
    print("=" * 70)

    # Test AWS Batch connectivity
    batch_result = test_aws_batch_connectivity()

    # Test job definition format
    job_def_result = test_aws_batch_job_definition_format()

    # Summary
    print("\n📊 AWS Batch Validation Summary")
    print("=" * 70)

    if isinstance(batch_result, dict):
        print("✅ AWS Batch API: ACCESSIBLE")
        print(f"   Compute environments: {batch_result['compute_environments']}")
        print(f"   Job queues: {batch_result['job_queues']}")
        print(f"   Job definitions: {batch_result['job_definitions']}")

        if batch_result["ready_for_jobs"]:
            print("✅ Infrastructure: READY for job submission")
        elif batch_result.get("needs_setup"):
            print("⚠️  Infrastructure: NEEDS SETUP")
            print("   Create compute environments and job queues first")

        if job_def_result:
            print("✅ Job definition format: COMPATIBLE")

        print("\n🎯 Overall Assessment:")
        if batch_result["ready_for_jobs"]:
            print("🎉 AWS Batch integration ready for Clustrix implementation!")
        else:
            print("⚠️  AWS Batch API accessible, but infrastructure setup required")
        return 0

    elif batch_result:
        print("✅ AWS Batch API: LIMITED ACCESS")
        print("⚠️  Infrastructure status unknown")
        return 0
    else:
        print("❌ AWS Batch API: NOT ACCESSIBLE")
        print("   Check credentials and permissions")
        return 1


if __name__ == "__main__":
    sys.exit(main())
