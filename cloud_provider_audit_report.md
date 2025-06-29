# Cloud Provider Configuration Audit Report

## Executive Summary

This report identifies field name mismatches, validation issues, and inconsistencies across all cloud providers in the Clustrix widget system. Similar to the Lambda Cloud issue that was found earlier, several providers have discrepancies between the widget field names, ClusterConfig field names, and the actual cloud provider implementation expectations.

## Key Findings

### 1. AWS Provider Issues

**Field Name Mismatches:**
- **Widget uses:** `aws_access_key` and `aws_secret_key` 
- **ClusterConfig has:** Both `aws_access_key_id`/`aws_secret_access_key` (standard AWS naming) AND `aws_access_key`/`aws_secret_key` (widget naming)
- **Provider expects:** `access_key_id` and `secret_access_key` (standard boto3 naming)

**Validation Issues:**
- The `_test_aws_connectivity()` method uses `config.get("aws_profile")` but the widget has no profile field
- Test method expects credentials from config but doesn't map widget field names correctly
- Missing validation for required fields like `aws_access_key_id` vs `aws_access_key`

**DEFAULT_CONFIGS Issues:**
- Uses `aws_region`, `aws_instance_type`, `aws_cluster_type` - matches widget
- Missing any credential field examples in defaults

### 2. Azure Provider Issues

**Field Name Mismatches:**
- **Widget uses:** `azure_subscription_id`, `azure_client_id`, `azure_client_secret`
- **ClusterConfig has:** Same names - GOOD
- **Provider expects:** `subscription_id`, `client_id`, `client_secret` (without azure_ prefix)

**Validation Issues:**
- The `_test_azure_connectivity()` method tries to use `DefaultAzureCredential()` but should use the widget-provided credentials
- Test method doesn't properly map `azure_subscription_id` -> `subscription_id` etc.
- Missing `tenant_id` in widget (provider requires it)

**DEFAULT_CONFIGS Issues:**
- Uses `azure_region`, `azure_instance_type` - matches widget and config
- Missing credential fields in defaults

### 3. GCP Provider Issues

**Field Name Mismatches:**
- **Widget uses:** `gcp_project_id`, `gcp_region`, `gcp_instance_type`, `gcp_service_account_key`
- **ClusterConfig has:** Same names - GOOD
- **Provider expects:** `project_id`, `service_account_key`, `region` (without gcp_ prefix)

**Validation Issues:**
- The `_test_gcp_connectivity()` method tries to use default credentials instead of widget-provided service account key
- No proper validation of service account JSON format in widget
- Test method doesn't map field names correctly

**DEFAULT_CONFIGS Issues:**
- Uses `gcp_region`, `gcp_instance_type` - matches widget
- Missing `gcp_project_id` and credentials in defaults

### 4. Lambda Cloud Provider Issues

**Field Name Mismatches:**
- **Widget uses:** `lambda_api_key`, `lambda_instance_type`
- **ClusterConfig has:** Same names - GOOD
- **Provider expects:** `api_key` (without lambda_ prefix)

**Validation Issues:**
- The `_test_lambda_connectivity()` method correctly maps `lambda_api_key` to `api_key` - GOOD
- This is the one provider that was fixed!

**DEFAULT_CONFIGS Issues:**
- Uses `lambda_instance_type` - matches widget and config
- Missing `lambda_api_key` in defaults (expected for security)

### 5. HuggingFace Provider Issues

**Field Name Mismatches:**
- **Widget uses:** `hf_token`, `hf_username`, `hf_hardware`, `hf_sdk`
- **ClusterConfig has:** Same names - GOOD
- **Provider expects:** `token`, `username` (without hf_ prefix)

**Validation Issues:**
- No test connectivity method implemented in widget for HuggingFace
- Provider expects credentials with different names than ClusterConfig

**DEFAULT_CONFIGS Issues:**
- Uses `hf_hardware`, `hf_sdk` - matches widget and config
- Missing credential fields in defaults

## Detailed Analysis

### Widget Test Configuration Methods

The widget has test connectivity methods that attempt to validate cloud provider configurations:

1. `_test_aws_connectivity()` - Uses wrong credential field names
2. `_test_azure_connectivity()` - Uses DefaultAzureCredential instead of provided credentials
3. `_test_gcp_connectivity()` - Uses default credentials instead of service account key
4. `_test_lambda_connectivity()` - Works correctly (recently fixed)
5. `_test_huggingface_connectivity()` - Not implemented

### ClusterConfig Field Mapping Issues

The ClusterConfig class tries to support both standard and widget naming:
- AWS: Has both `aws_access_key_id` and `aws_access_key` fields
- Other providers: Only have widget-style naming

### Provider Implementation Expectations

Each provider's `authenticate()` method expects specific field names:
- **AWS:** `access_key_id`, `secret_access_key`, `region`, `session_token`
- **Azure:** `subscription_id`, `client_id`, `client_secret`, `tenant_id`, `region`, `resource_group`
- **GCP:** `project_id`, `service_account_key`, `region`
- **Lambda:** `api_key`
- **HuggingFace:** `token`, `username`

## Recommended Fixes

### 1. Standardize Field Name Mapping

Create a consistent mapping system between widget fields, ClusterConfig fields, and provider expectations:

```python
PROVIDER_FIELD_MAPPING = {
    "aws": {
        "aws_access_key": "access_key_id",
        "aws_secret_key": "secret_access_key", 
        "aws_region": "region"
    },
    "azure": {
        "azure_subscription_id": "subscription_id",
        "azure_client_id": "client_id",
        "azure_client_secret": "client_secret",
        "azure_region": "region"
    },
    "gcp": {
        "gcp_project_id": "project_id",
        "gcp_service_account_key": "service_account_key",
        "gcp_region": "region"
    },
    "lambda": {
        "lambda_api_key": "api_key"
    },
    "huggingface": {
        "hf_token": "token",
        "hf_username": "username"
    }
}
```

### 2. Fix Test Connectivity Methods

Update each `_test_*_connectivity()` method to:
1. Use provided credentials from widget fields
2. Map field names correctly to provider expectations
3. Handle authentication errors properly

### 3. Add Missing Required Fields

- **Azure:** Add `azure_tenant_id` field to widget
- **AWS:** Consider adding `aws_session_token` for temporary credentials
- **All:** Add validation for required vs optional fields

### 4. Implement Missing Test Methods

- Add `_test_huggingface_connectivity()` method
- Ensure all test methods are actually called from `_on_test_config()`

### 5. Update Configuration Saving/Loading

Ensure the `_save_config_from_widgets()` method properly maps field names when saving configurations.

## Critical Issues

1. **Security Risk:** Some test methods may fail silently, giving users false confidence in invalid configurations
2. **User Experience:** Configuration that appears to save successfully may fail at runtime due to field name mismatches
3. **Inconsistency:** Each provider handles field mapping differently, making the system unpredictable

## Priority Recommendations

1. **High Priority:** Fix AWS, Azure, and GCP test connectivity methods
2. **Medium Priority:** Implement HuggingFace test connectivity
3. **Medium Priority:** Add missing required fields (azure_tenant_id)
4. **Low Priority:** Standardize field naming across all providers

This audit reveals that the Lambda Cloud fix was just the tip of the iceberg - similar issues exist across all cloud providers and need systematic resolution.