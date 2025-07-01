"""
Unit tests for cost monitoring functionality.
"""

import pytest
import unittest.mock as mock
from datetime import datetime

from clustrix.cost_monitoring import (
    ResourceUsage,
    CostEstimate,
    CostReport,
    cost_tracking_decorator,
    get_cost_monitor,
    start_cost_monitoring,
    generate_cost_report,
    get_pricing_info,
)
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor


class TestResourceUsage:
    """Test ResourceUsage dataclass."""

    def test_resource_usage_creation(self):
        """Test creating ResourceUsage object."""
        usage = ResourceUsage(
            cpu_percent=75.5,
            memory_used_mb=8192,
            memory_total_mb=16384,
            memory_percent=50.0,
        )

        assert usage.cpu_percent == 75.5
        assert usage.memory_used_mb == 8192
        assert usage.memory_total_mb == 16384
        assert usage.memory_percent == 50.0
        assert usage.gpu_stats is None

    def test_resource_usage_with_gpu(self):
        """Test ResourceUsage with GPU stats."""
        gpu_stats = [
            {
                "gpu_id": 0,
                "utilization_percent": 85,
                "memory_used_mb": 15000,
                "memory_total_mb": 16000,
            }
        ]

        usage = ResourceUsage(
            cpu_percent=50.0,
            memory_used_mb=4096,
            memory_total_mb=8192,
            memory_percent=50.0,
            gpu_stats=gpu_stats,
        )

        assert len(usage.gpu_stats) == 1
        assert usage.gpu_stats[0]["utilization_percent"] == 85


class TestCostEstimate:
    """Test CostEstimate dataclass."""

    def test_cost_estimate_creation(self):
        """Test creating CostEstimate object."""
        estimate = CostEstimate(
            instance_type="a100_40gb",
            hourly_rate=1.10,
            hours_used=2.5,
            estimated_cost=2.75,
        )

        assert estimate.instance_type == "a100_40gb"
        assert estimate.hourly_rate == 1.10
        assert estimate.hours_used == 2.5
        assert estimate.estimated_cost == 2.75
        assert estimate.currency == "USD"


class TestLambdaCostMonitor:
    """Test Lambda Cloud cost monitoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.monitor = LambdaCostMonitor()

    def test_initialization(self):
        """Test monitor initialization."""
        assert self.monitor.provider_name == "Lambda Cloud"
        assert "a100_40gb" in self.monitor.pricing
        assert self.monitor.pricing["a100_40gb"] == 1.10

    def test_estimate_cost(self):
        """Test cost estimation."""
        estimate = self.monitor.estimate_cost("a100_40gb", 2.0)

        assert estimate.instance_type == "a100_40gb"
        assert estimate.hourly_rate == 1.10
        assert estimate.hours_used == 2.0
        assert estimate.estimated_cost == 2.20

    def test_estimate_cost_unknown_instance(self):
        """Test cost estimation for unknown instance type."""
        estimate = self.monitor.estimate_cost("unknown_instance", 1.0)

        assert estimate.instance_type == "unknown_instance"
        assert estimate.hourly_rate == 1.00  # default rate
        assert estimate.estimated_cost == 1.00

    def test_get_pricing_info(self):
        """Test getting pricing information."""
        pricing = self.monitor.get_pricing_info()

        assert isinstance(pricing, dict)
        assert "a100_40gb" in pricing
        assert pricing["a100_40gb"] == 1.10

    @mock.patch("clustrix.cost_monitoring.BaseCostMonitor.get_cpu_memory_usage")
    @mock.patch("clustrix.cost_monitoring.BaseCostMonitor.get_gpu_utilization")
    def test_get_resource_usage(self, mock_gpu, mock_cpu):
        """Test getting resource usage."""
        # Mock CPU/memory usage
        mock_cpu.return_value = (75.0, 8192, 16384, 50.0)

        # Mock GPU usage
        mock_gpu.return_value = [
            {
                "gpu_id": 0,
                "utilization_percent": 85,
                "memory_used_mb": 15000,
                "memory_total_mb": 16000,
            }
        ]

        usage = self.monitor.get_resource_usage()

        assert usage.cpu_percent == 75.0
        assert usage.memory_used_mb == 8192
        assert len(usage.gpu_stats) == 1
        assert usage.gpu_stats[0]["utilization_percent"] == 85

    def test_get_instance_recommendations(self):
        """Test getting instance recommendations."""
        # Test with low GPU utilization
        usage = ResourceUsage(
            cpu_percent=50.0,
            memory_used_mb=4096,
            memory_total_mb=8192,
            memory_percent=50.0,
            gpu_stats=[
                {
                    "gpu_id": 0,
                    "utilization_percent": 25,
                    "memory_utilization_percent": 20,
                }
            ],
        )

        recommendations = self.monitor.get_instance_recommendations(usage)

        assert len(recommendations) > 0
        assert any("Low GPU utilization" in rec for rec in recommendations)

    def test_estimate_monthly_cost(self):
        """Test monthly cost estimation."""
        monthly_cost = self.monitor.estimate_monthly_cost("a100_40gb", 8.0)

        assert monthly_cost["instance_type"] == "a100_40gb"
        assert monthly_cost["hourly_rate"] == 1.10
        assert monthly_cost["daily_cost_8h"] == 8.8  # 1.10 * 8
        assert monthly_cost["weekly_cost_40h"] == 44.0  # 1.10 * 40


class TestAWSCostMonitor:
    """Test AWS cost monitoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.monitor = AWSCostMonitor()

    def test_initialization(self):
        """Test monitor initialization."""
        assert self.monitor.provider_name == "AWS"
        assert self.monitor.region == "us-east-1"
        assert "p3.2xlarge" in self.monitor.ec2_pricing

    def test_estimate_cost_on_demand(self):
        """Test on-demand cost estimation."""
        estimate = self.monitor.estimate_cost("p3.2xlarge", 2.0, use_spot=False)

        assert "p3.2xlarge (On-Demand)" in estimate.instance_type
        assert estimate.hourly_rate == 3.06
        assert estimate.estimated_cost == 6.12

    def test_estimate_cost_spot(self):
        """Test spot instance cost estimation."""
        estimate = self.monitor.estimate_cost("p3.2xlarge", 2.0, use_spot=True)

        assert "p3.2xlarge (Spot)" in estimate.instance_type
        assert estimate.hourly_rate < 3.06  # Should be discounted
        expected_rate = 3.06 * 0.3  # p3 spot discount
        assert abs(estimate.hourly_rate - expected_rate) < 0.01

    def test_get_spot_pricing_info(self):
        """Test getting spot pricing information."""
        spot_pricing = self.monitor.get_spot_pricing_info()

        assert isinstance(spot_pricing, dict)
        assert "p3.2xlarge" in spot_pricing
        assert spot_pricing["p3.2xlarge"] < self.monitor.ec2_pricing["p3.2xlarge"]

    def test_estimate_batch_cost(self):
        """Test AWS Batch cost estimation."""
        batch_cost = self.monitor.estimate_batch_cost(
            job_queue="test-queue",
            compute_environment="test-env",
            estimated_jobs=10,
            avg_job_duration_hours=0.5,
        )

        assert batch_cost["estimated_jobs"] == 10
        assert batch_cost["avg_job_duration_hours"] == 0.5
        assert batch_cost["total_compute_hours"] == 5.0
        assert "recommendations" in batch_cost

    def test_get_region_pricing_comparison(self):
        """Test regional pricing comparison."""
        regional_pricing = self.monitor.get_region_pricing_comparison("p3.2xlarge")

        assert isinstance(regional_pricing, dict)
        assert "us-east-1" in regional_pricing
        assert "eu-west-1" in regional_pricing
        assert (
            regional_pricing["eu-west-1"]["on_demand_hourly"]
            > regional_pricing["us-east-1"]["on_demand_hourly"]
        )


class TestAzureCostMonitor:
    """Test Azure cost monitoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.monitor = AzureCostMonitor()

    def test_initialization(self):
        """Test monitor initialization."""
        assert self.monitor.provider_name == "Azure"
        assert self.monitor.region == "eastus"
        assert "Standard_NC6s_v3" in self.monitor.vm_pricing

    def test_estimate_cost_pay_as_you_go(self):
        """Test pay-as-you-go cost estimation."""
        estimate = self.monitor.estimate_cost("Standard_NC6s_v3", 2.0, use_spot=False)

        assert "Standard_NC6s_v3 (Pay-as-you-go)" in estimate.instance_type
        assert estimate.hourly_rate == 3.06
        assert estimate.estimated_cost == 6.12

    def test_estimate_cost_spot(self):
        """Test spot VM cost estimation."""
        estimate = self.monitor.estimate_cost("Standard_NC6s_v3", 2.0, use_spot=True)

        assert "Standard_NC6s_v3 (Spot)" in estimate.instance_type
        assert estimate.hourly_rate < 3.06  # Should be discounted

    def test_estimate_batch_cost(self):
        """Test Azure Batch cost estimation."""
        batch_cost = self.monitor.estimate_batch_cost(
            pool_name="test-pool",
            vm_size="Standard_D4s_v3",
            target_nodes=5,
            estimated_duration_hours=2.0,
        )

        assert batch_cost["target_nodes"] == 5
        assert batch_cost["estimated_duration_hours"] == 2.0
        assert batch_cost["total_compute_hours"] == 10.0
        assert "recommendations" in batch_cost


class TestGCPCostMonitor:
    """Test GCP cost monitoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.monitor = GCPCostMonitor(use_pricing_api=False)

    def test_initialization(self):
        """Test monitor initialization."""
        assert self.monitor.provider_name == "Google Cloud Platform"
        assert self.monitor.region == "us-central1"
        assert "a2-highgpu-1g" in self.monitor.compute_pricing

    def test_estimate_cost_on_demand(self):
        """Test on-demand cost estimation."""
        estimate = self.monitor.estimate_cost(
            "a2-highgpu-1g", 2.0, use_preemptible=False
        )

        assert "a2-highgpu-1g (On-Demand)" in estimate.instance_type
        assert estimate.hourly_rate == 3.673
        assert estimate.estimated_cost == 7.346

    def test_estimate_cost_preemptible(self):
        """Test preemptible instance cost estimation."""
        estimate = self.monitor.estimate_cost(
            "a2-highgpu-1g", 2.0, use_preemptible=True
        )

        assert "a2-highgpu-1g (Preemptible)" in estimate.instance_type
        assert estimate.hourly_rate < 3.673  # Should be discounted
        expected_rate = 3.673 * 0.2  # Preemptible discount
        assert abs(estimate.hourly_rate - expected_rate) < 0.01

    def test_estimate_cost_with_sustained_use_discount(self):
        """Test sustained use discount calculation."""
        estimate = self.monitor.estimate_cost(
            "n2-standard-4", 2.0, sustained_use_percent=80
        )

        # Should have sustained use discount applied
        base_rate = self.monitor.compute_pricing["n2-standard-4"]
        expected_rate = base_rate * 0.7  # 30% discount for 75-100% usage
        assert abs(estimate.hourly_rate - expected_rate) < 0.01

    def test_estimate_sustained_use_discount(self):
        """Test sustained use discount calculation."""
        discount_info = self.monitor.estimate_sustained_use_discount(
            600
        )  # 600 hours per month

        assert discount_info["usage_percentage"] > 75
        assert discount_info["discount_percentage"] == 30
        assert discount_info["discount_tier"] == "75-100%"

    def test_get_preemptible_pricing_info(self):
        """Test getting preemptible pricing."""
        preemptible_pricing = self.monitor.get_preemptible_pricing_info()

        assert isinstance(preemptible_pricing, dict)
        assert "a2-highgpu-1g" in preemptible_pricing
        assert (
            preemptible_pricing["a2-highgpu-1g"]
            < self.monitor.compute_pricing["a2-highgpu-1g"]
        )


class TestCostTrackingDecorator:
    """Test cost tracking decorator functionality."""

    @mock.patch("clustrix.cost_monitoring.get_cost_monitor")
    def test_cost_tracking_decorator_success(self, mock_get_monitor):
        """Test cost tracking decorator with successful function execution."""
        # Mock the monitor
        mock_monitor = mock.Mock()
        mock_monitor.start_monitoring.return_value = None
        mock_monitor.stop_monitoring.return_value = CostReport(
            timestamp=datetime.now(),
            duration_seconds=1.5,
            resource_usage=ResourceUsage(50.0, 4096, 8192, 50.0),
            cost_estimate=CostEstimate("test", 1.0, 1.5, 1.5),
            provider="test",
        )
        mock_get_monitor.return_value = mock_monitor

        @cost_tracking_decorator("lambda", "a100_40gb")
        def test_function():
            return "success"

        result = test_function()

        assert result["success"] is True
        assert result["result"] == "success"
        assert result["provider"] == "lambda"
        assert result["instance_type"] == "a100_40gb"
        assert result["cost_report"] is not None

        mock_monitor.start_monitoring.assert_called_once()
        mock_monitor.stop_monitoring.assert_called_once()

    @mock.patch("clustrix.cost_monitoring.get_cost_monitor")
    def test_cost_tracking_decorator_failure(self, mock_get_monitor):
        """Test cost tracking decorator with function failure."""
        # Mock the monitor
        mock_monitor = mock.Mock()
        mock_monitor.start_monitoring.return_value = None
        mock_monitor.stop_monitoring.return_value = CostReport(
            timestamp=datetime.now(),
            duration_seconds=0.5,
            resource_usage=ResourceUsage(50.0, 4096, 8192, 50.0),
            cost_estimate=CostEstimate("test", 1.0, 0.5, 0.5),
            provider="test",
        )
        mock_get_monitor.return_value = mock_monitor

        @cost_tracking_decorator("lambda", "a100_40gb")
        def failing_function():
            raise ValueError("Test error")

        result = failing_function()

        assert result["success"] is False
        assert result["result"] is None
        assert "Test error" in result["error"]
        assert result["cost_report"] is not None

    @mock.patch("clustrix.cost_monitoring.get_cost_monitor")
    def test_cost_tracking_decorator_no_monitor(self, mock_get_monitor):
        """Test cost tracking decorator when monitor is not available."""
        mock_get_monitor.return_value = None

        @cost_tracking_decorator("unsupported", "instance")
        def test_function():
            return "success"

        result = test_function()

        # Should execute function normally without cost tracking
        assert result == "success"


class TestCostMonitoringUtilities:
    """Test utility functions."""

    def test_get_cost_monitor_lambda(self):
        """Test getting Lambda cost monitor."""
        monitor = get_cost_monitor("lambda")
        assert isinstance(monitor, LambdaCostMonitor)

    def test_get_cost_monitor_aws(self):
        """Test getting AWS cost monitor."""
        monitor = get_cost_monitor("aws")
        assert isinstance(monitor, AWSCostMonitor)

    def test_get_cost_monitor_azure(self):
        """Test getting Azure cost monitor."""
        monitor = get_cost_monitor("azure")
        assert isinstance(monitor, AzureCostMonitor)

    def test_get_cost_monitor_gcp(self):
        """Test getting GCP cost monitor."""
        monitor = get_cost_monitor("gcp")
        assert isinstance(monitor, GCPCostMonitor)

    def test_get_cost_monitor_unsupported(self):
        """Test getting unsupported cost monitor."""
        monitor = get_cost_monitor("unsupported")
        assert monitor is None

    def test_start_cost_monitoring(self):
        """Test starting cost monitoring."""
        monitor = start_cost_monitoring("lambda")
        assert isinstance(monitor, LambdaCostMonitor)
        assert monitor.start_time is not None

    @mock.patch("clustrix.cost_monitoring.get_cost_monitor")
    def test_generate_cost_report(self, mock_get_monitor):
        """Test generating cost report."""
        # Mock the monitor
        mock_monitor = mock.Mock()
        mock_monitor.get_resource_usage.return_value = ResourceUsage(
            50.0, 4096, 8192, 50.0
        )
        mock_monitor.estimate_cost.return_value = CostEstimate("test", 1.0, 1.0, 1.0)
        mock_monitor.get_cost_optimization_recommendations.return_value = [
            "Test recommendation"
        ]
        mock_get_monitor.return_value = mock_monitor

        report = generate_cost_report("lambda", "a100_40gb")

        assert report is not None
        assert report["provider"] == "lambda"
        assert "resource_usage" in report
        assert "cost_estimate" in report
        assert "recommendations" in report

    def test_get_pricing_info(self):
        """Test getting pricing information."""
        pricing = get_pricing_info("lambda")

        assert isinstance(pricing, dict)
        assert "a100_40gb" in pricing
        assert pricing["a100_40gb"] == 1.10


if __name__ == "__main__":
    pytest.main([__file__])
