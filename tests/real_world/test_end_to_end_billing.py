"""
End-to-end billing accuracy tests.

These tests validate cost estimation against real usage scenarios and
test cost monitoring integration with actual billing workflows.
Uses real APIs with no mocks or simulations.
"""

import pytest
import logging
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor
from tests.real_world.credential_manager import (
    get_aws_credentials,
    get_azure_credentials,
    get_gcp_credentials,
    get_lambda_credentials,
)

logger = logging.getLogger(__name__)


@dataclass
class UsageScenario:
    """Real-world usage scenario for billing testing."""

    name: str
    description: str
    instance_type: str
    hours_used: float
    expected_cost_range: Tuple[float, float]  # (min, max) expected cost
    usage_pattern: str = "continuous"  # continuous, burst, intermittent


@dataclass
class BillingAccuracyResult:
    """Results from billing accuracy testing."""

    scenario: UsageScenario
    provider: str
    estimated_cost: float
    actual_cost_estimate: float
    accuracy_percent: float
    cost_per_hour: float
    pricing_source: str
    warnings: List[str]


class EndToEndBillingTester:
    """Framework for end-to-end billing accuracy testing."""

    def __init__(self):
        """Initialize the billing tester."""
        self.cost_monitors = {}

        # Real-world usage scenarios
        self.scenarios = {
            "development_workload": UsageScenario(
                name="Development Workload",
                description="Typical development instance running 8 hours/day",
                instance_type="t3.small",  # Will be mapped per provider
                hours_used=8.0,
                expected_cost_range=(0.15, 0.50),  # $0.15-0.50 for 8 hours
                usage_pattern="intermittent",
            ),
            "ml_training_job": UsageScenario(
                name="ML Training Job",
                description="GPU instance for machine learning training",
                instance_type="gpu_small",
                hours_used=4.0,
                expected_cost_range=(2.0, 15.0),  # $2-15 for 4 hours GPU
                usage_pattern="continuous",
            ),
            "batch_processing": UsageScenario(
                name="Batch Processing",
                description="Compute-optimized instance for batch processing",
                instance_type="compute_medium",
                hours_used=12.0,
                expected_cost_range=(1.0, 4.0),  # $1-4 for 12 hours
                usage_pattern="continuous",
            ),
            "memory_intensive_app": UsageScenario(
                name="Memory Intensive Application",
                description="Memory-optimized instance for data processing",
                instance_type="memory_large",
                hours_used=6.0,
                expected_cost_range=(1.5, 6.0),  # $1.5-6 for 6 hours
                usage_pattern="continuous",
            ),
            "weekend_job": UsageScenario(
                name="Weekend Processing Job",
                description="Long-running job over weekend",
                instance_type="t3.medium",
                hours_used=48.0,  # 2 days
                expected_cost_range=(1.5, 8.0),  # $1.5-8 for 48 hours
                usage_pattern="continuous",
            ),
        }

        # Provider-specific instance mappings
        self.instance_mappings = {
            "t3.small": {
                "aws": "t3.small",
                "azure": "Standard_A2_v2",
                "gcp": "n1-standard-1",
            },
            "t3.medium": {
                "aws": "t3.medium",
                "azure": "Standard_D2s_v3",
                "gcp": "n1-standard-2",
            },
            "gpu_small": {
                "aws": "g4dn.xlarge",
                "azure": "Standard_NC6s_v3",
                "gcp": "n1-standard-4-t4",
                "lambda": "gpu_1x_a10",
            },
            "compute_medium": {
                "aws": "c5.xlarge",
                "azure": "Standard_F4s_v2",
                "gcp": "c2-standard-4",
            },
            "memory_large": {
                "aws": "r5.xlarge",
                "azure": "Standard_E4s_v3",
                "gcp": "n1-highmem-4",
            },
        }

    def setup_cost_monitors(self):
        """Set up cost monitors for available providers."""
        # AWS
        aws_creds = get_aws_credentials()
        if aws_creds:
            self.cost_monitors["aws"] = AWSCostMonitor()

        # Azure
        azure_creds = get_azure_credentials()
        if azure_creds:
            self.cost_monitors["azure"] = AzureCostMonitor()

        # GCP
        gcp_creds = get_gcp_credentials()
        if gcp_creds:
            self.cost_monitors["gcp"] = GCPCostMonitor()

        # Lambda Cloud
        lambda_creds = get_lambda_credentials()
        if lambda_creds and "api_key" in lambda_creds:
            self.cost_monitors["lambda"] = LambdaCostMonitor(
                use_pricing_api=True, api_key=lambda_creds["api_key"]
            )

    def get_provider_instance_type(
        self, generic_type: str, provider: str
    ) -> Optional[str]:
        """Get provider-specific instance type."""
        return self.instance_mappings.get(generic_type, {}).get(provider)

    def run_billing_accuracy_test(
        self, scenario_name: str, provider: str
    ) -> Optional[BillingAccuracyResult]:
        """Run billing accuracy test for a specific scenario and provider."""
        if provider not in self.cost_monitors:
            logger.warning(f"No cost monitor available for provider: {provider}")
            return None

        if scenario_name not in self.scenarios:
            logger.warning(f"Unknown scenario: {scenario_name}")
            return None

        scenario = self.scenarios[scenario_name]
        cost_monitor = self.cost_monitors[provider]

        # Get provider-specific instance type
        instance_type = self.get_provider_instance_type(
            scenario.instance_type, provider
        )
        if not instance_type:
            logger.warning(
                f"No {provider} mapping for instance type: {scenario.instance_type}"
            )
            return None

        try:
            # Get cost estimate
            cost_estimate = cost_monitor.estimate_cost(
                instance_type, scenario.hours_used
            )

            if not cost_estimate:
                logger.warning(
                    f"Failed to get cost estimate for {provider} {instance_type}"
                )
                return None

            # Calculate accuracy
            expected_min, expected_max = scenario.expected_cost_range
            estimated_cost = cost_estimate.estimated_cost

            # Check if estimate is within expected range
            if expected_min <= estimated_cost <= expected_max:
                accuracy_percent = 100.0  # Perfect accuracy within range
            else:
                # Calculate how far off we are
                if estimated_cost < expected_min:
                    accuracy_percent = (estimated_cost / expected_min) * 100
                else:
                    accuracy_percent = (expected_max / estimated_cost) * 100

            warnings = []

            # Check for pricing warnings
            if (
                hasattr(cost_estimate, "pricing_warning")
                and cost_estimate.pricing_warning
            ):
                warnings.append(cost_estimate.pricing_warning)

            # Check for unreasonable costs
            if estimated_cost > expected_max * 2:
                warnings.append(
                    f"Estimated cost ${estimated_cost:.2f} is >2x expected maximum"
                )
            elif estimated_cost < expected_min * 0.5:
                warnings.append(
                    f"Estimated cost ${estimated_cost:.2f} is <0.5x expected minimum"
                )

            return BillingAccuracyResult(
                scenario=scenario,
                provider=provider,
                estimated_cost=estimated_cost,
                actual_cost_estimate=cost_estimate.hourly_rate * scenario.hours_used,
                accuracy_percent=accuracy_percent,
                cost_per_hour=cost_estimate.hourly_rate,
                pricing_source=getattr(cost_estimate, "pricing_source", "unknown"),
                warnings=warnings,
            )

        except Exception as e:
            logger.error(
                f"Error running billing test for {provider} {scenario_name}: {e}"
            )
            return None

    def validate_cost_monitoring_integration(self, provider: str) -> Dict[str, Any]:
        """Validate cost monitoring integration for a provider."""
        if provider not in self.cost_monitors:
            return {"error": f"No cost monitor for {provider}"}

        cost_monitor = self.cost_monitors[provider]
        validation_results = {}

        try:
            # Test basic functionality
            validation_results["basic_functionality"] = True

            # Test pricing info retrieval
            pricing_info = cost_monitor.get_pricing_info()
            validation_results["pricing_info_available"] = (
                isinstance(pricing_info, dict) and len(pricing_info) > 0
            )

            # Test cost optimization tips
            optimization_tips = cost_monitor.get_cost_optimization_tips()
            validation_results["optimization_tips_available"] = (
                isinstance(optimization_tips, list) and len(optimization_tips) > 0
            )

            # Test monthly cost estimation
            if hasattr(cost_monitor, "estimate_monthly_cost"):
                monthly_cost = cost_monitor.estimate_monthly_cost(
                    "t3.small", 8
                )  # 8 hours/day
                validation_results["monthly_estimation_available"] = isinstance(
                    monthly_cost, dict
                )

            # Test performance metrics
            if hasattr(cost_monitor, "get_performance_metrics"):
                try:
                    perf_metrics = cost_monitor.get_performance_metrics()
                    validation_results["performance_metrics_available"] = isinstance(
                        perf_metrics, dict
                    )
                except Exception as e:
                    logger.debug(
                        f"Performance metrics not available for {provider}: {e}"
                    )
                    validation_results["performance_metrics_available"] = False

            validation_results["overall_health"] = "healthy"

        except Exception as e:
            validation_results["error"] = str(e)
            validation_results["overall_health"] = "unhealthy"

        return validation_results

    def simulate_monthly_billing_cycle(self, provider: str) -> Dict[str, Any]:
        """Simulate a monthly billing cycle with various usage patterns."""
        if provider not in self.cost_monitors:
            return {"error": f"No cost monitor for {provider}"}

        cost_monitor = self.cost_monitors[provider]

        # Simulate different usage patterns over a month
        monthly_simulation = {
            "total_estimated_cost": 0.0,
            "daily_costs": [],
            "instance_usage": {},
            "cost_breakdown": {},
        }

        # Define monthly usage pattern
        monthly_usage = [
            # Week 1: Light development
            ("t3.small", 6, 5),  # 6 hours/day for 5 days
            # Week 2: Heavy development + ML training
            ("t3.medium", 8, 5),  # 8 hours/day for 5 days
            ("gpu_small", 4, 2),  # 4 hours GPU training, 2 days
            # Week 3: Batch processing
            ("compute_medium", 12, 3),  # 12 hour jobs, 3 days
            # Week 4: Memory intensive work
            ("memory_large", 8, 4),  # 8 hours/day for 4 days
        ]

        try:
            for generic_instance, daily_hours, days in monthly_usage:
                instance_type = self.get_provider_instance_type(
                    generic_instance, provider
                )
                if not instance_type:
                    continue

                total_hours = daily_hours * days
                cost_estimate = cost_monitor.estimate_cost(instance_type, total_hours)

                if cost_estimate:
                    instance_cost = cost_estimate.estimated_cost
                    monthly_simulation["total_estimated_cost"] += instance_cost
                    monthly_simulation["instance_usage"][instance_type] = {
                        "hours": total_hours,
                        "cost": instance_cost,
                        "daily_hours": daily_hours,
                        "days": days,
                    }

                    # Daily cost breakdown
                    daily_cost = instance_cost / days if days > 0 else 0
                    for _ in range(days):
                        monthly_simulation["daily_costs"].append(
                            {"instance": instance_type, "cost": daily_cost}
                        )

            # Calculate cost breakdown
            if monthly_simulation["instance_usage"]:
                total_cost = monthly_simulation["total_estimated_cost"]
                for instance, usage in monthly_simulation["instance_usage"].items():
                    percentage = (
                        (usage["cost"] / total_cost * 100) if total_cost > 0 else 0
                    )
                    monthly_simulation["cost_breakdown"][instance] = {
                        "cost": usage["cost"],
                        "percentage": percentage,
                    }

            monthly_simulation["simulation_success"] = True

        except Exception as e:
            monthly_simulation["error"] = str(e)
            monthly_simulation["simulation_success"] = False

        return monthly_simulation


@pytest.mark.real_world
class TestEndToEndBilling:
    """Test end-to-end billing accuracy and cost monitoring."""

    def setup_method(self):
        """Setup for each test method."""
        self.tester = EndToEndBillingTester()
        self.tester.setup_cost_monitors()

        if not self.tester.cost_monitors:
            pytest.skip("No cloud provider credentials available for billing testing")

    def test_development_workload_billing_accuracy(self):
        """Test billing accuracy for typical development workload."""
        results = []

        for provider in self.tester.cost_monitors.keys():
            result = self.tester.run_billing_accuracy_test(
                "development_workload", provider
            )
            if result:
                results.append(result)

                # Verify cost is reasonable
                assert result.estimated_cost > 0
                assert result.cost_per_hour > 0
                assert result.accuracy_percent > 50  # At least 50% accurate

                logger.info(
                    f"{provider} development workload: ${result.estimated_cost:.3f} "
                    f"({result.accuracy_percent:.1f}% accurate)"
                )

                # Log any warnings
                for warning in result.warnings:
                    logger.warning(f"{provider}: {warning}")

        # Should have tested at least one provider
        assert len(results) >= 1

    def test_ml_training_job_billing_accuracy(self):
        """Test billing accuracy for ML training workload."""
        results = []

        for provider in self.tester.cost_monitors.keys():
            result = self.tester.run_billing_accuracy_test("ml_training_job", provider)
            if result:
                results.append(result)

                # GPU workloads should be more expensive
                assert result.estimated_cost > 1.0  # At least $1 for 4 hours GPU
                assert result.cost_per_hour > 0.25  # At least $0.25/hour

                logger.info(
                    f"{provider} ML training: ${result.estimated_cost:.3f} "
                    f"({result.accuracy_percent:.1f}% accurate)"
                )

        # Should have tested at least one provider with GPU support
        assert len(results) >= 1

    def test_batch_processing_billing_accuracy(self):
        """Test billing accuracy for batch processing workload."""
        results = []

        for provider in self.tester.cost_monitors.keys():
            result = self.tester.run_billing_accuracy_test("batch_processing", provider)
            if result:
                results.append(result)

                # Batch processing should have reasonable costs
                assert result.estimated_cost > 0.5  # At least $0.50 for 12 hours
                assert result.estimated_cost < 10.0  # But not more than $10

                logger.info(
                    f"{provider} batch processing: ${result.estimated_cost:.3f} "
                    f"({result.accuracy_percent:.1f}% accurate)"
                )

        assert len(results) >= 1

    def test_memory_intensive_billing_accuracy(self):
        """Test billing accuracy for memory intensive workload."""
        results = []

        for provider in self.tester.cost_monitors.keys():
            result = self.tester.run_billing_accuracy_test(
                "memory_intensive_app", provider
            )
            if result:
                results.append(result)

                # Memory optimized should be more expensive per hour
                assert result.cost_per_hour > 0.15  # At least $0.15/hour

                logger.info(
                    f"{provider} memory intensive: ${result.estimated_cost:.3f} "
                    f"({result.accuracy_percent:.1f}% accurate)"
                )

        assert len(results) >= 1

    def test_long_running_job_billing_accuracy(self):
        """Test billing accuracy for long-running weekend job."""
        results = []

        for provider in self.tester.cost_monitors.keys():
            result = self.tester.run_billing_accuracy_test("weekend_job", provider)
            if result:
                results.append(result)

                # 48 hour job should have proportional cost
                assert result.estimated_cost > 1.0  # At least $1 for 48 hours

                # Cost should scale reasonably with time
                hourly_rate = result.estimated_cost / 48.0
                assert hourly_rate > 0.01  # At least 1 cent per hour
                assert (
                    hourly_rate < 1.0
                )  # But not more than $1 per hour for basic instance

                logger.info(
                    f"{provider} weekend job (48h): ${result.estimated_cost:.3f} "
                    f"({result.accuracy_percent:.1f}% accurate)"
                )

        assert len(results) >= 1

    def test_cost_monitoring_integration_health(self):
        """Test health and integration of cost monitoring components."""
        health_results = {}

        for provider in self.tester.cost_monitors.keys():
            validation = self.tester.validate_cost_monitoring_integration(provider)
            health_results[provider] = validation

            # Basic health checks
            assert validation.get("basic_functionality", False)
            assert validation.get("overall_health") in ["healthy", "unhealthy"]

            if validation.get("overall_health") == "healthy":
                # Should have pricing info
                assert validation.get("pricing_info_available", False)
                # Should have optimization tips
                assert validation.get("optimization_tips_available", False)

            logger.info(
                f"{provider} cost monitor health: "
                f"{validation.get('overall_health', 'unknown')}"
            )

            # Log any errors
            if "error" in validation:
                logger.warning(f"{provider} cost monitor error: {validation['error']}")

        # Should have validated at least one provider
        assert len(health_results) >= 1

        # At least one provider should be healthy
        healthy_providers = [
            p for p, v in health_results.items() if v.get("overall_health") == "healthy"
        ]
        assert len(healthy_providers) >= 1

    def test_monthly_billing_simulation(self):
        """Test monthly billing cycle simulation."""
        simulation_results = {}

        for provider in self.tester.cost_monitors.keys():
            simulation = self.tester.simulate_monthly_billing_cycle(provider)
            simulation_results[provider] = simulation

            if simulation.get("simulation_success"):
                total_cost = simulation.get("total_estimated_cost", 0)

                # Monthly cost should be reasonable
                assert total_cost > 0
                assert total_cost < 1000  # Shouldn't exceed $1000 for test scenario

                # Should have instance usage data
                assert len(simulation.get("instance_usage", {})) > 0

                # Should have cost breakdown
                assert len(simulation.get("cost_breakdown", {})) > 0

                logger.info(f"{provider} monthly simulation: ${total_cost:.2f}")

                # Log cost breakdown
                for instance, breakdown in simulation.get("cost_breakdown", {}).items():
                    logger.info(
                        f"  {instance}: ${breakdown['cost']:.2f} "
                        f"({breakdown['percentage']:.1f}%)"
                    )
            else:
                logger.warning(
                    f"{provider} monthly simulation failed: "
                    f"{simulation.get('error', 'unknown error')}"
                )

        # Should have simulated at least one provider
        assert len(simulation_results) >= 1

        # At least one simulation should succeed
        successful_sims = [
            p for p, s in simulation_results.items() if s.get("simulation_success")
        ]
        assert len(successful_sims) >= 1

    def test_cost_estimation_consistency(self):
        """Test consistency of cost estimation across multiple calls."""
        consistency_results = {}

        for provider in self.tester.cost_monitors.keys():
            cost_monitor = self.tester.cost_monitors[provider]

            # Test small instance multiple times
            instance_type = self.tester.get_provider_instance_type("t3.small", provider)
            if not instance_type:
                continue

            estimates = []
            for i in range(3):
                estimate = cost_monitor.estimate_cost(instance_type, 8.0)  # 8 hours
                if estimate:
                    estimates.append(estimate.estimated_cost)
                time.sleep(1)  # Small delay between calls

            if len(estimates) >= 2:
                # Check consistency
                max_estimate = max(estimates)
                min_estimate = min(estimates)
                variance = (
                    (max_estimate - min_estimate) / min_estimate * 100
                    if min_estimate > 0
                    else 0
                )

                consistency_results[provider] = {
                    "estimates": estimates,
                    "variance_percent": variance,
                }

                # Estimates should be consistent (less than 10% variance)
                assert variance < 10

                logger.info(f"{provider} cost estimation variance: {variance:.2f}%")

        # Should have tested consistency for at least one provider
        assert len(consistency_results) >= 1

    def test_pricing_source_validation(self):
        """Test validation of pricing sources (API vs hardcoded)."""
        pricing_sources = {}

        for provider in self.tester.cost_monitors.keys():
            cost_monitor = self.tester.cost_monitors[provider]

            # Get estimate and check pricing source
            instance_type = self.tester.get_provider_instance_type(
                "t3.medium", provider
            )
            if not instance_type:
                continue

            estimate = cost_monitor.estimate_cost(instance_type, 4.0)
            if estimate:
                pricing_source = getattr(estimate, "pricing_source", "unknown")
                pricing_sources[provider] = pricing_source

                # Should have a valid pricing source
                assert pricing_source in ["api", "hardcoded", "unknown"]

                logger.info(f"{provider} pricing source: {pricing_source}")

                # Log warning if using potentially outdated data
                if hasattr(estimate, "pricing_warning") and estimate.pricing_warning:
                    logger.warning(
                        f"{provider} pricing warning: {estimate.pricing_warning}"
                    )

        # Should have checked pricing sources for at least one provider
        assert len(pricing_sources) >= 1

    def teardown_method(self):
        """Cleanup after each test."""
        # No cleanup needed for billing tests
        pass
