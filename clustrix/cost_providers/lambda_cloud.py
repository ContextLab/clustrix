"""
Lambda Cloud cost monitoring implementation.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any

from ..cost_monitoring import BaseCostMonitor, ResourceUsage, CostEstimate

logger = logging.getLogger(__name__)


class LambdaCostMonitor(BaseCostMonitor):
    """Cost monitoring for Lambda Cloud instances."""

    def __init__(self):
        super().__init__("Lambda Cloud")

        # Lambda Cloud pricing (as of 2025, approximate rates in USD/hour)
        self.pricing = {
            # Single GPU instances
            "rtx6000ada": 0.75,
            "a10": 0.60,
            "a6000": 0.80,
            "a100_40gb": 1.10,
            "a100_80gb": 1.40,
            "h100": 2.50,
            # Multi-GPU instances
            "2xa100_40gb": 2.20,
            "4xa100_40gb": 4.40,
            "8xa100_40gb": 8.80,
            "2xa100_80gb": 2.80,
            "4xa100_80gb": 5.60,
            "8xa100_80gb": 11.20,
            "8xh100": 20.00,
            # CPU instances
            "cpu_small": 0.10,
            "cpu_medium": 0.20,
            "cpu_large": 0.40,
            # Default fallback
            "default": 1.00,
        }

        # Instance type metadata
        self.instance_metadata = {
            "rtx6000ada": {
                "gpus": 1,
                "gpu_memory": "48GB",
                "cpu_cores": 14,
                "ram": "46GB",
            },
            "a10": {"gpus": 1, "gpu_memory": "24GB", "cpu_cores": 12, "ram": "46GB"},
            "a6000": {"gpus": 1, "gpu_memory": "48GB", "cpu_cores": 14, "ram": "46GB"},
            "a100_40gb": {
                "gpus": 1,
                "gpu_memory": "40GB",
                "cpu_cores": 30,
                "ram": "200GB",
            },
            "a100_80gb": {
                "gpus": 1,
                "gpu_memory": "80GB",
                "cpu_cores": 30,
                "ram": "200GB",
            },
            "h100": {"gpus": 1, "gpu_memory": "80GB", "cpu_cores": 26, "ram": "150GB"},
            "2xa100_40gb": {
                "gpus": 2,
                "gpu_memory": "80GB",
                "cpu_cores": 60,
                "ram": "400GB",
            },
            "4xa100_40gb": {
                "gpus": 4,
                "gpu_memory": "160GB",
                "cpu_cores": 120,
                "ram": "800GB",
            },
            "8xa100_40gb": {
                "gpus": 8,
                "gpu_memory": "320GB",
                "cpu_cores": 240,
                "ram": "1600GB",
            },
        }

    def get_resource_usage(self) -> ResourceUsage:
        """Get current resource utilization for Lambda Cloud instance."""
        # Get CPU and memory usage
        cpu_percent, mem_used_mb, mem_total_mb, mem_percent = (
            self.get_cpu_memory_usage()
        )

        # Get GPU utilization
        gpu_stats = self.get_gpu_utilization()

        return ResourceUsage(
            cpu_percent=cpu_percent,
            memory_used_mb=mem_used_mb,
            memory_total_mb=mem_total_mb,
            memory_percent=mem_percent,
            gpu_stats=gpu_stats,
        )

    def estimate_cost(self, instance_type: str, hours_used: float) -> CostEstimate:
        """Estimate cost for Lambda Cloud instance usage."""
        # Normalize instance type
        instance_type = instance_type.lower().replace("-", "_")

        # Get hourly rate
        hourly_rate = self.pricing.get(instance_type, self.pricing["default"])

        # Calculate estimated cost
        estimated_cost = hourly_rate * hours_used

        return CostEstimate(
            instance_type=instance_type,
            hourly_rate=hourly_rate,
            hours_used=hours_used,
            estimated_cost=estimated_cost,
            currency="USD",
            last_updated=datetime.now(),
        )

    def get_pricing_info(self) -> Dict[str, float]:
        """Get Lambda Cloud pricing information."""
        return self.pricing.copy()

    def get_instance_recommendations(
        self, resource_usage: ResourceUsage, current_instance: str = None
    ) -> List[str]:
        """Get instance type recommendations based on current usage."""
        recommendations = []

        if not resource_usage.gpu_stats:
            # No GPU usage detected
            if resource_usage.cpu_percent < 50 and resource_usage.memory_percent < 50:
                recommendations.append(
                    "Consider using CPU instances instead of GPU instances for this workload."
                )
            return recommendations

        # Analyze GPU usage
        gpu_utilizations = [
            gpu.get("utilization_percent", 0) for gpu in resource_usage.gpu_stats
        ]
        avg_gpu_util = (
            sum(gpu_utilizations) / len(gpu_utilizations) if gpu_utilizations else 0
        )

        gpu_memory_utils = [
            gpu.get("memory_utilization_percent", 0) for gpu in resource_usage.gpu_stats
        ]
        avg_gpu_mem_util = (
            sum(gpu_memory_utils) / len(gpu_memory_utils) if gpu_memory_utils else 0
        )

        # Single GPU recommendations
        if len(resource_usage.gpu_stats) == 1:
            if avg_gpu_util < 30:
                recommendations.append(
                    "Low GPU utilization. Consider optimizing your code or using a smaller instance."
                )
            elif avg_gpu_util > 95:
                recommendations.append(
                    "High GPU utilization. Consider multi-GPU instances for better performance."
                )

            if avg_gpu_mem_util > 90:
                recommendations.append(
                    "High GPU memory usage. Consider using GPU instances with more memory."
                )
            elif avg_gpu_mem_util < 20:
                recommendations.append(
                    "Low GPU memory usage. Consider using GPU instances with less memory."
                )

        # Multi-GPU recommendations
        else:
            underutilized_gpus = sum(1 for util in gpu_utilizations if util < 50)
            if underutilized_gpus > len(gpu_utilizations) / 2:
                recommendations.append(
                    f"{underutilized_gpus}/{len(gpu_utilizations)} GPUs are underutilized. "
                    "Consider using fewer GPUs or optimizing data parallelism."
                )

        # Cost-efficiency recommendations
        if current_instance:
            current_rate = self.pricing.get(
                current_instance.lower().replace("-", "_"), 0
            )
            if (
                current_rate > 5.0 and avg_gpu_util < 60
            ):  # Expensive instance with low utilization
                recommendations.append(
                    "High-cost instance with low utilization. "
                    "Consider using spot instances or smaller instance types."
                )

        return recommendations

    def get_cost_optimization_tips(self) -> List[str]:
        """Get general cost optimization tips for Lambda Cloud."""
        return [
            "Use spot instances for non-critical workloads (up to 50% savings)",
            "Terminate instances immediately after completing jobs",
            "Monitor GPU utilization and right-size instances accordingly",
            "Use multi-GPU instances efficiently with proper data parallelism",
            "Consider CPU instances for non-GPU workloads",
            "Use mixed precision training to reduce memory requirements",
            "Implement checkpointing to handle potential spot instance interruptions",
            "Monitor costs regularly and set up budget alerts",
            "Choose the right region based on pricing and latency requirements",
            "Batch multiple experiments to maximize instance utilization",
        ]

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get comprehensive performance metrics for Lambda Cloud instances."""
        resource_usage = self.get_resource_usage()

        metrics = {
            "cpu_utilization": resource_usage.cpu_percent,
            "memory_utilization": resource_usage.memory_percent,
            "memory_used_gb": resource_usage.memory_used_mb / 1024,
            "memory_total_gb": resource_usage.memory_total_mb / 1024,
            "timestamp": datetime.now().isoformat(),
        }

        if resource_usage.gpu_stats:
            metrics["gpu_count"] = len(resource_usage.gpu_stats)
            metrics["gpu_utilization_avg"] = sum(
                gpu.get("utilization_percent", 0) for gpu in resource_usage.gpu_stats
            ) / len(resource_usage.gpu_stats)
            metrics["gpu_memory_utilization_avg"] = sum(
                gpu.get("memory_utilization_percent", 0)
                for gpu in resource_usage.gpu_stats
            ) / len(resource_usage.gpu_stats)
            metrics["gpu_details"] = resource_usage.gpu_stats

        return metrics

    def estimate_monthly_cost(
        self, instance_type: str, hours_per_day: float = 8
    ) -> Dict[str, float]:
        """Estimate monthly costs for different usage patterns."""
        instance_type = instance_type.lower().replace("-", "_")
        hourly_rate = self.pricing.get(instance_type, self.pricing["default"])

        return {
            "hourly_rate": hourly_rate,
            "daily_cost_8h": hourly_rate * hours_per_day,
            "weekly_cost_40h": hourly_rate * 40,  # 5 days * 8 hours
            "monthly_cost_160h": hourly_rate * 160,  # ~20 working days * 8 hours
            "monthly_cost_24x7": hourly_rate * 24 * 30,  # 24/7 usage
            "instance_type": instance_type,
        }
