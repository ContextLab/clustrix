"""Field mapping system for cloud provider configuration compatibility.

This module provides standardized field mapping between widget field names,
ClusterConfig field names, and cloud provider API expectations.
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


# Comprehensive field mapping for all cloud providers
CLOUD_PROVIDER_FIELD_MAPPING = {
    "aws": {
        # Authentication fields
        "aws_access_key": "access_key_id",
        "aws_access_key_id": "access_key_id",  # ClusterConfig compatibility
        "aws_secret_key": "secret_access_key",
        "aws_secret_access_key": "secret_access_key",  # ClusterConfig compatibility
        "aws_session_token": "session_token",
        "aws_region": "region",
        "aws_profile": "profile",
        # Infrastructure fields
        "aws_instance_type": "instance_type",
        "aws_cluster_type": "cluster_type",
        "eks_cluster_name": "eks_cluster_name",
    },
    "azure": {
        # Authentication fields
        "azure_subscription_id": "subscription_id",
        "azure_client_id": "client_id",
        "azure_client_secret": "client_secret",
        "azure_tenant_id": "tenant_id",
        "azure_region": "region",
        "azure_resource_group": "resource_group",
        # Infrastructure fields
        "azure_instance_type": "instance_type",
        "aks_cluster_name": "aks_cluster_name",
    },
    "gcp": {
        # Authentication fields
        "gcp_project_id": "project_id",
        "gcp_service_account_key": "service_account_key",
        "gcp_zone": "zone",
        "gcp_region": "region",
        # Infrastructure fields
        "gcp_instance_type": "instance_type",
        "gke_cluster_name": "gke_cluster_name",
    },
    "huggingface": {
        # Authentication fields
        "hf_token": "token",
        "hf_username": "username",
        # Infrastructure fields
        "hf_hardware": "hardware",
        "hf_sdk": "sdk",
    },
    "lambda": {
        # Authentication fields
        "lambda_api_key": "api_key",
        # Infrastructure fields
        "lambda_instance_type": "instance_type",
    },
}

# Required fields for each provider (for validation)
REQUIRED_FIELDS = {
    "aws": ["access_key_id", "secret_access_key"],
    "azure": ["subscription_id", "client_id", "client_secret", "tenant_id"],
    "gcp": ["project_id", "service_account_key"],
    "huggingface": ["token"],
    "lambda": ["api_key"],
}

# Optional fields with default values
OPTIONAL_FIELD_DEFAULTS = {
    "aws": {
        "region": "us-east-1",
    },
    "azure": {
        "region": "eastus",
        "resource_group": "clustrix-rg",
    },
    "gcp": {
        "region": "us-central1",
    },
    "huggingface": {},
    "lambda": {},
}


def map_widget_fields_to_provider(
    provider: str, widget_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Map widget field names to cloud provider API field names.

    Args:
        provider: Cloud provider name (aws, azure, gcp, huggingface, lambda)
        widget_config: Configuration dictionary with widget field names

    Returns:
        Dictionary with provider API field names

    Raises:
        ValueError: If provider is not supported
        KeyError: If required fields are missing
    """
    if provider not in CLOUD_PROVIDER_FIELD_MAPPING:
        raise ValueError(f"Unsupported cloud provider: {provider}")

    mapping = CLOUD_PROVIDER_FIELD_MAPPING[provider]
    provider_config = {}

    # Map fields from widget names to provider names
    for widget_field, provider_field in mapping.items():
        if widget_field in widget_config and widget_config[widget_field]:
            provider_config[provider_field] = widget_config[widget_field]
            logger.debug(
                f"Mapped {widget_field} -> {provider_field}: "
                f"{widget_config[widget_field]}"
            )

    # Add optional defaults for missing fields
    defaults = OPTIONAL_FIELD_DEFAULTS.get(provider, {})
    if defaults:
        for field, default_value in defaults.items():
            if field not in provider_config:
                provider_config[field] = default_value
                logger.debug(f"Added default {field}: {default_value}")

    # Validate required fields are present
    required = REQUIRED_FIELDS.get(provider, [])
    missing_fields = [field for field in required if field not in provider_config]

    if missing_fields:
        logger.error(f"Missing required fields for {provider}: {missing_fields}")
        raise KeyError(f"Missing required {provider} fields: {missing_fields}")

    logger.info(f"Successfully mapped {len(provider_config)} fields for {provider}")
    return provider_config


def validate_provider_config(provider: str, config: Dict[str, Any]) -> bool:
    """
    Validate that a provider configuration has all required fields.

    Args:
        provider: Cloud provider name
        config: Configuration dictionary to validate

    Returns:
        True if configuration is valid, False otherwise
    """
    try:
        required = REQUIRED_FIELDS.get(provider, [])
        missing = [field for field in required if not config.get(field)]

        if missing:
            logger.warning(
                f"Provider {provider} config missing required fields: {missing}"
            )
            return False

        logger.info(f"Provider {provider} configuration is valid")
        return True

    except Exception as e:
        logger.error(f"Error validating {provider} config: {e}")
        return False


def get_widget_field_for_provider_field(
    provider: str, provider_field: str
) -> Optional[str]:
    """
    Get the widget field name that maps to a provider field.

    Args:
        provider: Cloud provider name
        provider_field: Provider API field name

    Returns:
        Widget field name or None if not found
    """
    if provider not in CLOUD_PROVIDER_FIELD_MAPPING:
        return None

    mapping = CLOUD_PROVIDER_FIELD_MAPPING[provider]

    # Reverse lookup: find widget field that maps to this provider field
    for widget_field, mapped_provider_field in mapping.items():
        if mapped_provider_field == provider_field:
            return widget_field

    return None


def get_all_provider_fields(provider: str) -> Dict[str, str]:
    """
    Get all field mappings for a provider.

    Args:
        provider: Cloud provider name

    Returns:
        Dictionary mapping widget fields to provider fields
    """
    return CLOUD_PROVIDER_FIELD_MAPPING.get(provider, {}).copy()


def get_supported_providers() -> list:
    """Get list of all supported cloud providers."""
    return list(CLOUD_PROVIDER_FIELD_MAPPING.keys())


# Convenience functions for common use cases
def map_aws_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Map AWS widget fields to boto3 field names."""
    return map_widget_fields_to_provider("aws", config)


def map_azure_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Map Azure widget fields to Azure SDK field names."""
    return map_widget_fields_to_provider("azure", config)


def map_gcp_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Map GCP widget fields to Google Cloud SDK field names."""
    return map_widget_fields_to_provider("gcp", config)


def map_huggingface_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Map HuggingFace widget fields to HuggingFace API field names."""
    return map_widget_fields_to_provider("huggingface", config)


def map_lambda_fields(config: Dict[str, Any]) -> Dict[str, Any]:
    """Map Lambda Cloud widget fields to Lambda API field names."""
    return map_widget_fields_to_provider("lambda", config)
