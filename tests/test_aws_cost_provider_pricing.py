"""Tests for AWS cost provider pricing functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_monitoring import CostEstimate


class TestAWSCostMonitorPricing:
    """Test AWS cost monitor pricing functionality."""

    def test_init_with_pricing_api(self):
        """Test initialization with pricing API enabled."""
        monitor = AWSCostMonitor(region="us-west-2", use_pricing_api=True)

        assert monitor.region == "us-west-2"
        assert monitor.use_pricing_api is True
        assert monitor.pricing_client is not None

    def test_init_without_pricing_api(self):
        """Test initialization with pricing API disabled."""
        monitor = AWSCostMonitor(region="eu-west-1", use_pricing_api=False)

        assert monitor.region == "eu-west-1"
        assert monitor.use_pricing_api is False
        assert monitor.pricing_client is None

    @patch("clustrix.cost_providers.aws.AWSPricingClient")
    def test_estimate_cost_with_api_success(self, mock_pricing_client_class):
        """Test cost estimation with successful API call."""
        # Set up mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.return_value = 0.0150
        mock_pricing_client.is_pricing_data_outdated.return_value = False
        mock_pricing_client_class.return_value = mock_pricing_client

        monitor = AWSCostMonitor(region="us-east-1", use_pricing_api=True)

        # Estimate cost
        estimate = monitor.estimate_cost("t3.micro", hours_used=2.5)

        assert estimate.instance_type == "t3.micro (On-Demand)"
        assert estimate.hourly_rate == 0.0150
        assert estimate.hours_used == 2.5
        assert estimate.estimated_cost == 0.0375
        assert estimate.pricing_source == "api"
        assert estimate.pricing_warning is None

        # Verify API was called
        mock_pricing_client.get_instance_pricing.assert_called_once_with(
            instance_type="t3.micro", region="us-east-1"
        )

    @patch("clustrix.cost_providers.aws.AWSPricingClient")
    def test_estimate_cost_with_api_failure(self, mock_pricing_client_class):
        """Test cost estimation falling back to hardcoded when API fails."""
        # Set up mock pricing client that returns None
        mock_pricing_client = Mock()
        mock_pricing_client.get_instance_pricing.return_value = None
        mock_pricing_client.is_pricing_data_outdated.return_value = True
        mock_pricing_client._hardcoded_pricing_date = "2025-01-01"
        mock_pricing_client_class.return_value = mock_pricing_client

        monitor = AWSCostMonitor(region="us-east-1", use_pricing_api=True)

        # Estimate cost
        estimate = monitor.estimate_cost("t3.micro", hours_used=1.0)

        assert estimate.instance_type == "t3.micro (On-Demand)"
        assert estimate.hourly_rate == 0.0104  # Hardcoded price
        assert estimate.hours_used == 1.0
        assert estimate.estimated_cost == 0.0104
        assert estimate.pricing_source == "hardcoded"
        assert "potentially outdated pricing data" in estimate.pricing_warning

    def test_estimate_cost_spot_pricing(self):
        """Test spot instance cost estimation."""
        monitor = AWSCostMonitor(use_pricing_api=False)

        # Estimate spot cost
        estimate = monitor.estimate_cost("c5.large", hours_used=10.0, use_spot=True)

        assert estimate.instance_type == "c5.large (Spot)"
        assert estimate.hourly_rate == pytest.approx(
            0.085 * 0.65, rel=1e-4
        )  # 35% discount
        assert estimate.hours_used == 10.0
        assert estimate.pricing_source == "hardcoded"

        # Check spot pricing warning
        if estimate.pricing_warning:
            assert "Spot pricing is estimated" in estimate.pricing_warning

    def test_estimate_cost_unknown_instance(self):
        """Test cost estimation for unknown instance type."""
        monitor = AWSCostMonitor(use_pricing_api=False)

        estimate = monitor.estimate_cost("unknown.xlarge", hours_used=1.0)

        assert estimate.instance_type == "unknown.xlarge (On-Demand)"
        assert estimate.hourly_rate == 0.10  # Default price
        assert estimate.pricing_source == "hardcoded"

    @patch("clustrix.cost_providers.aws.AWSPricingClient")
    def test_get_pricing_info_with_warning(self, mock_pricing_client_class):
        """Test getting pricing info with outdated data warning."""
        # Set up mock pricing client
        mock_pricing_client = Mock()
        mock_pricing_client.is_pricing_data_outdated.return_value = True
        mock_pricing_client_class.return_value = mock_pricing_client

        monitor = AWSCostMonitor(use_pricing_api=True)

        with patch("clustrix.cost_providers.aws.logger") as mock_logger:
            pricing_info = monitor.get_pricing_info()

            # Verify warning was logged
            mock_logger.warning.assert_called_once()
            warning_msg = mock_logger.warning.call_args[0][0]
            assert "outdated" in warning_msg

        assert isinstance(pricing_info, dict)
        assert "t3.micro" in pricing_info

    def test_estimate_cost_with_different_regions(self):
        """Test cost estimation respects region parameter."""
        monitor_east = AWSCostMonitor(region="us-east-1", use_pricing_api=False)
        monitor_west = AWSCostMonitor(region="us-west-2", use_pricing_api=False)

        # Both should use same hardcoded pricing for now
        estimate_east = monitor_east.estimate_cost("m5.large", hours_used=1.0)
        estimate_west = monitor_west.estimate_cost("m5.large", hours_used=1.0)

        assert estimate_east.hourly_rate == estimate_west.hourly_rate
        assert estimate_east.pricing_source == "hardcoded"
        assert estimate_west.pricing_source == "hardcoded"

    def test_cost_estimate_fields(self):
        """Test all CostEstimate fields are properly set."""
        monitor = AWSCostMonitor(use_pricing_api=False)

        estimate = monitor.estimate_cost("p3.2xlarge", hours_used=24.0)

        # Check all fields
        assert estimate.instance_type == "p3.2xlarge (On-Demand)"
        assert estimate.hourly_rate == 3.06
        assert estimate.hours_used == 24.0
        assert estimate.estimated_cost == 73.44
        assert estimate.currency == "USD"
        assert isinstance(estimate.last_updated, datetime)
        assert estimate.pricing_source == "hardcoded"
        assert hasattr(estimate, "pricing_warning")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
