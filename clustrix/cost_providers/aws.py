"""
AWS cost monitoring implementation.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any

from ..cost_monitoring import BaseCostMonitor, ResourceUsage, CostEstimate
from ..pricing_clients.aws_pricing import AWSPricingClient

logger = logging.getLogger(__name__)


class AWSCostMonitor(BaseCostMonitor):
    """Cost monitoring for AWS instances (EC2, Batch, etc.)."""

    def __init__(self, region: str = "us-east-1", use_pricing_api: bool = True):
        super().__init__("AWS")
        self.region = region
        self.use_pricing_api = use_pricing_api

        # Initialize pricing client
        self.pricing_client = AWSPricingClient() if use_pricing_api else None

        # AWS EC2 On-Demand pricing (us-east-1, as of 2025, approximate rates in USD/hour)
        # These serve as fallback when API is unavailable
        self.ec2_pricing = {
            # General Purpose
            "t3.micro": 0.0104,
            "t3.small": 0.0208,
            "t3.medium": 0.0416,
            "t3.large": 0.0832,
            "t3.xlarge": 0.1664,
            "t3.2xlarge": 0.3328,
            # Compute Optimized
            "c5.large": 0.085,
            "c5.xlarge": 0.17,
            "c5.2xlarge": 0.34,
            "c5.4xlarge": 0.68,
            "c5.9xlarge": 1.53,
            "c5.18xlarge": 3.06,
            # Memory Optimized
            "r5.large": 0.126,
            "r5.xlarge": 0.252,
            "r5.2xlarge": 0.504,
            "r5.4xlarge": 1.008,
            "r5.8xlarge": 2.016,
            "r5.16xlarge": 4.032,
            # GPU Instances
            "p3.2xlarge": 3.06,  # 1 V100
            "p3.8xlarge": 12.24,  # 4 V100
            "p3.16xlarge": 24.48,  # 8 V100
            "p4d.24xlarge": 32.77,  # 8 A100
            "g4dn.xlarge": 0.526,  # 1 T4
            "g4dn.2xlarge": 0.752,  # 1 T4
            "g4dn.4xlarge": 1.204,  # 1 T4
            "g4dn.8xlarge": 2.176,  # 1 T4
            "g4dn.12xlarge": 3.912,  # 4 T4
            "g4dn.16xlarge": 4.352,  # 1 T4
            # Default fallback
            "default": 0.10,
        }

        # Spot instance discount factors (approximate)
        self.spot_discounts = {
            "t3": 0.7,  # ~30% discount
            "c5": 0.65,  # ~35% discount
            "r5": 0.6,  # ~40% discount
            "p3": 0.3,  # ~70% discount
            "p4d": 0.35,  # ~65% discount
            "g4dn": 0.4,  # ~60% discount
            "default": 0.7,  # ~30% discount
        }

        # Instance metadata
        self.instance_metadata = {
            "p3.2xlarge": {
                "gpus": 1,
                "gpu_type": "V100",
                "gpu_memory": "16GB",
                "cpu_cores": 8,
                "ram": "61GB",
            },
            "p3.8xlarge": {
                "gpus": 4,
                "gpu_type": "V100",
                "gpu_memory": "64GB",
                "cpu_cores": 32,
                "ram": "244GB",
            },
            "p3.16xlarge": {
                "gpus": 8,
                "gpu_type": "V100",
                "gpu_memory": "128GB",
                "cpu_cores": 64,
                "ram": "488GB",
            },
            "p4d.24xlarge": {
                "gpus": 8,
                "gpu_type": "A100",
                "gpu_memory": "320GB",
                "cpu_cores": 96,
                "ram": "1152GB",
            },
            "g4dn.xlarge": {
                "gpus": 1,
                "gpu_type": "T4",
                "gpu_memory": "16GB",
                "cpu_cores": 4,
                "ram": "16GB",
            },
            "g4dn.2xlarge": {
                "gpus": 1,
                "gpu_type": "T4",
                "gpu_memory": "16GB",
                "cpu_cores": 8,
                "ram": "32GB",
            },
            "g4dn.12xlarge": {
                "gpus": 4,
                "gpu_type": "T4",
                "gpu_memory": "64GB",
                "cpu_cores": 48,
                "ram": "192GB",
            },
        }

    def get_resource_usage(self) -> ResourceUsage:
        """Get current resource utilization for AWS instance."""
        # Get CPU and memory usage
        cpu_percent, mem_used_mb, mem_total_mb, mem_percent = (
            self.get_cpu_memory_usage()
        )

        # Get GPU utilization (if available)
        gpu_stats = self.get_gpu_utilization()

        return ResourceUsage(
            cpu_percent=cpu_percent,
            memory_used_mb=mem_used_mb,
            memory_total_mb=mem_total_mb,
            memory_percent=mem_percent,
            gpu_stats=gpu_stats,
        )

    def estimate_cost(
        self, instance_type: str, hours_used: float, use_spot: bool = False
    ) -> CostEstimate:
        """Estimate cost for AWS instance usage."""
        hourly_rate = None
        pricing_source = "hardcoded"
        pricing_warning = None

        # Try to get pricing from API first
        if self.pricing_client and not use_spot:
            try:
                hourly_rate = self.pricing_client.get_instance_pricing(
                    instance_type=instance_type, region=self.region
                )
                if hourly_rate:
                    logger.debug(
                        f"Got pricing for {instance_type} from API: ${hourly_rate}/hr"
                    )
                    pricing_source = "api"
            except Exception as e:
                logger.debug(f"Failed to get pricing from API: {e}")

        # Fall back to hardcoded pricing if API failed
        if hourly_rate is None:
            hourly_rate = self.ec2_pricing.get(
                instance_type, self.ec2_pricing["default"]
            )
            pricing_source = "hardcoded"
            if self.pricing_client and self.pricing_client.is_pricing_data_outdated():
                pricing_warning = (
                    f"Using potentially outdated pricing data (last updated: "
                    f"{self.pricing_client._hardcoded_pricing_date}). "
                    f"Current prices may differ. Consider checking AWS pricing page."
                )
                logger.warning(pricing_warning)

        # Apply spot discount if requested
        if use_spot:
            instance_family = instance_type.split(".")[0]
            discount_factor = self.spot_discounts.get(
                instance_family, self.spot_discounts["default"]
            )
            hourly_rate *= discount_factor
            if pricing_source == "hardcoded":
                pricing_warning = (
                    pricing_warning or ""
                ) + " Spot pricing is estimated and may vary significantly."

        # Calculate estimated cost
        estimated_cost = hourly_rate * hours_used

        pricing_type = "Spot" if use_spot else "On-Demand"

        return CostEstimate(
            instance_type=f"{instance_type} ({pricing_type})",
            hourly_rate=hourly_rate,
            hours_used=hours_used,
            estimated_cost=estimated_cost,
            currency="USD",
            last_updated=datetime.now(),
            pricing_source=pricing_source,
            pricing_warning=pricing_warning,
        )

    def get_pricing_info(self) -> Dict[str, float]:
        """Get AWS EC2 pricing information."""
        # For now, return hardcoded pricing with a warning if outdated
        # A full implementation would query the API for all instance types
        if self.pricing_client and self.pricing_client.is_pricing_data_outdated():
            logger.warning(
                "Pricing data may be outdated. Consider refreshing from AWS Pricing API."
            )
        return self.ec2_pricing.copy()

    def get_spot_pricing_info(self) -> Dict[str, float]:
        """Get estimated AWS spot pricing."""
        spot_pricing = {}
        for instance_type, on_demand_price in self.ec2_pricing.items():
            if instance_type != "default":
                instance_family = instance_type.split(".")[0]
                discount_factor = self.spot_discounts.get(
                    instance_family, self.spot_discounts["default"]
                )
                spot_pricing[instance_type] = on_demand_price * discount_factor
        return spot_pricing

    def get_cost_optimization_recommendations(
        self, resource_usage: ResourceUsage, cost_estimate: CostEstimate
    ) -> List[str]:
        """Get AWS-specific cost optimization recommendations."""
        recommendations = super().get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # AWS-specific recommendations
        recommendations.extend(
            [
                "Consider using Spot Instances for fault-tolerant workloads (up to 90% savings)",
                "Use Reserved Instances for predictable workloads (up to 75% savings)",
                "Consider AWS Batch for large-scale batch processing",
                "Use EBS-optimized instances for I/O intensive workloads",
                "Enable detailed monitoring to track resource utilization",
                "Consider using AWS ParallelCluster for HPC workloads",
                "Use S3 for data storage instead of expensive EBS volumes when possible",
                "Set up CloudWatch alarms for cost monitoring and auto-termination",
            ]
        )

        # Instance-specific recommendations
        current_instance = cost_estimate.instance_type.split(" ")[
            0
        ]  # Remove pricing type
        if current_instance.startswith("p3") or current_instance.startswith("p4d"):
            if resource_usage.gpu_stats:
                avg_gpu_util = sum(
                    gpu.get("utilization_percent", 0)
                    for gpu in resource_usage.gpu_stats
                ) / len(resource_usage.gpu_stats)
                if avg_gpu_util < 50:
                    recommendations.append(
                        "Low GPU utilization on expensive GPU instance. Consider g4dn instances or CPU instances."
                    )

        return recommendations

    def estimate_batch_cost(
        self,
        job_queue: str,
        compute_environment: str,
        estimated_jobs: int,
        avg_job_duration_hours: float,
    ) -> Dict[str, Any]:
        """Estimate costs for AWS Batch workloads."""
        # This would typically integrate with AWS Batch APIs
        # For now, provide a basic estimation framework

        base_instance_cost = self.ec2_pricing.get(
            "c5.large", 0.085
        )  # Default compute instance

        total_compute_hours = estimated_jobs * avg_job_duration_hours
        estimated_cost = total_compute_hours * base_instance_cost

        return {
            "job_queue": job_queue,
            "compute_environment": compute_environment,
            "estimated_jobs": estimated_jobs,
            "avg_job_duration_hours": avg_job_duration_hours,
            "total_compute_hours": total_compute_hours,
            "estimated_cost": estimated_cost,
            "cost_per_job": (
                estimated_cost / estimated_jobs if estimated_jobs > 0 else 0
            ),
            "recommendations": [
                "Use Spot instances in Batch compute environments for additional savings",
                "Optimize job packaging to reduce overhead",
                "Monitor job efficiency and resource utilization",
                "Use appropriate instance types for different job characteristics",
            ],
        }

    def get_region_pricing_comparison(
        self, instance_type: str
    ) -> Dict[str, Dict[str, Any]]:
        """Compare pricing across AWS regions (simplified)."""
        # Regional pricing multipliers (approximate)
        region_multipliers = {
            "us-east-1": 1.0,  # N. Virginia (baseline)
            "us-west-2": 1.05,  # Oregon
            "eu-west-1": 1.1,  # Ireland
            "ap-southeast-1": 1.15,  # Singapore
            "ap-northeast-1": 1.2,  # Tokyo
        }

        base_price = self.ec2_pricing.get(instance_type, self.ec2_pricing["default"])

        regional_pricing = {}
        for region, multiplier in region_multipliers.items():
            regional_pricing[region] = {
                "on_demand_hourly": base_price * multiplier,
                "estimated_spot_hourly": base_price
                * multiplier
                * 0.7,  # Rough spot estimate
                "region_name": region,
            }

        return regional_pricing

    def get_aws_specific_metrics(self) -> Dict[str, Any]:
        """Get AWS-specific cost and performance metrics."""
        return {
            "region": self.region,
            "availability_zone": "auto-detect",  # Would detect from instance metadata
            "instance_lifecycle": "on-demand",  # or 'spot'
            "ebs_optimized": True,
            "enhanced_networking": True,
            "placement_group": None,
            "cost_optimization_score": self._calculate_cost_optimization_score(),
        }

    def _calculate_cost_optimization_score(self) -> float:
        """Calculate a cost optimization score (0-100)."""
        # This would analyze various factors:
        # - Resource utilization
        # - Instance type appropriateness
        # - Spot vs on-demand usage
        # - Reserved instance coverage
        # For now, return a placeholder
        return 75.0
