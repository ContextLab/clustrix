"""
Enhanced error handling and resilience features for pricing clients.

This module provides retry logic, fallback strategies, and improved error handling
for robust production deployments.
"""

import time
import logging
import random
from functools import wraps
from typing import Optional, Callable, Any, Dict, List, Tuple
from dataclasses import dataclass
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple = (
        requests.RequestException,
        ConnectionError,
        TimeoutError,
        Exception,
    )


class ExponentialBackoffRetry:
    """Exponential backoff retry decorator with jitter."""

    def __init__(self, config: RetryConfig):
        """Initialize retry decorator with configuration."""
        self.config = config

    def __call__(self, func: Callable) -> Callable:
        """Apply retry logic to function."""

        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(self.config.max_attempts):
                try:
                    return func(*args, **kwargs)

                except self.config.retryable_exceptions as e:
                    last_exception = e

                    if attempt == self.config.max_attempts - 1:
                        # Last attempt, re-raise the exception
                        logger.error(
                            f"Function {func.__name__} failed after {self.config.max_attempts} attempts: {e}"
                        )
                        raise e

                    # Calculate delay with exponential backoff and jitter
                    delay = min(
                        self.config.base_delay
                        * (self.config.exponential_base**attempt),
                        self.config.max_delay,
                    )

                    if self.config.jitter:
                        delay *= 0.5 + random.random() * 0.5  # Add 0-50% jitter

                    logger.warning(
                        f"Function {func.__name__} failed (attempt {attempt + 1}/{self.config.max_attempts}): {e}. "
                        f"Retrying in {delay:.2f} seconds..."
                    )

                    time.sleep(delay)

            # This should never be reached, but just in case
            if last_exception is not None:
                raise last_exception
            else:
                raise Exception("Maximum retries exceeded with no recorded exception")

        return wrapper


class PricingAPISession:
    """Enhanced requests session with retry and timeout configuration."""

    def __init__(
        self,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        status_forcelist: Optional[List[int]] = None,
    ):
        """Initialize API session with retry configuration.

        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries
            backoff_factor: Backoff factor for retries
            status_forcelist: HTTP status codes to retry on
        """
        self.session = requests.Session()
        self.timeout = timeout

        if status_forcelist is None:
            status_forcelist = [
                500,
                502,
                503,
                504,
                429,
            ]  # Server errors and rate limiting

        # Configure retry strategy
        retry_strategy = Retry(
            total=max_retries,
            status_forcelist=status_forcelist,
            method_whitelist=["HEAD", "GET", "OPTIONS"],
            backoff_factor=backoff_factor,
            raise_on_status=False,
        )

        # Mount adapter with retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Set default headers
        self.session.headers.update(
            {
                "User-Agent": "Clustrix-Pricing-Client/1.0",
                "Accept": "application/json",
                "Connection": "keep-alive",
            }
        )

    def get(self, url: str, **kwargs) -> requests.Response:
        """Make GET request with timeout and retry logic."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        """Make POST request with timeout and retry logic."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(url, **kwargs)

    def close(self):
        """Close the session."""
        self.session.close()


class FallbackPricingStrategy:
    """Fallback strategy manager for pricing data."""

    def __init__(self):
        """Initialize fallback strategy."""
        self.fallback_sources: List[Tuple[int, Callable]] = []
        self.fallback_data: Dict[str, Any] = {}

    def add_fallback_source(self, source_func: Callable, priority: int = 0):
        """Add a fallback data source.

        Args:
            source_func: Function that returns pricing data
            priority: Priority level (higher number = higher priority)
        """
        self.fallback_sources.append((priority, source_func))
        self.fallback_sources.sort(key=lambda x: x[0], reverse=True)

    def get_fallback_price(
        self, instance_type: str, region: Optional[str] = None, **kwargs
    ) -> Optional[float]:
        """Get pricing from fallback sources.

        Args:
            instance_type: Instance type to get pricing for
            region: Region for pricing
            **kwargs: Additional parameters

        Returns:
            Price from fallback source or None
        """
        for priority, source_func in self.fallback_sources:
            try:
                price = source_func(instance_type, region, **kwargs)
                if price is not None:
                    logger.info(
                        f"Using fallback pricing source (priority {priority}) for {instance_type}"
                    )
                    return price
            except Exception as e:
                logger.warning(f"Fallback source (priority {priority}) failed: {e}")
                continue

        return None


class PricingDataValidator:
    """Validator for pricing data to detect anomalies."""

    def __init__(
        self,
        min_price: float = 0.001,
        max_price: float = 1000.0,
        max_price_change_percent: float = 200.0,
    ):
        """Initialize pricing validator.

        Args:
            min_price: Minimum reasonable price per hour
            max_price: Maximum reasonable price per hour
            max_price_change_percent: Maximum price change percentage to accept
        """
        self.min_price = min_price
        self.max_price = max_price
        self.max_price_change_percent = max_price_change_percent
        self.historical_prices: Dict[str, float] = {}

    def validate_price(
        self, instance_type: str, price: float, provider: str = "unknown"
    ) -> bool:
        """Validate a pricing value for reasonableness.

        Args:
            instance_type: Instance type being priced
            price: Price to validate
            provider: Provider name for logging

        Returns:
            True if price appears valid, False otherwise
        """
        if price is None:
            return False

        # Check basic bounds
        if not (self.min_price <= price <= self.max_price):
            logger.warning(
                f"Price ${price:.4f} for {provider} {instance_type} outside reasonable bounds "
                f"(${self.min_price:.4f} - ${self.max_price:.4f})"
            )
            return False

        # Check against historical data
        key = f"{provider}:{instance_type}"
        if key in self.historical_prices:
            historical_price = self.historical_prices[key]
            price_change_percent = (
                abs(price - historical_price) / historical_price * 100
            )

            if price_change_percent > self.max_price_change_percent:
                logger.warning(
                    f"Price ${price:.4f} for {provider} {instance_type} changed {price_change_percent:.1f}% "
                    f"from historical ${historical_price:.4f} (threshold: {self.max_price_change_percent:.1f}%)"
                )
                return False

        # Update historical data
        self.historical_prices[key] = price
        return True

    def get_validation_summary(self) -> Dict[str, Any]:
        """Get summary of validation rules and statistics."""
        return {
            "validation_rules": {
                "min_price": self.min_price,
                "max_price": self.max_price,
                "max_price_change_percent": self.max_price_change_percent,
            },
            "historical_prices_tracked": len(self.historical_prices),
            "tracked_instances": list(self.historical_prices.keys()),
        }


class GracefulDegradation:
    """Graceful degradation manager for pricing services."""

    def __init__(self):
        """Initialize graceful degradation manager."""
        self.degradation_strategies: Dict[str, Callable] = {}
        self.service_health: Dict[str, bool] = {}

    def register_degradation_strategy(self, service_name: str, strategy_func: Callable):
        """Register a degradation strategy for a service.

        Args:
            service_name: Name of the service
            strategy_func: Function to call when service is degraded
        """
        self.degradation_strategies[service_name] = strategy_func

    def mark_service_unhealthy(self, service_name: str):
        """Mark a service as unhealthy."""
        self.service_health[service_name] = False
        logger.warning(f"Service {service_name} marked as unhealthy")

    def mark_service_healthy(self, service_name: str):
        """Mark a service as healthy."""
        self.service_health[service_name] = True
        logger.info(f"Service {service_name} marked as healthy")

    def is_service_healthy(self, service_name: str) -> bool:
        """Check if a service is healthy."""
        return self.service_health.get(service_name, True)  # Default to healthy

    def execute_with_degradation(
        self, service_name: str, primary_func: Callable, *args, **kwargs
    ) -> Any:
        """Execute function with graceful degradation.

        Args:
            service_name: Name of the service
            primary_func: Primary function to execute
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Result from primary function or degradation strategy
        """
        if self.is_service_healthy(service_name):
            try:
                result = primary_func(*args, **kwargs)
                return result
            except Exception as e:
                logger.error(f"Primary function failed for {service_name}: {e}")
                self.mark_service_unhealthy(service_name)

                # Fall through to degradation strategy

        # Execute degradation strategy
        if service_name in self.degradation_strategies:
            try:
                logger.info(f"Executing degradation strategy for {service_name}")
                return self.degradation_strategies[service_name](*args, **kwargs)
            except Exception as e:
                logger.error(f"Degradation strategy failed for {service_name}: {e}")
                raise
        else:
            raise Exception(f"No degradation strategy available for {service_name}")


class HealthCheck:
    """Health check system for pricing services."""

    def __init__(self, check_interval_seconds: int = 300):
        """Initialize health check system.

        Args:
            check_interval_seconds: How often to run health checks
        """
        self.check_interval_seconds = check_interval_seconds
        self.health_checks: Dict[str, Callable] = {}
        self.health_status: Dict[str, Dict[str, Any]] = {}

    def register_health_check(self, service_name: str, check_func: Callable):
        """Register a health check function.

        Args:
            service_name: Name of the service
            check_func: Function that returns health status
        """
        self.health_checks[service_name] = check_func

    def run_health_check(self, service_name: str) -> Dict[str, Any]:
        """Run health check for a specific service.

        Args:
            service_name: Name of the service to check

        Returns:
            Health status dictionary
        """
        if service_name not in self.health_checks:
            return {"status": "unknown", "error": "No health check registered"}

        try:
            start_time = time.time()
            result = self.health_checks[service_name]()
            response_time = time.time() - start_time

            status = {
                "status": "healthy" if result else "unhealthy",
                "response_time_seconds": response_time,
                "last_check": time.time(),
                "details": result if isinstance(result, dict) else {"result": result},
            }

        except Exception as e:
            status = {"status": "error", "error": str(e), "last_check": time.time()}

        self.health_status[service_name] = status
        return status

    def run_all_health_checks(self) -> Dict[str, Dict[str, Any]]:
        """Run all registered health checks.

        Returns:
            Dictionary of all health check results
        """
        results = {}
        for service_name in self.health_checks:
            results[service_name] = self.run_health_check(service_name)

        return results

    def get_overall_health(self) -> Dict[str, Any]:
        """Get overall system health.

        Returns:
            Overall health summary
        """
        all_results = self.run_all_health_checks()

        healthy_services = sum(
            1 for status in all_results.values() if status.get("status") == "healthy"
        )
        total_services = len(all_results)

        overall_status = "healthy" if healthy_services == total_services else "degraded"
        if healthy_services == 0:
            overall_status = "unhealthy"

        return {
            "overall_status": overall_status,
            "healthy_services": healthy_services,
            "total_services": total_services,
            "health_percentage": (
                (healthy_services / total_services * 100) if total_services > 0 else 0
            ),
            "service_details": all_results,
        }


# Global instances for common use
_global_fallback_strategy = FallbackPricingStrategy()
_global_pricing_validator = PricingDataValidator()
_global_degradation_manager = GracefulDegradation()
_global_health_checker = HealthCheck()


def get_global_fallback_strategy() -> FallbackPricingStrategy:
    """Get the global fallback strategy instance."""
    return _global_fallback_strategy


def get_global_pricing_validator() -> PricingDataValidator:
    """Get the global pricing validator instance."""
    return _global_pricing_validator


def get_global_degradation_manager() -> GracefulDegradation:
    """Get the global graceful degradation manager."""
    return _global_degradation_manager


def get_global_health_checker() -> HealthCheck:
    """Get the global health checker instance."""
    return _global_health_checker


def create_retry_decorator(max_attempts: int = 3, base_delay: float = 1.0) -> Callable:
    """Create a retry decorator with specified configuration.

    Args:
        max_attempts: Maximum retry attempts
        base_delay: Base delay between retries

    Returns:
        Configured retry decorator
    """
    config = RetryConfig(max_attempts=max_attempts, base_delay=base_delay)
    return ExponentialBackoffRetry(config)


def create_api_session(provider: str, timeout: int = 30) -> PricingAPISession:
    """Create an enhanced API session for a provider.

    Args:
        provider: Provider name for user agent
        timeout: Request timeout in seconds

    Returns:
        Configured API session
    """
    session = PricingAPISession(timeout=timeout)
    session.session.headers["User-Agent"] = f"Clustrix-{provider.title()}-Client/1.0"
    return session
