# Clustrix Pricing API Reference

This document provides comprehensive API documentation for Clustrix's cloud provider pricing system, including all pricing clients, cost monitors, and utility functions.

## Table of Contents

- [Overview](#overview)
- [Pricing Clients](#pricing-clients)
- [Cost Monitors](#cost-monitors)
- [Removed Functionality](#removed-functionality)
- [Examples](#examples)
- [Error Codes](#error-codes)

## Overview

The Clustrix pricing system provides programmatic access to cloud provider pricing data through a unified interface. It supports AWS, Azure, GCP, and Lambda Cloud with automatic fallback to hardcoded pricing when APIs are unavailable.

### Core Architecture

```
Cost Monitors → Pricing Clients → Cloud Provider APIs
     ↓              ↓                    ↓
  User Interface   Caching           Real-time Pricing
```

### Key Features

- **Real-time pricing**: Live API integration with all major cloud providers
- **Automatic fallback**: Graceful degradation to hardcoded pricing when the live API call fails or returns nothing
- **Caching system**: Simple file-based caching with TTL management (`clustrix.pricing_clients.base.PricingCache`)

> **Note:** Earlier versions of this document also described a performance-monitoring
> module (metrics, circuit breakers) and a resilience module (retry decorators,
> fallback strategies, data validators, health checks). Those modules
> (`clustrix.pricing_clients.performance_monitor`, `clustrix.pricing_clients.resilience`,
> `clustrix.pricing_clients.validation_alerts`) have been removed from the codebase after being
> identified as unused (orphaned) code. See [Removed Functionality](#removed-functionality).

## Pricing Clients

### BasePricingClient

Base class for all pricing client implementations.

This is the real abstract base class (`clustrix/pricing_clients/base.py`). Note
that it does **not** define `authenticate` -- that method only exists on
`LambdaPricingClient`, because Lambda Cloud is the one provider here that
needs an API key for pricing. Every subclass must implement all three
abstract methods below, including `_fetch_pricing_from_api`.

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from clustrix.pricing_clients.base import PricingCache

class BasePricingClient(ABC):
    """Abstract base class for pricing clients."""

    def __init__(self, cache_ttl_hours: int = 24):
        self.cache = PricingCache(ttl_hours=cache_ttl_hours)
        self._hardcoded_pricing: Dict[str, Any] = {}
        self._hardcoded_pricing_date: Optional[str] = None

    @abstractmethod
    def get_instance_pricing(self, instance_type: str, region: str, **kwargs) -> Optional[float]:
        """Get hourly pricing for a specific instance type."""

    @abstractmethod
    def get_all_pricing(self, region: str, **kwargs) -> Dict[str, float]:
        """Get pricing for all instance types in a region."""

    @abstractmethod
    def _fetch_pricing_from_api(
        self, instance_type: Optional[str], region: str, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Fetch raw pricing data from the provider's API."""

    def _get_fallback_price(self, instance_type: str) -> Optional[float]:
        """Look up `instance_type` in `self._hardcoded_pricing`."""

    def is_pricing_data_outdated(self, days: int = 30) -> bool:
        """True if `self._hardcoded_pricing_date` is more than `days` old (or unset)."""
```

#### Methods

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `get_instance_pricing` (abstract) | `instance_type`, `region`, `**kwargs` | `Optional[float]` | Get hourly price for instance |
| `get_all_pricing` (abstract) | `region`, `**kwargs` | `Dict[str, float]` | Get all pricing data |
| `_fetch_pricing_from_api` (abstract) | `instance_type`, `region`, `**kwargs` | `Optional[Dict[str, Any]]` | Fetch raw data from the provider's API; every concrete subclass must implement this |
| `_get_fallback_price` | `instance_type` | `Optional[float]` | Hardcoded fallback price, logged as a warning when used |
| `is_pricing_data_outdated` | `days=30` | `bool` | Whether the hardcoded fallback table is older than `days` |

### AWSPricingClient

AWS Pricing API client implementation.

```python
from clustrix.pricing_clients.aws_pricing import AWSPricingClient

client = AWSPricingClient()
price = client.get_instance_pricing("m5.large", "us-east-1", "Linux")
all_prices = client.get_all_pricing("us-east-1")
```

#### Methods

**`get_instance_pricing(instance_type: str, region: str, operating_system: str = "Linux") -> Optional[float]`**

Get hourly pricing for a specific EC2 instance type.

- **Parameters:**
  - `instance_type` (str): EC2 instance type (e.g., "m5.large")
  - `region` (str): AWS region (e.g., "us-east-1")
  - `operating_system` (str): OS type ("Linux", "Windows", etc.)

- **Returns:** Hourly price in USD or None if not found

- **Example:**
  ```python
  price = client.get_instance_pricing("t3.medium", "us-west-2", "Linux")
  # Returns: 0.0416 (for example)
  ```

**`get_all_pricing(region: str, operating_system: str = "Linux") -> Dict[str, float]`**

Get pricing for all EC2 instance types in a region.

- **Returns:** Dictionary mapping instance types to hourly prices

### AzurePricingClient

Azure Retail Prices API client implementation.

```python
from clustrix.pricing_clients.azure_pricing import AzurePricingClient

client = AzurePricingClient()
price = client.get_instance_pricing("Standard_D2s_v3", "eastus", "Linux")
```

#### Methods

**`get_instance_pricing(instance_type: str, region: str, operating_system: str = "Linux") -> Optional[float]`**

Get hourly pricing for Azure VM sizes.

- **Parameters:**
  - `instance_type` (str): Azure VM size (e.g., "Standard_D2s_v3")
  - `region` (str): Azure region (e.g., "eastus")
  - `operating_system` (str): OS type ("Linux", "Windows")

### GCPPricingClient

Google Cloud Billing Catalog API client implementation.

```python
from clustrix.pricing_clients.gcp_pricing import GCPPricingClient

client = GCPPricingClient()
price = client.get_instance_pricing("n1-standard-4", "us-central1")
```

#### Methods

**`get_instance_pricing(instance_type: str, region: str) -> Optional[float]`**

Get hourly pricing for GCP machine types.

- **Parameters:**
  - `instance_type` (str): GCP machine type (e.g., "n1-standard-4")
  - `region` (str): GCP region (e.g., "us-central1")

### LambdaPricingClient

Lambda Cloud API client implementation.

```python
from clustrix.pricing_clients.lambda_pricing import LambdaPricingClient

client = LambdaPricingClient()
client.authenticate(api_key="your-api-key")
price = client.get_instance_pricing("gpu_1x_a10", "us-east-1")
```

#### Methods

**`authenticate(api_key: str) -> bool`**

Authenticate with Lambda Cloud API.

- **Parameters:**
  - `api_key` (str): Lambda Cloud API key

- **Returns:** True if authentication successful

**`get_instance_pricing(instance_type: str, region: str) -> Optional[float]`**

Get hourly pricing for Lambda Cloud GPU instances.

- **Parameters:**
  - `instance_type` (str): Instance type (e.g., "gpu_1x_a10")
  - `region` (str): Region (currently supports "us-east-1")

## Cost Monitors

Cost monitors provide high-level cost estimation interfaces.

### AWSCostMonitor

```python
from clustrix.cost_providers.aws import AWSCostMonitor

monitor = AWSCostMonitor()
cost_estimate = monitor.estimate_cost("t3.large", 8.0)  # 8 hours
```

#### Methods

**`estimate_cost(instance_type: str, hours: float) -> CostEstimate`**

Estimate cost for running an instance.

- **Parameters:**
  - `instance_type` (str): Instance type
  - `hours` (float): Number of hours

- **Returns:** `CostEstimate` object with detailed cost breakdown

#### CostEstimate Object

This is the real dataclass (`clustrix/cost_monitoring.py`); an earlier
version of this document had it wrong -- inventing `provider`, `hours`, and
`region` fields that don't exist, and omitting the real `currency`,
`hours_used`, and `last_updated` fields:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class CostEstimate:
    """Cost estimation information."""

    instance_type: str
    hourly_rate: float
    hours_used: float
    estimated_cost: float
    currency: str = "USD"
    last_updated: Optional[datetime] = None
    pricing_source: str = "api"  # "api" or "hardcoded"
    pricing_warning: Optional[str] = None
```

### Configuration Options

All cost monitors support configuration via environment variables or config file:

```python
# Enable API pricing
monitor = AWSCostMonitor(use_pricing_api=True)

# Use specific region
monitor = AWSCostMonitor(region="us-west-2")
```

## Removed Functionality

Three modules that used to live under `clustrix/pricing_clients/` --
`performance_monitor.py`, `resilience.py`, and `validation_alerts.py` -- have
been deleted as unused (orphaned) code. Nothing in `clustrix/cost_providers/`
or the rest of `clustrix/pricing_clients/` depended on them. The classes and
functions below **no longer exist**; do not import them:

- `performance_monitor`: `PricingPerformanceMonitor`, `PerformanceMetric`,
  `CircuitBreaker`, `PricingCache` (a *different* `PricingCache` than the one
  below), `get_global_performance_monitor`
- `resilience`: `ExponentialBackoffRetry`, `RetryConfig`, `PricingAPISession`,
  `FallbackPricingStrategy`, `PricingDataValidator`,
  `get_global_fallback_strategy`, `get_global_pricing_validator`,
  `get_global_degradation_manager`, `get_global_health_checker`,
  `create_retry_decorator`, `create_api_session`, `create_circuit_breaker`
- `validation_alerts`: everything in this module

There is no drop-in replacement for the performance monitoring, circuit
breaking, retry/backoff, or data validation those modules provided. What
*does* still exist for error handling and caching is:

- **Automatic fallback to hardcoded pricing.** Every pricing client (AWS,
  Azure, GCP, Lambda) carries a hardcoded pricing table and falls back to it
  via `BasePricingClient._get_fallback_price()` -- see each client's
  `_hardcoded_pricing` dict and `_fetch_pricing_from_api` implementation.
- **A simple file-based cache**, `clustrix.pricing_clients.base.PricingCache`
  (this is the real, current `PricingCache` -- not the deleted
  `performance_monitor.PricingCache`, which had a different, size-limited
  API):

```python
from clustrix.pricing_clients.base import PricingCache

cache = PricingCache(ttl_hours=24)

# Cache pricing data (any JSON-serializable dict)
cache.set("aws_t3.large_us-east-1", {"price": 0.0832})

# Retrieve cached data (None if missing or expired)
cached = cache.get("aws_t3.large_us-east-1")
print(cached)
```

If you need retry/backoff, circuit breaking, or custom validation, write it
yourself around the pricing clients' public methods (`get_instance_pricing`,
`get_all_pricing`) -- see the "Batch Pricing with Manual Retry" example
below for a minimal, dependency-free retry loop.

## Examples

### Basic Usage

```python
from clustrix.cost_providers.aws import AWSCostMonitor

# Initialize cost monitor
monitor = AWSCostMonitor(use_pricing_api=True)

# Estimate cost for development workload
cost_estimate = monitor.estimate_cost("t3.medium", 8.0)  # 8 hours

print(f"Estimated cost: ${cost_estimate.estimated_cost:.2f}")
print(f"Hourly rate: ${cost_estimate.hourly_rate:.4f}")
print(f"Pricing source: {cost_estimate.pricing_source}")

if cost_estimate.pricing_warning:
    print(f"Warning: {cost_estimate.pricing_warning}")
```

### Multi-Provider Cost Comparison

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor

# Initialize monitors
monitors = {
    'aws': AWSCostMonitor(),
    'azure': AzureCostMonitor(), 
    'gcp': GCPCostMonitor()
}

# Instance mappings for equivalent resources
instances = {
    'aws': 't3.large',
    'azure': 'Standard_D2s_v3',
    'gcp': 'n1-standard-2'
}

hours = 24.0
results = {}

for provider, monitor in monitors.items():
    instance_type = instances[provider]
    estimate = monitor.estimate_cost(instance_type, hours)
    results[provider] = estimate.estimated_cost

# Find cheapest option
cheapest = min(results.items(), key=lambda x: x[1])
print(f"Cheapest option: {cheapest[0]} at ${cheapest[1]:.2f} for {hours} hours")
```

### Advanced Configuration: Lambda Cloud with an API Key

```python
# cluster-required: needs a real Lambda Cloud API key to authenticate meaningfully
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor

# Initialize Lambda Cloud monitor with API key
monitor = LambdaCostMonitor(
    use_pricing_api=True,
    api_key="your-lambda-api-key"
)

# Estimate GPU workload cost
gpu_cost = monitor.estimate_cost("gpu_1x_a10", 4.0)  # 4 hours
print(f"GPU training cost: ${gpu_cost.estimated_cost:.2f}")
```

### Manual Health Check (No External Monitoring Module)

There is no built-in health-check registry anymore (`get_global_health_checker`
was part of the deleted `resilience` module). The pattern below gets the same
result by calling the pricing clients directly -- it's a normal function, not
a special API, and it's exercised for real (including the fallback path) as
part of this documentation's own test suite:

```python
import logging
from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.azure_pricing import AzurePricingClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def aws_health_check():
    """AWS pricing health check: succeeds via live API or the hardcoded fallback."""
    try:
        client = AWSPricingClient()
        price = client.get_instance_pricing("t3.micro", "us-east-1", "Linux")
        return {"healthy": price is not None, "price": price}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


def azure_health_check():
    """Azure pricing health check: succeeds via live API or the hardcoded fallback."""
    try:
        client = AzurePricingClient()
        price = client.get_instance_pricing("Standard_A1_v2", "eastus", "Linux")
        return {"healthy": price is not None, "price": price}
    except Exception as e:
        return {"healthy": False, "error": str(e)}


checks = {"aws": aws_health_check, "azure": azure_health_check}
results = {name: check() for name, check in checks.items()}

healthy_count = sum(1 for r in results.values() if r["healthy"])
logger.info(f"Healthy services: {healthy_count}/{len(results)}")
for service, details in results.items():
    logger.info(f"  {service}: {'healthy' if details['healthy'] else 'unhealthy'}")
    if not details["healthy"]:
        logger.warning(f"    Error: {details.get('error', 'no price returned')}")
```

Both `get_instance_pricing` calls above either return a live price or fall
back to the client's hardcoded table -- they do not raise just because the
live API is unreachable, so `"healthy"` here really means "returned *some*
price," not "the live API responded."

### Batch Pricing with Manual Retry

There is no built-in retry decorator anymore (`create_retry_decorator` was
part of the deleted `resilience` module). A plain retry loop, written with
only the standard library and the real client, replaces it:

```python
import time
from clustrix.pricing_clients.aws_pricing import AWSPricingClient


def get_price_with_retry(client, instance_type, region, os, max_attempts=3, base_delay=1.0):
    """Get price, retrying on exceptions with linear backoff."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            return client.get_instance_pricing(instance_type, region, os)
        except Exception as e:  # get_instance_pricing already falls back
            last_error = e      # internally; this only catches true failures
            time.sleep(base_delay * (attempt + 1))
    raise last_error


# Initialize client
client = AWSPricingClient()

# Instance types to check
instance_types = [
    "t3.micro", "t3.small", "t3.medium", "t3.large",
    "m5.large", "m5.xlarge", "c5.large", "r5.large"
]

region = "us-east-1"
results = {}

for instance_type in instance_types:
    try:
        price = get_price_with_retry(client, instance_type, region, "Linux")
        results[instance_type] = {
            "price": price,
            "status": "success" if price is not None else "no_data"
        }
        print(f"{instance_type}: ${price:.4f}/hour" if price else f"{instance_type}: No data")
    except Exception as e:
        results[instance_type] = {
            "price": None,
            "status": "error",
            "error": str(e)
        }
        print(f"{instance_type}: Error - {e}")

# Summary
successful = sum(1 for r in results.values() if r["status"] == "success")
print(f"\nBatch pricing complete: {successful}/{len(instance_types)} successful")
```

## Error Codes

### HTTP Error Codes

| Code | Description | Action |
|------|-------------|---------|
| 401 | Unauthorized | Check API credentials |
| 403 | Forbidden | Verify API permissions |
| 429 | Rate Limited | Back off and retry (write your own; see [Batch Pricing with Manual Retry](#batch-pricing-with-manual-retry)) |
| 500 | Server Error | Retry, or rely on the automatic hardcoded-pricing fallback |
| 503 | Service Unavailable | Rely on the automatic hardcoded-pricing fallback |

### Pricing Client Errors

There are no custom exception classes in `clustrix.pricing_clients` or
`clustrix.cost_providers` (an earlier version of this document listed
`AuthenticationError`, `RegionNotFoundError`, `InstanceTypeNotFoundError`,
`PricingDataUnavailableError`, and `RateLimitExceededError` here; none of
those classes exist in the codebase). What actually happens instead:

| Situation | What happens |
|-----------|--------------|
| The live API call fails for any reason (network error, bad region, rate limit, auth failure) | `get_instance_pricing` catches the exception internally, logs a warning, and returns `_get_fallback_price(instance_type)` -- the hardcoded price, or `None` if the instance type isn't in the hardcoded table either |
| The instance type isn't in the hardcoded fallback table and the API also failed | `get_instance_pricing` returns `None` |
| Lambda Cloud authentication fails (`LambdaPricingClient.authenticate`) | Returns `False`; it does not raise |

Since failures are swallowed and turned into `None` or a fallback price
rather than raised, code that calls these clients should check for `None`,
not wrap the call in a broad `try/except` expecting a custom exception type.

### Common Error Patterns

**Handling a missing price:**
```python
from clustrix.pricing_clients.aws_pricing import AWSPricingClient

client = AWSPricingClient()
price = client.get_instance_pricing("t3.large", "us-east-1", "Linux")

if price is None:
    # Neither the live API nor the hardcoded table had this instance type
    price = 0.0832  # your own last-resort default
```

**Authentication (Lambda Cloud only):**

`authenticate` is not part of `BasePricingClient` -- only
`LambdaPricingClient` defines it, because it is the one provider here that
needs an API key:

```python
from clustrix.pricing_clients.lambda_pricing import LambdaPricingClient

client = LambdaPricingClient()
authenticated = client.authenticate(api_key="your-lambda-api-key")
if not authenticated:
    print("Authentication failed; get_instance_pricing will fall back to hardcoded pricing")
```

**Data validation:**

There is no built-in validator anymore (`get_global_pricing_validator` was
part of the deleted `resilience` module). A plain sanity check replaces it:

```python
from clustrix.pricing_clients.aws_pricing import AWSPricingClient

client = AWSPricingClient()
price = client.get_instance_pricing("t3.large", "us-east-1", "Linux")

MIN_REASONABLE_PRICE, MAX_REASONABLE_PRICE = 0.001, 1000.0
if price is not None and not (MIN_REASONABLE_PRICE <= price <= MAX_REASONABLE_PRICE):
    print(f"Suspicious pricing data: ${price:.4f}; ignoring it")
    price = None
```

## Best Practices

### Performance Optimization

1. **Use caching**: `PricingCache` (in `clustrix.pricing_clients.base`) already backs every pricing client with a 24-hour TTL by default
2. **Batch requests**: Group multiple pricing queries when possible
3. **Monitor performance yourself**: there is no built-in performance monitor; wrap calls with your own timing/logging if you need it (see [Removed Functionality](#removed-functionality))
4. **Implement your own circuit breaking** if you need it: there is no built-in circuit breaker

### Error Handling

1. **Check for `None`**: pricing calls return `None` rather than raising when no price is available -- see [Pricing Client Errors](#pricing-client-errors)
2. **Implement your own retries** if transient failures matter to you: see [Batch Pricing with Manual Retry](#batch-pricing-with-manual-retry)
3. **Validate data yourself**: there is no built-in validator; a simple range check is often enough (see [Common Error Patterns](#common-error-patterns))
4. **Rely on the built-in fallback**: every client already falls back to a hardcoded price automatically
5. **Log appropriately**: the clients already log warnings when they fall back; add your own logging around calls if you need more detail

### Security

1. **Secure credentials**: Use environment variables or secure storage
2. **Rotate keys**: Regularly rotate API keys
3. **Monitor usage**: Watch for unusual API usage patterns
4. **Use HTTPS**: Always use secure connections

### Production Deployment

1. **Health checks**: no built-in registry exists; call clients directly as shown in [Manual Health Check](#manual-health-check-no-external-monitoring-module)
2. **Alerting**: build your own on top of the health-check pattern above
3. **Backup strategies**: the hardcoded fallback tables are the only built-in backup; keep them current if pricing changes materially
4. **Documentation**: keep this document in sync with `clustrix/pricing_clients/` and `clustrix/cost_providers/` -- it drifted out of sync with the real code once already

This completes the comprehensive API reference documentation for Clustrix's pricing system.