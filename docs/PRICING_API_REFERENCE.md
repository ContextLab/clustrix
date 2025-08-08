# Clustrix Pricing API Reference

This document provides comprehensive API documentation for Clustrix's cloud provider pricing system, including all pricing clients, cost monitors, and utility functions.

## Table of Contents

- [Overview](#overview)
- [Pricing Clients](#pricing-clients)
- [Cost Monitors](#cost-monitors)
- [Performance Monitoring](#performance-monitoring)
- [Resilience and Error Handling](#resilience-and-error-handling)
- [Utilities](#utilities)
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
- **Automatic fallback**: Graceful degradation to hardcoded pricing
- **Performance monitoring**: Comprehensive metrics and circuit breakers
- **Caching system**: Intelligent caching with TTL management
- **Error handling**: Exponential backoff and resilience patterns

## Pricing Clients

### BasePricingClient

Base class for all pricing client implementations.

```python
from clustrix.pricing_clients.base import BasePricingClient

class BasePricingClient:
    """Abstract base class for cloud provider pricing clients."""
    
    def authenticate(self, **credentials) -> bool:
        """Authenticate with the cloud provider API."""
        raise NotImplementedError
    
    def get_instance_pricing(self, instance_type: str, region: str, **kwargs) -> Optional[float]:
        """Get hourly pricing for a specific instance type."""
        raise NotImplementedError
    
    def get_all_pricing(self, region: str, **kwargs) -> Dict[str, float]:
        """Get pricing for all instance types in a region."""
        raise NotImplementedError
```

#### Methods

| Method | Parameters | Returns | Description |
|--------|------------|---------|-------------|
| `authenticate` | `**credentials` | `bool` | Authenticate with provider API |
| `get_instance_pricing` | `instance_type`, `region`, `**kwargs` | `Optional[float]` | Get hourly price for instance |
| `get_all_pricing` | `region`, `**kwargs` | `Dict[str, float]` | Get all pricing data |

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

```python
@dataclass
class CostEstimate:
    """Cost estimation result."""
    
    estimated_cost: float
    hourly_rate: float
    provider: str
    instance_type: str
    hours: float
    region: str
    pricing_source: str  # "api" or "hardcoded"
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

## Performance Monitoring

### PricingPerformanceMonitor

Comprehensive performance monitoring for pricing operations.

```python
from clustrix.pricing_clients.performance_monitor import PricingPerformanceMonitor

monitor = PricingPerformanceMonitor()

# Record a metric
from clustrix.pricing_clients.performance_monitor import PerformanceMetric
metric = PerformanceMetric(
    provider="aws",
    operation="get_instance_pricing", 
    response_time_seconds=1.25,
    success=True
)
monitor.record_metric(metric)

# Get performance summary
summary = monitor.get_performance_summary(hours=1)
```

#### Methods

**`record_metric(metric: PerformanceMetric)`**

Record a performance metric.

**`get_performance_summary(hours: int = 1) -> Dict[str, Any]`**

Get performance statistics for the specified time period.

- **Returns:**
  ```python
  {
      'total_requests': 150,
      'error_rate': 0.02,
      'cache_hit_rate': 0.85,
      'average_response_time': 0.45,
      'provider_summary': {
          'aws': {'requests': 50, 'error_rate': 0.0, 'average_response_time': 0.3},
          'azure': {'requests': 100, 'error_rate': 0.03, 'average_response_time': 0.55}
      }
  }
  ```

**`get_provider_health(provider: str) -> ProviderHealthStatus`**

Get health status for a specific provider.

### CircuitBreaker

Protect against cascading failures with circuit breaker pattern.

```python
from clustrix.pricing_clients.performance_monitor import CircuitBreaker

@CircuitBreaker(failure_threshold=5, recovery_timeout=60)
def risky_api_call():
    """API call that might fail."""
    pass
```

#### Parameters

- `failure_threshold` (int): Number of failures before opening circuit
- `recovery_timeout` (int): Seconds to wait before trying again
- `expected_exception` (type): Exception type that triggers circuit breaker

### Enhanced Caching

```python
from clustrix.pricing_clients.performance_monitor import PricingCache

cache = PricingCache(ttl_hours=24, max_size_mb=100)

# Cache pricing data
cache.set("aws_t3.large_us-east-1", 0.0832)

# Retrieve cached data
price = cache.get("aws_t3.large_us-east-1")

# Get cache statistics
stats = cache.get_cache_stats()
```

## Resilience and Error Handling

### ExponentialBackoffRetry

Automatic retry with exponential backoff.

```python
from clustrix.pricing_clients.resilience import ExponentialBackoffRetry, RetryConfig

config = RetryConfig(
    max_attempts=3,
    base_delay=1.0,
    max_delay=60.0,
    exponential_base=2.0
)

@ExponentialBackoffRetry(config)
def api_call_with_retry():
    """API call with automatic retry."""
    pass
```

### PricingAPISession

Enhanced requests session with built-in retry logic.

```python
from clustrix.pricing_clients.resilience import PricingAPISession

session = PricingAPISession(timeout=30, max_retries=3)
response = session.get("https://api.example.com/pricing")
session.close()
```

### Fallback Strategy

Automatic fallback to alternative pricing sources.

```python
from clustrix.pricing_clients.resilience import FallbackPricingStrategy

strategy = FallbackPricingStrategy()
strategy.add_fallback_source(hardcoded_pricing_source, priority=1)
strategy.add_fallback_source(cached_pricing_source, priority=2)

price = strategy.get_fallback_price("t3.large", "us-east-1")
```

### Data Validation

Validate pricing data for reasonableness.

```python
from clustrix.pricing_clients.resilience import PricingDataValidator

validator = PricingDataValidator(
    min_price=0.001,
    max_price=1000.0,
    max_price_change_percent=200.0
)

is_valid = validator.validate_price("t3.large", 0.0832, "aws")
```

## Utilities

### Global Instances

Access global instances for common functionality:

```python
from clustrix.pricing_clients.resilience import (
    get_global_fallback_strategy,
    get_global_pricing_validator,
    get_global_degradation_manager,
    get_global_health_checker
)
from clustrix.pricing_clients.performance_monitor import get_global_performance_monitor

# Get global performance monitor
monitor = get_global_performance_monitor()

# Get global validator  
validator = get_global_pricing_validator()
```

### Helper Functions

**`create_retry_decorator(max_attempts: int = 3, base_delay: float = 1.0) -> Callable`**

Create a configured retry decorator.

**`create_api_session(provider: str, timeout: int = 30) -> PricingAPISession`**

Create a provider-specific API session.

**`create_circuit_breaker(provider: str) -> CircuitBreaker`**

Create a circuit breaker for a provider.

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

### Advanced Configuration with Performance Monitoring

```python
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor
from clustrix.pricing_clients.performance_monitor import get_global_performance_monitor

# Initialize Lambda Cloud monitor with API key
monitor = LambdaCostMonitor(
    use_pricing_api=True,
    api_key="your-lambda-api-key"
)

# Estimate GPU workload cost
gpu_cost = monitor.estimate_cost("gpu_1x_a10", 4.0)  # 4 hours
print(f"GPU training cost: ${gpu_cost.estimated_cost:.2f}")

# Check performance metrics
perf_monitor = get_global_performance_monitor()
summary = perf_monitor.get_performance_summary(hours=24)

print(f"API performance summary:")
print(f"  Total requests: {summary['total_requests']}")
print(f"  Error rate: {summary['error_rate']:.2%}")
print(f"  Cache hit rate: {summary['cache_hit_rate']:.2%}")
print(f"  Average response time: {summary['average_response_time']:.3f}s")
```

### Production Monitoring Setup

```python
import logging
from clustrix.pricing_clients.resilience import get_global_health_checker
from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.azure_pricing import AzurePricingClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set up health monitoring
health_checker = get_global_health_checker()

# Register health checks for each provider
def aws_health_check():
    """AWS pricing health check."""
    try:
        client = AWSPricingClient()
        price = client.get_instance_pricing("t3.micro", "us-east-1", "Linux")
        return {"healthy": price is not None, "price": price}
    except Exception as e:
        return {"healthy": False, "error": str(e)}

def azure_health_check():
    """Azure pricing health check."""
    try:
        client = AzurePricingClient()
        price = client.get_instance_pricing("Standard_A1_v2", "eastus", "Linux")
        return {"healthy": price is not None, "price": price}
    except Exception as e:
        return {"healthy": False, "error": str(e)}

health_checker.register_health_check("aws", aws_health_check)
health_checker.register_health_check("azure", azure_health_check)

# Run health checks
overall_health = health_checker.get_overall_health()
logger.info(f"Overall system health: {overall_health['overall_status']}")
logger.info(f"Healthy services: {overall_health['healthy_services']}/{overall_health['total_services']}")

for service, details in overall_health['service_details'].items():
    status = details['status']
    logger.info(f"  {service}: {status}")
    if status != 'healthy':
        logger.warning(f"    Error: {details.get('error', 'Unknown error')}")
```

### Batch Pricing with Error Handling

```python
from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.resilience import create_retry_decorator

# Create retry decorator for API calls
@create_retry_decorator(max_attempts=3, base_delay=2.0)
def get_price_with_retry(client, instance_type, region, os):
    """Get price with automatic retry."""
    return client.get_instance_pricing(instance_type, region, os)

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
| 429 | Rate Limited | Implement exponential backoff |
| 500 | Server Error | Retry with backoff |
| 503 | Service Unavailable | Use fallback pricing |

### Pricing Client Errors

| Error | Description | Resolution |
|-------|-------------|------------|
| `AuthenticationError` | Invalid API credentials | Update credentials |
| `RegionNotFoundError` | Invalid region specified | Check region names |
| `InstanceTypeNotFoundError` | Invalid instance type | Verify instance type |
| `PricingDataUnavailableError` | No pricing data available | Check API status |
| `RateLimitExceededError` | API rate limit reached | Implement retry logic |

### Common Error Patterns

**Network Connectivity Issues:**
```python
try:
    price = client.get_instance_pricing("t3.large", "us-east-1", "Linux")
except requests.exceptions.ConnectionError:
    # Fall back to cached or hardcoded pricing
    price = fallback_pricing.get("t3.large", 0.0832)
```

**Authentication Failures:**
```python
try:
    authenticated = client.authenticate(api_key=api_key)
    if not authenticated:
        logger.warning("Authentication failed, using hardcoded pricing")
        use_fallback = True
except Exception as e:
    logger.error(f"Authentication error: {e}")
    use_fallback = True
```

**Data Validation Errors:**
```python
from clustrix.pricing_clients.resilience import get_global_pricing_validator

validator = get_global_pricing_validator()
price = client.get_instance_pricing("t3.large", "us-east-1", "Linux")

if price and not validator.validate_price("t3.large", price, "aws"):
    logger.warning(f"Suspicious pricing data: ${price:.4f}")
    # Use fallback or cached price
    price = None
```

## Best Practices

### Performance Optimization

1. **Use caching**: Enable caching with appropriate TTL
2. **Batch requests**: Group multiple pricing queries when possible
3. **Monitor performance**: Use PricingPerformanceMonitor
4. **Implement circuit breakers**: Protect against cascading failures

### Error Handling

1. **Implement retries**: Use exponential backoff for transient failures
2. **Validate data**: Check pricing data for reasonableness
3. **Use fallbacks**: Always have backup pricing sources
4. **Log appropriately**: Log warnings and errors for monitoring

### Security

1. **Secure credentials**: Use environment variables or secure storage
2. **Rotate keys**: Regularly rotate API keys
3. **Monitor usage**: Watch for unusual API usage patterns
4. **Use HTTPS**: Always use secure connections

### Production Deployment

1. **Health checks**: Implement comprehensive health monitoring
2. **Alerting**: Set up alerts for pricing data issues
3. **Backup strategies**: Have multiple fallback pricing sources
4. **Documentation**: Keep API documentation up to date

This completes the comprehensive API reference documentation for Clustrix's pricing system.