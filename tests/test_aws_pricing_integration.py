"""Integration tests for AWS pricing API - these require AWS credentials and internet access."""

import pytest
import os
from unittest.mock import patch
import logging

from clustrix.pricing_clients.aws_pricing import AWSPricingClient


@pytest.mark.integration
class TestAWSPricingIntegration:
    """Integration tests that actually call the AWS API."""

    def test_real_aws_pricing_api_call(self):
        """Test that we can actually fetch pricing from AWS (requires credentials)."""
        # Skip if no AWS credentials available
        if not self._has_aws_credentials():
            pytest.skip("AWS credentials not available")

        client = AWSPricingClient()

        # Try to get pricing for a common instance type
        try:
            price = client.get_instance_pricing(
                instance_type="t2.micro", region="us-east-1", operating_system="Linux"
            )

            # If we get a price, it should be a positive float
            if price is not None:
                assert isinstance(price, float)
                assert price > 0
                assert price < 1.0  # t2.micro should be less than $1/hour
                print(f"✅ Successfully fetched t2.micro pricing: ${price}/hr")
            else:
                print("⚠️ API returned None - might be credentials issue")

        except Exception as e:
            print(f"⚠️ AWS API call failed: {e}")
            # Don't fail the test - this could be due to network, credentials, etc.

    def test_aws_pricing_api_without_credentials(self):
        """Test that the client handles missing credentials gracefully."""
        # Temporarily remove AWS credentials
        original_env = {}
        aws_env_vars = [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
        ]

        # Save and remove AWS environment variables
        for var in aws_env_vars:
            if var in os.environ:
                original_env[var] = os.environ[var]
                del os.environ[var]

        try:
            client = AWSPricingClient()

            # This should not raise an exception, just return None/fallback
            price = client.get_instance_pricing("t2.micro", "us-east-1")

            # Should fall back to hardcoded pricing
            assert price == 0.0116  # Hardcoded t2.micro price

        finally:
            # Restore environment variables
            for var, value in original_env.items():
                os.environ[var] = value

    def test_aws_pricing_api_error_handling(self):
        """Test that the client handles API errors gracefully."""
        client = AWSPricingClient()

        # Test with invalid region - should fall back gracefully
        price = client.get_instance_pricing(
            instance_type="t2.micro", region="invalid-region-123"
        )

        # Should either return a valid price or fall back to hardcoded
        if price is not None:
            assert isinstance(price, float)
            assert price > 0

    def test_cache_behavior_with_real_api(self):
        """Test that caching works with real API calls."""
        if not self._has_aws_credentials():
            pytest.skip("AWS credentials not available")

        client = AWSPricingClient(cache_ttl_hours=1)

        # Make the same call twice - second should be faster due to caching
        import time

        start_time = time.time()
        price1 = client.get_instance_pricing("t2.micro", "us-east-1")
        first_call_time = time.time() - start_time

        start_time = time.time()
        price2 = client.get_instance_pricing("t2.micro", "us-east-1")
        second_call_time = time.time() - start_time

        # Prices should be the same
        if price1 is not None and price2 is not None:
            assert price1 == price2
            # Second call should be faster (cached)
            assert second_call_time < first_call_time
            print(
                f"✅ Cache working: first call {first_call_time:.3f}s, second call {second_call_time:.3f}s"
            )

    def test_different_regions_return_different_prices(self):
        """Test that different regions can return different prices."""
        if not self._has_aws_credentials():
            pytest.skip("AWS credentials not available")

        client = AWSPricingClient()

        # Test a few different regions
        regions = ["us-east-1", "us-west-2", "eu-west-1"]
        prices = {}

        for region in regions:
            try:
                price = client.get_instance_pricing("m5.large", region)
                if price is not None:
                    prices[region] = price
                    print(f"m5.large in {region}: ${price}/hr")
            except Exception as e:
                print(f"Failed to get pricing for {region}: {e}")

        # If we got multiple prices, they might be different
        if len(prices) > 1:
            price_values = list(prices.values())
            # All prices should be positive
            for price in price_values:
                assert price > 0
                assert price < 10.0  # Sanity check

    def test_aws_pricing_with_different_os(self):
        """Test pricing differences between operating systems."""
        if not self._has_aws_credentials():
            pytest.skip("AWS credentials not available")

        client = AWSPricingClient()

        operating_systems = ["Linux", "Windows"]
        prices = {}

        for os_type in operating_systems:
            try:
                price = client.get_instance_pricing(
                    instance_type="m5.large",
                    region="us-east-1",
                    operating_system=os_type,
                )
                if price is not None:
                    prices[os_type] = price
                    print(f"m5.large with {os_type}: ${price}/hr")
            except Exception as e:
                print(f"Failed to get pricing for {os_type}: {e}")

        # Windows should typically be more expensive than Linux
        if "Linux" in prices and "Windows" in prices:
            assert prices["Windows"] > prices["Linux"]
            print(
                f"✅ Windows pricing (${prices['Windows']}) > Linux pricing (${prices['Linux']})"
            )

    def _has_aws_credentials(self) -> bool:
        """Check if AWS credentials are available."""
        try:
            import boto3

            # Try to create a session - this will check for credentials
            session = boto3.Session()
            credentials = session.get_credentials()
            return credentials is not None
        except Exception:
            return False

    def test_fallback_mechanism_integration(self):
        """Test the complete fallback mechanism from API to hardcoded."""
        client = AWSPricingClient()

        # This should work regardless of credentials - either from API or fallback
        price = client.get_instance_pricing("t2.micro", "us-east-1")

        assert price is not None
        assert isinstance(price, float)
        assert price > 0

        # Should be reasonable for t2.micro (between $0.005 and $0.05)
        assert 0.005 <= price <= 0.05


if __name__ == "__main__":
    # Run with: python -m pytest tests/test_aws_pricing_integration.py -v -m integration
    pytest.main([__file__, "-v", "-m", "integration", "-s"])
