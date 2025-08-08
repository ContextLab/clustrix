"""Lambda Cloud pricing client implementation."""

import logging
from typing import Dict, Optional, Any
import requests

from .base import BasePricingClient

logger = logging.getLogger(__name__)


class LambdaPricingClient(BasePricingClient):
    """Client for fetching Lambda Cloud instance pricing."""

    def __init__(self, cache_ttl_hours: int = 24):
        """Initialize Lambda Cloud pricing client."""
        super().__init__(cache_ttl_hours)

        # Hardcoded pricing as fallback (as of 2025-01)
        self._hardcoded_pricing_date = "2025-01-08"
        self._hardcoded_pricing = {
            # Single GPU instances
            "gpu_1x_rtx6000ada": 0.75,
            "gpu_1x_a10": 0.60,
            "gpu_1x_a6000": 0.80,
            "gpu_1x_a100": 1.10,
            "gpu_1x_a100_80gb": 1.40,
            "gpu_1x_h100": 2.50,
            # Multi-GPU instances
            "gpu_2x_a10": 1.20,
            "gpu_2x_a6000": 1.60,
            "gpu_2x_a100": 2.20,
            "gpu_2x_a100_80gb": 2.80,
            "gpu_4x_a10": 2.40,
            "gpu_4x_a6000": 3.20,
            "gpu_4x_a100": 4.40,
            "gpu_4x_a100_80gb": 5.60,
            "gpu_8x_a100": 8.80,
            "gpu_8x_a100_80gb": 11.20,
            "gpu_8x_v100": 8.00,
            "gpu_8x_h100": 20.00,
            # CPU instances
            "cpu_4x": 0.10,
            "cpu_8x": 0.20,
            "cpu_16x": 0.40,
            # Common aliases
            "rtx6000ada": 0.75,
            "a10": 0.60,
            "a6000": 0.80,
            "a100": 1.10,
            "a100_40gb": 1.10,
            "a100_80gb": 1.40,
            "h100": 2.50,
            "2xa100_40gb": 2.20,
            "4xa100_40gb": 4.40,
            "8xa100_40gb": 8.80,
            "2xa100_80gb": 2.80,
            "4xa100_80gb": 5.60,
            "8xa100_80gb": 11.20,
            "8xh100": 20.00,
            # Default fallback
            "default": 1.00,
        }

        self.base_url = "https://cloud.lambdalabs.com/api/v1"
        self.api_key: Optional[str] = None
        self.authenticated = False

    def authenticate(self, api_key: str) -> bool:
        """Authenticate with Lambda Cloud API.

        Args:
            api_key: Lambda Cloud API key

        Returns:
            True if authentication successful
        """
        if not api_key:
            logger.warning("No Lambda Cloud API key provided")
            return False

        self.api_key = api_key
        self.authenticated = True

        # Test authentication by making a simple API call
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            response = requests.get(
                f"{self.base_url}/instance-types", headers=headers, timeout=10
            )
            if response.status_code == 200:
                logger.info("Lambda Cloud API authentication successful")
                return True
            else:
                logger.warning(
                    f"Lambda Cloud API authentication failed: {response.status_code}"
                )
                self.authenticated = False
                return False
        except requests.RequestException as e:
            logger.warning(f"Lambda Cloud API authentication error: {e}")
            self.authenticated = False
            return False

    def get_instance_pricing(
        self, instance_type: str, region: str = "us-east-1", **kwargs
    ) -> Optional[float]:
        """Get hourly pricing for a specific Lambda Cloud instance type.

        Args:
            instance_type: Lambda Cloud instance type (e.g., 'gpu_1x_a100')
            region: Region (Lambda Cloud is primarily US-based)
            **kwargs: Additional parameters (unused for Lambda Cloud)

        Returns:
            Hourly price in USD or None if not found
        """
        # Try to get from cache first
        cache_key = f"lambda_{instance_type}_{region}"
        cached_price = self.cache.get(cache_key)
        if cached_price:
            return cached_price.get("price")

        # Try to fetch from API
        if self.authenticated:
            try:
                api_pricing = self._fetch_pricing_from_api(instance_type, region)
                if api_pricing and instance_type in api_pricing:
                    price = api_pricing[instance_type]
                    # Cache the result
                    self.cache.set(cache_key, {"price": price, "source": "api"})
                    return price
            except Exception as e:
                logger.warning(f"Failed to fetch Lambda Cloud pricing from API: {e}")

        # Fall back to hardcoded pricing
        fallback_price = self._get_fallback_price(instance_type)
        if fallback_price is not None:
            # Cache fallback result (shorter TTL)
            self.cache.set(cache_key, {"price": fallback_price, "source": "hardcoded"})
            return fallback_price

        # Try common variations of instance type names
        variations = self._get_instance_variations(instance_type)
        for variation in variations:
            fallback_price = self._get_fallback_price(variation)
            if fallback_price is not None:
                logger.info(
                    f"Found pricing for {instance_type} using alias {variation}"
                )
                self.cache.set(
                    cache_key, {"price": fallback_price, "source": "hardcoded_alias"}
                )
                return fallback_price

        # Default fallback
        default_price = self._hardcoded_pricing.get("default")
        if default_price:
            logger.warning(
                f"Using default Lambda Cloud pricing for unknown instance type: {instance_type}"
            )
            return default_price

        return None

    def get_all_pricing(self, region: str = "us-east-1", **kwargs) -> Dict[str, float]:
        """Get all Lambda Cloud instance pricing for a region.

        Args:
            region: Region code (Lambda Cloud is primarily US-based)
            **kwargs: Additional parameters

        Returns:
            Dictionary mapping instance types to hourly prices
        """
        # Try to get comprehensive pricing from API
        if self.authenticated:
            try:
                api_pricing = self._fetch_pricing_from_api(None, region)
                if api_pricing:
                    return api_pricing
            except Exception as e:
                logger.warning(f"Failed to fetch all Lambda Cloud pricing: {e}")

        # Return hardcoded pricing as fallback
        logger.warning(
            f"Using hardcoded Lambda Cloud pricing "
            f"(last updated: {self._hardcoded_pricing_date})"
        )
        return self._hardcoded_pricing.copy()

    def _fetch_pricing_from_api(
        self, instance_type: Optional[str], region: str, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Fetch pricing data from Lambda Cloud API.

        Args:
            instance_type: Optional specific instance type
            region: Region code
            **kwargs: Additional parameters

        Returns:
            Dictionary with pricing data or None if failed
        """
        if not self.authenticated:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            # First, get instance types to see if pricing is included
            response = requests.get(
                f"{self.base_url}/instance-types", headers=headers, timeout=15
            )

            if response.status_code != 200:
                logger.warning(
                    f"Lambda Cloud API returned status {response.status_code}"
                )
                return None

            data = response.json()
            if "data" not in data:
                logger.warning("Lambda Cloud API response missing 'data' field")
                return None

            pricing_data = {}
            instance_types = data["data"]

            for instance_info in instance_types:
                # Lambda Cloud API structure may vary, extract pricing if available
                name = instance_info.get("name")
                if not name:
                    continue

                # Look for price in different possible fields
                price = None
                price_fields = ["price", "price_per_hour", "hourly_price", "cost"]
                for field in price_fields:
                    if field in instance_info:
                        price_val = instance_info[field]
                        if isinstance(price_val, (int, float)):
                            price = float(price_val)
                            break
                        elif isinstance(price_val, dict) and "amount" in price_val:
                            price = float(price_val["amount"])
                            break

                # If we found a price, add it to our pricing data
                if price is not None:
                    pricing_data[name] = price
                    logger.debug(
                        f"Found Lambda Cloud price for {name}: ${price:.3f}/hr"
                    )

            # If we got pricing data from API, return it
            if pricing_data:
                logger.info(
                    f"Successfully fetched Lambda Cloud pricing for {len(pricing_data)} instance types"
                )
                return pricing_data
            else:
                logger.info(
                    "Lambda Cloud API response doesn't contain pricing information"
                )
                return None

        except requests.RequestException as e:
            logger.warning(f"Lambda Cloud API request failed: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error processing Lambda Cloud API response: {e}")
            return None

    def _get_instance_variations(self, instance_type: str) -> list:
        """Get common variations of an instance type name.

        Args:
            instance_type: Original instance type

        Returns:
            List of possible variations
        """
        variations = [instance_type]

        # Common transformations
        if instance_type.startswith("gpu_1x_"):
            # gpu_1x_a100 -> a100, a100_40gb
            base = instance_type.replace("gpu_1x_", "")
            variations.extend([base, f"{base}_40gb"])
        elif instance_type.startswith("gpu_"):
            # gpu_2x_a100 -> 2xa100, 2xa100_40gb
            parts = instance_type.split("_")
            if len(parts) >= 3:
                count = parts[1]  # 2x, 4x, etc.
                gpu_type = "_".join(parts[2:])  # a100, h100, etc.
                variations.extend([f"{count}{gpu_type}", f"{count}{gpu_type}_40gb"])
        else:
            # Try adding gpu_1x_ prefix
            variations.append(f"gpu_1x_{instance_type}")

        # Add 80GB variants for A100
        if "a100" in instance_type and "80gb" not in instance_type:
            for var in variations.copy():
                if "a100" in var:
                    variations.append(var.replace("a100", "a100_80gb"))

        return variations

    def get_pricing_info(self) -> Dict[str, Any]:
        """Get information about the pricing client.

        Returns:
            Dictionary with pricing client information
        """
        return {
            "provider": "lambda",
            "authenticated": self.authenticated,
            "api_available": self.authenticated,
            "fallback_pricing_date": self._hardcoded_pricing_date,
            "cache_ttl_hours": 24,
            "supported_regions": ["us-east-1", "us-west-1", "us-west-2"],
            "instance_count": len(self._hardcoded_pricing),
        }
