"""Tests for pricing client implementations."""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from pathlib import Path
import tempfile

from clustrix.pricing_clients.base import BasePricingClient, PricingCache
from clustrix.pricing_clients.aws_pricing import AWSPricingClient


class TestPricingCache:
    """Test the pricing cache functionality."""

    def test_cache_init(self):
        """Test cache initialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "test_cache"
            cache = PricingCache(cache_dir=cache_dir, ttl_hours=24)

            assert cache.cache_dir == cache_dir
            assert cache.ttl == timedelta(hours=24)
            assert cache_dir.exists()

    def test_cache_get_miss(self):
        """Test cache miss returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PricingCache(cache_dir=Path(tmpdir))
            result = cache.get("nonexistent_key")
            assert result is None

    def test_cache_set_and_get(self):
        """Test setting and getting cached data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PricingCache(cache_dir=Path(tmpdir), ttl_hours=1)

            test_data = {"instance_type": "t2.micro", "price": 0.0116}
            cache.set("test_key", test_data)

            # Retrieve data
            result = cache.get("test_key")
            assert result == test_data

    def test_cache_expiration(self):
        """Test cache expiration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PricingCache(
                cache_dir=Path(tmpdir), ttl_hours=0
            )  # Immediate expiration

            test_data = {"price": 0.0116}
            cache.set("test_key", test_data)

            # Add a small delay to ensure cache expires
            import time

            time.sleep(0.001)  # 1ms delay

            # Data should be expired now
            result = cache.get("test_key")
            assert result is None

    def test_cache_file_corruption_handling(self):
        """Test cache handles corrupted files gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PricingCache(cache_dir=Path(tmpdir))

            # Create a corrupted cache file
            cache_file = cache.cache_dir / "corrupt_key.json"
            cache_file.write_text("invalid json content")

            # Should return None instead of raising exception
            result = cache.get("corrupt_key")
            assert result is None


class TestBasePricingClient:
    """Test the base pricing client functionality."""

    def test_is_pricing_data_outdated(self):
        """Test checking if pricing data is outdated."""

        class TestClient(BasePricingClient):
            def get_instance_pricing(self, instance_type, region, **kwargs):
                return None

            def get_all_pricing(self, region, **kwargs):
                return {}

            def _fetch_pricing_from_api(self, instance_type, region, **kwargs):
                return None

        client = TestClient()

        # No date set - should be outdated
        assert client.is_pricing_data_outdated(days=30) is True

        # Recent date - not outdated
        client._hardcoded_pricing_date = datetime.now().isoformat()
        assert client.is_pricing_data_outdated(days=30) is False

        # Old date - outdated
        old_date = (datetime.now() - timedelta(days=60)).isoformat()
        client._hardcoded_pricing_date = old_date
        assert client.is_pricing_data_outdated(days=30) is True

    def test_get_fallback_price(self):
        """Test fallback pricing retrieval."""

        class TestClient(BasePricingClient):
            def __init__(self):
                super().__init__()
                self._hardcoded_pricing = {"t2.micro": 0.0116}
                self._hardcoded_pricing_date = "2025-01-01"

            def get_instance_pricing(self, instance_type, region, **kwargs):
                return None

            def get_all_pricing(self, region, **kwargs):
                return {}

            def _fetch_pricing_from_api(self, instance_type, region, **kwargs):
                return None

        client = TestClient()

        # Existing instance type
        price = client._get_fallback_price("t2.micro")
        assert price == 0.0116

        # Non-existent instance type
        price = client._get_fallback_price("nonexistent.type")
        assert price is None


class TestAWSPricingClient:
    """Test the AWS pricing client implementation."""

    def test_init(self):
        """Test AWS pricing client initialization."""
        client = AWSPricingClient(cache_ttl_hours=12)

        assert client.cache.ttl == timedelta(hours=12)
        assert client._hardcoded_pricing_date is not None
        assert "t2.micro" in client._hardcoded_pricing

    def test_get_region_name(self):
        """Test region code to name conversion."""
        client = AWSPricingClient()

        # Test known regions
        assert client._get_region_name("us-east-1") == "US East (N. Virginia)"
        assert client._get_region_name("eu-west-1") == "EU (Ireland)"

        # Test fallback for unknown region
        assert client._get_region_name("unknown-region") == "US East (N. Virginia)"

    @patch("boto3.client")
    def test_get_instance_pricing_from_api(self, mock_boto_client):
        """Test getting pricing from AWS API."""
        # Mock the boto3 pricing client
        mock_pricing_client = MagicMock()
        mock_boto_client.return_value = mock_pricing_client

        # Mock API response
        mock_response = {
            "PriceList": [
                json.dumps(
                    {
                        "terms": {
                            "OnDemand": {
                                "sku1": {
                                    "priceDimensions": {
                                        "dim1": {"pricePerUnit": {"USD": "0.0104"}}
                                    }
                                }
                            }
                        }
                    }
                )
            ]
        }
        mock_pricing_client.get_products.return_value = mock_response

        client = AWSPricingClient()

        # Clear cache to ensure API is called
        import shutil

        if client.cache.cache_dir.exists():
            shutil.rmtree(client.cache.cache_dir)
        client.cache.cache_dir.mkdir(exist_ok=True)

        price = client.get_instance_pricing("t3.micro", "us-east-1")

        assert price == 0.0104
        mock_pricing_client.get_products.assert_called_once()

    def test_get_instance_pricing_fallback(self):
        """Test fallback to hardcoded pricing when API fails."""
        client = AWSPricingClient()

        # Mock the API to fail
        with patch.object(client, "_fetch_pricing_from_api", return_value=None):
            price = client.get_instance_pricing("t2.micro", "us-east-1")

            assert price == 0.0116  # Hardcoded price

    def test_get_instance_pricing_with_cache(self):
        """Test pricing retrieval with caching."""
        with tempfile.TemporaryDirectory() as tmpdir:
            client = AWSPricingClient()
            client.cache.cache_dir = Path(tmpdir)

            # Mock successful API call
            with patch.object(client, "_fetch_pricing_from_api") as mock_fetch:
                mock_fetch.return_value = {"price": 0.0104}

                # First call - should hit API
                price1 = client.get_instance_pricing("t3.micro", "us-east-1")
                assert price1 == 0.0104
                assert mock_fetch.call_count == 1

                # Second call - should hit cache
                price2 = client.get_instance_pricing("t3.micro", "us-east-1")
                assert price2 == 0.0104
                assert mock_fetch.call_count == 1  # No additional API call

    def test_get_spot_pricing(self):
        """Test spot instance pricing calculation."""
        client = AWSPricingClient()

        # Test with known instance type
        spot_price = client.get_spot_pricing("t2.micro", "us-east-1")
        on_demand_price = 0.0116
        expected_spot_price = on_demand_price * 0.3  # 70% discount

        assert spot_price == pytest.approx(expected_spot_price, rel=1e-4)

        # Test with unknown instance type
        spot_price = client.get_spot_pricing("unknown.type", "us-east-1")
        assert spot_price is None

    @patch("boto3.client")
    def test_fetch_pricing_no_credentials(self, mock_boto_client):
        """Test handling of missing AWS credentials."""
        from botocore.exceptions import NoCredentialsError

        mock_boto_client.side_effect = NoCredentialsError()

        client = AWSPricingClient()
        result = client._fetch_pricing_from_api("t2.micro", "us-east-1")

        assert result is None

    def test_get_all_pricing(self):
        """Test getting all pricing information."""
        client = AWSPricingClient()

        all_pricing = client.get_all_pricing("us-east-1")

        assert isinstance(all_pricing, dict)
        assert "t2.micro" in all_pricing
        assert all_pricing["t2.micro"] == 0.0116


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
