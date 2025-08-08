"""
Cross-provider pricing accuracy tests.

These tests compare pricing across different cloud providers to validate
accuracy and identify pricing opportunities. Uses real APIs with no mocks.
"""

import pytest
import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.azure_pricing import AzurePricingClient
from clustrix.pricing_clients.gcp_pricing import GCPPricingClient
from clustrix.pricing_clients.lambda_pricing import LambdaPricingClient
from tests.real_world.credential_manager import (
    get_aws_credentials,
    get_azure_credentials,
    get_gcp_credentials,
    get_lambda_credentials,
)

logger = logging.getLogger(__name__)


@dataclass
class InstanceSpecs:
    """Specifications for comparing equivalent instances across providers."""

    vcpus: int
    memory_gb: float
    gpu_count: int = 0
    gpu_type: Optional[str] = None
    category: str = "general"  # general, compute, memory, gpu


@dataclass
class ProviderInstance:
    """Instance type mapping for a specific provider."""

    provider: str
    instance_type: str
    specs: InstanceSpecs
    hourly_price: Optional[float] = None
    price_per_vcpu: Optional[float] = None
    price_per_gb_memory: Optional[float] = None


@dataclass
class CrossProviderComparison:
    """Comparison results across providers."""

    specs: InstanceSpecs
    instances: List[ProviderInstance]
    price_range: Tuple[float, float] = (0.0, 0.0)
    price_variance_percent: float = 0.0
    best_value_provider: Optional[str] = None


class CrossProviderAccuracyTester:
    """Framework for cross-provider pricing accuracy testing."""

    def __init__(self):
        """Initialize the cross-provider tester."""
        self.aws_client = None
        self.azure_client = None
        self.gcp_client = None
        self.lambda_client = None

        # Equivalent instance mappings across providers
        self.instance_mappings = {
            # Small general purpose instances (~2 vCPU, 4GB RAM)
            "small_general": InstanceSpecs(vcpus=2, memory_gb=4.0, category="general"),
            # Medium general purpose instances (~4 vCPU, 8GB RAM)
            "medium_general": InstanceSpecs(vcpus=4, memory_gb=8.0, category="general"),
            # Large general purpose instances (~8 vCPU, 16GB RAM)
            "large_general": InstanceSpecs(vcpus=8, memory_gb=16.0, category="general"),
            # Compute optimized instances (~4 vCPU, high CPU performance)
            "compute_optimized": InstanceSpecs(
                vcpus=4, memory_gb=8.0, category="compute"
            ),
            # Memory optimized instances (~4 vCPU, 32GB RAM)
            "memory_optimized": InstanceSpecs(
                vcpus=4, memory_gb=32.0, category="memory"
            ),
            # Single GPU instances
            "single_gpu": InstanceSpecs(
                vcpus=4, memory_gb=16.0, gpu_count=1, category="gpu"
            ),
        }

        # Provider-specific instance type mappings
        self.provider_mappings = {
            "small_general": {
                "aws": "t3.small",  # 2 vCPU, 2GB RAM
                "azure": "Standard_A2_v2",  # 2 vCPU, 4GB RAM
                "gcp": "n1-standard-1",  # 1 vCPU, 3.75GB RAM
                # Lambda Cloud doesn't have exact equivalents for general purpose
            },
            "medium_general": {
                "aws": "t3.large",  # 2 vCPU, 8GB RAM
                "azure": "Standard_D2s_v3",  # 2 vCPU, 8GB RAM
                "gcp": "n1-standard-2",  # 2 vCPU, 7.5GB RAM
            },
            "large_general": {
                "aws": "m5.2xlarge",  # 8 vCPU, 32GB RAM
                "azure": "Standard_D8s_v3",  # 8 vCPU, 32GB RAM
                "gcp": "n1-standard-8",  # 8 vCPU, 30GB RAM
            },
            "compute_optimized": {
                "aws": "c5.xlarge",  # 4 vCPU, 8GB RAM
                "azure": "Standard_F4s_v2",  # 4 vCPU, 8GB RAM
                "gcp": "c2-standard-4",  # 4 vCPU, 16GB RAM
            },
            "memory_optimized": {
                "aws": "r5.xlarge",  # 4 vCPU, 32GB RAM
                "azure": "Standard_E4s_v3",  # 4 vCPU, 32GB RAM
                "gcp": "n1-highmem-4",  # 4 vCPU, 26GB RAM
            },
            "single_gpu": {
                "aws": "g4dn.xlarge",  # 4 vCPU, 16GB RAM, T4 GPU
                "azure": "Standard_NC6s_v3",  # 6 vCPU, 112GB RAM, V100 GPU
                "gcp": "n1-standard-4-t4",  # 4 vCPU, 15GB RAM, T4 GPU
                "lambda": "gpu_1x_a10",  # 1x A10 GPU
            },
        }

    def setup_clients(self):
        """Set up pricing clients for available providers."""
        # AWS
        aws_creds = get_aws_credentials()
        if aws_creds:
            self.aws_client = AWSPricingClient()
            # AWS client doesn't need explicit authentication setup

        # Azure
        azure_creds = get_azure_credentials()
        if azure_creds:
            self.azure_client = AzurePricingClient()

        # GCP
        gcp_creds = get_gcp_credentials()
        if gcp_creds:
            self.gcp_client = GCPPricingClient()

        # Lambda Cloud
        lambda_creds = get_lambda_credentials()
        if lambda_creds and "api_key" in lambda_creds:
            self.lambda_client = LambdaPricingClient()
            self.lambda_client.authenticate(lambda_creds["api_key"])

    def get_provider_pricing(
        self, provider: str, instance_type: str, region: str = None
    ) -> Optional[float]:
        """Get pricing for a specific provider and instance type."""
        try:
            if provider == "aws" and self.aws_client:
                region = region or "us-east-1"
                return self.aws_client.get_instance_pricing(
                    instance_type=instance_type, region=region, operating_system="Linux"
                )
            elif provider == "azure" and self.azure_client:
                region = region or "eastus"
                return self.azure_client.get_instance_pricing(
                    instance_type=instance_type, region=region, operating_system="Linux"
                )
            elif provider == "gcp" and self.gcp_client:
                region = region or "us-central1"
                return self.gcp_client.get_instance_pricing(
                    instance_type=instance_type, region=region
                )
            elif provider == "lambda" and self.lambda_client:
                return self.lambda_client.get_instance_pricing(
                    instance_type=instance_type, region="us-east-1"
                )
        except Exception as e:
            logger.warning(f"Error getting {provider} pricing for {instance_type}: {e}")

        return None

    def compare_instance_category(self, category: str) -> CrossProviderComparison:
        """Compare pricing for equivalent instances in a category."""
        if category not in self.instance_mappings:
            raise ValueError(f"Unknown category: {category}")

        specs = self.instance_mappings[category]
        provider_instances = []

        # Get pricing from each provider
        for provider, instance_type in self.provider_mappings[category].items():
            price = self.get_provider_pricing(provider, instance_type)

            if price is not None:
                instance = ProviderInstance(
                    provider=provider,
                    instance_type=instance_type,
                    specs=specs,
                    hourly_price=price,
                    price_per_vcpu=price / specs.vcpus if specs.vcpus > 0 else None,
                    price_per_gb_memory=(
                        price / specs.memory_gb if specs.memory_gb > 0 else None
                    ),
                )
                provider_instances.append(instance)
                logger.info(f"{provider} {instance_type}: ${price:.4f}/hour")

        if not provider_instances:
            logger.warning(f"No pricing data available for category: {category}")
            return CrossProviderComparison(specs=specs, instances=[])

        # Calculate price statistics
        prices = [
            instance.hourly_price
            for instance in provider_instances
            if instance.hourly_price
        ]
        if prices:
            min_price = min(prices)
            max_price = max(prices)
            price_variance = (
                ((max_price - min_price) / min_price * 100) if min_price > 0 else 0.0
            )

            # Find best value provider (lowest price per vCPU)
            best_value = min(
                provider_instances, key=lambda x: x.price_per_vcpu or float("inf")
            )
            best_value_provider = best_value.provider
        else:
            min_price = max_price = price_variance = 0.0
            best_value_provider = None

        return CrossProviderComparison(
            specs=specs,
            instances=provider_instances,
            price_range=(min_price, max_price),
            price_variance_percent=price_variance,
            best_value_provider=best_value_provider,
        )

    def analyze_pricing_patterns(
        self, comparisons: List[CrossProviderComparison]
    ) -> Dict[str, any]:
        """Analyze pricing patterns across multiple comparisons."""
        if not comparisons:
            return {}

        # Track provider performance
        provider_wins = {}
        provider_price_ratios = {}

        for comparison in comparisons:
            if comparison.best_value_provider:
                provider_wins[comparison.best_value_provider] = (
                    provider_wins.get(comparison.best_value_provider, 0) + 1
                )

            # Calculate price ratios
            if len(comparison.instances) > 1:
                prices = [
                    inst.hourly_price
                    for inst in comparison.instances
                    if inst.hourly_price
                ]
                if len(prices) > 1:
                    min_price = min(prices)
                    for instance in comparison.instances:
                        if instance.hourly_price:
                            ratio = instance.hourly_price / min_price
                            if instance.provider not in provider_price_ratios:
                                provider_price_ratios[instance.provider] = []
                            provider_price_ratios[instance.provider].append(ratio)

        # Calculate average price ratios
        avg_ratios = {}
        for provider, ratios in provider_price_ratios.items():
            avg_ratios[provider] = sum(ratios) / len(ratios) if ratios else 1.0

        return {
            "provider_wins": provider_wins,
            "average_price_ratios": avg_ratios,
            "total_comparisons": len(comparisons),
            "providers_tested": list(
                set(inst.provider for comp in comparisons for inst in comp.instances)
            ),
        }


@pytest.mark.real_world
class TestCrossProviderAccuracy:
    """Test cross-provider pricing accuracy and comparisons."""

    def setup_method(self):
        """Setup for each test method."""
        self.tester = CrossProviderAccuracyTester()
        self.tester.setup_clients()

        # Check if we have at least 2 providers available
        available_providers = sum(
            [
                self.tester.aws_client is not None,
                self.tester.azure_client is not None,
                self.tester.gcp_client is not None,
                self.tester.lambda_client is not None,
            ]
        )

        if available_providers < 2:
            pytest.skip(
                "Need at least 2 cloud provider credentials for cross-provider testing"
            )

    def test_small_general_instance_comparison_real(self):
        """Test pricing comparison for small general purpose instances."""
        comparison = self.tester.compare_instance_category("small_general")

        # Should have pricing data from multiple providers
        assert len(comparison.instances) >= 2

        # All instances should have valid pricing
        for instance in comparison.instances:
            assert instance.hourly_price is not None
            assert instance.hourly_price > 0
            assert instance.price_per_vcpu is not None
            assert instance.price_per_vcpu > 0

        # Price variance should be reasonable (not more than 300%)
        assert comparison.price_variance_percent < 300

        logger.info(
            f"Small general instances price range: "
            f"${comparison.price_range[0]:.4f} - ${comparison.price_range[1]:.4f}"
        )
        logger.info(f"Price variance: {comparison.price_variance_percent:.1f}%")
        logger.info(f"Best value provider: {comparison.best_value_provider}")

    def test_medium_general_instance_comparison_real(self):
        """Test pricing comparison for medium general purpose instances."""
        comparison = self.tester.compare_instance_category("medium_general")

        # Should have pricing data from multiple providers
        assert len(comparison.instances) >= 2

        # Verify pricing relationships
        for instance in comparison.instances:
            assert instance.hourly_price > 0
            # Medium instances should cost more than $0.05/hour
            assert instance.hourly_price > 0.05
            # But less than $1.00/hour for general purpose
            assert instance.hourly_price < 1.00

        logger.info(
            f"Medium general instances price range: "
            f"${comparison.price_range[0]:.4f} - ${comparison.price_range[1]:.4f}"
        )
        logger.info(f"Best value provider: {comparison.best_value_provider}")

    def test_compute_optimized_instance_comparison_real(self):
        """Test pricing comparison for compute optimized instances."""
        comparison = self.tester.compare_instance_category("compute_optimized")

        # Should have pricing data
        assert len(comparison.instances) >= 1

        # Compute optimized instances should have good price per vCPU
        for instance in comparison.instances:
            assert instance.price_per_vcpu is not None
            # Should be competitive pricing per vCPU
            assert instance.price_per_vcpu < 0.50

        logger.info(
            f"Compute optimized price range: "
            f"${comparison.price_range[0]:.4f} - ${comparison.price_range[1]:.4f}"
        )

    def test_memory_optimized_instance_comparison_real(self):
        """Test pricing comparison for memory optimized instances."""
        comparison = self.tester.compare_instance_category("memory_optimized")

        # Should have pricing data
        assert len(comparison.instances) >= 1

        # Memory optimized should have good price per GB memory
        for instance in comparison.instances:
            assert instance.price_per_gb_memory is not None
            # Should be reasonable pricing per GB memory
            assert instance.price_per_gb_memory < 0.10

        logger.info(
            f"Memory optimized price range: "
            f"${comparison.price_range[0]:.4f} - ${comparison.price_range[1]:.4f}"
        )

    def test_gpu_instance_comparison_real(self):
        """Test pricing comparison for GPU instances."""
        comparison = self.tester.compare_instance_category("single_gpu")

        # Should have pricing data from at least one provider
        assert len(comparison.instances) >= 1

        # GPU instances should be more expensive
        for instance in comparison.instances:
            assert instance.hourly_price > 0.50  # GPU should cost at least $0.50/hour
            assert (
                instance.hourly_price < 10.00
            )  # But not more than $10/hour for single GPU

        logger.info(
            f"GPU instances price range: "
            f"${comparison.price_range[0]:.4f} - ${comparison.price_range[1]:.4f}"
        )

    def test_comprehensive_price_analysis_real(self):
        """Test comprehensive pricing analysis across all categories."""
        categories = [
            "small_general",
            "medium_general",
            "compute_optimized",
            "memory_optimized",
        ]
        comparisons = []

        for category in categories:
            try:
                comparison = self.tester.compare_instance_category(category)
                if comparison.instances:  # Only add if we got pricing data
                    comparisons.append(comparison)
            except Exception as e:
                logger.warning(f"Failed to get comparison for {category}: {e}")

        # Should have successful comparisons
        assert len(comparisons) >= 2

        # Analyze patterns
        analysis = self.tester.analyze_pricing_patterns(comparisons)

        # Should have identified patterns
        assert "provider_wins" in analysis
        assert "average_price_ratios" in analysis
        assert analysis["total_comparisons"] >= 2

        logger.info(f"Cross-provider analysis: {analysis}")

        # Log provider performance
        if analysis["provider_wins"]:
            best_provider = max(analysis["provider_wins"].items(), key=lambda x: x[1])
            logger.info(
                f"Most competitive provider: {best_provider[0]} "
                f"(won {best_provider[1]} categories)"
            )

    def test_price_performance_ratio_validation_real(self):
        """Test price-performance ratio validation across providers."""
        # Compare general purpose instances of different sizes
        small_comparison = self.tester.compare_instance_category("small_general")
        medium_comparison = self.tester.compare_instance_category("medium_general")

        if not small_comparison.instances or not medium_comparison.instances:
            pytest.skip("Need pricing data for both small and medium instances")

        # Group by provider
        provider_small = {inst.provider: inst for inst in small_comparison.instances}
        provider_medium = {inst.provider: inst for inst in medium_comparison.instances}

        common_providers = set(provider_small.keys()) & set(provider_medium.keys())
        assert len(common_providers) >= 1

        for provider in common_providers:
            small_inst = provider_small[provider]
            medium_inst = provider_medium[provider]

            # Medium instance should cost more than small
            assert medium_inst.hourly_price > small_inst.hourly_price

            # Price per vCPU should be reasonably consistent
            price_ratio = medium_inst.hourly_price / small_inst.hourly_price
            vcpu_ratio = medium_inst.specs.vcpus / small_inst.specs.vcpus

            # Price scaling should be reasonable (not more than 3x the resource scaling)
            assert price_ratio <= vcpu_ratio * 3

            logger.info(
                f"{provider} scaling: {small_inst.specs.vcpus}vCPU@"
                f"${small_inst.hourly_price:.4f} -> "
                f"{medium_inst.specs.vcpus}vCPU@${medium_inst.hourly_price:.4f}"
            )

    def test_regional_pricing_consistency_real(self):
        """Test pricing consistency across regions for each provider."""
        regions = {
            "aws": ["us-east-1", "us-west-2"],
            "azure": ["eastus", "westus2"],
            "gcp": ["us-central1", "us-west1"],
        }

        instance_types = {
            "aws": "t3.small",
            "azure": "Standard_D2s_v3",
            "gcp": "n1-standard-1",
        }

        regional_variations = {}

        for provider in ["aws", "azure", "gcp"]:
            if not getattr(self.tester, f"{provider}_client"):
                continue

            provider_regions = regions.get(provider, [])
            if len(provider_regions) < 2:
                continue

            instance_type = instance_types[provider]
            regional_prices = []

            for region in provider_regions:
                price = self.tester.get_provider_pricing(
                    provider, instance_type, region
                )
                if price:
                    regional_prices.append(price)
                    logger.info(
                        f"{provider} {instance_type} in {region}: ${price:.4f}/hour"
                    )

            if len(regional_prices) >= 2:
                min_price = min(regional_prices)
                max_price = max(regional_prices)
                variance = (
                    (max_price - min_price) / min_price * 100 if min_price > 0 else 0
                )
                regional_variations[provider] = variance

                # Regional pricing shouldn't vary by more than 50%
                assert variance < 50
                logger.info(f"{provider} regional price variance: {variance:.1f}%")

        # Should have tested at least one provider
        assert len(regional_variations) >= 1

    def test_pricing_data_freshness_real(self):
        """Test that pricing data is reasonably fresh and consistent."""
        # Get pricing data multiple times and check consistency
        comparison1 = self.tester.compare_instance_category("small_general")
        time.sleep(2)  # Small delay
        comparison2 = self.tester.compare_instance_category("small_general")

        if not comparison1.instances or not comparison2.instances:
            pytest.skip("Need consistent pricing data for freshness testing")

        # Group by provider
        prices1 = {inst.provider: inst.hourly_price for inst in comparison1.instances}
        prices2 = {inst.provider: inst.hourly_price for inst in comparison2.instances}

        common_providers = set(prices1.keys()) & set(prices2.keys())
        assert len(common_providers) >= 1

        for provider in common_providers:
            price1 = prices1[provider]
            price2 = prices2[provider]

            # Prices should be identical (cached) or very close
            price_diff_percent = (
                abs(price1 - price2) / price1 * 100 if price1 > 0 else 0
            )
            assert price_diff_percent < 5  # Allow 5% variance for API pricing updates

            logger.info(
                f"{provider} price consistency: ${price1:.4f} vs ${price2:.4f} "
                f"({price_diff_percent:.2f}% diff)"
            )

    def teardown_method(self):
        """Cleanup after each test."""
        # No cleanup needed for pricing tests
        pass
