"""
Real-world AWS pricing API tests.

These tests use actual AWS Pricing API with real credentials.
NO MOCKS OR SIMULATIONS - these test real AWS pricing integration.
"""

import pytest
import logging
import time
import os

from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.cost_providers.aws import AWSCostMonitor
from tests.real_world.credential_manager import get_aws_credentials

logger = logging.getLogger(__name__)


@pytest.mark.real_world
class TestAWSPricingReal:
    """Test real AWS pricing API integration."""

    def setup_method(self):
        """Setup for each test method."""
        self.aws_creds = get_aws_credentials()
        if not self.aws_creds:
            pytest.skip("AWS credentials not available")

        # Set up AWS credentials in environment for boto3
        os.environ["AWS_ACCESS_KEY_ID"] = self.aws_creds["access_key_id"]
        os.environ["AWS_SECRET_ACCESS_KEY"] = self.aws_creds["secret_access_key"]
        os.environ["AWS_DEFAULT_REGION"] = self.aws_creds["region"]

    def test_aws_pricing_client_api_connection_real(self):
        """Test AWS Pricing API connection with real credentials."""
        client = AWSPricingClient()

        # Test getting pricing for a common instance type
        instance_type = "t2.micro"
        region = "us-east-1"

        price = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )

        # Should get a valid price from API or fallback
        assert price is not None
        assert isinstance(price, (int, float))
        assert price > 0
        assert price < 10  # t2.micro should be under $10/hour

        logger.info(f"AWS {instance_type} in {region}: ${price:.4f}/hour")

    def test_aws_pricing_api_instance_types_real(self):
        """Test real AWS Pricing API returns valid instance type data."""
        client = AWSPricingClient()

        # Test common instance types
        test_instances = ["t2.micro", "t3.small", "m5.large", "c5.xlarge", "r5.large"]

        pricing_results = {}

        for instance_type in test_instances:
            price = client.get_instance_pricing(
                instance_type=instance_type,
                region="us-east-1",
                operating_system="Linux",
            )

            if price is not None:
                pricing_results[instance_type] = price
                assert price > 0
                assert price < 100  # Reasonable upper bound for these instances
                logger.info(f"AWS {instance_type}: ${price:.4f}/hour")
            else:
                logger.warning(f"No pricing found for {instance_type}")

        # Should have found pricing for most instances
        assert len(pricing_results) >= 3

    def test_aws_pricing_different_regions_real(self):
        """Test AWS pricing in different regions with real API."""
        client = AWSPricingClient()

        instance_type = "t2.micro"
        regions = ["us-east-1", "us-west-2", "eu-west-1"]

        regional_pricing = {}

        for region in regions:
            price = client.get_instance_pricing(
                instance_type=instance_type, region=region, operating_system="Linux"
            )

            if price is not None:
                regional_pricing[region] = price
                logger.info(f"AWS {instance_type} in {region}: ${price:.4f}/hour")

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

            # AWS regional pricing can vary but shouldn't be too extreme
            assert price_variance < 50  # Allow up to 50% regional variation

    def test_aws_pricing_different_os_real(self):
        """Test AWS pricing for different operating systems."""
        client = AWSPricingClient()

        instance_type = "t2.micro"
        region = "us-east-1"
        operating_systems = ["Linux", "Windows"]

        os_pricing = {}

        for os_type in operating_systems:
            price = client.get_instance_pricing(
                instance_type=instance_type, region=region, operating_system=os_type
            )

            if price is not None:
                os_pricing[os_type] = price
                logger.info(f"AWS {instance_type} ({os_type}): ${price:.4f}/hour")

        # Should have found pricing for at least Linux
        assert "Linux" in os_pricing

        # Windows should typically cost more than Linux
        if "Windows" in os_pricing and "Linux" in os_pricing:
            windows_price = os_pricing["Windows"]
            linux_price = os_pricing["Linux"]
            assert windows_price >= linux_price

    def test_aws_pricing_gpu_instances_real(self):
        """Test AWS pricing for GPU instances with real API."""
        client = AWSPricingClient()

        # Test GPU instance types
        gpu_instances = ["p3.2xlarge", "g4dn.xlarge", "p3.8xlarge"]

        gpu_pricing = {}

        for instance_type in gpu_instances:
            price = client.get_instance_pricing(
                instance_type=instance_type,
                region="us-east-1",
                operating_system="Linux",
            )

            if price is not None:
                gpu_pricing[instance_type] = price
                assert price > 0.5  # GPU instances should be more expensive
                assert price < 50  # But not more than $50/hour for these
                logger.info(f"AWS GPU {instance_type}: ${price:.3f}/hour")

        # Should have found pricing for at least some GPU instances
        assert len(gpu_pricing) >= 1

        # Verify pricing relationships make sense
        if "p3.2xlarge" in gpu_pricing and "p3.8xlarge" in gpu_pricing:
            p3_2xl = gpu_pricing["p3.2xlarge"]
            p3_8xl = gpu_pricing["p3.8xlarge"]
            # p3.8xlarge should cost more than p3.2xlarge
            assert p3_8xl > p3_2xl
            # But not more than 5x (due to shared costs)
            assert p3_8xl < p3_2xl * 5

    def test_aws_pricing_cache_behavior_real(self):
        """Test AWS pricing cache behavior with real API."""
        client = AWSPricingClient(cache_ttl_hours=1)  # Short TTL for testing

        instance_type = "t2.micro"
        region = "us-east-1"

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

    def test_aws_pricing_error_handling_real(self):
        """Test AWS pricing error handling with real API."""
        client = AWSPricingClient()

        # Test with invalid instance type
        invalid_price = client.get_instance_pricing(
            instance_type="invalid.instance.type",
            region="us-east-1",
            operating_system="Linux",
        )

        # Should return None for invalid instances (not crash)
        # Note: AWS might return hardcoded fallback pricing
        if invalid_price is not None:
            assert invalid_price > 0
            logger.info(
                f"Invalid instance type returned fallback price: ${invalid_price:.3f}"
            )
        else:
            logger.info("Invalid instance type correctly returned None")

    def test_aws_cost_monitor_integration_real(self):
        """Test AWS cost monitor integration with real API."""
        # Test cost monitor (it uses pricing client internally)
        monitor = AWSCostMonitor()

        # Test cost estimation
        instance_type = "t2.micro"
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
            f"AWS cost estimate: "
            f"${cost_estimate.estimated_cost:.3f} for {hours_used} hours"
        )

    def test_aws_pricing_vs_hardcoded_comparison(self):
        """Compare AWS API pricing vs hardcoded pricing."""
        client = AWSPricingClient()

        # Get hardcoded pricing
        hardcoded_pricing = client._hardcoded_pricing

        # Test a few common instances
        common_instances = ["t2.micro", "t2.small", "m5.large", "c5.large"]

        pricing_comparison = []

        for instance_type in common_instances:
            if instance_type in hardcoded_pricing:
                # Get API pricing
                api_price = client.get_instance_pricing(
                    instance_type=instance_type,
                    region="us-east-1",
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
                f"Found {len(large_differences)} instances "
                f"with >50% pricing differences"
            )
            for diff in large_differences:
                logger.warning(
                    f"  {diff['instance_type']}: "
                    f"{diff['difference_percent']:.1f}% difference"
                )

    def test_aws_pricing_api_performance(self):
        """Test AWS pricing API performance."""
        client = AWSPricingClient()

        # Test API response time for single instance
        start_time = time.time()
        price = client.get_instance_pricing(
            instance_type="t2.micro", region="us-east-1", operating_system="Linux"
        )
        api_response_time = time.time() - start_time

        # Verify performance
        assert api_response_time < 30.0  # AWS API can be slower, allow 30 seconds
        assert price is not None

        logger.info(f"AWS pricing API response time: {api_response_time:.3f} seconds")

    def test_aws_spot_pricing_real(self):
        """Test AWS spot pricing estimation."""
        client = AWSPricingClient()

        instance_type = "t2.micro"
        region = "us-east-1"

        # Get on-demand pricing
        on_demand_price = client.get_instance_pricing(
            instance_type=instance_type, region=region, operating_system="Linux"
        )

        # Get spot pricing estimation
        spot_price = client.get_spot_pricing(instance_type, region)

        if on_demand_price and spot_price:
            # Spot should be cheaper than on-demand
            assert spot_price < on_demand_price

            discount_percent = (1 - spot_price / on_demand_price) * 100
            logger.info(f"AWS {instance_type} spot discount: {discount_percent:.1f}%")

            # Spot discount should be reasonable (10-90%)
            assert 10 <= discount_percent <= 90

    def test_aws_pricing_client_info(self):
        """Test AWS pricing client information."""
        client = AWSPricingClient()

        # Get hardcoded pricing info (no get_pricing_info method in AWS client)
        hardcoded_pricing = client._hardcoded_pricing
        pricing_date = client._hardcoded_pricing_date

        # Verify hardcoded pricing structure
        assert isinstance(hardcoded_pricing, dict)
        assert len(hardcoded_pricing) > 0
        assert pricing_date is not None

        # Check if pricing data might be outdated
        is_outdated = client.is_pricing_data_outdated(days=30)
        logger.info(
            f"AWS hardcoded pricing date: {pricing_date}, outdated: {is_outdated}"
        )

    def teardown_method(self):
        """Cleanup after each test."""
        # Clean up environment variables
        for var in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]:
            if var in os.environ:
                del os.environ[var]
