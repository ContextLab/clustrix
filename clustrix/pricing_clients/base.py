"""Base pricing client for cloud providers."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class PricingCache:
    """Simple file-based cache for pricing data."""

    def __init__(self, cache_dir: Optional[Path] = None, ttl_hours: int = 24):
        """Initialize the pricing cache.

        Args:
            cache_dir: Directory to store cache files. Defaults to ~/.clustrix/cache
            ttl_hours: Time to live for cached data in hours
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".clustrix" / "cache"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Get cached pricing data if it exists and is not expired."""
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r") as f:
                data = json.load(f)

            cached_time = datetime.fromisoformat(data["cached_at"])
            if datetime.now() - cached_time > self.ttl:
                logger.debug(f"Cache expired for key: {key}")
                return None

            logger.debug(f"Cache hit for key: {key}")
            return data["pricing"]
        except Exception as e:
            logger.warning(f"Error reading cache for {key}: {e}")
            return None

    def set(self, key: str, data: Dict[str, Any]):
        """Cache pricing data."""
        cache_file = self.cache_dir / f"{key}.json"
        try:
            cache_data = {"cached_at": datetime.now().isoformat(), "pricing": data}
            with open(cache_file, "w") as f:
                json.dump(cache_data, f, indent=2)
            logger.debug(f"Cached data for key: {key}")
        except Exception as e:
            logger.warning(f"Error caching data for {key}: {e}")


class BasePricingClient(ABC):
    """Abstract base class for pricing clients."""

    def __init__(self, cache_ttl_hours: int = 24):
        """Initialize the pricing client.

        Args:
            cache_ttl_hours: Time to live for cached pricing data
        """
        self.cache = PricingCache(ttl_hours=cache_ttl_hours)
        self._hardcoded_pricing: Dict[str, Any] = {}
        self._hardcoded_pricing_date: Optional[str] = None

    @abstractmethod
    def get_instance_pricing(
        self, instance_type: str, region: str, **kwargs
    ) -> Optional[float]:
        """Get hourly pricing for a specific instance type.

        Args:
            instance_type: The instance type (e.g., 't2.micro', 'm5.large')
            region: The region code (e.g., 'us-east-1', 'eu-west-1')
            **kwargs: Additional provider-specific parameters

        Returns:
            Hourly price in USD or None if not found
        """
        pass

    @abstractmethod
    def get_all_pricing(self, region: str, **kwargs) -> Dict[str, float]:
        """Get all instance pricing for a region.

        Args:
            region: The region code
            **kwargs: Additional provider-specific parameters

        Returns:
            Dictionary mapping instance types to hourly prices
        """
        pass

    @abstractmethod
    def _fetch_pricing_from_api(
        self, instance_type: Optional[str], region: str, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Fetch pricing data from the provider's API.

        Args:
            instance_type: Optional instance type to filter by
            region: The region code
            **kwargs: Additional provider-specific parameters

        Returns:
            Raw pricing data from the API or None if failed
        """
        pass

    def _get_fallback_price(self, instance_type: str) -> Optional[float]:
        """Get hardcoded fallback price for an instance type.

        Args:
            instance_type: The instance type

        Returns:
            Hourly price or None if not found
        """
        if instance_type in self._hardcoded_pricing:
            logger.warning(
                f"Using hardcoded pricing for {instance_type} "
                f"(last updated: {self._hardcoded_pricing_date or 'unknown'})"
            )
            return self._hardcoded_pricing[instance_type]
        return None

    def is_pricing_data_outdated(self, days: int = 30) -> bool:
        """Check if hardcoded pricing data is outdated.

        Args:
            days: Number of days to consider data outdated

        Returns:
            True if data is older than specified days
        """
        if self._hardcoded_pricing_date is None:
            return True

        try:
            pricing_date = datetime.fromisoformat(self._hardcoded_pricing_date)
            return (datetime.now() - pricing_date).days > days
        except Exception:
            return True
