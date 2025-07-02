"""Comprehensive tests for GCP cost provider."""

import logging
from unittest.mock import Mock, patch, MagicMock
import pytest

from clustrix.cost_providers.gcp import GCPCostMonitor
from clustrix.cost_monitoring import ResourceUsage, CostEstimate


class TestGCPCostMonitor:
    """Test GCPCostMonitor class."""

    def test_init_with_pricing_api(self):
        """Test initialization with pricing API enabled."""
        monitor = GCPCostMonitor(region="us-central1", use_pricing_api=True)

        assert monitor.region == "us-central1"
        assert monitor.use_pricing_api is True
        assert monitor.pricing_client is not None
        assert monitor.provider_name == "Google Cloud Platform"
        assert isinstance(monitor.compute_pricing, dict)
        assert "n2-standard-2" in monitor.compute_pricing

    def test_init_without_pricing_api(self):
        """Test initialization with pricing API disabled."""
        monitor = GCPCostMonitor(region="us-east1", use_pricing_api=False)

        assert monitor.region == "us-east1"
        assert monitor.use_pricing_api is False
        assert monitor.pricing_client is None

    def test_init_default_values(self):
        """Test initialization with default values."""
        monitor = GCPCostMonitor()

        assert monitor.region == "us-central1"
        assert monitor.use_pricing_api is True

    @patch("clustrix.cost_monitoring.BaseCostMonitor.get_cpu_memory_usage")
    @patch("clustrix.cost_monitoring.BaseCostMonitor.get_gpu_utilization")
    def test_get_resource_usage(self, mock_gpu, mock_cpu_mem):
        """Test getting current resource usage."""
        monitor = GCPCostMonitor()

        # Mock CPU and memory usage
        mock_cpu_mem.return_value = (65.5, 3072, 8192, 37.5)

        # Mock GPU usage
        mock_gpu.return_value = [{"utilization_percent": 75.0, "memory_used_mb": 2048}]

        usage = monitor.get_resource_usage()

        assert isinstance(usage, ResourceUsage)
        assert usage.cpu_percent == 65.5
        assert usage.memory_used_mb == 3072
        assert usage.memory_total_mb == 8192
        assert usage.memory_percent == 37.5
        assert usage.gpu_stats == [
            {"utilization_percent": 75.0, "memory_used_mb": 2048}
        ]

    def test_estimate_cost_with_api_success(self):
        """Test cost estimation with successful API call."""
        monitor = GCPCostMonitor(use_pricing_api=True)

        # Mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.return_value = 0.194
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost("n2-standard-4", 8.0)

        assert isinstance(cost_estimate, CostEstimate)
        assert cost_estimate.hourly_rate == 0.194
        assert cost_estimate.estimated_cost == 1.552  # 0.194 * 8
        assert cost_estimate.instance_type == "n2-standard-4 (On-Demand)"
        assert cost_estimate.hours_used == 8.0
        assert cost_estimate.pricing_source == "api"

        mock_pricing_client.get_instance_pricing.assert_called_once_with(
            instance_type="n2-standard-4", region="us-central1"
        )

    def test_estimate_cost_with_api_failure(self):
        """Test cost estimation with API failure fallback."""
        monitor = GCPCostMonitor(use_pricing_api=True)

        # Mock pricing client that fails
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.side_effect = Exception("API error")
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost("n2-standard-4", 5.0)

        assert cost_estimate.hourly_rate == 0.194  # fallback to hardcoded
        assert cost_estimate.estimated_cost == 0.97  # 0.194 * 5
        assert cost_estimate.pricing_source == "hardcoded"

    def test_estimate_cost_preemptible_pricing(self):
        """Test cost estimation with preemptible pricing."""
        monitor = GCPCostMonitor(use_pricing_api=True)

        # Mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.get_preemptible_pricing.return_value = (
            0.058  # ~70% discount
        )
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost(
            "n2-standard-4", 10.0, use_preemptible=True
        )

        assert cost_estimate.hourly_rate == 0.058
        assert abs(cost_estimate.estimated_cost - 0.58) < 0.001
        assert cost_estimate.pricing_source == "api"

        mock_pricing_client.get_preemptible_pricing.assert_called_once_with(
            "n2-standard-4", "us-central1"
        )

    def test_estimate_cost_preemptible_pricing_failure(self):
        """Test preemptible pricing with API failure."""
        monitor = GCPCostMonitor(use_pricing_api=True)

        # Mock pricing client that fails
        mock_pricing_client = Mock()
        mock_pricing_client.get_preemptible_pricing.side_effect = Exception(
            "Preemptible API error"
        )
        monitor.pricing_client = mock_pricing_client

        cost_estimate = monitor.estimate_cost(
            "n2-standard-4", 5.0, use_preemptible=True
        )

        # Should fall back to preemptible calculation
        expected_preemptible_rate = 0.194 * 0.2  # 80% discount
        assert abs(cost_estimate.hourly_rate - expected_preemptible_rate) < 0.001

    def test_estimate_cost_unknown_instance(self):
        """Test cost estimation for unknown instance type."""
        monitor = GCPCostMonitor(use_pricing_api=False)

        cost_estimate = monitor.estimate_cost("unknown-instance-type", 6.0)

        assert cost_estimate.hourly_rate == 0.10  # default price
        assert abs(cost_estimate.estimated_cost - 0.6) < 0.001

    def test_estimate_cost_without_pricing_client(self):
        """Test cost estimation without pricing client."""
        monitor = GCPCostMonitor(use_pricing_api=False)

        cost_estimate = monitor.estimate_cost("n2-standard-2", 4.0)

        assert cost_estimate.hourly_rate == 0.097
        assert cost_estimate.estimated_cost == 0.388
        assert cost_estimate.pricing_source == "hardcoded"

    def test_estimate_cost_with_sustained_use_discount(self):
        """Test cost estimation with sustained use discount."""
        monitor = GCPCostMonitor(use_pricing_api=True)

        # Mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.return_value = 0.194
        monitor.pricing_client = mock_pricing_client

        # 100% usage should get 30% discount (1 - 0.3 = 0.7 multiplier)
        cost_estimate = monitor.estimate_cost(
            "n2-standard-4", 720.0, sustained_use_percent=100
        )

        # Hourly rate should be discounted: 0.194 * 0.7 = 0.1358
        expected_hourly_rate = 0.194 * 0.7
        assert abs(cost_estimate.hourly_rate - expected_hourly_rate) < 0.001

        # Total cost: discounted_rate * hours
        expected_cost = expected_hourly_rate * 720.0
        assert abs(cost_estimate.estimated_cost - expected_cost) < 0.1

    def test_get_pricing_info(self):
        """Test getting pricing information."""
        monitor = GCPCostMonitor()

        pricing_info = monitor.get_pricing_info()

        assert isinstance(pricing_info, dict)
        assert "n2-standard-2" in pricing_info
        assert "n2-standard-4" in pricing_info
        assert pricing_info["n2-standard-2"] == 0.097
        assert pricing_info["n2-standard-4"] == 0.194

    def test_get_preemptible_pricing_info(self):
        """Test getting preemptible pricing information."""
        monitor = GCPCostMonitor()

        preemptible_pricing = monitor.get_preemptible_pricing_info()

        assert isinstance(preemptible_pricing, dict)
        assert "n2-standard-2" in preemptible_pricing
        assert "n2-standard-4" in preemptible_pricing

        # Check that preemptible pricing is discounted (80% off)
        on_demand_price = monitor.compute_pricing["n2-standard-2"]
        preemptible_price = preemptible_pricing["n2-standard-2"]
        expected_preemptible_price = on_demand_price * 0.2

        assert abs(preemptible_price - expected_preemptible_price) < 0.001

    def test_get_cost_optimization_recommendations_basic(self):
        """Test basic cost optimization recommendations."""
        monitor = GCPCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=55.0,
            memory_used_mb=2500,
            memory_total_mb=8192,
            memory_percent=30.5,
            gpu_stats=None,
        )

        cost_estimate = CostEstimate(
            hourly_rate=0.194,
            estimated_cost=1.552,
            hours_used=8.0,
            instance_type="n2-standard-4",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        assert isinstance(recommendations, list)
        assert len(recommendations) > 0

        # Check for GCP-specific recommendations
        gcp_recommendations = [
            r
            for r in recommendations
            if any(
                keyword in r
                for keyword in ["GCP", "Google", "Preemptible", "Committed"]
            )
        ]
        assert len(gcp_recommendations) > 0

    def test_get_cost_optimization_recommendations_gpu_low_usage(self):
        """Test recommendations for GPU instances with low utilization."""
        monitor = GCPCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=70.0,
            memory_used_mb=6000,
            memory_total_mb=16384,
            memory_percent=36.6,
            gpu_stats=[
                {"utilization_percent": 20.0, "memory_used_mb": 1500},
                {"utilization_percent": 35.0, "memory_used_mb": 2000},
            ],
        )

        cost_estimate = CostEstimate(
            hourly_rate=2.48,
            estimated_cost=19.84,
            hours_used=8.0,
            instance_type="n1-standard-4-k80",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # Should include GPU utilization warning
        gpu_recommendations = [r for r in recommendations if "GPU utilization" in r]
        assert len(gpu_recommendations) > 0

    def test_get_cost_optimization_recommendations_high_memory_usage(self):
        """Test recommendations for high memory usage."""
        monitor = GCPCostMonitor()

        resource_usage = ResourceUsage(
            cpu_percent=45.0,
            memory_used_mb=7500,
            memory_total_mb=8192,
            memory_percent=91.6,
            gpu_stats=None,
        )

        cost_estimate = CostEstimate(
            hourly_rate=0.194,
            estimated_cost=1.552,
            hours_used=8.0,
            instance_type="n2-standard-4",
            pricing_source="api",
        )

        recommendations = monitor.get_cost_optimization_recommendations(
            resource_usage, cost_estimate
        )

        # Should include memory optimization recommendation
        memory_recommendations = [
            r
            for r in recommendations
            if any(keyword in r.lower() for keyword in ["memory", "highmem"])
        ]
        assert len(memory_recommendations) > 0

    def test_preemptible_discounts_structure(self):
        """Test preemptible discount structure."""
        monitor = GCPCostMonitor()

        assert hasattr(monitor, "preemptible_discount")
        assert isinstance(monitor.preemptible_discount, (int, float))
        assert 0 < monitor.preemptible_discount < 1  # Should be a discount factor

    def test_compute_pricing_structure(self):
        """Test compute pricing structure."""
        monitor = GCPCostMonitor()

        assert isinstance(monitor.compute_pricing, dict)
        assert len(monitor.compute_pricing) > 10  # Should have many instance types

        # Check some expected instance types
        expected_types = ["n2-standard-2", "n2-standard-4", "c2-standard-4", "default"]
        for instance_type in expected_types:
            assert instance_type in monitor.compute_pricing
            assert isinstance(monitor.compute_pricing[instance_type], (int, float))
            assert monitor.compute_pricing[instance_type] > 0
