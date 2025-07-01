"""
Azure cost monitoring implementation.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any

from ..cost_monitoring import BaseCostMonitor, ResourceUsage, CostEstimate
from ..pricing_clients.azure_pricing import AzurePricingClient

logger = logging.getLogger(__name__)


class AzureCostMonitor(BaseCostMonitor):
    """Cost monitoring for Azure Virtual Machines and Batch."""

    def __init__(self, region: str = "eastus", use_pricing_api: bool = True):
        super().__init__("Azure")
        self.region = region
        self.use_pricing_api = use_pricing_api

        # Initialize pricing client
        self.pricing_client = AzurePricingClient() if use_pricing_api else None

        # Azure VM pricing (East US, as of 2025, approximate rates in USD/hour)
        # These serve as fallback when API is unavailable
        self.vm_pricing = {
            # B-series (Burstable)
            "Standard_B1s": 0.0104,
            "Standard_B1ms": 0.0208,
            "Standard_B2s": 0.0416,
            "Standard_B2ms": 0.0832,
            "Standard_B4ms": 0.1664,
            "Standard_B8ms": 0.3328,
            # D-series (General Purpose)
            "Standard_D2s_v3": 0.096,
            "Standard_D4s_v3": 0.192,
            "Standard_D8s_v3": 0.384,
            "Standard_D16s_v3": 0.768,
            "Standard_D32s_v3": 1.536,
            "Standard_D64s_v3": 3.072,
            # F-series (Compute Optimized)
            "Standard_F2s_v2": 0.085,
            "Standard_F4s_v2": 0.169,
            "Standard_F8s_v2": 0.338,
            "Standard_F16s_v2": 0.676,
            "Standard_F32s_v2": 1.352,
            "Standard_F64s_v2": 2.704,
            # E-series (Memory Optimized)
            "Standard_E2s_v3": 0.126,
            "Standard_E4s_v3": 0.252,
            "Standard_E8s_v3": 0.504,
            "Standard_E16s_v3": 1.008,
            "Standard_E32s_v3": 2.016,
            "Standard_E64s_v3": 4.032,
            # GPU VMs
            "Standard_NC6s_v3": 3.06,  # 1 V100
            "Standard_NC12s_v3": 6.12,  # 2 V100
            "Standard_NC24s_v3": 12.24,  # 4 V100
            "Standard_ND40rs_v2": 22.0,  # 8 V100
            "Standard_NC6s_v2": 0.90,  # 1 P100
            "Standard_NC12s_v2": 1.80,  # 2 P100
            "Standard_NC24s_v2": 3.60,  # 4 P100
            # Default fallback
            "default": 0.10,
        }

        # Spot VM discount factors (approximate)
        self.spot_discounts = {
            "Standard_B": 0.8,  # ~20% discount
            "Standard_D": 0.7,  # ~30% discount
            "Standard_F": 0.65,  # ~35% discount
            "Standard_E": 0.6,  # ~40% discount
            "Standard_NC": 0.3,  # ~70% discount
            "Standard_ND": 0.35,  # ~65% discount
            "default": 0.7,  # ~30% discount
        }

        # Instance metadata
        self.instance_metadata = {
            "Standard_NC6s_v3": {
                "gpus": 1,
                "gpu_type": "V100",
                "gpu_memory": "16GB",
                "cpu_cores": 6,
                "ram": "112GB",
            },
            "Standard_NC12s_v3": {
                "gpus": 2,
                "gpu_type": "V100",
                "gpu_memory": "32GB",
                "cpu_cores": 12,
                "ram": "224GB",
            },
            "Standard_NC24s_v3": {
                "gpus": 4,
                "gpu_type": "V100",
                "gpu_memory": "64GB",
                "cpu_cores": 24,
                "ram": "448GB",
            },
            "Standard_ND40rs_v2": {
                "gpus": 8,
                "gpu_type": "V100",
                "gpu_memory": "128GB",
                "cpu_cores": 40,
                "ram": "672GB",
            },
            "Standard_NC6s_v2": {
                "gpus": 1,
                "gpu_type": "P100",
                "gpu_memory": "16GB",
                "cpu_cores": 6,
                "ram": "112GB",
            },
        }

    def get_resource_usage(self) -> ResourceUsage:
        """Get current resource utilization for Azure VM."""
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
        """Estimate cost for Azure VM usage."""
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
                        f"Got pricing for {instance_type} from Azure API: ${hourly_rate}/hr"
                    )
                    pricing_source = "api"
            except Exception as e:
                logger.debug(f"Failed to get pricing from Azure API: {e}")

        # Handle spot pricing
        if use_spot and self.pricing_client:
            try:
                hourly_rate = self.pricing_client.get_spot_pricing(
                    instance_type, self.region
                )
                if hourly_rate:
                    pricing_source = "api"
            except Exception as e:
                logger.debug(f"Failed to get spot pricing from Azure API: {e}")

        # Fall back to hardcoded pricing if API failed
        if hourly_rate is None:
            hourly_rate = self.vm_pricing.get(instance_type, self.vm_pricing["default"])
            pricing_source = "hardcoded"

            if self.pricing_client and self.pricing_client.is_pricing_data_outdated():
                pricing_warning = (
                    f"Using potentially outdated pricing data (last updated: "
                    f"{self.pricing_client._hardcoded_pricing_date}). "
                    f"Current prices may differ. Consider checking Azure pricing page."
                )
                logger.warning(pricing_warning)

            # Apply spot discount if requested and using hardcoded pricing
            if use_spot:
                instance_family = (
                    instance_type.split("_")[1]
                    if "_" in instance_type
                    else instance_type
                )
                # Match instance family prefix
                discount_key = next(
                    (
                        k
                        for k in self.spot_discounts.keys()
                        if instance_family.startswith(k.replace("Standard_", ""))
                    ),
                    "default",
                )
                discount_factor = self.spot_discounts[discount_key]
                hourly_rate *= discount_factor
                if pricing_source == "hardcoded":
                    pricing_warning = (
                        pricing_warning or ""
                    ) + " Spot pricing is estimated and may vary significantly."

        # Calculate estimated cost
        estimated_cost = hourly_rate * hours_used

        pricing_type = "Spot" if use_spot else "Pay-as-you-go"

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
        """Get Azure VM pricing information."""
        return self.vm_pricing.copy()

    def get_spot_pricing_info(self) -> Dict[str, float]:
        """Get estimated Azure Spot VM pricing."""
        spot_pricing = {}
        for instance_type, on_demand_price in self.vm_pricing.items():
            if instance_type != "default":
                instance_family = (
                    instance_type.split("_")[1]
                    if "_" in instance_type
                    else instance_type
                )
                discount_key = next(
                    (
                        k
                        for k in self.spot_discounts.keys()
                        if instance_family.startswith(k.replace("Standard_", ""))
                    ),
                    "default",
                )
                discount_factor = self.spot_discounts[discount_key]
                spot_pricing[instance_type] = on_demand_price * discount_factor
        return spot_pricing

    def get_cost_optimization_recommendations(
        self, resource_usage: ResourceUsage, cost_estimate: CostEstimate
    ) -> List[str]:
        """Get Azure-specific cost optimization recommendations."""
        recommendations = super().get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # Azure-specific recommendations
        recommendations.extend(
            [
                "Consider using Azure Spot VMs for fault-tolerant workloads (up to 90% savings)",
                "Use Reserved VM Instances for predictable workloads (up to 72% savings)",
                "Consider Azure Batch for large-scale parallel processing",
                "Use Azure CycleCloud for HPC workloads and cluster management",
                "Enable auto-shutdown for development VMs to avoid unnecessary costs",
                "Use Azure Monitor for detailed cost tracking and alerts",
                "Consider B-series burstable VMs for variable workloads",
                "Use managed disks with appropriate performance tiers",
                "Implement lifecycle policies for blob storage to reduce storage costs",
            ]
        )

        # Instance-specific recommendations
        current_instance = cost_estimate.instance_type.split(" ")[
            0
        ]  # Remove pricing type
        if current_instance.startswith("Standard_NC") or current_instance.startswith(
            "Standard_ND"
        ):
            if resource_usage.gpu_stats:
                avg_gpu_util = sum(
                    gpu.get("utilization_percent", 0)
                    for gpu in resource_usage.gpu_stats
                ) / len(resource_usage.gpu_stats)
                if avg_gpu_util < 50:
                    recommendations.append(
                        "Low GPU utilization on expensive GPU VM. Consider Standard_D or Standard_F series VMs."
                    )

        return recommendations

    def estimate_batch_cost(
        self,
        pool_name: str,
        vm_size: str,
        target_nodes: int,
        estimated_duration_hours: float,
    ) -> Dict[str, Any]:
        """Estimate costs for Azure Batch workloads."""
        # Get VM hourly cost
        vm_hourly_cost = self.vm_pricing.get(vm_size, self.vm_pricing["default"])

        # Calculate total cost
        total_compute_hours = target_nodes * estimated_duration_hours
        estimated_cost = total_compute_hours * vm_hourly_cost

        return {
            "pool_name": pool_name,
            "vm_size": vm_size,
            "target_nodes": target_nodes,
            "estimated_duration_hours": estimated_duration_hours,
            "total_compute_hours": total_compute_hours,
            "vm_hourly_cost": vm_hourly_cost,
            "estimated_cost": estimated_cost,
            "cost_per_node_hour": vm_hourly_cost,
            "recommendations": [
                "Use Low Priority VMs in Batch pools for additional savings",
                "Implement auto-scaling to optimize node utilization",
                "Use appropriate VM sizes for different task characteristics",
                "Monitor task efficiency and optimize task packaging",
                "Consider using VMSS (Virtual Machine Scale Sets) for flexibility",
            ],
        }

    def get_region_pricing_comparison(
        self, instance_type: str
    ) -> Dict[str, Dict[str, Any]]:
        """Compare pricing across Azure regions (simplified)."""
        # Regional pricing multipliers (approximate)
        region_multipliers = {
            "East US": 1.0,  # Baseline
            "West US 2": 1.0,  # Same as East US
            "Central US": 0.95,  # Slightly cheaper
            "West Europe": 1.1,  # ~10% more expensive
            "Southeast Asia": 1.15,  # ~15% more expensive
            "Japan East": 1.2,  # ~20% more expensive
        }

        base_price = self.vm_pricing.get(instance_type, self.vm_pricing["default"])

        regional_pricing = {}
        for region, multiplier in region_multipliers.items():
            regional_pricing[region] = {
                "pay_as_you_go_hourly": base_price * multiplier,
                "estimated_spot_hourly": base_price
                * multiplier
                * 0.7,  # Rough spot estimate
                "region_name": region,
            }

        return regional_pricing

    def get_azure_specific_metrics(self) -> Dict[str, Any]:
        """Get Azure-specific cost and performance metrics."""
        return {
            "region": self.region,
            "availability_zone": "auto-detect",  # Would detect from instance metadata
            "vm_lifecycle": "pay-as-you-go",  # or 'spot' or 'reserved'
            "managed_disks": True,
            "accelerated_networking": True,
            "proximity_placement_group": None,
            "cost_optimization_score": self._calculate_cost_optimization_score(),
        }

    def get_azure_consumption_api_integration(self) -> Dict[str, Any]:
        """Framework for Azure Consumption API integration."""
        # This would integrate with Azure Consumption APIs for real billing data
        # For now, provide a framework structure

        return {
            "billing_period": datetime.now().strftime("%Y-%m"),
            "subscription_id": "auto-detect",
            "resource_group": "auto-detect",
            "cost_to_date": 0.0,  # Would fetch from API
            "forecasted_cost": 0.0,  # Would calculate based on usage
            "budget_alerts": [],  # Would fetch configured alerts
            "cost_breakdown": {
                "compute": 0.0,
                "storage": 0.0,
                "networking": 0.0,
                "other": 0.0,
            },
            "api_available": False,  # Would check API connectivity
            "last_updated": datetime.now().isoformat(),
        }

    def _calculate_cost_optimization_score(self) -> float:
        """Calculate a cost optimization score (0-100)."""
        # This would analyze various factors:
        # - Resource utilization
        # - VM size appropriateness
        # - Spot vs pay-as-you-go usage
        # - Reserved instance coverage
        # - Auto-shutdown configurations
        # For now, return a placeholder
        return 72.0
