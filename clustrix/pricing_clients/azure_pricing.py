"""Azure pricing client implementation using Azure Retail Prices API."""

import logging
from typing import Dict, Optional, Any

import requests

from .base import BasePricingClient

logger = logging.getLogger(__name__)


class AzurePricingClient(BasePricingClient):
    """Client for fetching Azure VM pricing using Azure Retail Prices API."""

    def __init__(self, cache_ttl_hours: int = 24):
        """Initialize Azure pricing client."""
        super().__init__(cache_ttl_hours)

        # Azure Retail Prices API endpoint
        self.api_url = "https://prices.azure.com/api/retail/prices"
        self.api_version = "2021-10-01-preview"

        # Hardcoded pricing as fallback (as of 2025-01)
        self._hardcoded_pricing_date = "2025-01-01"
        self._hardcoded_pricing = {
            # Basic VMs
            "Standard_A1_v2": 0.085,
            "Standard_A2_v2": 0.17,
            "Standard_A4_v2": 0.34,
            # General Purpose
            "Standard_D2s_v3": 0.096,
            "Standard_D4s_v3": 0.192,
            "Standard_D8s_v3": 0.384,
            "Standard_D16s_v3": 0.768,
            # Compute Optimized
            "Standard_F2s_v2": 0.085,
            "Standard_F4s_v2": 0.17,
            "Standard_F8s_v2": 0.34,
            "Standard_F16s_v2": 0.68,
            # Memory Optimized
            "Standard_E2s_v3": 0.126,
            "Standard_E4s_v3": 0.252,
            "Standard_E8s_v3": 0.504,
            "Standard_E16s_v3": 1.008,
            # GPU VMs
            "Standard_NC6s_v3": 3.06,
            "Standard_NC12s_v3": 6.12,
            "Standard_NC24s_v3": 12.24,
            "Standard_ND40rs_v2": 27.20,
            # Default fallback
            "default": 0.10,
        }

        # Region mapping for Azure API
        self.region_mapping = {
            "eastus": "East US",
            "eastus2": "East US 2",
            "westus": "West US",
            "westus2": "West US 2",
            "westus3": "West US 3",
            "centralus": "Central US",
            "northcentralus": "North Central US",
            "southcentralus": "South Central US",
            "westcentralus": "West Central US",
            "canadacentral": "Canada Central",
            "canadaeast": "Canada East",
            "brazilsouth": "Brazil South",
            "northeurope": "North Europe",
            "westeurope": "West Europe",
            "francecentral": "France Central",
            "germanywestcentral": "Germany West Central",
            "norwayeast": "Norway East",
            "switzerlandnorth": "Switzerland North",
            "uksouth": "UK South",
            "ukwest": "UK West",
            "eastasia": "East Asia",
            "southeastasia": "Southeast Asia",
            "australiaeast": "Australia East",
            "australiasoutheast": "Australia Southeast",
            "centralindia": "Central India",
            "southindia": "South India",
            "westindia": "West India",
            "japaneast": "Japan East",
            "japanwest": "Japan West",
            "koreacentral": "Korea Central",
            "koreasouth": "Korea South",
        }

    def get_instance_pricing(
        self,
        instance_type: str,
        region: str,
        operating_system: str = "Linux",
        **kwargs,
    ) -> Optional[float]:
        """Get hourly pricing for a specific Azure VM size.

        Args:
            instance_type: Azure VM size (e.g., 'Standard_D2s_v3')
            region: Azure region (e.g., 'eastus', 'westeurope')
            operating_system: OS type ('Linux', 'Windows')

        Returns:
            Hourly price in USD or None if not found
        """
        # Generate cache key
        cache_key = f"azure_{region}_{instance_type}_{operating_system}"

        # Check cache first
        cached_data = self.cache.get(cache_key)
        if cached_data and "price" in cached_data:
            return cached_data["price"]

        # Try to fetch from API
        try:
            pricing_data = self._fetch_pricing_from_api(
                instance_type=instance_type,
                region=region,
                operating_system=operating_system,
            )

            if pricing_data and "price" in pricing_data:
                # Cache the result
                self.cache.set(cache_key, pricing_data)
                return pricing_data["price"]
        except Exception as e:
            logger.warning(f"Failed to fetch Azure pricing from API: {e}")

        # Fall back to hardcoded pricing
        fallback_price = self._get_fallback_price(instance_type)
        if fallback_price is None:
            # Use default price for unknown instance types
            fallback_price = self._hardcoded_pricing.get("default")
        return fallback_price

    def get_all_pricing(
        self, region: str, operating_system: str = "Linux", **kwargs
    ) -> Dict[str, float]:
        """Get all Azure VM pricing for a region.

        Args:
            region: Azure region
            operating_system: OS type

        Returns:
            Dictionary mapping VM sizes to hourly prices
        """
        # For simplicity, return hardcoded pricing with a warning
        # In a full implementation, this would query the API for all VM sizes
        if self.is_pricing_data_outdated():
            logger.warning(
                f"Using potentially outdated pricing data from {self._hardcoded_pricing_date}"
            )

        return self._hardcoded_pricing.copy()

    def _fetch_pricing_from_api(
        self,
        instance_type: Optional[str],
        region: str,
        operating_system: str = "Linux",
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """Fetch pricing from Azure Retail Prices API.

        Args:
            instance_type: Azure VM size
            region: Azure region code
            operating_system: Operating system

        Returns:
            Pricing data dictionary or None
        """
        try:
            # Build filter query for Azure API
            filters = [
                "serviceName eq 'Virtual Machines'",
                f"armRegionName eq '{region.lower()}'",
                f"armSkuName eq '{instance_type}'",
                "priceType eq 'Consumption'",
            ]

            # Add OS filter - be more specific
            if operating_system.lower() == "windows":
                filters.append("contains(productName, 'Windows')")
            # Skip the "not contains" filter for Linux to avoid OData issues

            filter_query = " and ".join(filters)

            # Make API request
            params = {"api-version": self.api_version, "$filter": filter_query}

            response = requests.get(self.api_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            if "Items" in data and len(data["Items"]) > 0:
                # Find the best matching item (regular pricing, not spot/low priority)
                best_item = None
                for item in data["Items"]:
                    meter_name = item.get("meterName", "")
                    product_name = item.get("productName", "")

                    # Skip spot and low priority instances
                    if "Spot" in meter_name or "Low Priority" in meter_name:
                        continue

                    # For Linux, avoid Windows products
                    if (
                        operating_system.lower() == "linux"
                        and "Windows" in product_name
                    ):
                        continue

                    # For Windows, prefer Windows products
                    if (
                        operating_system.lower() == "windows"
                        and "Windows" not in product_name
                    ):
                        continue

                    # This looks like the right item
                    best_item = item
                    break

                if best_item:
                    price = float(best_item["retailPrice"])
                else:
                    # Fallback to first item if no perfect match
                    price = float(data["Items"][0]["retailPrice"])
                    best_item = data["Items"][0]

                return {
                    "price": price,
                    "instance_type": instance_type,
                    "region": region,
                    "operating_system": operating_system,
                    "currency": best_item.get("currencyCode", "USD"),
                    "meter_name": best_item.get("meterName", ""),
                    "product_name": best_item.get("productName", ""),
                }

        except requests.RequestException as e:
            logger.debug(f"Azure API request failed: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Error parsing Azure API response: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error fetching Azure pricing: {e}")

        return None

    def _get_region_name(self, region_code: str) -> str:
        """Convert region code to region name for Azure API.

        Args:
            region_code: Azure region code (e.g., 'eastus')

        Returns:
            Region name (e.g., 'East US')
        """
        return self.region_mapping.get(region_code.lower(), region_code)

    def get_spot_pricing(self, instance_type: str, region: str) -> Optional[float]:
        """Get spot VM pricing for Azure.

        Args:
            instance_type: Azure VM size
            region: Azure region

        Returns:
            Estimated spot price (uses API or hardcoded discount)
        """
        # Try to get spot pricing from API
        try:
            pricing_data = self._fetch_spot_pricing_from_api(instance_type, region)
            if pricing_data and "price" in pricing_data:
                return pricing_data["price"]
        except Exception as e:
            logger.debug(f"Failed to get spot pricing from API: {e}")

        # Fall back to estimating from on-demand pricing
        on_demand_price = self.get_instance_pricing(instance_type, region)
        if on_demand_price:
            # Apply approximate spot discount (varies by VM family)
            spot_discount = 0.8  # 80% discount is typical for Azure spot VMs
            return on_demand_price * (1 - spot_discount)
        return None

    def _fetch_spot_pricing_from_api(
        self, instance_type: str, region: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch spot pricing from Azure API.

        Args:
            instance_type: Azure VM size
            region: Azure region

        Returns:
            Spot pricing data or None
        """
        try:
            # Build filter query for spot pricing
            filters = [
                "serviceName eq 'Virtual Machines'",
                f"armRegionName eq '{region.lower()}'",
                f"armSkuName eq '{instance_type}'",
                "priceType eq 'Consumption'",
                "contains(meterName, 'Spot')",
            ]

            filter_query = " and ".join(filters)
            params = {"api-version": self.api_version, "$filter": filter_query}

            response = requests.get(self.api_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()

            if "Items" in data and len(data["Items"]) > 0:
                item = data["Items"][0]
                price = float(item["retailPrice"])

                return {
                    "price": price,
                    "instance_type": instance_type,
                    "region": region,
                    "pricing_type": "spot",
                    "currency": item.get("currencyCode", "USD"),
                }

        except Exception as e:
            logger.debug(f"Failed to fetch Azure spot pricing: {e}")

        return None

    def get_pricing_by_service(
        self, service_name: str = "Virtual Machines", region: str = "eastus"
    ) -> Dict[str, Any]:
        """Get pricing for all items in a specific Azure service.

        Args:
            service_name: Azure service name (e.g., 'Virtual Machines')
            region: Azure region

        Returns:
            Dictionary with pricing information
        """
        try:
            filters = [
                f"serviceName eq '{service_name}'",
                f"armRegionName eq '{region.lower()}'",
                "priceType eq 'Consumption'",
            ]

            filter_query = " and ".join(filters)
            params = {"api-version": self.api_version, "$filter": filter_query}

            response = requests.get(self.api_url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            pricing_info = {}

            if "Items" in data:
                for item in data["Items"]:
                    sku_name = item.get("armSkuName", "unknown")
                    if sku_name not in pricing_info:
                        pricing_info[sku_name] = {
                            "price": float(item["retailPrice"]),
                            "currency": item.get("currencyCode", "USD"),
                            "meter_name": item.get("meterName", ""),
                            "product_name": item.get("productName", ""),
                        }

            return pricing_info

        except Exception as e:
            logger.warning(f"Failed to fetch Azure service pricing: {e}")
            return {}
