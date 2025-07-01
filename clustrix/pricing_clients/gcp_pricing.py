"""GCP pricing client implementation using Cloud Billing Catalog API."""

import logging
from typing import Dict, Optional, Any

from .base import BasePricingClient

logger = logging.getLogger(__name__)


class GCPPricingClient(BasePricingClient):
    """Client for fetching GCP Compute Engine pricing using Cloud Billing Catalog API."""

    def __init__(self, cache_ttl_hours: int = 24):
        """Initialize GCP pricing client."""
        super().__init__(cache_ttl_hours)

        # Compute Engine service ID for GCP pricing API
        self.compute_service_id = "6F81-5844-456A"

        # Hardcoded pricing as fallback (as of 2025-01)
        self._hardcoded_pricing_date = "2025-01-01"
        self._hardcoded_pricing = {
            # General Purpose - N1 Series
            "n1-standard-1": 0.0475,
            "n1-standard-2": 0.095,
            "n1-standard-4": 0.19,
            "n1-standard-8": 0.38,
            # General Purpose - N2 Series
            "n2-standard-2": 0.078,
            "n2-standard-4": 0.156,
            "n2-standard-8": 0.312,
            "n2-standard-16": 0.624,
            # Compute Optimized - C2 Series
            "c2-standard-4": 0.168,
            "c2-standard-8": 0.336,
            "c2-standard-16": 0.672,
            "c2-standard-30": 1.26,
            # Memory Optimized - M1 Series
            "m1-ultramem-40": 3.844,
            "m1-ultramem-80": 7.688,
            "m1-ultramem-160": 15.376,
            # GPU Instances
            "n1-standard-4-k80": 0.64,  # with K80 GPU
            "n1-standard-8-k80": 1.28,  # with K80 GPU
            "n1-standard-4-t4": 0.54,  # with T4 GPU
            "n1-standard-8-t4": 1.08,  # with T4 GPU
            "n1-standard-4-v100": 2.73,  # with V100 GPU
            "n1-standard-8-v100": 5.46,  # with V100 GPU
            # Default fallback
            "default": 0.10,
        }

        # Region mapping for GCP
        self.region_mapping = {
            "us-central1": "us-central1",
            "us-east1": "us-east1",
            "us-east4": "us-east4",
            "us-west1": "us-west1",
            "us-west2": "us-west2",
            "us-west3": "us-west3",
            "us-west4": "us-west4",
            "europe-north1": "europe-north1",
            "europe-west1": "europe-west1",
            "europe-west2": "europe-west2",
            "europe-west3": "europe-west3",
            "europe-west4": "europe-west4",
            "europe-west6": "europe-west6",
            "asia-east1": "asia-east1",
            "asia-east2": "asia-east2",
            "asia-northeast1": "asia-northeast1",
            "asia-northeast2": "asia-northeast2",
            "asia-northeast3": "asia-northeast3",
            "asia-south1": "asia-south1",
            "asia-southeast1": "asia-southeast1",
            "asia-southeast2": "asia-southeast2",
            "australia-southeast1": "australia-southeast1",
        }

    def get_instance_pricing(
        self,
        instance_type: str,
        region: str,
        **kwargs,
    ) -> Optional[float]:
        """Get hourly pricing for a specific GCP machine type.

        Args:
            instance_type: GCP machine type (e.g., 'n1-standard-4')
            region: GCP region (e.g., 'us-central1')

        Returns:
            Hourly price in USD or None if not found
        """
        # Generate cache key
        cache_key = f"gcp_{region}_{instance_type}"

        # Check cache first
        cached_data = self.cache.get(cache_key)
        if cached_data and "price" in cached_data:
            return cached_data["price"]

        # Try to fetch from API
        try:
            pricing_data = self._fetch_pricing_from_api(
                instance_type=instance_type, region=region
            )

            if pricing_data and "price" in pricing_data:
                # Cache the result
                self.cache.set(cache_key, pricing_data)
                return pricing_data["price"]
        except Exception as e:
            logger.warning(f"Failed to fetch GCP pricing from API: {e}")

        # Fall back to hardcoded pricing
        fallback_price = self._get_fallback_price(instance_type)
        if fallback_price is None:
            # Use default price for unknown instance types
            fallback_price = self._hardcoded_pricing.get("default")
        return fallback_price

    def get_all_pricing(self, region: str, **kwargs) -> Dict[str, float]:
        """Get all GCP machine type pricing for a region.

        Args:
            region: GCP region

        Returns:
            Dictionary mapping machine types to hourly prices
        """
        # For simplicity, return hardcoded pricing with a warning
        # In a full implementation, this would query the API for all machine types
        if self.is_pricing_data_outdated():
            logger.warning(
                f"Using potentially outdated pricing data from {self._hardcoded_pricing_date}"
            )

        return self._hardcoded_pricing.copy()

    def _fetch_pricing_from_api(
        self,
        instance_type: Optional[str],
        region: str,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """Fetch pricing from GCP Cloud Billing Catalog API.

        Args:
            instance_type: GCP machine type
            region: GCP region

        Returns:
            Pricing data dictionary or None
        """
        try:
            # Try to import Google Cloud libraries
            from google.cloud import billing_v1
            from google.auth.exceptions import DefaultCredentialsError
            from google.api_core.exceptions import GoogleAPIError
        except ImportError:
            logger.debug(
                "Google Cloud libraries not available, falling back to hardcoded pricing"
            )
            return None

        try:
            # Create billing catalog client
            client = billing_v1.CloudCatalogClient()

            # List services to find Compute Engine
            services = client.list_services()
            compute_service = None

            for service in services:
                if service.display_name == "Compute Engine":
                    compute_service = service
                    break

            if not compute_service:
                logger.debug("Could not find Compute Engine service in GCP catalog")
                return None

            # List SKUs for Compute Engine in the specified region
            skus_request = billing_v1.ListSkusRequest(parent=compute_service.name)

            skus = client.list_skus(request=skus_request)

            # Look for matching SKU
            for sku in skus:
                # Check if this SKU matches our instance type and region
                if (
                    region in sku.service_regions
                    and instance_type in sku.description.lower()
                ):

                    # Extract pricing information
                    if sku.pricing_info:
                        pricing_info = sku.pricing_info[0]
                        if pricing_info.pricing_expression.tiered_rates:
                            rate = pricing_info.pricing_expression.tiered_rates[0]
                            if rate.unit_price.currency_code == "USD":
                                # Convert from nanos to dollars
                                price = rate.unit_price.nanos / 1_000_000_000

                                return {
                                    "price": price,
                                    "instance_type": instance_type,
                                    "region": region,
                                    "currency": "USD",
                                    "sku_id": sku.sku_id,
                                    "description": sku.description,
                                }

        except DefaultCredentialsError:
            logger.debug("GCP credentials not available")
        except GoogleAPIError as e:
            logger.debug(f"GCP API error: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error fetching GCP pricing: {e}")

        return None

    def get_preemptible_pricing(
        self, instance_type: str, region: str
    ) -> Optional[float]:
        """Get preemptible instance pricing for GCP.

        Args:
            instance_type: GCP machine type
            region: GCP region

        Returns:
            Estimated preemptible price (uses API or hardcoded discount)
        """
        # Try to get preemptible pricing from API
        try:
            pricing_data = self._fetch_preemptible_pricing_from_api(
                instance_type, region
            )
            if pricing_data and "price" in pricing_data:
                return pricing_data["price"]
        except Exception as e:
            logger.debug(f"Failed to get preemptible pricing from API: {e}")

        # Fall back to estimating from on-demand pricing
        on_demand_price = self.get_instance_pricing(instance_type, region)
        if on_demand_price:
            # Apply approximate preemptible discount (typically 60-91%)
            preemptible_discount = 0.8  # 80% discount is typical
            return on_demand_price * (1 - preemptible_discount)
        return None

    def _fetch_preemptible_pricing_from_api(
        self, instance_type: str, region: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch preemptible pricing from GCP API.

        Args:
            instance_type: GCP machine type
            region: GCP region

        Returns:
            Preemptible pricing data or None
        """
        try:
            from google.cloud import billing_v1
        except ImportError:
            return None

        try:
            client = billing_v1.CloudCatalogClient()

            # List services to find Compute Engine
            services = client.list_services()
            compute_service = None

            for service in services:
                if service.display_name == "Compute Engine":
                    compute_service = service
                    break

            if not compute_service:
                return None

            # List SKUs for preemptible instances
            skus_request = billing_v1.ListSkusRequest(parent=compute_service.name)

            skus = client.list_skus(request=skus_request)

            # Look for preemptible SKU
            for sku in skus:
                if (
                    region in sku.service_regions
                    and instance_type in sku.description.lower()
                    and "preemptible" in sku.description.lower()
                ):

                    if sku.pricing_info:
                        pricing_info = sku.pricing_info[0]
                        if pricing_info.pricing_expression.tiered_rates:
                            rate = pricing_info.pricing_expression.tiered_rates[0]
                            if rate.unit_price.currency_code == "USD":
                                price = rate.unit_price.nanos / 1_000_000_000

                                return {
                                    "price": price,
                                    "instance_type": instance_type,
                                    "region": region,
                                    "pricing_type": "preemptible",
                                    "currency": "USD",
                                }

        except Exception as e:
            logger.debug(f"Failed to fetch GCP preemptible pricing: {e}")

        return None

    def get_sustained_use_discount(self, hours_used: float, base_price: float) -> float:
        """Calculate GCP sustained use discount.

        GCP automatically applies sustained use discounts for instances
        that run for a significant portion of the month.

        Args:
            hours_used: Number of hours the instance was used
            base_price: Base hourly price

        Returns:
            Discounted price
        """
        # GCP sustained use discounts (approximation)
        # 25% for >25% of month, 50% for >50%, 75% for >75%
        month_hours = 24 * 30  # Approximate month
        usage_percentage = hours_used / month_hours

        if usage_percentage > 0.75:
            discount = 0.3  # 30% discount
        elif usage_percentage > 0.5:
            discount = 0.2  # 20% discount
        elif usage_percentage > 0.25:
            discount = 0.1  # 10% discount
        else:
            discount = 0.0  # No discount

        return base_price * (1 - discount)

    def get_custom_machine_pricing(
        self, vcpus: int, memory_gb: float, region: str
    ) -> Optional[float]:
        """Get pricing for custom machine types in GCP.

        Args:
            vcpus: Number of vCPUs
            memory_gb: Amount of memory in GB
            region: GCP region

        Returns:
            Hourly price for custom machine or None
        """
        # GCP custom machine pricing is based on vCPU and memory separately
        # These are approximate rates for us-central1
        vcpu_price_per_hour = 0.033174  # per vCPU per hour
        memory_price_per_hour = 0.004446  # per GB per hour

        # Regional pricing adjustments (approximate)
        region_multipliers = {
            "us-central1": 1.0,
            "us-east1": 1.0,
            "us-west1": 1.0,
            "europe-west1": 1.1,
            "europe-west2": 1.15,
            "asia-east1": 1.1,
            "asia-northeast1": 1.2,
        }

        multiplier = region_multipliers.get(region, 1.1)  # Default to 10% markup

        total_price = (
            vcpus * vcpu_price_per_hour + memory_gb * memory_price_per_hour
        ) * multiplier

        return total_price
