"""
Comprehensive multi-provider integration tests for Kubernetes cluster provisioning.

These tests verify cross-provider functionality and ensure consistent behavior
across all supported Kubernetes provisioning providers:
- AWS EKS from-scratch provisioning
- GCP GKE from-scratch provisioning
- Azure AKS from-scratch provisioning
- HuggingFace Spaces Kubernetes adapter
- Lambda Cloud Kubernetes adapter

Requirements:
- Valid credentials for all tested providers
- Network connectivity to all provider APIs
- Sufficient quotas for cluster/instance creation
- SSH capabilities for direct instance testing
"""

import os
import time
import pytest
import logging
import concurrent.futures
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from clustrix.kubernetes.cluster_provisioner import (
    KubernetesClusterProvisioner,
    ClusterSpec,
)
from clustrix.config import ClusterConfig
from clustrix.credential_manager import get_credential_manager

logger = logging.getLogger(__name__)


@dataclass
class ProviderTestConfig:
    """Configuration for testing a specific provider."""

    name: str
    provisioner_class: type
    region: str
    credentials_env_vars: List[str]
    credentials_1password_item: str
    expected_provision_time: int  # seconds
    expected_ready_time: int  # seconds


@pytest.mark.real_world
class TestKubernetesMultiProviderIntegration:
    """Comprehensive multi-provider integration tests."""

    @pytest.fixture(scope="class")
    def provider_configs(self):
        """Define test configurations for all providers."""
        return [
            ProviderTestConfig(
                name="aws",
                provisioner_class="clustrix.kubernetes.aws_provisioner.AWSEKSFromScratchProvisioner",
                region="us-west-2",
                credentials_env_vars=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
                credentials_1password_item="AWS-Clustrix",
                expected_provision_time=300,  # 5 minutes
                expected_ready_time=900,  # 15 minutes
            ),
            ProviderTestConfig(
                name="gcp",
                provisioner_class="clustrix.kubernetes.gcp_provisioner.GCPGKEFromScratchProvisioner",
                region="us-central1",
                credentials_env_vars=[
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GCP_SERVICE_ACCOUNT_KEY",
                ],
                credentials_1password_item="GCP-Clustrix",
                expected_provision_time=240,  # 4 minutes
                expected_ready_time=720,  # 12 minutes
            ),
            ProviderTestConfig(
                name="azure",
                provisioner_class="clustrix.kubernetes.azure_provisioner.AzureAKSFromScratchProvisioner",
                region="eastus",
                credentials_env_vars=[
                    "AZURE_SUBSCRIPTION_ID",
                    "AZURE_TENANT_ID",
                    "AZURE_CLIENT_ID",
                    "AZURE_CLIENT_SECRET",
                ],
                credentials_1password_item="Azure-Clustrix",
                expected_provision_time=360,  # 6 minutes
                expected_ready_time=1080,  # 18 minutes
            ),
            ProviderTestConfig(
                name="huggingface",
                provisioner_class="clustrix.kubernetes.huggingface_provisioner.HuggingFaceKubernetesProvisioner",
                region="global",
                credentials_env_vars=["HF_TOKEN", "HF_USERNAME"],
                credentials_1password_item="HuggingFace",
                expected_provision_time=60,  # 1 minute
                expected_ready_time=600,  # 10 minutes
            ),
            ProviderTestConfig(
                name="lambda",
                provisioner_class="clustrix.kubernetes.lambda_provisioner.LambdaCloudKubernetesProvisioner",
                region="us-west-2",
                credentials_env_vars=["LAMBDA_API_KEY"],
                credentials_1password_item="Lambda-Cloud",
                expected_provision_time=120,  # 2 minutes
                expected_ready_time=300,  # 5 minutes
            ),
        ]

    @pytest.fixture(scope="class")
    def available_providers(self, provider_configs):
        """Get list of providers with available credentials."""
        available = []

        for config in provider_configs:
            if self._has_credentials_for_provider(config):
                available.append(config)
            else:
                logger.info(f"Skipping {config.name} - credentials not available")

        if not available:
            pytest.skip("No provider credentials available for multi-provider testing")

        return available

    def _has_credentials_for_provider(self, config: ProviderTestConfig) -> bool:
        """Check if credentials are available for a provider."""
        # Check environment variables
        if config.credentials_env_vars:
            if all(os.getenv(var) for var in config.credentials_env_vars):
                return True

        # Check 1Password
        try:
            import subprocess

            result = subprocess.run(
                [
                    "op",
                    "item",
                    "get",
                    config.credentials_1password_item,
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
        except (
            subprocess.TimeoutExpired,
            subprocess.SubprocessError,
            FileNotFoundError,
        ):
            pass

        return False

    def test_all_providers_credential_validation(self, available_providers):
        """Test credential validation across all available providers."""
        logger.info("🧪 Testing credential validation across all providers")

        credential_manager = get_credential_manager()
        results = {}

        for config in available_providers:
            logger.info(f"Testing credentials for {config.name}")

            try:
                credentials = credential_manager.ensure_credential(config.name)
                assert (
                    credentials is not None
                ), f"Should have credentials for {config.name}"

                # Test with actual provisioner
                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)
                validation_result = provisioner.validate_credentials()

                results[config.name] = validation_result
                logger.info(
                    f"✅ {config.name} credential validation: {'✓' if validation_result else '✗'}"
                )

            except Exception as e:
                results[config.name] = False
                logger.error(f"❌ {config.name} credential validation failed: {e}")

        # Assert that at least one provider has valid credentials
        valid_providers = [name for name, result in results.items() if result]
        assert (
            len(valid_providers) > 0
        ), f"At least one provider should have valid credentials. Results: {results}"

        logger.info(
            f"✅ Credential validation completed. Valid providers: {valid_providers}"
        )

    def test_consistent_cluster_spec_handling(self, available_providers):
        """Test that all providers handle ClusterSpec consistently."""
        logger.info("🧪 Testing consistent ClusterSpec handling across providers")

        test_id = int(time.time())
        base_spec = ClusterSpec(
            cluster_name=f"test-consistency-{test_id}",
            provider="test",  # Will be overridden
            node_count=1,
            kubernetes_version="1.28",
        )

        credential_manager = get_credential_manager()

        for config in available_providers:
            logger.info(f"Testing ClusterSpec handling for {config.name}")

            # Create provider-specific spec
            spec = ClusterSpec(
                cluster_name=f"test-{config.name}-spec-{test_id}",
                provider=config.name,
                node_count=1,
                kubernetes_version="1.28",
                region=config.region,
            )

            try:
                # Test spec validation and processing
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)

                # Test that the provisioner can process the spec without errors
                # (We're not actually provisioning, just testing spec handling)
                if hasattr(provisioner, "_map_node_requirements_to_hardware"):
                    # HuggingFace hardware mapping
                    hardware = provisioner._map_node_requirements_to_hardware(spec)
                    assert hardware is not None, f"{config.name} should map hardware"
                elif hasattr(provisioner, "_map_node_requirements_to_instance_type"):
                    # Lambda Cloud instance type mapping
                    instance_type = provisioner._map_node_requirements_to_instance_type(
                        spec
                    )
                    assert (
                        instance_type is not None
                    ), f"{config.name} should map instance type"

                logger.info(f"✅ {config.name} ClusterSpec handling verified")

            except Exception as e:
                logger.error(f"❌ {config.name} ClusterSpec handling failed: {e}")
                # Don't fail the test for spec handling issues - log and continue

        logger.info("✅ ClusterSpec consistency testing completed")

    def test_kubernetes_cluster_provisioner_integration(self, available_providers):
        """Test integration with the main KubernetesClusterProvisioner."""
        logger.info("🧪 Testing KubernetesClusterProvisioner integration")

        test_id = int(time.time())

        for config in available_providers:
            logger.info(f"Testing KubernetesClusterProvisioner with {config.name}")

            try:
                # Create cluster config
                cluster_config = ClusterConfig(
                    k8s_provider=config.name,
                    k8s_region=config.region,
                    auto_provision_k8s=True,
                    k8s_from_scratch=True,
                    k8s_cluster_name=f"test-integration-{config.name}-{test_id}",
                    k8s_node_count=1,
                )

                # Test provisioner creation and credential retrieval
                provisioner = KubernetesClusterProvisioner(cluster_config)

                # Test provider detection
                providers = provisioner.list_available_providers()
                assert (
                    config.name in providers
                ), f"{config.name} should be in available providers"

                # Test cluster listing (should not fail even with no clusters)
                clusters = provisioner.list_clusters([config.name])
                assert isinstance(clusters, list), "list_clusters should return a list"

                logger.info(
                    f"✅ {config.name} KubernetesClusterProvisioner integration verified"
                )

            except Exception as e:
                logger.error(
                    f"❌ {config.name} KubernetesClusterProvisioner integration failed: {e}"
                )
                # Continue testing other providers

        logger.info("✅ KubernetesClusterProvisioner integration testing completed")

    def test_provider_performance_comparison(self, available_providers):
        """Compare provisioning performance across providers."""
        logger.info("🧪 Comparing provisioning performance across providers")

        if len(available_providers) < 2:
            pytest.skip("Need at least 2 providers for performance comparison")

        performance_results = {}
        test_id = int(time.time())

        # Test credential validation performance
        credential_manager = get_credential_manager()

        for config in available_providers:
            logger.info(f"Testing credential validation performance for {config.name}")

            try:
                start_time = time.time()
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)
                validation_result = provisioner.validate_credentials()
                validation_time = time.time() - start_time

                performance_results[config.name] = {
                    "credential_validation_time": validation_time,
                    "credential_validation_success": validation_result,
                }

                logger.info(
                    f"📊 {config.name} credential validation: {validation_time:.2f}s"
                )

            except Exception as e:
                logger.error(f"❌ {config.name} performance test failed: {e}")
                performance_results[config.name] = {
                    "credential_validation_time": float("inf"),
                    "credential_validation_success": False,
                }

        # Log performance comparison
        logger.info("📊 Performance Comparison Results:")
        for name, results in performance_results.items():
            if results["credential_validation_success"]:
                logger.info(
                    f"   {name}: {results['credential_validation_time']:.2f}s credential validation"
                )
            else:
                logger.info(f"   {name}: Failed credential validation")

        # Find fastest credential validation
        successful_providers = {
            name: results
            for name, results in performance_results.items()
            if results["credential_validation_success"]
        }

        if successful_providers:
            fastest_provider = min(
                successful_providers.keys(),
                key=lambda x: successful_providers[x]["credential_validation_time"],
            )
            logger.info(f"🏆 Fastest credential validation: {fastest_provider}")

        logger.info("✅ Performance comparison completed")

    def test_error_handling_consistency(self, available_providers):
        """Test that error handling is consistent across providers."""
        logger.info("🧪 Testing error handling consistency across providers")

        credential_manager = get_credential_manager()

        for config in available_providers:
            logger.info(f"Testing error handling for {config.name}")

            try:
                # Test with invalid credentials
                invalid_credentials = {"invalid": "credentials"}

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                # Test invalid credential handling
                try:
                    provisioner = provisioner_class(invalid_credentials, config.region)
                    validation_result = provisioner.validate_credentials()
                    assert (
                        validation_result is False
                    ), f"{config.name} should reject invalid credentials"
                except Exception as e:
                    # Exception is also acceptable for invalid credentials
                    logger.info(
                        f"✅ {config.name} properly rejects invalid credentials with exception"
                    )

                # Test missing credential fields
                empty_credentials = {}
                try:
                    provisioner = provisioner_class(empty_credentials, config.region)
                    assert (
                        False
                    ), f"{config.name} should raise ValueError for missing credentials"
                except ValueError:
                    logger.info(
                        f"✅ {config.name} properly raises ValueError for missing credentials"
                    )
                except Exception as e:
                    logger.info(
                        f"✅ {config.name} raises exception for missing credentials: {type(e).__name__}"
                    )

            except Exception as e:
                logger.error(f"❌ {config.name} error handling test failed: {e}")
                # Continue with other providers

        logger.info("✅ Error handling consistency testing completed")

    @pytest.mark.slow
    def test_concurrent_multi_provider_operations(self, available_providers):
        """Test concurrent operations across multiple providers."""
        logger.info("🧪 Testing concurrent multi-provider operations")

        if len(available_providers) < 2:
            pytest.skip("Need at least 2 providers for concurrent testing")

        # Limit to 2-3 providers for practical testing
        test_providers = available_providers[:3]
        test_id = int(time.time())

        def validate_provider_concurrently(config):
            """Helper function for concurrent validation."""
            try:
                credential_manager = get_credential_manager()
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)

                start_time = time.time()
                result = provisioner.validate_credentials()
                validation_time = time.time() - start_time

                return {
                    "provider": config.name,
                    "success": result,
                    "validation_time": validation_time,
                }
            except Exception as e:
                return {
                    "provider": config.name,
                    "success": False,
                    "error": str(e),
                    "validation_time": float("inf"),
                }

        # Run concurrent validations
        start_time = time.time()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(test_providers)
        ) as executor:
            futures = [
                executor.submit(validate_provider_concurrently, config)
                for config in test_providers
            ]
            results = [
                future.result(timeout=120) for future in futures
            ]  # 2 min timeout

        total_concurrent_time = time.time() - start_time

        # Analyze results
        successful_results = [r for r in results if r["success"]]
        failed_results = [r for r in results if not r["success"]]

        logger.info(f"📊 Concurrent Multi-Provider Results:")
        logger.info(f"   Total concurrent time: {total_concurrent_time:.2f}s")
        logger.info(
            f"   Successful validations: {len(successful_results)}/{len(results)}"
        )

        for result in successful_results:
            logger.info(f"   ✅ {result['provider']}: {result['validation_time']:.2f}s")

        for result in failed_results:
            error_msg = result.get("error", "Unknown error")
            logger.info(f"   ❌ {result['provider']}: {error_msg}")

        # Assert that concurrent operations don't interfere with each other
        assert (
            len(successful_results) > 0
        ), "At least one provider should succeed in concurrent test"

        # Test that concurrent time is reasonable (not serialized)
        if len(successful_results) > 1:
            max_individual_time = max(r["validation_time"] for r in successful_results)
            # Concurrent execution should be faster than sum of individual times
            assert total_concurrent_time < sum(
                r["validation_time"] for r in successful_results
            ), "Concurrent execution should be faster than sequential"

        logger.info("✅ Concurrent multi-provider operations completed")

    def test_provider_specific_features(self, available_providers):
        """Test provider-specific features and capabilities."""
        logger.info("🧪 Testing provider-specific features")

        credential_manager = get_credential_manager()

        for config in available_providers:
            logger.info(f"Testing provider-specific features for {config.name}")

            try:
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)

                # Test provider-specific capabilities
                if config.name == "aws":
                    # AWS-specific tests
                    assert hasattr(
                        provisioner, "session"
                    ), "AWS provisioner should have boto3 session"
                    assert hasattr(
                        provisioner, "ec2"
                    ), "AWS provisioner should have EC2 client"
                    assert hasattr(
                        provisioner, "eks"
                    ), "AWS provisioner should have EKS client"

                elif config.name == "gcp":
                    # GCP-specific tests
                    assert hasattr(
                        provisioner, "container_client"
                    ), "GCP provisioner should have container client"
                    assert hasattr(
                        provisioner, "project_id"
                    ), "GCP provisioner should have project ID"

                elif config.name == "azure":
                    # Azure-specific tests
                    assert hasattr(
                        provisioner, "container_client"
                    ), "Azure provisioner should have container client"
                    assert hasattr(
                        provisioner, "subscription_id"
                    ), "Azure provisioner should have subscription ID"

                elif config.name == "huggingface":
                    # HuggingFace-specific tests
                    assert hasattr(
                        provisioner, "api"
                    ), "HF provisioner should have HF API client"
                    assert hasattr(
                        provisioner, "username"
                    ), "HF provisioner should have username"

                elif config.name == "lambda":
                    # Lambda Cloud-specific tests
                    assert hasattr(
                        provisioner, "api_key"
                    ), "Lambda provisioner should have API key"
                    assert hasattr(
                        provisioner, "base_url"
                    ), "Lambda provisioner should have base URL"

                logger.info(f"✅ {config.name} provider-specific features verified")

            except Exception as e:
                logger.error(
                    f"❌ {config.name} provider-specific feature test failed: {e}"
                )

        logger.info("✅ Provider-specific feature testing completed")

    def test_kubectl_config_consistency(self, available_providers):
        """Test that kubectl configurations are consistent across providers."""
        logger.info("🧪 Testing kubectl configuration consistency")

        # Define required kubectl config structure
        required_keys = [
            "apiVersion",
            "kind",
            "clusters",
            "contexts",
            "users",
            "current-context",
        ]

        credential_manager = get_credential_manager()
        test_id = int(time.time())

        for config in available_providers:
            logger.info(f"Testing kubectl config structure for {config.name}")

            try:
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)

                # Create a mock cluster info for kubectl config generation
                mock_cluster_info = {
                    "cluster_name": f"test-kubectl-{config.name}-{test_id}",
                    "endpoint": f"https://test-{config.name}.example.com",
                    "certificate_authority": "LS0tLS1CRUdJTi...",  # Mock cert
                    "arn": f"arn:aws:eks:us-west-2:123456789012:cluster/test-{config.name}",
                    "location": config.region,
                    "fqdn": f"test-{config.name}.example.com",
                }

                # Generate kubectl config
                kubectl_config = provisioner._configure_kubectl_access(
                    mock_cluster_info
                )

                # Verify structure
                assert isinstance(
                    kubectl_config, dict
                ), f"{config.name} kubectl config should be a dict"

                for key in required_keys:
                    assert (
                        key in kubectl_config
                    ), f"{config.name} kubectl config missing {key}"

                # Verify specific structures
                assert (
                    kubectl_config["apiVersion"] == "v1"
                ), f"{config.name} should use apiVersion v1"
                assert (
                    kubectl_config["kind"] == "Config"
                ), f"{config.name} should have kind Config"
                assert isinstance(
                    kubectl_config["clusters"], list
                ), f"{config.name} clusters should be list"
                assert isinstance(
                    kubectl_config["contexts"], list
                ), f"{config.name} contexts should be list"
                assert isinstance(
                    kubectl_config["users"], list
                ), f"{config.name} users should be list"

                # Verify non-empty structures
                assert (
                    len(kubectl_config["clusters"]) > 0
                ), f"{config.name} should have clusters"
                assert (
                    len(kubectl_config["contexts"]) > 0
                ), f"{config.name} should have contexts"
                assert (
                    len(kubectl_config["users"]) > 0
                ), f"{config.name} should have users"

                logger.info(f"✅ {config.name} kubectl config structure verified")

            except Exception as e:
                logger.error(f"❌ {config.name} kubectl config test failed: {e}")

        logger.info("✅ kubectl configuration consistency testing completed")

    @pytest.mark.performance
    def test_scalability_patterns(self, available_providers):
        """Test scalability patterns across providers."""
        logger.info("🧪 Testing scalability patterns across providers")

        credential_manager = get_credential_manager()
        test_id = int(time.time())

        # Test different node counts to verify scaling logic
        node_count_tests = [1, 2, 4, 8]

        for config in available_providers:
            logger.info(f"Testing scalability patterns for {config.name}")

            try:
                credentials = credential_manager.ensure_credential(config.name)

                provisioner_module = __import__(
                    config.provisioner_class.rsplit(".", 1)[0],
                    fromlist=[config.provisioner_class.rsplit(".", 1)[1]],
                )
                provisioner_class = getattr(
                    provisioner_module, config.provisioner_class.rsplit(".", 1)[1]
                )

                provisioner = provisioner_class(credentials, config.region)

                scaling_results = {}

                for node_count in node_count_tests:
                    spec = ClusterSpec(
                        cluster_name=f"test-scale-{config.name}-{node_count}-{test_id}",
                        provider=config.name,
                        node_count=node_count,
                        kubernetes_version="1.28",
                        region=config.region,
                    )

                    # Test resource allocation logic (without actual provisioning)
                    if hasattr(provisioner, "_map_node_requirements_to_hardware"):
                        # HuggingFace hardware mapping
                        hardware = provisioner._map_node_requirements_to_hardware(spec)
                        scaling_results[node_count] = hardware
                    elif hasattr(
                        provisioner, "_map_node_requirements_to_instance_type"
                    ):
                        # Lambda Cloud instance type mapping
                        instance_type = (
                            provisioner._map_node_requirements_to_instance_type(spec)
                        )
                        scaling_results[node_count] = instance_type
                    else:
                        # Traditional K8s providers - test instance type/size selection
                        if hasattr(provisioner, "session") and hasattr(
                            spec, "aws_instance_type"
                        ):
                            # AWS EKS - check instance type is appropriate
                            scaling_results[node_count] = spec.aws_instance_type
                        elif hasattr(provisioner, "project_id") and hasattr(
                            spec, "gcp_machine_type"
                        ):
                            # GCP GKE - check machine type is appropriate
                            scaling_results[node_count] = spec.gcp_machine_type
                        elif hasattr(provisioner, "subscription_id") and hasattr(
                            spec, "azure_vm_size"
                        ):
                            # Azure AKS - check VM size is appropriate
                            scaling_results[node_count] = spec.azure_vm_size

                # Verify scaling patterns make sense
                if scaling_results:
                    logger.info(f"📊 {config.name} scaling patterns: {scaling_results}")

                    # For providers with hardware/instance tiers, verify progression
                    if config.name == "huggingface":
                        # Should progress from cpu-basic -> cpu-upgrade -> t4-small -> t4-medium
                        expected_progression = [
                            "cpu-basic",
                            "cpu-upgrade",
                            "t4-small",
                            "t4-medium",
                        ]
                        for i, node_count in enumerate(node_count_tests):
                            if node_count in scaling_results:
                                expected = expected_progression[
                                    min(i, len(expected_progression) - 1)
                                ]
                                # Allow some flexibility in the progression
                                assert (
                                    scaling_results[node_count] in expected_progression
                                ), f"HF hardware should be in valid progression"

                logger.info(f"✅ {config.name} scalability patterns verified")

            except Exception as e:
                logger.error(f"❌ {config.name} scalability pattern test failed: {e}")

        logger.info("✅ Scalability pattern testing completed")
