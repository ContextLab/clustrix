"""
Cost monitoring functionality for Clustrix across different cloud providers.

This module provides unified cost tracking, resource utilization monitoring,
and cost optimization recommendations for various cloud platforms.
"""

import time
import subprocess
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Callable
from functools import wraps
from dataclasses import dataclass, asdict
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class ResourceUsage:
    """Resource utilization metrics."""

    cpu_percent: float
    memory_used_mb: int
    memory_total_mb: int
    memory_percent: float
    gpu_stats: Optional[List[Dict[str, Any]]] = None
    network_io_mb: Optional[float] = None
    disk_io_mb: Optional[float] = None


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


@dataclass
class CostReport:
    """Comprehensive cost and usage report."""

    timestamp: datetime
    duration_seconds: float
    resource_usage: ResourceUsage
    cost_estimate: CostEstimate
    provider: str
    region: Optional[str] = None
    recommendations: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class BaseCostMonitor(ABC):
    """Base class for cloud provider cost monitoring."""

    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.start_time = None
        self.monitoring_enabled = True

    @abstractmethod
    def get_resource_usage(self) -> ResourceUsage:
        """Get current resource utilization metrics."""
        pass

    @abstractmethod
    def estimate_cost(self, instance_type: str, hours_used: float) -> CostEstimate:
        """Estimate cost for given instance type and usage duration."""
        pass

    @abstractmethod
    def get_pricing_info(self) -> Dict[str, float]:
        """Get current pricing information for different instance types."""
        pass

    def start_monitoring(self):
        """Start cost monitoring session."""
        self.start_time = time.time()
        logger.info(f"Started cost monitoring for {self.provider_name}")

    def stop_monitoring(self) -> Optional[CostReport]:
        """Stop monitoring and generate cost report."""
        if self.start_time is None:
            logger.warning("Monitoring was not started")
            return None

        end_time = time.time()
        duration = end_time - self.start_time

        # Get current resource usage
        resource_usage = self.get_resource_usage()

        # Estimate cost (requires instance type to be set)
        cost_estimate = self.estimate_cost("default", duration / 3600)

        # Generate recommendations
        recommendations = self.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        report = CostReport(
            timestamp=datetime.now(),
            duration_seconds=duration,
            resource_usage=resource_usage,
            cost_estimate=cost_estimate,
            provider=self.provider_name,
            recommendations=recommendations,
        )

        logger.info(
            f"Completed cost monitoring for {self.provider_name}. "
            f"Duration: {duration:.2f}s, Estimated cost: ${cost_estimate.estimated_cost:.4f}"
        )

        return report

    def get_cost_optimization_recommendations(
        self, resource_usage: ResourceUsage, cost_estimate: CostEstimate
    ) -> List[str]:
        """Generate cost optimization recommendations based on usage patterns."""
        recommendations = []

        # CPU utilization recommendations
        if resource_usage.cpu_percent < 20:
            recommendations.append(
                "Low CPU usage detected. Consider using a smaller instance type."
            )
        elif resource_usage.cpu_percent > 90:
            recommendations.append(
                "High CPU usage detected. Consider using a larger instance type or optimizing workload."
            )

        # Memory utilization recommendations
        if resource_usage.memory_percent < 30:
            recommendations.append(
                "Low memory usage detected. Consider using an instance with less memory."
            )
        elif resource_usage.memory_percent > 85:
            recommendations.append(
                "High memory usage detected. Consider using an instance with more memory."
            )

        # GPU utilization recommendations (if available)
        if resource_usage.gpu_stats:
            avg_gpu_util = sum(
                gpu.get("utilization_percent", 0) for gpu in resource_usage.gpu_stats
            ) / len(resource_usage.gpu_stats)
            if avg_gpu_util < 50:
                recommendations.append(
                    "Low GPU utilization detected. Consider using CPU instances or optimizing GPU workload."
                )
            elif avg_gpu_util > 95:
                recommendations.append(
                    "High GPU utilization detected. Consider multi-GPU instances for better performance."
                )

        # Cost-based recommendations
        if cost_estimate.estimated_cost > 10:  # $10 threshold
            recommendations.append(
                "High estimated cost detected. Consider using spot instances or reserved capacity."
            )

        return recommendations

    def get_gpu_utilization(self) -> List[Dict[str, Any]]:
        """Get GPU utilization metrics using nvidia-smi."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                gpu_stats = []
                for i, line in enumerate(lines):
                    if line.strip():
                        parts = line.split(", ")
                        if len(parts) >= 3:
                            try:
                                gpu_stats.append(
                                    {
                                        "gpu_id": i,
                                        "utilization_percent": int(parts[0]),
                                        "memory_used_mb": int(parts[1]),
                                        "memory_total_mb": int(parts[2]),
                                        "memory_utilization_percent": round(
                                            int(parts[1]) / int(parts[2]) * 100, 1
                                        ),
                                        "temperature_c": (
                                            int(parts[3]) if len(parts) > 3 else None
                                        ),
                                    }
                                )
                            except (ValueError, ZeroDivisionError):
                                continue
                return gpu_stats
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug(f"Could not get GPU utilization: {e}")

        return []

    def get_cpu_memory_usage(self) -> tuple:
        """Get CPU and memory usage using system tools."""
        try:
            # Try to get CPU usage
            cpu_result = subprocess.run(
                ["python", "-c", "import psutil; print(f'{psutil.cpu_percent():.1f}')"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            cpu_percent = (
                float(cpu_result.stdout.strip()) if cpu_result.returncode == 0 else 0.0
            )

            # Try to get memory usage
            mem_result = subprocess.run(
                [
                    "python",
                    "-c",
                    "import psutil; m=psutil.virtual_memory(); "
                    "print(f'{m.used//1024//1024},{m.total//1024//1024},{m.percent:.1f}')",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if mem_result.returncode == 0:
                mem_used, mem_total, mem_percent = mem_result.stdout.strip().split(",")
                return cpu_percent, int(mem_used), int(mem_total), float(mem_percent)
        except Exception as e:
            logger.debug(f"Could not get CPU/memory usage: {e}")

        return 0.0, 0, 0, 0.0


def cost_tracking_decorator(provider: str, instance_type: str = "default"):
    """
    Decorator to automatically track costs for Clustrix functions.

    Args:
        provider: Cloud provider name (e.g., 'lambda', 'aws', 'azure', 'gcp')
        instance_type: Instance type for cost estimation

    Example::

        @cost_tracking_decorator('lambda', 'a100_40gb')
        @cluster(cores=8, memory="32GB")
        def my_training_function():
            # Your code here
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get the appropriate cost monitor
            monitor = get_cost_monitor(provider)
            if monitor is None:
                logger.warning(
                    f"Cost monitoring not available for provider: {provider}"
                )
                return func(*args, **kwargs)

            # Start monitoring
            monitor.start_monitoring()

            try:
                # Execute the function
                result = func(*args, **kwargs)
                success = True
                error = None
            except Exception as e:
                result = None
                success = False
                error = str(e)
                logger.error(f"Function execution failed: {e}")

            # Stop monitoring and get report
            cost_report = monitor.stop_monitoring()

            # Return enhanced result with cost information
            return {
                "result": result,
                "success": success,
                "error": error,
                "cost_report": asdict(cost_report) if cost_report else None,
                "provider": provider,
                "instance_type": instance_type,
            }

        return wrapper

    return decorator


def get_cost_monitor(provider: str) -> Optional[BaseCostMonitor]:
    """
    Get the appropriate cost monitor for a cloud provider.

    Args:
        provider: Cloud provider name

    Returns:
        Cost monitor instance or None if not available
    """
    provider = provider.lower()

    if provider == "lambda":
        from .cost_providers.lambda_cloud import LambdaCostMonitor

        return LambdaCostMonitor()
    elif provider == "aws":
        from .cost_providers.aws import AWSCostMonitor

        return AWSCostMonitor()
    elif provider == "azure":
        from .cost_providers.azure import AzureCostMonitor

        return AzureCostMonitor()
    elif provider == "gcp":
        from .cost_providers.gcp import GCPCostMonitor

        return GCPCostMonitor()
    else:
        logger.warning(f"Unsupported cloud provider: {provider}")
        return None


# Convenience functions for direct use
def start_cost_monitoring(provider: str) -> Optional[BaseCostMonitor]:
    """Start cost monitoring for a specific provider."""
    monitor = get_cost_monitor(provider)
    if monitor:
        monitor.start_monitoring()
    return monitor


def generate_cost_report(
    provider: str, instance_type: str = "default"
) -> Optional[Dict[str, Any]]:
    """Generate a cost report for the current session."""
    monitor = get_cost_monitor(provider)
    if monitor:
        # Get current state without stopping monitoring
        resource_usage = monitor.get_resource_usage()
        cost_estimate = monitor.estimate_cost(instance_type, 1.0)  # 1 hour estimate
        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        return {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "resource_usage": asdict(resource_usage),
            "cost_estimate": asdict(cost_estimate),
            "recommendations": recommendations,
        }
    return None


def get_pricing_info(provider: str) -> Optional[Dict[str, float]]:
    """Get pricing information for a cloud provider."""
    monitor = get_cost_monitor(provider)
    if monitor:
        return monitor.get_pricing_info()
    return None
