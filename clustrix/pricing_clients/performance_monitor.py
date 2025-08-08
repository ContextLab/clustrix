"""
Performance monitoring and optimization for pricing clients.

This module provides comprehensive performance monitoring, caching optimization,
and resilience features for production deployments.
"""

import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, Callable
from dataclasses import dataclass, field
from collections import defaultdict, deque
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetric:
    """Performance metric for pricing API calls."""

    provider: str
    operation: str
    response_time_seconds: float
    success: bool
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    cache_hit: bool = False
    instance_type: Optional[str] = None
    region: Optional[str] = None


@dataclass
class ProviderHealthStatus:
    """Health status for a pricing provider."""

    provider: str
    is_healthy: bool
    last_success: Optional[datetime] = None
    last_error: Optional[datetime] = None
    error_count: int = 0
    success_count: int = 0
    average_response_time: float = 0.0
    last_error_message: Optional[str] = None


class PricingPerformanceMonitor:
    """Performance monitoring system for pricing clients."""

    def __init__(self, metrics_retention_hours: int = 24):
        """Initialize the performance monitor.

        Args:
            metrics_retention_hours: How long to retain metrics data
        """
        self.metrics_retention_hours = metrics_retention_hours
        self.metrics: deque = deque()
        self.provider_stats: Dict[str, ProviderHealthStatus] = {}
        self.lock = threading.Lock()

        # Performance thresholds
        self.response_time_threshold = 30.0  # seconds
        self.error_rate_threshold = 0.05  # 5%
        self.cache_hit_target = 0.8  # 80%

        # Start background cleanup thread
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_old_metrics, daemon=True
        )
        self.cleanup_thread.start()

    def record_metric(self, metric: PerformanceMetric):
        """Record a performance metric."""
        with self.lock:
            self.metrics.append(metric)
            self._update_provider_stats(metric)

    def _update_provider_stats(self, metric: PerformanceMetric):
        """Update provider health statistics."""
        if metric.provider not in self.provider_stats:
            self.provider_stats[metric.provider] = ProviderHealthStatus(
                provider=metric.provider, is_healthy=True
            )

        stats = self.provider_stats[metric.provider]

        if metric.success:
            stats.success_count += 1
            stats.last_success = metric.timestamp
        else:
            stats.error_count += 1
            stats.last_error = metric.timestamp
            stats.last_error_message = metric.error_message

        # Calculate average response time (last 100 requests)
        recent_metrics = [
            m
            for m in reversed(self.metrics)
            if m.provider == metric.provider and len([1 for _ in range(100)])
        ][:100]

        if recent_metrics:
            stats.average_response_time = sum(
                m.response_time_seconds for m in recent_metrics
            ) / len(recent_metrics)

        # Update health status
        total_requests = stats.success_count + stats.error_count
        error_rate = stats.error_count / total_requests if total_requests > 0 else 0

        stats.is_healthy = (
            error_rate < self.error_rate_threshold
            and stats.average_response_time < self.response_time_threshold
            and (
                stats.last_success is None
                or (datetime.now() - stats.last_success).total_seconds() < 3600
            )  # Success within 1 hour
        )

    def get_provider_health(self, provider: str) -> Optional[ProviderHealthStatus]:
        """Get health status for a specific provider."""
        with self.lock:
            return self.provider_stats.get(provider)

    def get_all_provider_health(self) -> Dict[str, ProviderHealthStatus]:
        """Get health status for all providers."""
        with self.lock:
            return dict(self.provider_stats)

    def get_performance_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get performance summary for the specified time period."""
        cutoff_time = datetime.now() - timedelta(hours=hours)

        with self.lock:
            recent_metrics = [m for m in self.metrics if m.timestamp >= cutoff_time]

        if not recent_metrics:
            return {"message": "No metrics available for specified time period"}

        # Calculate overall statistics
        total_requests = len(recent_metrics)
        successful_requests = sum(1 for m in recent_metrics if m.success)
        error_rate = (
            1 - (successful_requests / total_requests) if total_requests > 0 else 0
        )

        # Response time statistics
        response_times = [m.response_time_seconds for m in recent_metrics]
        avg_response_time = sum(response_times) / len(response_times)
        max_response_time = max(response_times)
        min_response_time = min(response_times)

        # Cache statistics
        cache_hits = sum(1 for m in recent_metrics if m.cache_hit)
        cache_hit_rate = cache_hits / total_requests if total_requests > 0 else 0

        # Per-provider statistics
        provider_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"requests": 0, "errors": 0, "response_times": []}
        )

        for metric in recent_metrics:
            stats = provider_stats[metric.provider]
            stats["requests"] += 1
            if not metric.success:
                stats["errors"] += 1
            stats["response_times"].append(metric.response_time_seconds)

        provider_summary = {}
        for provider, stats in provider_stats.items():
            avg_time = sum(stats["response_times"]) / len(stats["response_times"])
            error_rate = (
                stats["errors"] / stats["requests"] if stats["requests"] > 0 else 0
            )

            provider_summary[provider] = {
                "requests": stats["requests"],
                "error_rate": error_rate,
                "average_response_time": avg_time,
                "is_healthy": error_rate < self.error_rate_threshold
                and avg_time < self.response_time_threshold,
            }

        return {
            "time_period_hours": hours,
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "error_rate": error_rate,
            "cache_hit_rate": cache_hit_rate,
            "average_response_time": avg_response_time,
            "min_response_time": min_response_time,
            "max_response_time": max_response_time,
            "provider_summary": provider_summary,
            "thresholds": {
                "max_response_time": self.response_time_threshold,
                "max_error_rate": self.error_rate_threshold,
                "target_cache_hit_rate": self.cache_hit_target,
            },
        }

    def _cleanup_old_metrics(self):
        """Background thread to clean up old metrics."""
        while True:
            try:
                cutoff_time = datetime.now() - timedelta(
                    hours=self.metrics_retention_hours
                )

                with self.lock:
                    # Remove old metrics
                    while self.metrics and self.metrics[0].timestamp < cutoff_time:
                        self.metrics.popleft()

                # Sleep for 1 hour between cleanups
                time.sleep(3600)

            except Exception as e:
                logger.error(f"Error in metrics cleanup thread: {e}")
                time.sleep(300)  # Sleep 5 minutes on error

    def export_metrics(self, filepath: str):
        """Export metrics to JSON file."""
        with self.lock:
            metrics_data = [
                {
                    "provider": m.provider,
                    "operation": m.operation,
                    "response_time_seconds": m.response_time_seconds,
                    "success": m.success,
                    "error_message": m.error_message,
                    "timestamp": m.timestamp.isoformat(),
                    "cache_hit": m.cache_hit,
                    "instance_type": m.instance_type,
                    "region": m.region,
                }
                for m in self.metrics
            ]

        with open(filepath, "w") as f:
            json.dump(
                {
                    "metrics": metrics_data,
                    "export_timestamp": datetime.now().isoformat(),
                    "total_metrics": len(metrics_data),
                },
                f,
                indent=2,
            )


class OptimizedPricingClientMixin:
    """Mixin class to add performance monitoring to pricing clients."""

    def __init__(
        self,
        *args,
        performance_monitor: Optional[PricingPerformanceMonitor] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.performance_monitor = performance_monitor or _global_performance_monitor
        self.provider_name = getattr(
            self,
            "provider_name",
            self.__class__.__name__.lower().replace("pricingclient", ""),
        )

    def _monitor_api_call(self, operation: str, func: Callable, *args, **kwargs) -> Any:
        """Monitor an API call and record performance metrics."""
        start_time = time.time()
        success = False
        error_message = None
        result = None
        cache_hit = False

        # Extract instance_type and region if available
        instance_type = kwargs.get("instance_type") or (args[0] if args else None)
        region = kwargs.get("region") or (args[1] if len(args) > 1 else None)

        try:
            # Check if this is likely a cache hit (very fast response)
            result = func(*args, **kwargs)
            response_time = time.time() - start_time

            success = result is not None
            cache_hit = response_time < 0.1  # Less than 100ms likely means cache hit

        except Exception as e:
            response_time = time.time() - start_time
            error_message = str(e)
            logger.warning(f"API call failed for {self.provider_name} {operation}: {e}")

        finally:
            # Record the metric
            if self.performance_monitor:
                metric = PerformanceMetric(
                    provider=self.provider_name,
                    operation=operation,
                    response_time_seconds=response_time,
                    success=success,
                    error_message=error_message,
                    cache_hit=cache_hit,
                    instance_type=instance_type,
                    region=region,
                )
                self.performance_monitor.record_metric(metric)

        return result


class CircuitBreaker:
    """Circuit breaker pattern for pricing API calls."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            expected_exception: Exception type that triggers circuit breaker
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open
        self.lock = threading.Lock()

    def __call__(self, func):
        """Decorator to apply circuit breaker to a function."""

        def wrapper(*args, **kwargs):
            with self.lock:
                if self.state == "open":
                    if self._should_attempt_reset():
                        self.state = "half-open"
                    else:
                        raise Exception(f"Circuit breaker is OPEN for {func.__name__}")

                try:
                    result = func(*args, **kwargs)
                    self._on_success()
                    return result

                except self.expected_exception as e:
                    self._on_failure()
                    raise e

        return wrapper

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return False
        return time.time() - self.last_failure_time >= self.recovery_timeout

    def _on_success(self):
        """Handle successful call."""
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"


class PricingCache:
    """Enhanced pricing cache with performance optimizations."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl_hours: int = 24,
        max_size_mb: int = 100,
    ):
        """Initialize enhanced pricing cache.

        Args:
            cache_dir: Directory to store cache files
            ttl_hours: Time to live for cached data
            max_size_mb: Maximum cache size in MB
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".clustrix" / "cache"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_hours * 3600
        self.max_size_bytes = max_size_mb * 1024 * 1024

        # In-memory cache for frequently accessed items
        self.memory_cache: Dict[str, tuple] = {}  # key: (data, timestamp)
        self.access_count: Dict[str, int] = defaultdict(int)

        # Statistics
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_writes = 0

    def get(self, key: str) -> Optional[Any]:
        """Get item from cache with performance tracking."""
        # Check memory cache first
        if key in self.memory_cache:
            data, timestamp = self.memory_cache[key]
            if time.time() - timestamp < self.ttl_seconds:
                self.access_count[key] += 1
                self.cache_hits += 1
                return data
            else:
                # Expired, remove from memory cache
                del self.memory_cache[key]

        # Check file cache
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    cached_data = json.load(f)

                timestamp = cached_data.get("timestamp", 0)
                if time.time() - timestamp < self.ttl_seconds:
                    data = cached_data.get("data")

                    # Add to memory cache if frequently accessed
                    self.access_count[key] += 1
                    if self.access_count[key] > 3:  # Cache in memory after 3 accesses
                        self.memory_cache[key] = (data, timestamp)

                    self.cache_hits += 1
                    return data
                else:
                    # Expired, remove file
                    cache_file.unlink()

            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Error reading cache file {cache_file}: {e}")
                cache_file.unlink(missing_ok=True)

        self.cache_misses += 1
        return None

    def set(self, key: str, data: Any):
        """Set item in cache with size management."""
        timestamp = time.time()

        # Add to memory cache
        self.memory_cache[key] = (data, timestamp)

        # Write to file cache
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w") as f:
                json.dump(
                    {"data": data, "timestamp": timestamp, "key": key}, f, indent=2
                )

            self.cache_writes += 1

            # Manage cache size
            self._manage_cache_size()

        except (IOError, OSError) as e:
            logger.warning(f"Error writing cache file {cache_file}: {e}")

    def _manage_cache_size(self):
        """Manage cache size by removing old/least accessed files."""
        try:
            # Calculate current cache size
            total_size = sum(
                f.stat().st_size for f in self.cache_dir.glob("*.json") if f.is_file()
            )

            if total_size > self.max_size_bytes:
                # Remove oldest files first
                cache_files = [
                    (f, f.stat().st_mtime)
                    for f in self.cache_dir.glob("*.json")
                    if f.is_file()
                ]
                cache_files.sort(key=lambda x: x[1])  # Sort by modification time

                # Remove files until under size limit
                current_size = total_size
                for cache_file, _ in cache_files:
                    if current_size <= self.max_size_bytes * 0.8:  # Leave 20% buffer
                        break

                    file_size = cache_file.stat().st_size
                    cache_file.unlink()
                    current_size -= file_size

                    # Also remove from memory cache
                    key = cache_file.stem
                    if key in self.memory_cache:
                        del self.memory_cache[key]
                    if key in self.access_count:
                        del self.access_count[key]

        except Exception as e:
            logger.warning(f"Error managing cache size: {e}")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics."""
        total_requests = self.cache_hits + self.cache_misses
        hit_rate = self.cache_hits / total_requests if total_requests > 0 else 0

        # Calculate cache size
        try:
            cache_size = sum(
                f.stat().st_size for f in self.cache_dir.glob("*.json") if f.is_file()
            )
            file_count = len(list(self.cache_dir.glob("*.json")))
        except Exception:
            cache_size = 0
            file_count = 0

        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_writes": self.cache_writes,
            "hit_rate": hit_rate,
            "memory_cache_size": len(self.memory_cache),
            "file_cache_size_bytes": cache_size,
            "file_cache_count": file_count,
            "max_size_bytes": self.max_size_bytes,
        }


# Global performance monitor instance
_global_performance_monitor = PricingPerformanceMonitor()


def get_global_performance_monitor() -> PricingPerformanceMonitor:
    """Get the global performance monitor instance."""
    return _global_performance_monitor


def create_circuit_breaker(provider: str) -> CircuitBreaker:
    """Create a circuit breaker for a specific provider."""
    return CircuitBreaker(
        failure_threshold=5,
        recovery_timeout=300,  # 5 minutes
        expected_exception=Exception,
    )
