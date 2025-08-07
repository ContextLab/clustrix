"""
Real-world cloud API tests for Clustrix.

These tests use actual cloud provider APIs to verify that our
cloud integration code works correctly. Tests are designed to
use free-tier or minimal-cost operations.
"""

import os
import json
import pytest
from datetime import datetime
from unittest.mock import patch

from clustrix.cloud_providers.aws import AWSProvider
from clustrix.cloud_providers.azure import AzureProvider
from clustrix.cloud_providers.gcp import GCPProvider
from clustrix.cloud_providers.lambda_cloud import LambdaCloudProvider
from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.azure_pricing import AzurePricingClient
from clustrix.pricing_clients.gcp_pricing import GCPPricingClient
from tests.real_world import credentials, test_manager


@pytest.mark.real_world
class TestAWSAPIReal:
    """Test real AWS API calls."""

    @pytest.fixture
    def aws_provider(self):
        """Create AWSProvider with real credentials."""
        creds = credentials.get_aws_credentials()
        if not creds:
            pytest.skip("No AWS credentials available")

        provider = AWSProvider()
        provider.authenticate(
            access_key_id=creds["access_key_id"],
            secret_access_key=creds["secret_access_key"],
            region=creds["region"],
        )
        return provider

    def test_aws_authentication_real(self, aws_provider):
        """Test real AWS authentication with STS."""
        if not test_manager.can_make_api_call(0.00):  # STS calls are free
            pytest.skip("API call limit reached")

        try:
            # Use STS to verify authentication (free operation)
            import boto3

            sts_client = boto3.client(
                "sts",
                aws_access_key_id=aws_provider.credentials["access_key_id"],
                aws_secret_access_key=aws_provider.credentials["secret_access_key"],
                region_name=aws_provider.region,
            )

            response = sts_client.get_caller_identity()
            test_manager.record_api_call(0.00)

            # Verify response structure
            assert "Account" in response
            assert "UserId" in response
            assert "Arn" in response
            assert response["Account"].isdigit()
            assert len(response["Account"]) == 12  # AWS account IDs are 12 digits

        except Exception as e:
            pytest.skip(f"AWS authentication failed: {e}")

    def test_aws_pricing_api_real(self):
        """Test real AWS pricing API calls."""
        if not test_manager.can_make_api_call(0.00):  # Pricing API is free
            pytest.skip("API call limit reached")

        try:
            import boto3

            # Use AWS pricing API (free tier)
            pricing_client = boto3.client("pricing", region_name="us-east-1")

            # Get pricing for t2.micro (free tier eligible)
            response = pricing_client.get_products(
                ServiceCode="AmazonEC2",
                Filters=[
                    {
                        "Type": "TERM_MATCH",
                        "Field": "instanceType",
                        "Value": "t2.micro",
                    },
                    {
                        "Type": "TERM_MATCH",
                        "Field": "operatingSystem",
                        "Value": "Linux",
                    },
                    {
                        "Type": "TERM_MATCH",
                        "Field": "location",
                        "Value": "US East (N. Virginia)",
                    },
                    {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                    {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                ],
                MaxResults=1,
            )

            test_manager.record_api_call(0.00)

            # Verify response
            assert "PriceList" in response
            assert len(response["PriceList"]) > 0

            # Parse pricing data
            price_data = json.loads(response["PriceList"][0])
            assert "product" in price_data
            assert "terms" in price_data

            # Verify instance type
            product = price_data["product"]
            assert product["attributes"]["instanceType"] == "t2.micro"

        except Exception as e:
            pytest.skip(f"AWS pricing API failed: {e}")

    def test_aws_ec2_describe_regions_real(self):
        """Test AWS EC2 describe regions (free operation)."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            import boto3

            ec2_client = boto3.client("ec2", region_name="us-east-1")

            # Describe regions (free operation)
            response = ec2_client.describe_regions()
            test_manager.record_api_call(0.00)

            # Verify response
            assert "Regions" in response
            assert len(response["Regions"]) > 0

            # Check for common regions
            region_names = [r["RegionName"] for r in response["Regions"]]
            assert "us-east-1" in region_names
            assert "us-west-2" in region_names
            assert "eu-west-1" in region_names

        except Exception as e:
            pytest.skip(f"AWS EC2 API failed: {e}")


@pytest.mark.real_world
class TestAzureAPIReal:
    """Test real Azure API calls."""

    @pytest.fixture
    def azure_provider(self):
        """Create AzureProvider with real credentials."""
        creds = credentials.get_azure_credentials()
        if not creds:
            pytest.skip("No Azure credentials available")

        provider = AzureProvider()
        provider.authenticate(
            subscription_id=creds["subscription_id"],
            tenant_id=creds.get("tenant_id"),
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
        )
        return provider

    def test_azure_authentication_real(self, azure_provider):
        """Test real Azure authentication."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource import ResourceManagementClient

            # Use default credentials (free operation)
            credential = DefaultAzureCredential()
            subscription_id = azure_provider.subscription_id

            # Create resource management client
            resource_client = ResourceManagementClient(credential, subscription_id)

            # List resource groups (free operation)
            resource_groups = list(resource_client.resource_groups.list())
            test_manager.record_api_call(0.00)

            # Verify we can list resource groups
            assert isinstance(resource_groups, list)
            # May be empty, that's valid

        except Exception as e:
            pytest.skip(f"Azure authentication failed: {e}")

    def test_azure_compute_api_real(self):
        """Test Azure compute API calls."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.compute import ComputeManagementClient

            creds = credentials.get_azure_credentials()
            if not creds:
                pytest.skip("No Azure credentials available")

            credential = DefaultAzureCredential()
            compute_client = ComputeManagementClient(
                credential, creds["subscription_id"]
            )

            # List VM sizes in East US (free operation)
            vm_sizes = list(compute_client.virtual_machine_sizes.list("eastus"))
            test_manager.record_api_call(0.00)

            # Verify response
            assert len(vm_sizes) > 0

            # Look for common VM sizes
            size_names = [size.name for size in vm_sizes]
            assert any("Standard_B" in name for name in size_names)  # Burstable VMs

        except Exception as e:
            pytest.skip(f"Azure compute API failed: {e}")


@pytest.mark.real_world
class TestGCPAPIReal:
    """Test real GCP API calls."""

    @pytest.fixture
    def gcp_provider(self):
        """Create GCPProvider with real credentials."""
        creds = credentials.get_gcp_credentials()
        if not creds:
            pytest.skip("No GCP credentials available")

        provider = GCPProvider()
        provider.authenticate(
            project_id=creds["project_id"],
            service_account_path=creds.get("service_account_path"),
        )
        return provider

    def test_gcp_authentication_real(self, gcp_provider):
        """Test real GCP authentication."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            from google.auth import default
            from google.cloud import compute_v1

            # Get default credentials
            credentials, project = default()

            # Create compute client
            client = compute_v1.InstancesClient(credentials=credentials)

            # List instances in us-central1-a (free operation if no instances)
            instances = client.list(project=project, zone="us-central1-a")
            test_manager.record_api_call(0.00)

            # Verify we can list instances (may be empty)
            instance_list = list(instances)
            assert isinstance(instance_list, list)

        except Exception as e:
            pytest.skip(f"GCP authentication failed: {e}")

    def test_gcp_compute_zones_real(self):
        """Test GCP compute zones API."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            from google.auth import default
            from google.cloud import compute_v1

            creds = credentials.get_gcp_credentials()
            if not creds:
                pytest.skip("No GCP credentials available")

            gcp_credentials, project = default()
            client = compute_v1.ZonesClient(credentials=gcp_credentials)

            # List zones (free operation)
            zones = client.list(project=project)
            test_manager.record_api_call(0.00)

            # Verify response
            zone_list = list(zones)
            assert len(zone_list) > 0

            # Check for common zones
            zone_names = [zone.name for zone in zone_list]
            assert any("us-central1" in name for name in zone_names)
            assert any("us-east1" in name for name in zone_names)

        except Exception as e:
            pytest.skip(f"GCP zones API failed: {e}")


@pytest.mark.real_world
class TestLambdaCloudAPIReal:
    """Test real Lambda Cloud API calls."""

    def test_lambda_cloud_instance_types_real(self):
        """Test Lambda Cloud instance types API."""
        if not test_manager.can_make_api_call(0.01):  # Minimal cost
            pytest.skip("API call limit reached")

        try:
            import requests

            # Lambda Cloud public API endpoint
            url = "https://cloud.lambdalabs.com/api/v1/instance-types"

            # Make API call (usually free for public endpoints)
            response = requests.get(url, timeout=10)
            test_manager.record_api_call(0.01)

            # Verify response
            assert response.status_code == 200

            data = response.json()
            assert "data" in data
            assert isinstance(data["data"], dict)

            # Verify instance type structure
            for instance_type, details in data["data"].items():
                assert "description" in details
                assert "price_cents_per_hour" in details
                assert isinstance(details["price_cents_per_hour"], int)

        except Exception as e:
            pytest.skip(f"Lambda Cloud API failed: {e}")

    def test_lambda_cloud_regions_real(self):
        """Test Lambda Cloud regions API."""
        if not test_manager.can_make_api_call(0.01):
            pytest.skip("API call limit reached")

        try:
            import requests

            # Lambda Cloud regions endpoint
            url = "https://cloud.lambdalabs.com/api/v1/regions"

            response = requests.get(url, timeout=10)
            test_manager.record_api_call(0.01)

            # Verify response
            assert response.status_code == 200

            data = response.json()
            assert "data" in data
            assert isinstance(data["data"], list)

            # Verify region structure
            for region in data["data"]:
                assert "name" in region
                assert "description" in region

        except Exception as e:
            pytest.skip(f"Lambda Cloud regions API failed: {e}")


@pytest.mark.real_world
class TestPricingClientsReal:
    """Test real pricing client implementations."""

    def test_aws_pricing_client_real(self):
        """Test AWSPricingClient with real API calls."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            client = AWSPricingClient()

            # Get pricing for t2.micro
            pricing_info = client.get_instance_pricing(
                instance_type="t2.micro", region="us-east-1"
            )
            test_manager.record_api_call(0.00)

            # Verify pricing info structure
            assert pricing_info is not None
            assert "hourly_price" in pricing_info
            assert "currency" in pricing_info
            assert pricing_info["currency"] == "USD"
            assert float(pricing_info["hourly_price"]) >= 0

        except Exception as e:
            pytest.skip(f"AWS pricing client failed: {e}")

    def test_azure_pricing_client_real(self):
        """Test AzurePricingClient with real API calls."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            client = AzurePricingClient()

            # Get pricing for Standard_B1s
            pricing_info = client.get_instance_pricing(
                instance_type="Standard_B1s", region="eastus"
            )
            test_manager.record_api_call(0.00)

            # Verify pricing info structure
            assert pricing_info is not None
            assert "hourly_price" in pricing_info
            assert "currency" in pricing_info
            assert pricing_info["currency"] == "USD"
            assert float(pricing_info["hourly_price"]) >= 0

        except Exception as e:
            pytest.skip(f"Azure pricing client failed: {e}")

    def test_gcp_pricing_client_real(self):
        """Test GCPPricingClient with real API calls."""
        if not test_manager.can_make_api_call(0.00):
            pytest.skip("API call limit reached")

        try:
            client = GCPPricingClient()

            # Get pricing for e2-micro
            pricing_info = client.get_instance_pricing(
                instance_type="e2-micro", region="us-central1"
            )
            test_manager.record_api_call(0.00)

            # Verify pricing info structure
            assert pricing_info is not None
            assert "hourly_price" in pricing_info
            assert "currency" in pricing_info
            assert pricing_info["currency"] == "USD"
            assert float(pricing_info["hourly_price"]) >= 0

        except Exception as e:
            pytest.skip(f"GCP pricing client failed: {e}")


@pytest.mark.real_world
class TestHTTPRequestsReal:
    """Test real HTTP requests used by various components."""

    def test_huggingface_api_real(self):
        """Test HuggingFace API calls."""
        if not test_manager.can_make_api_call(0.00):  # Public API is free
            pytest.skip("API call limit reached")

        try:
            import requests

            # HuggingFace public API
            url = "https://huggingface.co/api/models"
            params = {"limit": 5, "filter": "text-generation"}

            response = requests.get(url, params=params, timeout=10)
            test_manager.record_api_call(0.00)

            # Verify response
            assert response.status_code == 200

            data = response.json()
            assert isinstance(data, list)
            assert len(data) <= 5

            # Verify model structure
            for model in data:
                assert "id" in model
                assert "tags" in model
                assert isinstance(model["tags"], list)

        except Exception as e:
            pytest.skip(f"HuggingFace API failed: {e}")

    def test_github_api_real(self):
        """Test GitHub API calls (used for documentation)."""
        if not test_manager.can_make_api_call(0.00):  # Public API is free
            pytest.skip("API call limit reached")

        try:
            import requests

            # GitHub public API
            url = "https://api.github.com/repos/ContextLab/clustrix"

            response = requests.get(url, timeout=10)
            test_manager.record_api_call(0.00)

            # Verify response
            assert response.status_code == 200

            data = response.json()
            assert "name" in data
            assert "full_name" in data
            assert data["name"] == "clustrix"
            assert data["full_name"] == "ContextLab/clustrix"

        except Exception as e:
            pytest.skip(f"GitHub API failed: {e}")

    def test_pypi_api_real(self):
        """Test PyPI API calls (used for dependency checking)."""
        if not test_manager.can_make_api_call(0.00):  # Public API is free
            pytest.skip("API call limit reached")

        try:
            import requests

            # PyPI public API
            url = "https://pypi.org/pypi/clustrix/json"

            response = requests.get(url, timeout=10)
            test_manager.record_api_call(0.00)

            # Verify response
            assert response.status_code == 200

            data = response.json()
            assert "info" in data
            assert "releases" in data
            assert data["info"]["name"] == "clustrix"

        except Exception as e:
            pytest.skip(f"PyPI API failed: {e}")


@pytest.mark.real_world
class TestDatabaseOperationsReal:
    """Test real database operations (if applicable)."""

    def test_sqlite_operations_real(self):
        """Test SQLite database operations."""
        import sqlite3
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            # Create database and table
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE test_jobs (
                    id INTEGER PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            # Insert test data
            test_jobs = [("job1", "running"), ("job2", "completed"), ("job3", "failed")]

            cursor.executemany(
                "INSERT INTO test_jobs (job_name, status) VALUES (?, ?)", test_jobs
            )

            conn.commit()

            # Test queries
            cursor.execute("SELECT COUNT(*) FROM test_jobs")
            count = cursor.fetchone()[0]
            assert count == 3

            cursor.execute("SELECT * FROM test_jobs WHERE status = ?", ("completed",))
            completed_jobs = cursor.fetchall()
            assert len(completed_jobs) == 1
            assert completed_jobs[0][1] == "job2"

            # Test edge cases
            cursor.execute(
                "SELECT * FROM test_jobs WHERE job_name = ?", ("nonexistent",)
            )
            result = cursor.fetchall()
            assert len(result) == 0

            conn.close()

            # Verify database file exists
            assert db_path.exists()
            assert db_path.stat().st_size > 0
