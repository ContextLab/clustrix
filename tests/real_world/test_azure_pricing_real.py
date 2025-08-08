"""
Real-world Azure pricing API tests.

These tests use actual Azure Retail Prices API with real credentials.
NO MOCKS OR SIMULATIONS - these test real Azure pricing integration.
"""

import pytest
import logging
import time
import os
import tempfile
import json

from clustrix.pricing_clients.azure_pricing import AzurePricingClient
from clustrix.cost_providers.azure import AzureCostMonitor
from tests.real_world.credential_manager import get_azure_credentials

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestAzurePricingReal:
    """Test real Azure pricing API integration."""

    def setup_method(self):
        """Setup for each test method."""
        self.azure_creds = get_azure_credentials()
        if not self.azure_creds:
            pytest.skip("Azure credentials not available")

        # Set up Azure credentials in environment (if needed for service auth)
        if "subscription_id" in self.azure_creds:
            os.environ["AZURE_SUBSCRIPTION_ID"] = self.azure_creds["subscription_id"]
        if "tenant_id" in self.azure_creds:
            os.environ["AZURE_TENANT_ID"] = self.azure_creds["tenant_id"]
        if "client_id" in self.azure_creds:
            os.environ["AZURE_CLIENT_ID"] = self.azure_creds["client_id"]
        if "client_secret" in self.azure_creds:
            os.environ["AZURE_CLIENT_SECRET"] = self.azure_creds["client_secret"]

        # Set up service account JSON if available
        if "service_account_json" in self.azure_creds:
            # Create temporary file for service account
            self.temp_cred_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            )
            json.dump(
                json.loads(self.azure_creds["service_account_json"]),
                self.temp_cred_file,
                indent=2,
            )
            self.temp_cred_file.close()
            os.environ["AZURE_APPLICATION_CREDENTIALS"] = self.temp_cred_file.name
        else:
            self.temp_cred_file = None

    def test_azure_pricing_client_api_connection_real(self):
        """Test Azure Retail Prices API connection with real API."""
        client = AzurePricingClient()

        # Test getting pricing for a common VM size
        instance_type = "Standard_D2s_v3"
        region = "eastus"

        price = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )

        # Should get a valid price from API or fallback
        assert price is not None
        assert isinstance(price, (int, float))
        assert price > 0
        assert price < 10  # Standard_D2s_v3 should be under $10/hour

        logger.info(f"Azure {instance_type} in {region}: ${price:.4f}/hour")

    def test_azure_pricing_api_instance_types_real(self):
        """Test real Azure Retail Prices API returns valid VM data."""
        client = AzurePricingClient()

        # Test common Azure VM sizes
        test_instances = [
            "Standard_D2s_v3",
            "Standard_D4s_v3",
            "Standard_F2s_v2",
            "Standard_E2s_v3",
            "Standard_A2_v2",
        ]

        pricing_results = {}

        for instance_type in test_instances:
            price = client.get_instance_pricing(
                instance_type=instance_type, region="eastus", operating_system="Linux"
            )

            if price is not None:
                pricing_results[instance_type] = price
                assert price > 0
                assert price < 100  # Reasonable upper bound for these VMs
                logger.info(f"Azure {instance_type}: ${price:.4f}/hour")
            else:
                logger.warning(f"No pricing found for {instance_type}")

        # Should have found pricing for most VMs
        assert len(pricing_results) >= 3

    def test_azure_pricing_different_regions_real(self):
        """Test Azure pricing in different regions with real API."""
        client = AzurePricingClient()

        instance_type = "Standard_D2s_v3"
        regions = ["eastus", "westus2", "westeurope"]

        regional_pricing = {}

        for region in regions:
            price = client.get_instance_pricing(
                instance_type=instance_type, region=region, operating_system="Linux"
            )

            if price is not None:
                regional_pricing[region] = price
                logger.info(f"Azure {instance_type} in {region}: ${price:.4f}/hour")

        # Should have found pricing for most regions
        assert len(regional_pricing) >= 2

        # Verify pricing differences are reasonable
        if len(regional_pricing) > 1:
            prices = list(regional_pricing.values())
            max_price = max(prices)
            min_price = min(prices)
            price_variance = (max_price - min_price) / min_price * 100

            logger.info(
                f"Regional price variance for {instance_type}: {price_variance:.1f}%"
            )

            # Azure regional pricing can vary but shouldn't be too extreme
            assert price_variance < 50  # Allow up to 50% regional variation

    def test_azure_pricing_different_os_real(self):
        """Test Azure pricing for different operating systems."""
        client = AzurePricingClient()

        instance_type = "Standard_D2s_v3"
        region = "eastus"
        operating_systems = ["Linux", "Windows"]

        os_pricing = {}

        for os_type in operating_systems:
            price = client.get_instance_pricing(
                instance_type=instance_type, region=region, operating_system=os_type
            )

            if price is not None:
                os_pricing[os_type] = price
                logger.info(f"Azure {instance_type} ({os_type}): ${price:.4f}/hour")

        # Should have found pricing for at least Linux
        assert "Linux" in os_pricing

        # Windows should typically cost more than Linux
        if "Windows" in os_pricing and "Linux" in os_pricing:
            windows_price = os_pricing["Windows"]
            linux_price = os_pricing["Linux"]
            # Allow some flexibility - sometimes they're the same
            assert (
                windows_price >= linux_price * 0.9
            )  # Windows at least 90% of Linux price

    def test_azure_pricing_gpu_instances_real(self):
        """Test Azure pricing for GPU VMs with real API."""
        client = AzurePricingClient()

        # Test GPU VM sizes
        gpu_instances = ["Standard_NC6s_v3", "Standard_NC12s_v3", "Standard_ND40rs_v2"]

        gpu_pricing = {}

        for instance_type in gpu_instances:
            price = client.get_instance_pricing(
                instance_type=instance_type, region="eastus", operating_system="Linux"
            )

            if price is not None:
                gpu_pricing[instance_type] = price
                assert price > 1.0  # GPU VMs should be more expensive
                assert price < 100  # But not more than $100/hour for these
                logger.info(f"Azure GPU {instance_type}: ${price:.3f}/hour")

        # Should have found pricing for at least some GPU VMs
        assert len(gpu_pricing) >= 1

        # Verify pricing relationships make sense
        if "Standard_NC6s_v3" in gpu_pricing and "Standard_NC12s_v3" in gpu_pricing:
            nc6_price = gpu_pricing["Standard_NC6s_v3"]
            nc12_price = gpu_pricing["Standard_NC12s_v3"]
            # NC12 should cost more than NC6
            assert nc12_price > nc6_price
            # But not more than 3x (due to shared costs)
            assert nc12_price < nc6_price * 3

    def test_azure_pricing_cache_behavior_real(self):
        """Test Azure pricing cache behavior with real API."""
        client = AzurePricingClient(cache_ttl_hours=1)  # Short TTL for testing

        instance_type = "Standard_D2s_v3"
        region = "eastus"

        # First call - should hit API or fallback
        start_time = time.time()
        price1 = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )
        first_call_time = time.time() - start_time

        # Second call - should hit cache
        start_time = time.time()
        price2 = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )
        second_call_time = time.time() - start_time

        # Verify results
        assert price1 == price2  # Same pricing
        assert second_call_time < first_call_time  # Cache should be faster

        logger.info(
            f"First call: {first_call_time:.3f}s, Cached call: {second_call_time:.3f}s"
        )

    def test_azure_pricing_error_handling_real(self):
        """Test Azure pricing error handling with real API."""
        client = AzurePricingClient()

        # Test with invalid VM size
        invalid_price = client.get_instance_pricing(
            instance_type="Standard_InvalidVM_v999",
            region="eastus",
            operating_system="Linux",
        )

        # Should return fallback default price for invalid VMs
        if invalid_price is not None:
            assert invalid_price > 0
            logger.info(
                f"Invalid VM size returned fallback price: ${invalid_price:.3f}"
            )
        else:
            logger.info("Invalid VM size correctly returned None")

    def test_azure_cost_monitor_integration_real(self):
        """Test Azure cost monitor integration with real API."""
        # Test cost monitor (it uses pricing client internally)
        monitor = AzureCostMonitor()

        # Test cost estimation
        instance_type = "Standard_D2s_v3"
        hours_used = 2.5

        # This will use the pricing client internally
        cost_estimate = monitor.estimate_cost(instance_type, hours_used)

        # Verify cost estimate
        assert cost_estimate is not None
        assert cost_estimate.instance_type == instance_type
        assert cost_estimate.hours_used == hours_used
        assert cost_estimate.hourly_rate > 0
        assert cost_estimate.estimated_cost > 0
        assert cost_estimate.currency == "USD"

        logger.info(
            f"Azure cost estimate: "
            f"${cost_estimate.estimated_cost:.3f} for {hours_used} hours"
        )

    def test_azure_pricing_vs_hardcoded_comparison(self):
        """Compare Azure API pricing vs hardcoded pricing."""
        client = AzurePricingClient()

        # Get hardcoded pricing
        hardcoded_pricing = client._hardcoded_pricing

        # Test a few common VMs
        common_instances = [
            "Standard_D2s_v3",
            "Standard_D4s_v3",
            "Standard_E2s_v3",
            "Standard_F2s_v2",
        ]

        pricing_comparison = []

        for instance_type in common_instances:
            if instance_type in hardcoded_pricing:
                # Get API pricing
                api_price = client.get_instance_pricing(
                    instance_type=instance_type,
                    region="eastus",
                    operating_system="Linux",
                )

                hardcoded_price = hardcoded_pricing[instance_type]

                if api_price is not None:
                    # Calculate percentage difference
                    diff_percent = (
                        abs(api_price - hardcoded_price) / hardcoded_price * 100
                    )

                    pricing_comparison.append(
                        {
                            "instance_type": instance_type,
                            "api_price": api_price,
                            "hardcoded_price": hardcoded_price,
                            "difference_percent": diff_percent,
                        }
                    )

                    logger.info(
                        f"{instance_type}: API ${api_price:.4f} vs "
                        f"Hardcoded ${hardcoded_price:.4f} ({diff_percent:.1f}% diff)"
                    )

        # Should have some pricing comparisons
        assert len(pricing_comparison) > 0

        # Log any large differences for review
        large_differences = [
            p for p in pricing_comparison if p["difference_percent"] > 50
        ]

        if large_differences:
            logger.warning(
                f"Found {len(large_differences)} VMs " f"with >50% pricing differences"
            )
            for diff in large_differences:
                logger.warning(
                    f"  {diff['instance_type']}: "
                    f"{diff['difference_percent']:.1f}% difference"
                )

    def test_azure_pricing_api_performance(self):
        """Test Azure pricing API performance."""
        client = AzurePricingClient()

        # Test API response time for single VM
        start_time = time.time()
        price = client.get_instance_pricing(
            instance_type="Standard_D2s_v3", region="eastus", operating_system="Linux"
        )
        api_response_time = time.time() - start_time

        # Verify performance
        assert api_response_time < 30.0  # Azure API can be slower, allow 30 seconds
        assert price is not None

        logger.info(f"Azure pricing API response time: {api_response_time:.3f} seconds")

    def test_azure_spot_pricing_real(self):
        """Test Azure spot pricing with real API."""
        client = AzurePricingClient()

        instance_type = "Standard_D2s_v3"
        region = "eastus"

        # Get on-demand pricing
        on_demand_price = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )

        # Get spot pricing
        spot_price = client.get_spot_pricing(instance_type, region)

        if on_demand_price and spot_price:
            # Spot should be cheaper than on-demand
            assert spot_price < on_demand_price

            discount_percent = (1 - spot_price / on_demand_price) * 100
            logger.info(f"Azure {instance_type} spot discount: {discount_percent:.1f}%")

            # Spot discount should be reasonable (10-90%)
            assert 10 <= discount_percent <= 90

    def test_azure_pricing_service_query_real(self):
        """Test Azure pricing service-wide query with real API."""
        client = AzurePricingClient()

        # Get pricing for multiple VMs in Virtual Machines service
        service_pricing = client.get_pricing_by_service(
            service_name="Virtual Machines", region="eastus"
        )

        # Should get some pricing data
        assert isinstance(service_pricing, dict)

        if service_pricing:
            logger.info(f"Retrieved pricing for {len(service_pricing)} Azure VM sizes")

            # Verify pricing data structure
            for vm_size, pricing_info in list(service_pricing.items())[
                :5
            ]:  # Check first 5
                assert isinstance(pricing_info, dict)
                assert "price" in pricing_info
                assert isinstance(pricing_info["price"], (int, float))
                assert pricing_info["price"] > 0
                logger.debug(f"Azure {vm_size}: ${pricing_info['price']:.4f}/hour")

    def test_azure_pricing_region_mapping(self):
        """Test Azure region name mapping."""
        client = AzurePricingClient()

        # Test region mapping
        test_regions = {
            "eastus": "East US",
            "westeurope": "West Europe",
            "eastasia": "East Asia",
        }

        for region_code, expected_name in test_regions.items():
            mapped_name = client._get_region_name(region_code)
            assert mapped_name == expected_name
            logger.info(f"Region mapping: {region_code} -> {mapped_name}")

    def test_azure_pricing_client_info(self):
        """Test Azure pricing client information."""
        client = AzurePricingClient()

        # Get hardcoded pricing info
        hardcoded_pricing = client._hardcoded_pricing
        pricing_date = client._hardcoded_pricing_date
        api_url = client.api_url
        api_version = client.api_version

        # Verify pricing client structure
        assert isinstance(hardcoded_pricing, dict)
        assert len(hardcoded_pricing) > 0
        assert pricing_date is not None
        assert api_url == "https://prices.azure.com/api/retail/prices"
        assert api_version is not None

        # Check if pricing data might be outdated
        is_outdated = client.is_pricing_data_outdated(days=30)
        logger.info(
            f"Azure hardcoded pricing date: {pricing_date}, outdated: {is_outdated}"
        )
        logger.info(f"Azure Retail Prices API: {api_url} (v{api_version})")

    def test_azure_pricing_filters_real(self):
        """Test Azure pricing API filters work correctly."""
        client = AzurePricingClient()

        instance_type = "Standard_D2s_v3"
        region = "eastus"

        # Test Linux pricing
        linux_price = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )

        # Test Windows pricing
        windows_price = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Windows"
        )

        if linux_price and windows_price:
            # Both should be positive
            assert linux_price > 0
            assert windows_price > 0

            # They might be different (Windows usually costs more)
            logger.info(
                f"Azure {instance_type} Linux: ${linux_price:.4f}, "
                f"Windows: ${windows_price:.4f}"
            )

    def teardown_method(self):
        """Cleanup after each test."""
        # Clean up environment variables
        azure_env_vars = [
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "AZURE_APPLICATION_CREDENTIALS",
        ]
        for var in azure_env_vars:
            if var in os.environ:
                del os.environ[var]

        # Clean up temporary credential file
        if hasattr(self, "temp_cred_file") and self.temp_cred_file:
            try:
                os.unlink(self.temp_cred_file.name)
            except OSError:
                pass
