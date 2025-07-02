"""Simplified comprehensive tests for GCP pricing client."""

import logging
from unittest.mock import Mock, patch, MagicMock
import pytest

from clustrix.pricing_clients.gcp_pricing import GCPPricingClient


class TestGCPPricingClient:
    """Test GCPPricingClient class."""

    def test_init(self):
        """Test initialization."""
        client = GCPPricingClient()
        assert client.cache.ttl.total_seconds() == 24 * 3600  # 24 hours in seconds
        assert client.compute_service_id == "6F81-5844-456A"
        assert client._hardcoded_pricing_date == "2025-01-01"
        assert isinstance(client._hardcoded_pricing, dict)
        assert "n1-standard-1" in client._hardcoded_pricing
        assert client.region_mapping["us-central1"] == "us-central1"

        client_custom = GCPPricingClient(cache_ttl_hours=12)
        assert (
            client_custom.cache.ttl.total_seconds() == 12 * 3600
        )  # 12 hours in seconds

    def test_get_instance_pricing_cached(self):
        """Test instance pricing retrieval from cache."""
        client = GCPPricingClient()

        # Mock cache hit
        cached_data = {"price": 0.15}
        client.cache.get = Mock(return_value=cached_data)

        result = client.get_instance_pricing("n1-standard-4", "us-central1")

        assert result == 0.15
        client.cache.get.assert_called_once_with("gcp_us-central1_n1-standard-4")

    @patch.object(GCPPricingClient, "_fetch_pricing_from_api")
    def test_get_instance_pricing_api_success(self, mock_fetch):
        """Test successful instance pricing retrieval from API."""
        client = GCPPricingClient()

        # Mock cache miss
        client.cache.get = Mock(return_value=None)
        client.cache.set = Mock()

        # Mock successful API response
        api_data = {
            "price": 0.19,
            "region": "us-central1",
            "instance_type": "n1-standard-4",
        }
        mock_fetch.return_value = api_data

        result = client.get_instance_pricing("n1-standard-4", "us-central1")

        assert result == 0.19
        client.cache.set.assert_called_once_with(
            "gcp_us-central1_n1-standard-4", api_data
        )

    @patch.object(GCPPricingClient, "_fetch_pricing_from_api")
    @patch.object(GCPPricingClient, "_get_fallback_price")
    def test_get_instance_pricing_api_failure_fallback(self, mock_fallback, mock_fetch):
        """Test instance pricing with API failure using fallback."""
        client = GCPPricingClient()

        # Mock cache miss
        client.cache.get = Mock(return_value=None)

        # Mock API failure
        mock_fetch.side_effect = Exception("API error")

        # Mock fallback price
        mock_fallback.return_value = 0.19

        result = client.get_instance_pricing("n1-standard-4", "us-central1")

        assert result == 0.19
        mock_fallback.assert_called_once_with("n1-standard-4")

    @patch.object(GCPPricingClient, "_fetch_pricing_from_api")
    @patch.object(GCPPricingClient, "_get_fallback_price")
    def test_get_instance_pricing_no_fallback_default(self, mock_fallback, mock_fetch):
        """Test instance pricing with no fallback using default price."""
        client = GCPPricingClient()

        # Mock cache miss
        client.cache.get = Mock(return_value=None)

        # Mock API failure
        mock_fetch.side_effect = Exception("API error")

        # Mock no fallback price
        mock_fallback.return_value = None

        result = client.get_instance_pricing("unknown-instance", "us-central1")

        assert result == 0.10  # default price

    @patch.object(GCPPricingClient, "is_pricing_data_outdated")
    def test_get_all_pricing(self, mock_outdated):
        """Test getting all pricing for a region."""
        client = GCPPricingClient()
        mock_outdated.return_value = False

        result = client.get_all_pricing("us-central1")

        assert isinstance(result, dict)
        assert "n1-standard-1" in result
        assert "n2-standard-2" in result
        assert result["n1-standard-1"] == 0.0475

    @patch.object(GCPPricingClient, "is_pricing_data_outdated")
    def test_get_all_pricing_outdated_warning(self, mock_outdated):
        """Test getting all pricing with outdated data warning."""
        client = GCPPricingClient()
        mock_outdated.return_value = True

        with patch("clustrix.pricing_clients.gcp_pricing.logger") as mock_logger:
            result = client.get_all_pricing("us-central1")

            mock_logger.warning.assert_called_once()
            assert "outdated pricing data" in mock_logger.warning.call_args[0][0]

    def test_fetch_pricing_from_api_import_error(self):
        """Test _fetch_pricing_from_api with ImportError."""
        client = GCPPricingClient()

        # Mock the import to fail
        with patch("builtins.__import__", side_effect=ImportError()):
            result = client._fetch_pricing_from_api("n1-standard-4", "us-central1")

            assert result is None

    # Note: Google Cloud billing API tests skipped due to library availability

    @patch.object(GCPPricingClient, "_fetch_preemptible_pricing_from_api")
    def test_get_preemptible_pricing_api_success(self, mock_fetch):
        """Test successful preemptible pricing from API."""
        client = GCPPricingClient()

        api_data = {"price": 0.038}  # 80% discount from 0.19
        mock_fetch.return_value = api_data

        result = client.get_preemptible_pricing("n1-standard-4", "us-central1")

        assert result == 0.038

    @patch.object(GCPPricingClient, "_fetch_preemptible_pricing_from_api")
    @patch.object(GCPPricingClient, "get_instance_pricing")
    def test_get_preemptible_pricing_fallback(self, mock_get_pricing, mock_fetch):
        """Test preemptible pricing fallback to on-demand with discount."""
        client = GCPPricingClient()

        # Mock API failure
        mock_fetch.side_effect = Exception("API error")

        # Mock on-demand pricing
        mock_get_pricing.return_value = 0.19

        result = client.get_preemptible_pricing("n1-standard-4", "us-central1")

        # Should be 20% of on-demand price (80% discount)
        assert abs(result - 0.038) < 0.001

    @patch.object(GCPPricingClient, "_fetch_preemptible_pricing_from_api")
    @patch.object(GCPPricingClient, "get_instance_pricing")
    def test_get_preemptible_pricing_no_on_demand(self, mock_get_pricing, mock_fetch):
        """Test preemptible pricing when on-demand price not available."""
        client = GCPPricingClient()

        # Mock API failure
        mock_fetch.side_effect = Exception("API error")

        # Mock no on-demand pricing
        mock_get_pricing.return_value = None

        result = client.get_preemptible_pricing("n1-standard-4", "us-central1")

        assert result is None

    def test_fetch_preemptible_pricing_from_api_import_error(self):
        """Test _fetch_preemptible_pricing_from_api with ImportError."""
        client = GCPPricingClient()

        with patch("builtins.__import__", side_effect=ImportError()):
            result = client._fetch_preemptible_pricing_from_api(
                "n1-standard-4", "us-central1"
            )

            assert result is None

    def test_get_sustained_use_discount_no_discount(self):
        """Test sustained use discount with low usage."""
        client = GCPPricingClient()

        # 10% of month usage
        hours_used = 24 * 3  # 3 days
        base_price = 0.19

        result = client.get_sustained_use_discount(hours_used, base_price)

        assert result == 0.19  # No discount

    def test_get_sustained_use_discount_25_percent(self):
        """Test sustained use discount with 25% usage."""
        client = GCPPricingClient()

        # 30% of month usage
        hours_used = 24 * 9  # 9 days
        base_price = 0.19

        result = client.get_sustained_use_discount(hours_used, base_price)

        # 10% discount
        expected = 0.19 * 0.9
        assert result == expected

    def test_get_sustained_use_discount_50_percent(self):
        """Test sustained use discount with 50% usage."""
        client = GCPPricingClient()

        # 60% of month usage
        hours_used = 24 * 18  # 18 days
        base_price = 0.19

        result = client.get_sustained_use_discount(hours_used, base_price)

        # 20% discount
        expected = 0.19 * 0.8
        assert result == expected

    def test_get_sustained_use_discount_75_percent(self):
        """Test sustained use discount with 75% usage."""
        client = GCPPricingClient()

        # 80% of month usage
        hours_used = 24 * 24  # 24 days
        base_price = 0.19

        result = client.get_sustained_use_discount(hours_used, base_price)

        # 30% discount
        expected = 0.19 * 0.7
        assert result == expected

    def test_get_custom_machine_pricing_us_central1(self):
        """Test custom machine pricing for us-central1."""
        client = GCPPricingClient()

        result = client.get_custom_machine_pricing(4, 16, "us-central1")

        # 4 vCPUs * 0.033174 + 16 GB * 0.004446 = 0.132696 + 0.071136 = 0.203832
        expected = 4 * 0.033174 + 16 * 0.004446
        assert abs(result - expected) < 0.001

    def test_get_custom_machine_pricing_europe_west1(self):
        """Test custom machine pricing for europe-west1 with regional multiplier."""
        client = GCPPricingClient()

        result = client.get_custom_machine_pricing(2, 8, "europe-west1")

        # Base price with 1.1x multiplier
        base_price = 2 * 0.033174 + 8 * 0.004446
        expected = base_price * 1.1
        assert abs(result - expected) < 0.001

    def test_get_custom_machine_pricing_unknown_region(self):
        """Test custom machine pricing for unknown region with default multiplier."""
        client = GCPPricingClient()

        result = client.get_custom_machine_pricing(1, 4, "unknown-region")

        # Base price with default 1.1x multiplier
        base_price = 1 * 0.033174 + 4 * 0.004446
        expected = base_price * 1.1
        assert abs(result - expected) < 0.001

    def test_get_custom_machine_pricing_asia_northeast1(self):
        """Test custom machine pricing for asia-northeast1."""
        client = GCPPricingClient()

        result = client.get_custom_machine_pricing(8, 32, "asia-northeast1")

        # Base price with 1.2x multiplier
        base_price = 8 * 0.033174 + 32 * 0.004446
        expected = base_price * 1.2
        assert abs(result - expected) < 0.001
