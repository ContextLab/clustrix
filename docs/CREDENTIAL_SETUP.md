# Credential Setup for Real-World Testing

This guide explains how to set up credentials for Clustrix real-world testing, supporting both local development (with 1Password) and GitHub Actions (with repository secrets).

## Overview

The credential system supports two modes:
- **Local Development**: Uses 1Password CLI for secure credential storage
- **GitHub Actions**: Uses repository secrets for CI/CD workflows

## Local Development Setup

### 1. Install 1Password CLI

**macOS:**
```bash
brew install --cask 1password-cli
```

**Linux:**
```bash
# Download from https://developer.1password.com/docs/cli/get-started/
curl -sS https://downloads.1password.com/linux/keys/1password.asc | gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main' | tee /etc/apt/sources.list.d/1password.list
apt update && apt install 1password-cli
```

**Windows:**
Download from https://developer.1password.com/docs/cli/get-started/

### 2. Configure 1Password CLI

```bash
# Sign in to 1Password
op signin

# Verify authentication
op account list
```

### 3. Create 1Password Items

Create the following items in your 1Password vault (use "Private" vault or create a dedicated "clustrix-dev" vault):

#### AWS Credentials (`clustrix-aws-validation`)
- **Type**: Login or API Credential
- **Fields**:
  - `aws_access_key_id`: Your AWS Access Key ID
  - `aws_secret_access_key`: Your AWS Secret Access Key
  - `aws_region`: AWS region (e.g., us-east-1)

#### GCP Credentials (`clustrix-gcp-validation`)
- **Type**: Login or API Credential
- **Fields**:
  - `project_id`: Your GCP Project ID
  - `service_account_json`: Full service account JSON key content
  - `region`: GCP region (e.g., us-central1)

#### Azure Credentials (`clustrix-azure-validation`)
- **Type**: Login or API Credential
- **Fields**:
  - `subscription_id`: Azure subscription ID
  - `tenant_id`: Azure tenant ID
  - `client_id`: Azure client ID
  - `client_secret`: Azure client secret

#### SSH Credentials (`clustrix-ssh-validation`)
- **Type**: Login or Server
- **Fields**:
  - `hostname`: SSH server hostname or IP
  - `username`: SSH username
  - `private_key`: SSH private key (PEM format)
  - `password`: SSH password (optional if using key)
  - `port`: SSH port (default: 22)

#### SLURM Credentials (`clustrix-slurm-validation`)
- **Type**: Login or Server
- **Fields**:
  - `hostname`: SLURM cluster hostname
  - `username`: SLURM username
  - `password`: SLURM password
  - `port`: SSH port (default: 22)

#### HuggingFace Credentials (`clustrix-huggingface-validation`)
- **Type**: Login or API Credential
- **Fields**:
  - `token`: HuggingFace API token
  - `username`: HuggingFace username

#### Lambda Cloud Credentials (`clustrix-lambda-cloud-validation`)
- **Type**: Login or API Credential
- **Fields**:
  - `api_key`: Lambda Cloud API key
  - `endpoint`: API endpoint (default: https://cloud.lambdalabs.com/api/v1)

### 4. Test Local Setup

```bash
# Test 1Password integration
python scripts/test_real_world_credentials.py

# Check credential status
python scripts/run_real_world_tests.py --check-creds

# Test specific credential access
python scripts/test_credential_access.py
```

## GitHub Actions Setup

### 1. Repository Secrets

Add the following secrets to your GitHub repository (`Settings → Secrets and variables → Actions`):

#### Required Secrets
- `CLUSTRIX_USERNAME`: Username for SSH and SLURM servers
- `CLUSTRIX_PASSWORD`: Password for SSH and SLURM servers
- `LAMBDA_CLOUD_API_KEY`: Lambda Cloud API key

#### Optional Secrets (for expanded testing)
- `AWS_ACCESS_KEY_ID`: AWS access key ID
- `AWS_ACCESS_KEY`: AWS secret access key
- `AZURE_SUBSCRIPTION_ID`: Azure subscription ID
- `AZURE_TENANT_ID`: Azure tenant ID
- `AZURE_CLIENT_ID`: Azure client ID
- `AZURE_CLIENT_SECRET`: Azure client secret
- `GCP_PROJECT_ID`: GCP project ID
- `GCP_JSON`: GCP service account JSON content
- `HF_USERNAME`: HuggingFace username
- `HF_TOKEN`: HuggingFace API token

### 2. Workflow Configuration

The GitHub Actions workflow (`.github/workflows/real-world-tests.yml`) automatically:
- Sets up SSH server for testing
- Uses repository secrets for authentication
- Runs tests with appropriate credentials
- Uploads test artifacts

### 3. Test GitHub Actions Locally

```bash
# Simulate GitHub Actions environment
export GITHUB_ACTIONS=true
export CLUSTRIX_USERNAME="your-username"
export CLUSTRIX_PASSWORD="your-password"
export LAMBDA_CLOUD_API_KEY="your-api-key"
export GCP_PROJECT_ID="your-gcp-project"
export GCP_JSON='{"type": "service_account", ...}'
export AWS_ACCESS_KEY_ID="your-aws-key-id"
export AWS_ACCESS_KEY="your-aws-secret"
export HF_USERNAME="your-hf-username"
export HF_TOKEN="your-hf-token"

# Run tests
python scripts/run_real_world_tests.py --check-creds
```

## Environment Variable Fallback

If neither 1Password nor GitHub Actions is available, the system falls back to environment variables:

```bash
# AWS
export TEST_AWS_ACCESS_KEY="your-access-key"
export TEST_AWS_SECRET_KEY="your-secret-key"
export TEST_AWS_REGION="us-east-1"

# Azure
export TEST_AZURE_SUBSCRIPTION_ID="your-subscription-id"
export TEST_AZURE_TENANT_ID="your-tenant-id"
export TEST_AZURE_CLIENT_ID="your-client-id"
export TEST_AZURE_CLIENT_SECRET="your-client-secret"

# GCP
export TEST_GCP_PROJECT_ID="your-project-id"
export TEST_GCP_SERVICE_ACCOUNT_PATH="/path/to/service-account.json"

# SSH
export TEST_SSH_HOST="your-ssh-host"
export TEST_SSH_USERNAME="your-username"
export TEST_SSH_PASSWORD="your-password"
export TEST_SSH_PRIVATE_KEY_PATH="/path/to/private-key"

# SLURM
export TEST_SLURM_HOST="your-slurm-host"
export TEST_SLURM_USERNAME="your-username"
export TEST_SLURM_PASSWORD="your-password"

# HuggingFace
export HUGGINGFACE_TOKEN="your-token"
export HUGGINGFACE_USERNAME="your-username"

# Lambda Cloud
export LAMBDA_CLOUD_API_KEY="your-api-key"
```

## Running Tests

### Local Development

```bash
# Check credentials
python scripts/run_real_world_tests.py --check-creds

# Run specific test categories
python scripts/run_real_world_tests.py --filesystem
python scripts/run_real_world_tests.py --ssh
python scripts/run_real_world_tests.py --api
python scripts/run_real_world_tests.py --visual

# Run all tests
python scripts/run_real_world_tests.py --all

# Run expensive tests (with cost controls)
python scripts/run_real_world_tests.py --all --expensive
```

### GitHub Actions

Tests run automatically on push/PR. To run expensive tests:

1. Go to `Actions` tab in GitHub
2. Select `Real-World Tests` workflow
3. Click `Run workflow`
4. Check `Run expensive tests`
5. Click `Run workflow`

## Cost Control

### Local Development
- Daily API call limit: 100 calls (configurable)
- Cost limit: $5 USD (configurable)
- Free-tier operations prioritized

### GitHub Actions
- Only free-tier operations in automatic runs
- Expensive tests only on manual trigger
- Cost monitoring through workflow logs

## Security Best Practices

### 1Password
- Use dedicated vault for Clustrix credentials
- Enable CLI integration in 1Password app
- Use temporary tokens where possible

### GitHub Actions
- Use repository secrets, not environment variables in workflow files
- Limit secret access to necessary workflows
- Rotate secrets regularly

### Environment Variables
- Use `.env` files for local development (add to `.gitignore`)
- Never commit credentials to version control
- Use temporary/limited-scope credentials for testing

## Troubleshooting

### 1Password Issues
```bash
# Check 1Password status
op account list

# Re-authenticate
op signin

# Test credential access
op item get "clustrix-aws-validation" --field aws_access_key_id
```

### GitHub Actions Issues
```bash
# Check workflow logs
# Go to Actions tab → Select workflow run → Check logs

# Test locally with secrets
export GITHUB_ACTIONS=true
export CLUSTRIX_USERNAME="..."
python scripts/test_real_world_credentials.py
```

### Permission Issues
```bash
# Check file permissions
ls -la ~/.ssh/
chmod 600 ~/.ssh/id_rsa

# Check SSH connection
ssh -vvv user@host
```

## Credential Rotation

### Regular Rotation Schedule
- **SSH keys**: Every 90 days
- **API keys**: Every 30 days
- **Cloud credentials**: Every 60 days

### Rotation Process
1. Create new credentials in respective services
2. Update 1Password items
3. Update GitHub repository secrets
4. Test with new credentials
5. Revoke old credentials

## Monitoring and Alerts

### Cost Monitoring
- AWS CloudWatch for AWS usage
- GCP Cloud Monitoring for GCP usage
- Azure Monitor for Azure usage
- Lambda Cloud dashboard for GPU usage

### Access Monitoring
- 1Password access logs
- GitHub Actions workflow logs
- Cloud provider audit logs

## Support

For issues with credential setup:
1. Check the troubleshooting section above
2. Run `python scripts/test_real_world_credentials.py` for diagnostics
3. Review workflow logs in GitHub Actions
4. Check 1Password CLI documentation: https://developer.1password.com/docs/cli/