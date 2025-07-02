"""Comprehensive tests for Azure cost provider."""

import logging
from unittest.mock import Mock, patch, MagicMock
import pytest

from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_monitoring import ResourceUsage, CostEstimate


class TestAzureCostMonitor:
    """Test AzureCostMonitor class."""

    def test_init_with_pricing_api(self):
        """Test initialization with pricing API enabled."""
        monitor = AzureCostMonitor(region="eastus", use_pricing_api=True)

        assert monitor.region == "eastus"
        assert monitor.use_pricing_api is True
        assert monitor.pricing_client is not None
        assert monitor.provider_name == "Azure"
        assert isinstance(monitor.vm_pricing, dict)
        assert "Standard_B1s" in monitor.vm_pricing

    def test_init_without_pricing_api(self):
        """Test initialization with pricing API disabled."""
        monitor = AzureCostMonitor(region="westus", use_pricing_api=False)

        assert monitor.region == "westus"
        assert monitor.use_pricing_api is False
        assert monitor.pricing_client is None

    def test_init_default_values(self):
        """Test initialization with default values."""
        monitor = AzureCostMonitor()

        assert monitor.region == "eastus"
        assert monitor.use_pricing_api is True

    @patch("clustrix.cost_monitoring.BaseCostMonitor.get_cpu_memory_usage")
    @patch("clustrix.cost_monitoring.BaseCostMonitor.get_gpu_utilization")
    def test_get_resource_usage(self, mock_gpu, mock_cpu_mem):
        """Test getting current resource usage."""
        monitor = AzureCostMonitor()

        # Mock CPU and memory usage
        mock_cpu_mem.return_value = (75.5, 2048, 4096, 50.0)

        # Mock GPU usage
        mock_gpu.return_value = [{"utilization_percent": 80.0, "memory_used_mb": 1024}]

        usage = monitor.get_resource_usage()

        assert isinstance(usage, ResourceUsage)
        assert usage.cpu_percent == 75.5
        assert usage.memory_used_mb == 2048
        assert usage.memory_total_mb == 4096
        assert usage.memory_percent == 50.0
        assert usage.gpu_stats == [
            {"utilization_percent": 80.0, "memory_used_mb": 1024}
        ]

    def test_estimate_cost_with_api_success(self):
        """Test cost estimation with successful API call."""
        monitor = AzureCostMonitor(use_pricing_api=True)

        # Mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.return_value = 0.192
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost("Standard_D4s_v3", 10.0)

        assert isinstance(cost_estimate, CostEstimate)
        assert cost_estimate.hourly_rate == 0.192
        assert cost_estimate.estimated_cost == 1.92  # 0.192 * 10
        assert cost_estimate.instance_type == "Standard_D4s_v3 (Pay-as-you-go)"
        assert cost_estimate.hours_used == 10.0
        assert cost_estimate.pricing_source == "api"

        mock_pricing_client.get_instance_pricing.assert_called_once_with(
            instance_type="Standard_D4s_v3", region="eastus"
        )

    def test_estimate_cost_with_api_failure(self):
        """Test cost estimation with API failure fallback."""
        monitor = AzureCostMonitor(use_pricing_api=True)

        # Mock pricing client that fails
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.side_effect = Exception("API error")
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost("Standard_D4s_v3", 5.0)

        assert cost_estimate.hourly_rate == 0.192  # fallback to hardcoded
        assert cost_estimate.estimated_cost == 0.96  # 0.192 * 5
        assert cost_estimate.pricing_source == "hardcoded"
        assert cost_estimate.instance_type == "Standard_D4s_v3 (Pay-as-you-go)"

    def test_estimate_cost_spot_pricing(self):
        """Test cost estimation with spot pricing."""
        monitor = AzureCostMonitor(use_pricing_api=True)

        # Mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.get_spot_pricing.return_value = 0.038  # ~80% discount
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost("Standard_D4s_v3", 10.0, use_spot=True)

        assert cost_estimate.hourly_rate == 0.038
        assert cost_estimate.estimated_cost == 0.38
        assert cost_estimate.pricing_source == "api"
        assert cost_estimate.instance_type == "Standard_D4s_v3 (Spot)"

        mock_pricing_client.get_spot_pricing.assert_called_once_with(
            "Standard_D4s_v3", "eastus"
        )

    def test_estimate_cost_spot_pricing_failure(self):
        """Test spot pricing with API failure."""
        monitor = AzureCostMonitor(use_pricing_api=True)

        # Mock pricing client that fails
        mock_pricing_client = Mock()
        mock_pricing_client.get_spot_pricing.side_effect = Exception("Spot API error")
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost("Standard_D4s_v3", 5.0, use_spot=True)

        # Should fall back to spot calculation
        expected_spot_rate = 0.192 * 0.7  # 30% discount (D-series)
        assert abs(cost_estimate.hourly_rate - expected_spot_rate) < 0.001
        assert cost_estimate.instance_type == "Standard_D4s_v3 (Spot)"

    def test_estimate_cost_unknown_instance(self):
        """Test cost estimation for unknown instance type."""
        monitor = AzureCostMonitor(use_pricing_api=False)

        cost_estimate = monitor.estimate_cost("Unknown_Instance", 8.0)

        assert cost_estimate.hourly_rate == 0.10  # default price
        assert cost_estimate.estimated_cost == 0.8
        assert cost_estimate.instance_type == "Unknown_Instance (Pay-as-you-go)"

    def test_estimate_cost_without_pricing_client(self):
        """Test cost estimation without pricing client."""
        monitor = AzureCostMonitor(use_pricing_api=False)

        cost_estimate = monitor.estimate_cost("Standard_B2s", 6.0)

        assert cost_estimate.hourly_rate == 0.0416
        assert cost_estimate.estimated_cost == 0.2496
        assert cost_estimate.pricing_source == "hardcoded"
        assert cost_estimate.instance_type == "Standard_B2s (Pay-as-you-go)"

    def test_get_pricing_info(self):
        """Test getting pricing information."""
        monitor = AzureCostMonitor()

        pricing_info = monitor.get_pricing_info()

        assert isinstance(pricing_info, dict)
        assert "Standard_B1s" in pricing_info
        assert "Standard_D4s_v3" in pricing_info
        assert pricing_info["Standard_B1s"] == 0.0104
        assert pricing_info["Standard_D4s_v3"] == 0.192

    def test_get_spot_pricing_info(self):
        """Test getting spot pricing information."""
        monitor = AzureCostMonitor()

        spot_pricing = monitor.get_spot_pricing_info()

        assert isinstance(spot_pricing, dict)
        assert "Standard_B1s" in spot_pricing
        assert "Standard_D4s_v3" in spot_pricing

        # Check that spot pricing is discounted
        on_demand_price = monitor.vm_pricing["Standard_B1s"]
        spot_price = spot_pricing["Standard_B1s"]
        assert spot_price < on_demand_price

        # B-series should have 20% discount (0.8 factor)
        expected_spot_price = on_demand_price * 0.8
        assert abs(spot_price - expected_spot_price) < 0.0001

    def test_get_spot_pricing_info_nc_series(self):
        """Test spot pricing for NC-series (GPU) instances."""
        monitor = AzureCostMonitor()

        # Add NC series to pricing for testing
        monitor.vm_pricing["Standard_NC6s_v3"] = 3.06

        spot_pricing = monitor.get_spot_pricing_info()

        # NC series should have 70% discount (0.3 factor)
        expected_spot_price = 3.06 * 0.3
        assert abs(spot_pricing["Standard_NC6s_v3"] - expected_spot_price) < 0.01

    def test_get_cost_optimization_recommendations_basic(self):
        """Test basic cost optimization recommendations."""
        monitor = AzureCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=45.0,
            memory_used_mb=1500,
            memory_total_mb=4096,
            memory_percent=36.6,
            gpu_stats=None,
        )

        cost_estimate = CostEstimate(
            hourly_rate=0.192,
            estimated_cost=1.92,
            hours_used=10.0,
            instance_type="Standard_D4s_v3",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        assert isinstance(recommendations, list)
        assert len(recommendations) > 0

        # Check for Azure-specific recommendations
        azure_recommendations = [r for r in recommendations if "Azure" in r]
        assert len(azure_recommendations) > 0

        # Should include spot VM recommendation
        spot_recommendations = [r for r in recommendations if "Spot" in r]
        assert len(spot_recommendations) > 0

    def test_get_cost_optimization_recommendations_gpu_low_usage(self):
        """Test recommendations for GPU instances with low utilization."""
        monitor = AzureCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=60.0,
            memory_used_mb=8000,
            memory_total_mb=16384,
            memory_percent=48.8,
            gpu_stats=[
                {"utilization_percent": 25.0, "memory_used_mb": 2000},
                {"utilization_percent": 30.0, "memory_used_mb": 2500},
            ],
        )

        cost_estimate = CostEstimate(
            hourly_rate=3.06,
            estimated_cost=30.6,
            hours_used=10.0,
            instance_type="Standard_NC6s_v3",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # Should include GPU utilization warning
        gpu_recommendations = [r for r in recommendations if "GPU utilization" in r]
        assert len(gpu_recommendations) > 0

    def test_get_cost_optimization_recommendations_gpu_high_usage(self):
        """Test recommendations for GPU instances with high utilization."""
        monitor = AzureCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=85.0,
            memory_used_mb=12000,
            memory_total_mb=16384,
            memory_percent=73.2,
            gpu_stats=[
                {"utilization_percent": 85.0, "memory_used_mb": 7000},
                {"utilization_percent": 90.0, "memory_used_mb": 7500},
            ],
        )

        cost_estimate = CostEstimate(
            hourly_rate=3.06,
            estimated_cost=30.6,
            hours_used=10.0,
            instance_type="Standard_NC6s_v3",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # Should NOT include GPU utilization warning for high usage
        gpu_recommendations = [r for r in recommendations if "Low GPU utilization" in r]
        assert len(gpu_recommendations) == 0

    def test_get_cost_optimization_recommendations_nd_series(self):
        """Test recommendations for ND-series instances."""
        monitor = AzureCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=60.0,
            memory_used_mb=8000,
            memory_total_mb=16384,
            memory_percent=48.8,
            gpu_stats=[{"utilization_percent": 40.0, "memory_used_mb": 4000}],
        )

        cost_estimate = CostEstimate(
            hourly_rate=6.12,
            estimated_cost=61.2,
            hours_used=10.0,
            instance_type="Standard_ND6s",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # Should include GPU utilization warning for ND series too
        gpu_recommendations = [r for r in recommendations if "GPU utilization" in r]
        assert len(gpu_recommendations) > 0

    def test_estimate_batch_cost(self):
        """Test Azure Batch cost estimation."""
        monitor = AzureCostMonitor()

        batch_estimate = monitor.estimate_batch_cost(
            pool_name="test-pool",
            vm_size="Standard_D4s_v3",
            target_nodes=5,
            estimated_duration_hours=2.0,
        )

        assert isinstance(batch_estimate, dict)
        assert "estimated_cost" in batch_estimate
        assert "total_compute_hours" in batch_estimate
        assert "vm_size" in batch_estimate
        assert "target_nodes" in batch_estimate
        assert "estimated_duration_hours" in batch_estimate
        assert "vm_hourly_cost" in batch_estimate
        assert "cost_per_node_hour" in batch_estimate

        # Check calculations
        vm_hourly_cost = 0.192  # Standard_D4s_v3
        total_compute_hours = 5 * 2.0  # 5 nodes * 2 hours
        total_compute_cost = total_compute_hours * vm_hourly_cost

        assert batch_estimate["total_compute_hours"] == total_compute_hours
        assert batch_estimate["estimated_cost"] == total_compute_cost
        assert batch_estimate["vm_size"] == "Standard_D4s_v3"
        assert batch_estimate["target_nodes"] == 5
        assert batch_estimate["estimated_duration_hours"] == 2.0

    def test_estimate_batch_cost_unknown_vm_size(self):
        """Test Batch cost estimation with unknown VM size."""
        monitor = AzureCostMonitor()

        batch_estimate = monitor.estimate_batch_cost(
            pool_name="test-pool",
            vm_size="Unknown_VM_Size",
            target_nodes=3,
            estimated_duration_hours=4.0,
        )

        # Should use default pricing
        vm_hourly_cost = 0.10  # default
        total_compute_hours = 3 * 4.0  # 3 nodes * 4 hours
        total_compute_cost = total_compute_hours * vm_hourly_cost

        assert batch_estimate["total_compute_hours"] == total_compute_hours
        assert batch_estimate["estimated_cost"] == total_compute_cost

    def test_spot_discounts_structure(self):
        """Test spot discount structure."""
        monitor = AzureCostMonitor()

        assert hasattr(monitor, "spot_discounts")
        assert isinstance(monitor.spot_discounts, dict)
        assert "Standard_B" in monitor.spot_discounts
        assert "Standard_D" in monitor.spot_discounts
        assert "Standard_F" in monitor.spot_discounts
        assert "Standard_NC" in monitor.spot_discounts
        assert "default" in monitor.spot_discounts

        # Check that all discounts are between 0 and 1
        for discount in monitor.spot_discounts.values():
            assert 0 < discount < 1

    def test_vm_pricing_structure(self):
        """Test VM pricing structure."""
        monitor = AzureCostMonitor()

        assert isinstance(monitor.vm_pricing, dict)
        assert len(monitor.vm_pricing) > 10  # Should have many instance types

        # Check some expected instance types
        expected_types = [
            "Standard_B1s",
            "Standard_B2s",
            "Standard_D2s_v3",
            "Standard_F2s_v2",
            "default",
        ]
        for instance_type in expected_types:
            assert instance_type in monitor.vm_pricing
            assert isinstance(monitor.vm_pricing[instance_type], (int, float))
            assert monitor.vm_pricing[instance_type] > 0
