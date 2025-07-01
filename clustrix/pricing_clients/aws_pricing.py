"""AWS pricing client implementation."""

import json
import logging
from typing import Dict, Optional, Any

# Remove pkg_resources dependency to avoid mypy issues

from .base import BasePricingClient

logger = logging.getLogger(__name__)


class AWSPricingClient(BasePricingClient):
    """Client for fetching AWS EC2 instance pricing."""

    def __init__(self, cache_ttl_hours: int = 24):
        """Initialize AWS pricing client."""
        super().__init__(cache_ttl_hours)

        # Hardcoded pricing as fallback (as of 2025-01)
        self._hardcoded_pricing_date = "2025-01-01"
        self._hardcoded_pricing = {
            # General Purpose
            "t2.micro": 0.0116,
            "t2.small": 0.023,
            "t2.medium": 0.0464,
            "t2.large": 0.0928,
            "t3.micro": 0.0104,
            "t3.small": 0.0208,
            "t3.medium": 0.0416,
            "t3.large": 0.0832,
            "m5.large": 0.096,
            "m5.xlarge": 0.192,
            "m5.2xlarge": 0.384,
            "m5.4xlarge": 0.768,
            # Compute Optimized
            "c5.large": 0.085,
            "c5.xlarge": 0.17,
            "c5.2xlarge": 0.34,
            "c5.4xlarge": 0.68,
            # Memory Optimized
            "r5.large": 0.126,
            "r5.xlarge": 0.252,
            "r5.2xlarge": 0.504,
            "r5.4xlarge": 1.008,
            # GPU Instances
            "p3.2xlarge": 3.06,
            "p3.8xlarge": 12.24,
            "p3.16xlarge": 24.48,
            "g4dn.xlarge": 0.526,
            "g4dn.2xlarge": 0.752,
            "g4dn.4xlarge": 1.204,
        }

    def get_instance_pricing(
        self,
        instance_type: str,
        region: str,
        operating_system: str = "Linux",
        tenancy: str = "Shared",
        **kwargs,
    ) -> Optional[float]:
        """Get hourly pricing for a specific EC2 instance type.

        Args:
            instance_type: EC2 instance type (e.g., 't2.micro')
            region: AWS region (e.g., 'us-east-1')
            operating_system: OS type ('Linux', 'Windows', 'RHEL', 'SUSE')
            tenancy: Instance tenancy ('Shared', 'Dedicated', 'Host')

        Returns:
            Hourly price in USD or None if not found
        """
        # Generate cache key
        cache_key = f"aws_{region}_{instance_type}_{operating_system}_{tenancy}"

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
                tenancy=tenancy,
            )

            if pricing_data and "price" in pricing_data:
                # Cache the result
                self.cache.set(cache_key, pricing_data)
                return pricing_data["price"]
        except Exception as e:
            logger.warning(f"Failed to fetch pricing from API: {e}")

        # Fall back to hardcoded pricing
        return self._get_fallback_price(instance_type)

    def get_all_pricing(
        self, region: str, operating_system: str = "Linux", **kwargs
    ) -> Dict[str, float]:
        """Get all EC2 instance pricing for a region.

        Args:
            region: AWS region
            operating_system: OS type

        Returns:
            Dictionary mapping instance types to hourly prices
        """
        # For simplicity, return hardcoded pricing with a warning
        # In a full implementation, this would query the API for all types
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
        tenancy: str = "Shared",
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """Fetch pricing from AWS Pricing API using boto3.

        Args:
            instance_type: EC2 instance type
            region: AWS region code
            operating_system: Operating system
            tenancy: Instance tenancy

        Returns:
            Pricing data dictionary or None
        """
        try:
            import boto3
            from botocore.exceptions import ClientError, NoCredentialsError
        except ImportError:
            logger.debug("boto3 not available, falling back to hardcoded pricing")
            return None

        try:
            # Create pricing client (must use specific regions)
            pricing_client = boto3.client("pricing", region_name="us-east-1")

            # Convert region code to region name
            region_name = self._get_region_name(region)

            # Define filters
            filters = [
                {"Type": "TERM_MATCH", "Field": "termType", "Value": "OnDemand"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                {"Type": "TERM_MATCH", "Field": "location", "Value": region_name},
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": tenancy},
                {
                    "Type": "TERM_MATCH",
                    "Field": "operatingSystem",
                    "Value": operating_system,
                },
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
            ]

            # Get pricing data
            response = pricing_client.get_products(
                ServiceCode="AmazonEC2", Filters=filters, FormatVersion="aws_v1"
            )

            if len(response["PriceList"]) > 0:
                price_data = json.loads(response["PriceList"][0])

                # Extract on-demand pricing
                on_demand = price_data["terms"]["OnDemand"]
                if on_demand:
                    first_sku = list(on_demand.keys())[0]
                    price_dimensions = on_demand[first_sku]["priceDimensions"]
                    first_price_dim = list(price_dimensions.keys())[0]
                    price = float(
                        price_dimensions[first_price_dim]["pricePerUnit"]["USD"]
                    )

                    return {
                        "price": price,
                        "instance_type": instance_type,
                        "region": region,
                        "operating_system": operating_system,
                        "currency": "USD",
                    }

        except NoCredentialsError:
            logger.debug("AWS credentials not available")
        except ClientError as e:
            logger.debug(f"AWS API error: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error fetching AWS pricing: {e}")

        return None

    def _get_region_name(self, region_code: str) -> str:
        """Convert region code to region name for Pricing API.

        Args:
            region_code: AWS region code (e.g., 'us-east-1')

        Returns:
            Region name (e.g., 'US East (N. Virginia)')
        """
        # Try to get from boto3 session
        try:
            import boto3

            session = boto3.Session()
            # Get available regions for EC2 service
            available_regions = session.get_available_regions("ec2")
            if region_code in available_regions:
                # Use boto3's built-in region descriptions if available
                try:
                    # This is a best-effort lookup using boto3 internals
                    from botocore.loaders import Loader

                    loader = Loader()
                    endpoints = loader.load_service_model("ec2", "service-2")
                    if "metadata" in endpoints and "regions" in endpoints["metadata"]:
                        regions_data = endpoints["metadata"]["regions"]
                        if region_code in regions_data:
                            description = regions_data[region_code].get(
                                "description", ""
                            )
                            if description:
                                # Pricing API uses 'EU' instead of 'Europe'
                                return description.replace("Europe", "EU")
                except Exception:
                    pass
        except Exception:
            pass

        # Fallback to common region mappings
        region_map = {
            "us-east-1": "US East (N. Virginia)",
            "us-east-2": "US East (Ohio)",
            "us-west-1": "US West (N. California)",
            "us-west-2": "US West (Oregon)",
            "eu-west-1": "EU (Ireland)",
            "eu-central-1": "EU (Frankfurt)",
            "ap-southeast-1": "Asia Pacific (Singapore)",
            "ap-northeast-1": "Asia Pacific (Tokyo)",
        }

        return region_map.get(region_code, "US East (N. Virginia)")

    def get_spot_pricing(self, instance_type: str, region: str) -> Optional[float]:
        """Get spot instance pricing.

        Args:
            instance_type: EC2 instance type
            region: AWS region

        Returns:
            Estimated spot price (uses hardcoded discount for now)
        """
        on_demand_price = self.get_instance_pricing(instance_type, region)
        if on_demand_price:
            # Apply approximate spot discount (varies by instance type)
            spot_discount = 0.7  # 70% discount is a rough average
            return on_demand_price * (1 - spot_discount)
        return None
