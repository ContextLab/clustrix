"""Tests for Azure pricing API integration."""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import requests

from clustrix.pricing_clients.azure_pricing import AzurePricingClient


class TestAzurePricingClient:
    """Test Azure pricing client functionality."""

    def test_init(self):
        """Test Azure pricing client initialization."""
        client = AzurePricingClient(cache_ttl_hours=12)

        assert client.api_url == "https://prices.azure.com/api/retail/prices"
        assert client.api_version == "2021-10-01-preview"
        assert "Standard_D2s_v3" in client._hardcoded_pricing
        assert client._hardcoded_pricing_date is not None

    def test_get_region_name(self):
        """Test region code to name conversion."""
        client = AzurePricingClient()

        # Test known regions
        assert client._get_region_name("eastus") == "East US"
        assert client._get_region_name("westeurope") == "West Europe"
        assert client._get_region_name("southeastasia") == "Southeast Asia"

        # Test case insensitive
        assert client._get_region_name("EASTUS") == "East US"

        # Test fallback for unknown region
        assert client._get_region_name("unknown-region") == "unknown-region"

    @patch("requests.get")
    def test_get_instance_pricing_from_api_success(self, mock_get):
        """Test successful Azure API pricing retrieval."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.json.return_value = {
            "Items": [
                {
                    "retailPrice": 0.096,
                    "currencyCode": "USD",
                    "meterName": "D2s v3",
                    "productName": "Virtual Machines D2s v3 Series",
                    "armSkuName": "Standard_D2s_v3",
                    "armRegionName": "eastus",
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = AzurePricingClient()

        # Clear cache to ensure API is called
        import shutil

        if client.cache.cache_dir.exists():
            shutil.rmtree(client.cache.cache_dir)
        client.cache.cache_dir.mkdir(exist_ok=True)

        price = client.get_instance_pricing("Standard_D2s_v3", "eastus")

        assert price == 0.096
        mock_get.assert_called_once()

        # Verify the API call parameters
        call_args = mock_get.call_args
        assert "prices.azure.com" in call_args[0][0]
        assert "armSkuName eq 'Standard_D2s_v3'" in call_args[1]["params"]["$filter"]

    @patch("requests.get")
    def test_get_instance_pricing_api_failure(self, mock_get):
        """Test fallback when Azure API fails."""
        # Mock API failure
        mock_get.side_effect = requests.RequestException("API Error")

        client = AzurePricingClient()
        price = client.get_instance_pricing("Standard_D2s_v3", "eastus")

        # Should fall back to hardcoded pricing
        assert price == 0.096  # Hardcoded price

    @patch("requests.get")
    def test_get_instance_pricing_empty_response(self, mock_get):
        """Test handling of empty API response."""
        # Mock empty response
        mock_response = Mock()
        mock_response.json.return_value = {"Items": []}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = AzurePricingClient()
        price = client.get_instance_pricing("Unknown_VM", "eastus")

        # Should fall back to default pricing for unknown VM
        assert price == 0.10  # Default fallback price

    def test_get_instance_pricing_fallback(self):
        """Test fallback to hardcoded pricing."""
        client = AzurePricingClient()

        # Mock the API to fail
        with patch.object(client, "_fetch_pricing_from_api", return_value=None):
            price = client.get_instance_pricing("Standard_D2s_v3", "eastus")

            assert price == 0.096  # Hardcoded price

    @patch("requests.get")
    def test_get_spot_pricing_from_api(self, mock_get):
        """Test spot pricing from Azure API."""
        # Mock spot pricing response
        mock_response = Mock()
        mock_response.json.return_value = {
            "Items": [
                {
                    "retailPrice": 0.0192,  # 80% discount
                    "currencyCode": "USD",
                    "meterName": "D2s v3 Spot",
                    "productName": "Virtual Machines D2s v3 Series Spot",
                    "armSkuName": "Standard_D2s_v3",
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = AzurePricingClient()
        spot_price = client.get_spot_pricing("Standard_D2s_v3", "eastus")

        assert spot_price == 0.0192
        mock_get.assert_called_once()

        # Verify spot filter is included
        call_args = mock_get.call_args
        assert "contains(meterName, 'Spot')" in call_args[1]["params"]["$filter"]

    def test_get_spot_pricing_fallback(self):
        """Test spot pricing fallback calculation."""
        client = AzurePricingClient()

        # Mock API calls to fail for spot, succeed for on-demand
        with patch.object(client, "_fetch_spot_pricing_from_api", return_value=None):
            with patch.object(client, "get_instance_pricing", return_value=0.096):
                spot_price = client.get_spot_pricing("Standard_D2s_v3", "eastus")

                # Should be 80% discount from on-demand
                expected_price = 0.096 * 0.2  # 80% discount
                assert spot_price == pytest.approx(expected_price, rel=1e-4)

    @patch("requests.get")
    def test_windows_vs_linux_pricing(self, mock_get):
        """Test different pricing for Windows vs Linux."""
        # Mock API response
        mock_response = Mock()
        mock_response.json.return_value = {"Items": []}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = AzurePricingClient()

        # Clear cache to ensure API is called
        import shutil

        if client.cache.cache_dir.exists():
            shutil.rmtree(client.cache.cache_dir)
        client.cache.cache_dir.mkdir(exist_ok=True)

        # Test Linux pricing call
        client.get_instance_pricing(
            "Standard_D2s_v3", "eastus", operating_system="Linux"
        )

        # Verify Linux filter - should not have Windows filter
        call_args = mock_get.call_args
        assert "Windows" not in call_args[1]["params"]["$filter"]

        # Test Windows pricing call
        client.get_instance_pricing(
            "Standard_D2s_v3", "eastus", operating_system="Windows"
        )

        # Verify Windows filter
        call_args = mock_get.call_args
        assert "contains(productName, 'Windows')" in call_args[1]["params"]["$filter"]

    def test_get_all_pricing(self):
        """Test getting all pricing information."""
        client = AzurePricingClient()

        all_pricing = client.get_all_pricing("eastus")

        assert isinstance(all_pricing, dict)
        assert "Standard_D2s_v3" in all_pricing
        assert all_pricing["Standard_D2s_v3"] == 0.096

    @patch("requests.get")
    def test_get_pricing_by_service(self, mock_get):
        """Test getting pricing for a specific service."""
        # Mock service pricing response
        mock_response = Mock()
        mock_response.json.return_value = {
            "Items": [
                {
                    "retailPrice": 0.096,
                    "currencyCode": "USD",
                    "meterName": "D2s v3",
                    "productName": "Virtual Machines D2s v3 Series",
                    "armSkuName": "Standard_D2s_v3",
                },
                {
                    "retailPrice": 0.192,
                    "currencyCode": "USD",
                    "meterName": "D4s v3",
                    "productName": "Virtual Machines D4s v3 Series",
                    "armSkuName": "Standard_D4s_v3",
                },
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = AzurePricingClient()
        pricing = client.get_pricing_by_service("Virtual Machines", "eastus")

        assert "Standard_D2s_v3" in pricing
        assert "Standard_D4s_v3" in pricing
        assert pricing["Standard_D2s_v3"]["price"] == 0.096
        assert pricing["Standard_D4s_v3"]["price"] == 0.192

    def test_cache_functionality(self):
        """Test pricing cache functionality."""
        # Create client with temporary cache directory
        with tempfile.TemporaryDirectory() as tmpdir:
            client = AzurePricingClient()
            client.cache.cache_dir = Path(tmpdir)

            # Mock successful API call
            with patch.object(client, "_fetch_pricing_from_api") as mock_fetch:
                mock_fetch.return_value = {"price": 0.096}

                # First call - should hit API
                price1 = client.get_instance_pricing("Standard_D2s_v3", "eastus")
                assert price1 == 0.096
                assert mock_fetch.call_count == 1

                # Second call - should hit cache
                price2 = client.get_instance_pricing("Standard_D2s_v3", "eastus")
                assert price2 == 0.096
                assert mock_fetch.call_count == 1  # No additional API call

    @patch("requests.get")
    def test_api_timeout_handling(self, mock_get):
        """Test handling of API timeouts."""
        # Mock timeout
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        client = AzurePricingClient()
        price = client.get_instance_pricing("Standard_D2s_v3", "eastus")

        # Should fall back to hardcoded pricing
        assert price == 0.096

    @patch("requests.get")
    def test_malformed_response_handling(self, mock_get):
        """Test handling of malformed API responses."""
        # Mock malformed response
        mock_response = Mock()
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client = AzurePricingClient()
        price = client.get_instance_pricing("Standard_D2s_v3", "eastus")

        # Should fall back to hardcoded pricing
        assert price == 0.096


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
