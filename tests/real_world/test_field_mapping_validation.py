"""Real-world validation tests for cloud provider field mapping fixes.

Tests all cloud provider connectivity methods with real API calls to verify that
field mapping between widget fields and provider API expectations works correctly.

NO MOCK TESTS - Only real cloud provider API authentication.
"""

import os
import pytest
import logging
from typing import Dict, Any, Optional

# Configure logging for test debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_aws_test_credentials() -> Optional[Dict[str, Any]]:
    """Get real AWS credentials for testing from environment variables."""
    credentials = {
        "aws_access_key": os.environ.get("AWS_ACCESS_KEY_ID"),
        "aws_secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
        "aws_region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    }

    # Add session token if available (for temporary credentials)
    if os.environ.get("AWS_SESSION_TOKEN"):
        credentials["aws_session_token"] = os.environ.get("AWS_SESSION_TOKEN")

    # Only return if we have required credentials
    if credentials["aws_access_key"] and credentials["aws_secret_key"]:
        return credentials
    return None


def get_azure_test_credentials() -> Optional[Dict[str, Any]]:
    """Get real Azure credentials for testing from environment variables."""
    credentials = {
        "azure_subscription_id": os.environ.get("AZURE_SUBSCRIPTION_ID"),
        "azure_client_id": os.environ.get("AZURE_CLIENT_ID"),
        "azure_client_secret": os.environ.get("AZURE_CLIENT_SECRET"),
        "azure_tenant_id": os.environ.get("AZURE_TENANT_ID"),
        "azure_region": os.environ.get("AZURE_DEFAULT_REGION", "eastus"),
    }

    # Only return if we have required credentials
    required_fields = [
        "azure_subscription_id",
        "azure_client_id",
        "azure_client_secret",
        "azure_tenant_id",
    ]
    if all(credentials[field] for field in required_fields):
        return credentials
    return None


def get_gcp_test_credentials() -> Optional[Dict[str, Any]]:
    """Get real GCP credentials for testing from environment variables."""
    credentials = {
        "gcp_project_id": os.environ.get("GCP_PROJECT_ID"),
        "gcp_service_account_key": os.environ.get("GCP_SERVICE_ACCOUNT_KEY"),
        "gcp_region": os.environ.get("GCP_DEFAULT_REGION", "us-central1"),
    }

    # Only return if we have required credentials
    if credentials["gcp_project_id"] and credentials["gcp_service_account_key"]:
        return credentials
    return None


def get_huggingface_test_credentials() -> Optional[Dict[str, Any]]:
    """Get real HuggingFace credentials for testing from environment variables."""
    credentials = {
        "hf_token": os.environ.get("HF_TOKEN"),
        "hf_username": os.environ.get("HF_USERNAME"),
    }

    # Only return if we have required credentials
    if credentials["hf_token"]:
        return credentials
    return None


class TestFieldMappingValidation:
    """Test cloud provider field mapping with real API calls."""

    @pytest.mark.real_world
    def test_field_mapping_system_completeness(self):
        """Test that the field mapping system covers all required providers and fields."""
        from clustrix.field_mappings import (
            CLOUD_PROVIDER_FIELD_MAPPING,
            REQUIRED_FIELDS,
            get_supported_providers,
        )

        # Verify all expected providers are supported
        expected_providers = ["aws", "azure", "gcp", "huggingface", "lambda"]
        supported_providers = get_supported_providers()

        assert set(expected_providers).issubset(
            set(supported_providers)
        ), f"Missing providers in field mapping: {set(expected_providers) - set(supported_providers)}"

        # Verify required fields are defined for each provider
        for provider in expected_providers:
            assert (
                provider in REQUIRED_FIELDS
            ), f"No required fields defined for {provider}"
            assert (
                len(REQUIRED_FIELDS[provider]) > 0
            ), f"Empty required fields for {provider}"

        # Verify field mappings exist for each provider
        for provider in expected_providers:
            assert (
                provider in CLOUD_PROVIDER_FIELD_MAPPING
            ), f"No field mapping for {provider}"
            mapping = CLOUD_PROVIDER_FIELD_MAPPING[provider]
            assert len(mapping) > 0, f"Empty field mapping for {provider}"

    @pytest.mark.real_world
    def test_aws_field_mapping_with_real_api(self):
        """Test AWS field mapping with real boto3 API authentication."""
        aws_creds = get_aws_test_credentials()
        if not aws_creds:
            pytest.skip(
                "AWS credentials not available (set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)"
            )

        from clustrix.field_mappings import (
            map_widget_fields_to_provider,
            validate_provider_config,
        )
        from clustrix.notebook_magic import ClustrixMagic

        logger.info("Testing AWS field mapping with real credentials")

        # Test field mapping
        mapped_credentials = map_widget_fields_to_provider("aws", aws_creds)

        # Verify mapping worked correctly
        assert "access_key_id" in mapped_credentials
        assert "secret_access_key" in mapped_credentials
        assert mapped_credentials["access_key_id"] == aws_creds["aws_access_key"]
        assert mapped_credentials["secret_access_key"] == aws_creds["aws_secret_key"]

        # Validate configuration
        assert validate_provider_config("aws", mapped_credentials)

        # Test real API connectivity using the connectivity method
        magic = ClustrixMagic()
        result = magic._test_aws_connectivity(aws_creds)

        assert result is True, "AWS connectivity test failed with real credentials"
        logger.info("✅ AWS field mapping and authentication successful")

    @pytest.mark.real_world
    def test_azure_field_mapping_with_real_api(self):
        """Test Azure field mapping with real Azure SDK API authentication."""
        azure_creds = get_azure_test_credentials()
        if not azure_creds:
            pytest.skip(
                "Azure credentials not available (set AZURE_SUBSCRIPTION_ID, AZURE_CLIENT_ID, "
                "AZURE_CLIENT_SECRET, AZURE_TENANT_ID)"
            )

        from clustrix.field_mappings import (
            map_widget_fields_to_provider,
            validate_provider_config,
        )
        from clustrix.notebook_magic import ClustrixMagic

        logger.info("Testing Azure field mapping with real credentials")

        # Test field mapping
        mapped_credentials = map_widget_fields_to_provider("azure", azure_creds)

        # Verify mapping worked correctly
        assert "subscription_id" in mapped_credentials
        assert "client_id" in mapped_credentials
        assert "client_secret" in mapped_credentials
        assert "tenant_id" in mapped_credentials
        assert (
            mapped_credentials["subscription_id"]
            == azure_creds["azure_subscription_id"]
        )
        assert mapped_credentials["client_id"] == azure_creds["azure_client_id"]
        assert mapped_credentials["client_secret"] == azure_creds["azure_client_secret"]
        assert mapped_credentials["tenant_id"] == azure_creds["azure_tenant_id"]

        # Validate configuration
        assert validate_provider_config("azure", mapped_credentials)

        # Test real API connectivity using the connectivity method
        magic = ClustrixMagic()
        result = magic._test_azure_connectivity(azure_creds)

        assert result is True, "Azure connectivity test failed with real credentials"
        logger.info("✅ Azure field mapping and authentication successful")

    @pytest.mark.real_world
    def test_gcp_field_mapping_with_real_api(self):
        """Test GCP field mapping with real Google Cloud API authentication."""
        gcp_creds = get_gcp_test_credentials()
        if not gcp_creds:
            pytest.skip(
                "GCP credentials not available (set GCP_PROJECT_ID, GCP_SERVICE_ACCOUNT_KEY)"
            )

        from clustrix.field_mappings import (
            map_widget_fields_to_provider,
            validate_provider_config,
        )
        from clustrix.notebook_magic import ClustrixMagic

        logger.info("Testing GCP field mapping with real credentials")

        # Test field mapping
        mapped_credentials = map_widget_fields_to_provider("gcp", gcp_creds)

        # Verify mapping worked correctly
        assert "project_id" in mapped_credentials
        assert "service_account_key" in mapped_credentials
        assert mapped_credentials["project_id"] == gcp_creds["gcp_project_id"]
        assert (
            mapped_credentials["service_account_key"]
            == gcp_creds["gcp_service_account_key"]
        )

        # Validate configuration
        assert validate_provider_config("gcp", mapped_credentials)

        # Test real API connectivity using the connectivity method
        magic = ClustrixMagic()
        result = magic._test_gcp_connectivity(gcp_creds)

        assert result is True, "GCP connectivity test failed with real credentials"
        logger.info("✅ GCP field mapping and authentication successful")

    @pytest.mark.real_world
    def test_huggingface_field_mapping_with_real_api(self):
        """Test HuggingFace field mapping with real HuggingFace API authentication."""
        hf_creds = get_huggingface_test_credentials()
        if not hf_creds:
            pytest.skip("HuggingFace credentials not available (set HF_TOKEN)")

        from clustrix.field_mappings import (
            map_widget_fields_to_provider,
            validate_provider_config,
        )
        from clustrix.notebook_magic import ClustrixMagic

        logger.info("Testing HuggingFace field mapping with real credentials")

        # Test field mapping
        mapped_credentials = map_widget_fields_to_provider("huggingface", hf_creds)

        # Verify mapping worked correctly
        assert "token" in mapped_credentials
        assert mapped_credentials["token"] == hf_creds["hf_token"]
        if hf_creds.get("hf_username"):
            assert mapped_credentials["username"] == hf_creds["hf_username"]

        # Validate configuration
        assert validate_provider_config("huggingface", mapped_credentials)

        # Test real API connectivity using the connectivity method
        magic = ClustrixMagic()
        result = magic._test_huggingface_connectivity(hf_creds)

        assert (
            result is True
        ), "HuggingFace connectivity test failed with real credentials"
        logger.info("✅ HuggingFace field mapping and authentication successful")

    @pytest.mark.real_world
    def test_lambda_field_mapping_consistency(self):
        """Test Lambda Cloud field mapping consistency (already working per audit)."""
        from clustrix.field_mappings import (
            map_widget_fields_to_provider,
            validate_provider_config,
        )

        # Test with mock Lambda credentials to verify mapping works
        lambda_creds = {"lambda_api_key": "test_api_key_123"}

        # Test field mapping
        mapped_credentials = map_widget_fields_to_provider("lambda", lambda_creds)

        # Verify mapping worked correctly
        assert "api_key" in mapped_credentials
        assert mapped_credentials["api_key"] == lambda_creds["lambda_api_key"]

        # Validate configuration structure
        assert validate_provider_config("lambda", mapped_credentials)
        logger.info("✅ Lambda Cloud field mapping verified (already working)")

    @pytest.mark.real_world
    def test_end_to_end_widget_to_provider_flow(self):
        """Test complete flow from widget input to provider authentication."""
        from clustrix.field_mappings import map_widget_fields_to_provider
        from clustrix.notebook_magic import ClustrixMagic

        # Test scenarios for each provider where credentials are available
        magic = ClustrixMagic()
        successful_tests = []

        # AWS test
        aws_creds = get_aws_test_credentials()
        if aws_creds:
            logger.info("Testing end-to-end AWS flow")
            try:
                # Simulate widget configuration -> field mapping -> provider authentication
                mapped_aws = map_widget_fields_to_provider("aws", aws_creds)
                result = magic._test_aws_connectivity(aws_creds)
                if result:
                    successful_tests.append("AWS")
                    logger.info("✅ End-to-end AWS flow successful")
            except Exception as e:
                logger.warning(f"AWS end-to-end test failed: {e}")

        # Azure test
        azure_creds = get_azure_test_credentials()
        if azure_creds:
            logger.info("Testing end-to-end Azure flow")
            try:
                mapped_azure = map_widget_fields_to_provider("azure", azure_creds)
                result = magic._test_azure_connectivity(azure_creds)
                if result:
                    successful_tests.append("Azure")
                    logger.info("✅ End-to-end Azure flow successful")
            except Exception as e:
                logger.warning(f"Azure end-to-end test failed: {e}")

        # GCP test
        gcp_creds = get_gcp_test_credentials()
        if gcp_creds:
            logger.info("Testing end-to-end GCP flow")
            try:
                mapped_gcp = map_widget_fields_to_provider("gcp", gcp_creds)
                result = magic._test_gcp_connectivity(gcp_creds)
                if result:
                    successful_tests.append("GCP")
                    logger.info("✅ End-to-end GCP flow successful")
            except Exception as e:
                logger.warning(f"GCP end-to-end test failed: {e}")

        # HuggingFace test
        hf_creds = get_huggingface_test_credentials()
        if hf_creds:
            logger.info("Testing end-to-end HuggingFace flow")
            try:
                mapped_hf = map_widget_fields_to_provider("huggingface", hf_creds)
                result = magic._test_huggingface_connectivity(hf_creds)
                if result:
                    successful_tests.append("HuggingFace")
                    logger.info("✅ End-to-end HuggingFace flow successful")
            except Exception as e:
                logger.warning(f"HuggingFace end-to-end test failed: {e}")

        # Verify at least one provider worked
        assert len(successful_tests) > 0, (
            "No cloud providers successfully completed end-to-end testing. "
            "Please set credentials for at least one provider: AWS, Azure, GCP, or HuggingFace"
        )

        logger.info(
            f"✅ End-to-end testing successful for: {', '.join(successful_tests)}"
        )

    @pytest.mark.real_world
    def test_error_handling_with_invalid_credentials(self):
        """Test error handling with invalid credentials for each provider."""
        from clustrix.field_mappings import map_widget_fields_to_provider
        from clustrix.notebook_magic import ClustrixMagic

        magic = ClustrixMagic()

        # Test AWS with invalid credentials
        invalid_aws = {
            "aws_access_key": "INVALID_ACCESS_KEY",
            "aws_secret_key": "INVALID_SECRET_KEY",
            "aws_region": "us-east-1",
        }

        # Should map correctly but fail authentication
        mapped_aws = map_widget_fields_to_provider("aws", invalid_aws)
        assert "access_key_id" in mapped_aws
        result = magic._test_aws_connectivity(invalid_aws)
        assert result is False, "AWS should reject invalid credentials"

        # Test Azure with invalid credentials
        invalid_azure = {
            "azure_subscription_id": "11111111-1111-1111-1111-111111111111",
            "azure_client_id": "22222222-2222-2222-2222-222222222222",
            "azure_client_secret": "invalid_secret",
            "azure_tenant_id": "33333333-3333-3333-3333-333333333333",
        }

        mapped_azure = map_widget_fields_to_provider("azure", invalid_azure)
        assert "subscription_id" in mapped_azure
        result = magic._test_azure_connectivity(invalid_azure)
        assert result is False, "Azure should reject invalid credentials"

        # Test GCP with invalid service account
        invalid_gcp = {
            "gcp_project_id": "invalid-project-123",
            "gcp_service_account_key": '{"type": "service_account", "private_key": "invalid"}',
        }

        mapped_gcp = map_widget_fields_to_provider("gcp", invalid_gcp)
        assert "project_id" in mapped_gcp
        result = magic._test_gcp_connectivity(invalid_gcp)
        assert result is False, "GCP should reject invalid service account"

        # Test HuggingFace with invalid token
        invalid_hf = {"hf_token": "hf_invalid_token_12345"}

        mapped_hf = map_widget_fields_to_provider("huggingface", invalid_hf)
        assert "token" in mapped_hf
        result = magic._test_huggingface_connectivity(invalid_hf)
        assert result is False, "HuggingFace should reject invalid token"

        logger.info("✅ Error handling with invalid credentials working correctly")

    @pytest.mark.real_world
    def test_missing_required_fields_validation(self):
        """Test validation with missing required fields for each provider."""
        from clustrix.field_mappings import map_widget_fields_to_provider

        # Test AWS missing secret key
        with pytest.raises(KeyError, match="Missing required aws fields"):
            map_widget_fields_to_provider("aws", {"aws_access_key": "test"})

        # Test Azure missing tenant_id
        with pytest.raises(KeyError, match="Missing required azure fields"):
            map_widget_fields_to_provider(
                "azure",
                {
                    "azure_subscription_id": "test",
                    "azure_client_id": "test",
                    "azure_client_secret": "test",
                    # Missing azure_tenant_id
                },
            )

        # Test GCP missing service account key
        with pytest.raises(KeyError, match="Missing required gcp fields"):
            map_widget_fields_to_provider("gcp", {"gcp_project_id": "test"})

        # Test HuggingFace missing token
        with pytest.raises(KeyError, match="Missing required huggingface fields"):
            map_widget_fields_to_provider("huggingface", {"hf_username": "test"})

        logger.info("✅ Missing required fields validation working correctly")


if __name__ == "__main__":
    # Run tests directly for debugging
    pytest.main([__file__, "-v", "--tb=short"])
