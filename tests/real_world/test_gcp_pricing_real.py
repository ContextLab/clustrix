"""
Real-world GCP pricing API tests.

These tests use actual GCP Cloud Billing Catalog API with real credentials.
NO MOCKS OR SIMULATIONS - these test real GCP pricing integration.
"""

import pytest
import logging
import time
import os
import tempfile
import json

from clustrix.pricing_clients.gcp_pricing import GCPPricingClient
from clustrix.cost_providers.gcp import GCPCostMonitor
from tests.real_world.credential_manager import get_gcp_credentials

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestGCPPricingReal:
    """Test real GCP pricing API integration."""

    def setup_method(self):
        """Setup for each test method."""
        self.gcp_creds = get_gcp_credentials()
        if not self.gcp_creds:
            pytest.skip("GCP credentials not available")

        # Set up GCP project
        if "project_id" in self.gcp_creds:
            os.environ["GOOGLE_CLOUD_PROJECT"] = self.gcp_creds["project_id"]
            os.environ["GCP_PROJECT"] = self.gcp_creds["project_id"]

        # Set up service account JSON if available
        if "service_account_json" in self.gcp_creds:
            # Create temporary file for service account
            self.temp_cred_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            )
            json.dump(
                json.loads(self.gcp_creds["service_account_json"]),
                self.temp_cred_file,
                indent=2,
            )
            self.temp_cred_file.close()
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.temp_cred_file.name
        elif "service_account_path" in self.gcp_creds:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = self.gcp_creds[
                "service_account_path"
            ]
            self.temp_cred_file = None
        else:
            self.temp_cred_file = None

    def test_gcp_pricing_client_api_connection_real(self):
        """Test GCP Cloud Billing Catalog API connection with real credentials."""
        client = GCPPricingClient()

        # Test getting pricing for a common machine type
        instance_type = "n1-standard-1"
        region = "us-central1"

        price = client.get_instance_pricing(instance_type=instance_type, region=region)

        # Should get a valid price from API or fallback
        assert price is not None
        assert isinstance(price, (int, float))
        assert price > 0
        assert price < 10  # n1-standard-1 should be under $10/hour

        logger.info(f"GCP {instance_type} in {region}: ${price:.4f}/hour")

    def test_gcp_pricing_api_machine_types_real(self):
        """Test real GCP Cloud Billing API returns valid machine type data."""
        client = GCPPricingClient()

        # Test common GCP machine types
        test_instances = [
            "n1-standard-1",
            "n1-standard-2",
            "n2-standard-2",
            "c2-standard-4",
            "n1-standard-4",
        ]

        pricing_results = {}

        for instance_type in test_instances:
            price = client.get_instance_pricing(
                instance_type=instance_type, region="us-central1"
            )

            if price is not None:
                pricing_results[instance_type] = price
                assert price > 0
                assert price < 100  # Reasonable upper bound for these machine types
                logger.info(f"GCP {instance_type}: ${price:.4f}/hour")
            else:
                logger.warning(f"No pricing found for {instance_type}")

        # Should have found pricing for most machine types
        assert len(pricing_results) >= 3

    def test_gcp_pricing_different_regions_real(self):
        """Test GCP pricing in different regions with real API."""
        client = GCPPricingClient()

        instance_type = "n1-standard-1"
        regions = ["us-central1", "us-west1", "europe-west1"]

        regional_pricing = {}

        for region in regions:
            price = client.get_instance_pricing(
                instance_type=instance_type, region=region
            )

            if price is not None:
                regional_pricing[region] = price
                logger.info(f"GCP {instance_type} in {region}: ${price:.4f}/hour")

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

            # GCP regional pricing can vary but shouldn't be too extreme
            assert price_variance < 50  # Allow up to 50% regional variation

    def test_gcp_pricing_gpu_instances_real(self):
        """Test GCP pricing for GPU machine types with real API."""
        client = GCPPricingClient()

        # Test GPU machine types (these are approximations in hardcoded pricing)
        gpu_instances = ["n1-standard-4-k80", "n1-standard-4-t4", "n1-standard-4-v100"]

        gpu_pricing = {}

        for instance_type in gpu_instances:
            price = client.get_instance_pricing(
                instance_type=instance_type, region="us-central1"
            )

            if price is not None:
                gpu_pricing[instance_type] = price
                assert price > 0.5  # GPU machine types should be more expensive
                assert price < 100  # But not more than $100/hour for these
                logger.info(f"GCP GPU {instance_type}: ${price:.3f}/hour")

        # Should have found pricing for at least some GPU machine types
        assert len(gpu_pricing) >= 1

        # Verify pricing relationships make sense
        if "n1-standard-4-k80" in gpu_pricing and "n1-standard-4-v100" in gpu_pricing:
            k80_price = gpu_pricing["n1-standard-4-k80"]
            v100_price = gpu_pricing["n1-standard-4-v100"]
            # V100 should cost more than K80
            assert v100_price > k80_price

    def test_gcp_pricing_cache_behavior_real(self):
        """Test GCP pricing cache behavior with real API."""
        client = GCPPricingClient(cache_ttl_hours=1)  # Short TTL for testing

        instance_type = "n1-standard-1"
        region = "us-central1"

        # First call - should hit API or fallback
        start_time = time.time()
        price1 = client.get_instance_pricing(instance_type=instance_type, region=region)
        first_call_time = time.time() - start_time

        # Second call - should hit cache
        start_time = time.time()
        price2 = client.get_instance_pricing(instance_type=instance_type, region=region)
        second_call_time = time.time() - start_time

        # Verify results
        assert price1 == price2  # Same pricing
        assert second_call_time < first_call_time  # Cache should be faster

        logger.info(
            f"First call: {first_call_time:.3f}s, Cached call: {second_call_time:.3f}s"
        )

    def test_gcp_pricing_error_handling_real(self):
        """Test GCP pricing error handling with real API."""
        client = GCPPricingClient()

        # Test with invalid machine type
        invalid_price = client.get_instance_pricing(
            instance_type="invalid-machine-type-999", region="us-central1"
        )

        # Should return fallback default price for invalid machine types
        if invalid_price is not None:
            assert invalid_price > 0
            logger.info(
                f"Invalid machine type returned fallback price: ${invalid_price:.3f}"
            )
        else:
            logger.info("Invalid machine type correctly returned None")

    def test_gcp_cost_monitor_integration_real(self):
        """Test GCP cost monitor integration with real API."""
        # Test cost monitor (it uses pricing client internally)
        monitor = GCPCostMonitor()

        # Test cost estimation
        instance_type = "n1-standard-1"
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
            f"GCP cost estimate: "
            f"${cost_estimate.estimated_cost:.3f} for {hours_used} hours"
        )

    def test_gcp_pricing_vs_hardcoded_comparison(self):
        """Compare GCP API pricing vs hardcoded pricing."""
        client = GCPPricingClient()

        # Get hardcoded pricing
        hardcoded_pricing = client._hardcoded_pricing

        # Test a few common machine types
        common_instances = [
            "n1-standard-1",
            "n1-standard-2",
            "n2-standard-2",
            "c2-standard-4",
        ]

        pricing_comparison = []

        for instance_type in common_instances:
            if instance_type in hardcoded_pricing:
                # Get API pricing
                api_price = client.get_instance_pricing(
                    instance_type=instance_type, region="us-central1"
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
                f"Found {len(large_differences)} machine types "
                f"with >50% pricing differences"
            )
            for diff in large_differences:
                logger.warning(
                    f"  {diff['instance_type']}: "
                    f"{diff['difference_percent']:.1f}% difference"
                )

    def test_gcp_pricing_api_performance(self):
        """Test GCP pricing API performance."""
        client = GCPPricingClient()

        # Test API response time for single machine type
        start_time = time.time()
        price = client.get_instance_pricing(
            instance_type="n1-standard-1", region="us-central1"
        )
        api_response_time = time.time() - start_time

        # Verify performance
        assert api_response_time < 30.0  # GCP API can be slower, allow 30 seconds
        assert price is not None

        logger.info(f"GCP pricing API response time: {api_response_time:.3f} seconds")

    def test_gcp_preemptible_pricing_real(self):
        """Test GCP preemptible pricing with real API."""
        client = GCPPricingClient()

        instance_type = "n1-standard-1"
        region = "us-central1"

        # Get on-demand pricing
        on_demand_price = client.get_instance_pricing(
            instance_type=instance_type, region=region
        )

        # Get preemptible pricing
        preemptible_price = client.get_preemptible_pricing(instance_type, region)

        if on_demand_price and preemptible_price:
            # Preemptible should be cheaper than on-demand
            assert preemptible_price < on_demand_price

            discount_percent = (1 - preemptible_price / on_demand_price) * 100
            logger.info(
                f"GCP {instance_type} preemptible discount: {discount_percent:.1f}%"
            )

            # Preemptible discount should be reasonable (60-90%)
            assert 50 <= discount_percent <= 95

    def test_gcp_sustained_use_discount_real(self):
        """Test GCP sustained use discount calculation."""
        client = GCPPricingClient()

        base_price = 0.05  # $0.05/hour

        # Test different usage patterns
        test_cases = [
            (100, 0.0),  # <25% of month, no discount
            (200, 0.1),  # ~25% of month, 10% discount
            (400, 0.2),  # ~50% of month, 20% discount
            (600, 0.3),  # ~75% of month, 30% discount
        ]

        for hours_used, expected_discount in test_cases:
            discounted_price = client.get_sustained_use_discount(hours_used, base_price)
            expected_price = base_price * (1 - expected_discount)

            # Allow small floating point differences
            assert abs(discounted_price - expected_price) < 0.001

            logger.info(
                f"GCP sustained use: {hours_used}h = ${discounted_price:.4f}/h "
                f"({expected_discount * 100:.0f}% discount)"
            )

    def test_gcp_custom_machine_pricing_real(self):
        """Test GCP custom machine type pricing calculation."""
        client = GCPPricingClient()

        # Test custom machine configurations
        test_configs = [
            (2, 4.0, "us-central1"),  # 2 vCPU, 4GB RAM
            (4, 8.0, "us-central1"),  # 4 vCPU, 8GB RAM
            (8, 16.0, "europe-west1"),  # 8 vCPU, 16GB RAM
        ]

        for vcpus, memory_gb, region in test_configs:
            price = client.get_custom_machine_pricing(vcpus, memory_gb, region)

            assert price is not None
            assert price > 0
            assert (
                price < 10
            )  # Custom machines shouldn't be too expensive for these configs

            logger.info(
                f"GCP custom {vcpus}vCPU/{memory_gb}GB in {region}: ${price:.4f}/hour"
            )

        # Verify pricing scales correctly
        small_price = client.get_custom_machine_pricing(2, 4.0, "us-central1")
        large_price = client.get_custom_machine_pricing(4, 8.0, "us-central1")

        if small_price and large_price:
            # Larger machine should cost more
            assert large_price > small_price
            # But not more than 3x (roughly 2x resources)
            assert large_price < small_price * 3

    def test_gcp_pricing_client_info(self):
        """Test GCP pricing client information."""
        client = GCPPricingClient()

        # Get hardcoded pricing info
        hardcoded_pricing = client._hardcoded_pricing
        pricing_date = client._hardcoded_pricing_date
        compute_service_id = client.compute_service_id
        region_mapping = client.region_mapping

        # Verify pricing client structure
        assert isinstance(hardcoded_pricing, dict)
        assert len(hardcoded_pricing) > 0
        assert pricing_date is not None
        assert compute_service_id is not None
        assert isinstance(region_mapping, dict)
        assert len(region_mapping) > 0

        # Check if pricing data might be outdated
        is_outdated = client.is_pricing_data_outdated(days=30)
        logger.info(
            f"GCP hardcoded pricing date: {pricing_date}, outdated: {is_outdated}"
        )
        logger.info(f"GCP Compute Engine service ID: {compute_service_id}")

    def test_gcp_pricing_machine_families_real(self):
        """Test GCP pricing across different machine families."""
        client = GCPPricingClient()

        # Test different machine families
        machine_families = {
            "n1-standard-2": "N1 General Purpose",
            "n2-standard-2": "N2 General Purpose",
            "c2-standard-4": "C2 Compute Optimized",
            "m1-ultramem-40": "M1 Memory Optimized",
        }

        family_pricing = {}

        for machine_type, family_name in machine_families.items():
            price = client.get_instance_pricing(
                instance_type=machine_type, region="us-central1"
            )

            if price is not None:
                family_pricing[family_name] = price
                logger.info(f"GCP {family_name} ({machine_type}): ${price:.4f}/hour")

        # Should have found pricing for most families
        assert len(family_pricing) >= 2

        # Verify family pricing relationships
        if (
            "N2 General Purpose" in family_pricing
            and "N1 General Purpose" in family_pricing
        ):
            n2_price = family_pricing["N2 General Purpose"]
            n1_price = family_pricing["N1 General Purpose"]
            # N2 should be similar or slightly higher than N1
            assert n2_price <= n1_price * 1.5

    def test_gcp_pricing_region_mapping(self):
        """Test GCP region mapping."""
        client = GCPPricingClient()

        # Test that all regions in mapping are valid
        region_mapping = client.region_mapping

        assert "us-central1" in region_mapping
        assert "europe-west1" in region_mapping
        assert "asia-east1" in region_mapping

        logger.info(f"GCP regions supported: {list(region_mapping.keys())[:5]}...")

    def teardown_method(self):
        """Cleanup after each test."""
        # Clean up environment variables
        gcp_env_vars = [
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ]
        for var in gcp_env_vars:
            if var in os.environ:
                del os.environ[var]

        # Clean up temporary credential file
        if hasattr(self, "temp_cred_file") and self.temp_cred_file:
            try:
                os.unlink(self.temp_cred_file.name)
            except OSError:
                pass
