"""
Real-world Lambda Cloud pricing API tests.

These tests use actual Lambda Cloud API endpoints with real credentials.
NO MOCKS OR SIMULATIONS - these test real Lambda Cloud pricing integration.
"""

import pytest
import logging
import time
from typing import Dict, Any

from clustrix.pricing_clients.lambda_pricing import LambdaPricingClient
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor
from tests.real_world.credential_manager import get_lambda_credentials

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestLambdaPricingReal:
    """Test real Lambda Cloud pricing API integration."""

    def setup_method(self):
        """Setup for each test method."""
        self.lambda_creds = get_lambda_credentials()
        if not self.lambda_creds:
            pytest.skip("Lambda Cloud credentials not available")

        self.api_key = self.lambda_creds.get("api_key")
        if not self.api_key:
            pytest.skip("Lambda Cloud API key not available")

    def test_lambda_pricing_client_authentication_real(self):
        """Test Lambda Cloud API authentication with real credentials."""
        client = LambdaPricingClient()

        # Test authentication
        auth_result = client.authenticate(self.api_key)

        # Verify authentication
        assert auth_result == True
        assert client.authenticated == True
        assert client.api_key == self.api_key

        logger.info("Lambda Cloud API authentication successful")

    def test_lambda_pricing_api_instance_types_real(self):
        """Test real Lambda Cloud API returns valid instance type data."""
        client = LambdaPricingClient()
        client.authenticate(self.api_key)

        # Test getting all pricing
        all_pricing = client.get_all_pricing()

        # Verify we got some pricing data
        assert isinstance(all_pricing, dict)
        assert len(all_pricing) > 0

        logger.info(
            f"Retrieved pricing for {len(all_pricing)} Lambda Cloud instance types"
        )

        # Verify pricing data is reasonable
        for instance_type, price in all_pricing.items():
            assert isinstance(price, (int, float))
            assert price > 0
            assert price < 100  # Sanity check - should be under $100/hour
            logger.debug(f"Lambda Cloud {instance_type}: ${price:.3f}/hour")

    def test_lambda_pricing_specific_instances_real(self):
        """Test pricing for specific Lambda Cloud instance types."""
        client = LambdaPricingClient()
        client.authenticate(self.api_key)

        # Test common GPU instance types
        test_instances = [
            "gpu_1x_a10",
            "gpu_1x_a100",
            "gpu_1x_h100",
            "gpu_2x_a100",
            "gpu_4x_a100",
        ]

        pricing_results = {}

        for instance_type in test_instances:
            price = client.get_instance_pricing(instance_type)

            if price is not None:
                pricing_results[instance_type] = price
                assert price > 0
                assert price < 50  # Reasonable upper bound
                logger.info(f"Lambda Cloud {instance_type}: ${price:.3f}/hour")
            else:
                logger.warning(f"No pricing found for {instance_type}")

        # Should have found pricing for at least some instances
        assert len(pricing_results) > 0

        # Verify pricing relationships make sense
        if "gpu_1x_a100" in pricing_results and "gpu_2x_a100" in pricing_results:
            single_a100 = pricing_results["gpu_1x_a100"]
            dual_a100 = pricing_results["gpu_2x_a100"]
            # Dual GPU should cost more than single GPU
            assert dual_a100 > single_a100
            # But not more than 2.5x (due to shared resources)
            assert dual_a100 < single_a100 * 2.5

    def test_lambda_pricing_cache_behavior_real(self):
        """Test Lambda Cloud pricing cache behavior with real API."""
        client = LambdaPricingClient(cache_ttl_hours=1)  # Short TTL for testing
        client.authenticate(self.api_key)

        instance_type = "gpu_1x_a100"

        # First call - should hit API
        start_time = time.time()
        price1 = client.get_instance_pricing(instance_type)
        first_call_time = time.time() - start_time

        # Second call - should hit cache
        start_time = time.time()
        price2 = client.get_instance_pricing(instance_type)
        second_call_time = time.time() - start_time

        # Verify results
        assert price1 == price2  # Same pricing
        assert second_call_time < first_call_time  # Cache should be faster

        logger.info(
            f"First API call: {first_call_time:.3f}s, Cached call: {second_call_time:.3f}s"
        )

    def test_lambda_pricing_error_handling_real(self):
        """Test Lambda Cloud pricing error handling with real API."""
        client = LambdaPricingClient()
        client.authenticate(self.api_key)

        # Test with invalid instance type
        invalid_price = client.get_instance_pricing("invalid_gpu_type_xyz")

        # Should return default or None, not crash
        if invalid_price is not None:
            assert invalid_price > 0
            logger.info(
                f"Invalid instance type returned default price: ${invalid_price:.3f}"
            )
        else:
            logger.info("Invalid instance type correctly returned None")

    def test_lambda_cost_monitor_integration_real(self):
        """Test Lambda Cloud cost monitor integration with real API."""
        # Test cost monitor with API integration
        monitor = LambdaCostMonitor(use_pricing_api=True, api_key=self.api_key)

        # Test cost estimation
        instance_type = "gpu_1x_a100"
        hours_used = 2.5

        cost_estimate = monitor.estimate_cost(instance_type, hours_used)

        # Verify cost estimate
        assert cost_estimate is not None
        assert cost_estimate.instance_type == instance_type
        assert cost_estimate.hours_used == hours_used
        assert cost_estimate.hourly_rate > 0
        assert cost_estimate.estimated_cost > 0
        assert cost_estimate.currency == "USD"

        # Should be using API pricing if available
        logger.info(f"Cost estimate pricing source: {cost_estimate.pricing_source}")

        # Test pricing info retrieval
        pricing_info = monitor.get_pricing_info()
        assert isinstance(pricing_info, dict)
        assert len(pricing_info) > 0

        logger.info(
            f"Lambda Cloud cost estimate: ${cost_estimate.estimated_cost:.2f} for {hours_used} hours"
        )

    def test_lambda_pricing_vs_hardcoded_comparison(self):
        """Compare Lambda Cloud API pricing vs hardcoded pricing."""
        # Get API pricing
        api_client = LambdaPricingClient()
        api_client.authenticate(self.api_key)
        api_pricing = api_client.get_all_pricing()

        # Get hardcoded pricing
        hardcoded_client = LambdaPricingClient()
        hardcoded_pricing = hardcoded_client._hardcoded_pricing

        # Compare common instance types
        common_instances = set(api_pricing.keys()) & set(hardcoded_pricing.keys())

        pricing_comparison = []

        for instance_type in common_instances:
            api_price = api_pricing[instance_type]
            hardcoded_price = hardcoded_pricing[instance_type]

            # Calculate percentage difference
            diff_percent = abs(api_price - hardcoded_price) / hardcoded_price * 100

            pricing_comparison.append(
                {
                    "instance_type": instance_type,
                    "api_price": api_price,
                    "hardcoded_price": hardcoded_price,
                    "difference_percent": diff_percent,
                }
            )

            logger.info(
                f"{instance_type}: API ${api_price:.3f} vs Hardcoded ${hardcoded_price:.3f} ({diff_percent:.1f}% diff)"
            )

        # Verify pricing differences are reasonable
        large_differences = [
            p for p in pricing_comparison if p["difference_percent"] > 50
        ]

        if large_differences:
            logger.warning(
                f"Found {len(large_differences)} instances with >50% pricing differences"
            )
            for diff in large_differences:
                logger.warning(
                    f"  {diff['instance_type']}: {diff['difference_percent']:.1f}% difference"
                )

        # Should have some pricing comparisons
        assert len(pricing_comparison) > 0

    def test_lambda_pricing_regional_consistency(self):
        """Test Lambda Cloud pricing consistency across regions."""
        client = LambdaPricingClient()
        client.authenticate(self.api_key)

        regions_to_test = ["us-east-1", "us-west-2"]
        instance_type = "gpu_1x_a100"

        regional_pricing = {}

        for region in regions_to_test:
            try:
                price = client.get_instance_pricing(instance_type, region)
                if price is not None:
                    regional_pricing[region] = price
                    logger.info(
                        f"Lambda Cloud {instance_type} in {region}: ${price:.3f}/hour"
                    )
            except Exception as e:
                logger.warning(f"Error getting pricing for {region}: {e}")

        # Verify we got some regional pricing
        if len(regional_pricing) > 1:
            # Check if regional prices are reasonably consistent
            prices = list(regional_pricing.values())
            max_price = max(prices)
            min_price = min(prices)
            price_variance = (max_price - min_price) / min_price * 100

            logger.info(
                f"Regional price variance for {instance_type}: {price_variance:.1f}%"
            )

            # Lambda Cloud pricing should be fairly consistent across regions
            assert price_variance < 20  # Allow up to 20% regional variation

    def test_lambda_pricing_api_performance(self):
        """Test Lambda Cloud pricing API performance."""
        client = LambdaPricingClient()
        client.authenticate(self.api_key)

        # Test API response time
        start_time = time.time()
        all_pricing = client.get_all_pricing()
        api_response_time = time.time() - start_time

        # Verify performance
        assert api_response_time < 10.0  # Should respond within 10 seconds
        assert len(all_pricing) > 0

        logger.info(
            f"Lambda Cloud pricing API response time: {api_response_time:.3f} seconds"
        )

        # Test individual instance pricing performance
        instance_type = "gpu_1x_a100"
        start_time = time.time()
        price = client.get_instance_pricing(instance_type)
        single_response_time = time.time() - start_time

        assert single_response_time < 5.0  # Should respond within 5 seconds
        assert price is not None

        logger.info(
            f"Single instance pricing response time: {single_response_time:.3f} seconds"
        )

    def test_lambda_pricing_client_info(self):
        """Test Lambda Cloud pricing client information."""
        client = LambdaPricingClient()
        client.authenticate(self.api_key)

        pricing_info = client.get_pricing_info()

        # Verify pricing info structure
        assert isinstance(pricing_info, dict)
        assert pricing_info["provider"] == "lambda"
        assert pricing_info["authenticated"] == True
        assert pricing_info["api_available"] == True
        assert "fallback_pricing_date" in pricing_info
        assert "cache_ttl_hours" in pricing_info
        assert "supported_regions" in pricing_info
        assert "instance_count" in pricing_info

        logger.info(f"Lambda Cloud pricing client info: {pricing_info}")

    def teardown_method(self):
        """Cleanup after each test."""
        # No cleanup needed for pricing tests
        pass
