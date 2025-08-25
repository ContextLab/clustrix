# Credential Setup for Real-World Testing

This guide explains how to set up credentials for Clustrix real-world testing, supporting both local development (with environment variables) and GitHub Actions (with repository secrets).

## Overview

The credential system supports two modes:
- **Local Development**: Uses environment variables for secure credential storage
- **GitHub Actions**: Uses repository secrets for CI/CD workflows

## Local Development Setup

### 1. Environment Variable Configuration

Create a `.env` file in your project root (this file should be added to `.gitignore` for security):

```bash
# .env file for local development - DO NOT COMMIT TO GIT

# AWS Credentials
TEST_AWS_ACCESS_KEY=your-access-key-here
TEST_AWS_SECRET_KEY=your-secret-key-here  
TEST_AWS_REGION=us-east-1

# GCP Credentials
TEST_GCP_PROJECT_ID=your-project-id
TEST_GCP_SERVICE_ACCOUNT_PATH=/path/to/service-account.json
TEST_GCP_REGION=us-central1

# Azure Credentials
TEST_AZURE_SUBSCRIPTION_ID=your-subscription-id
TEST_AZURE_TENANT_ID=your-tenant-id
TEST_AZURE_CLIENT_ID=your-client-id
TEST_AZURE_CLIENT_SECRET=your-client-secret

# SSH Cluster Credentials
TEST_SSH_HOST=your-ssh-host
TEST_SSH_USERNAME=your-username
TEST_SSH_PASSWORD=your-password
TEST_SSH_PRIVATE_KEY_PATH=/path/to/private-key

# SLURM Cluster Credentials
TEST_SLURM_HOST=your-slurm-host
TEST_SLURM_USERNAME=your-username
TEST_SLURM_PASSWORD=your-password

# HuggingFace Credentials
HUGGINGFACE_TOKEN=your-hf-token
HUGGINGFACE_USERNAME=your-username

# Lambda Cloud Credentials
LAMBDA_CLOUD_API_KEY=your-api-key
```

### 2. Alternative: Export Environment Variables

If you prefer not to use a `.env` file, export variables directly:

```bash
# AWS
export TEST_AWS_ACCESS_KEY="your-access-key"
export TEST_AWS_SECRET_KEY="your-secret-key"
export TEST_AWS_REGION="us-east-1"

# GCP  
export TEST_GCP_PROJECT_ID="your-project-id"
export TEST_GCP_SERVICE_ACCOUNT_PATH="/path/to/service-account.json"

# Azure
export TEST_AZURE_SUBSCRIPTION_ID="your-subscription-id"
export TEST_AZURE_TENANT_ID="your-tenant-id"
export TEST_AZURE_CLIENT_ID="your-client-id"
export TEST_AZURE_CLIENT_SECRET="your-client-secret"

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

### 3. Test Local Setup

```bash
# Check environment variable setup
python scripts/run_real_world_tests.py --check-creds

# Test credential access
python scripts/test_credential_access.py

# Verify specific services
python scripts/test_real_world_credentials.py
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

### Environment Variables
- Always use `.env` files for local development (add to `.gitignore`)
- Use temporary/limited-scope credentials for testing
- Never commit credentials to version control
- Rotate credentials regularly
- Use least-privilege access policies

### GitHub Actions
- Use repository secrets, not environment variables in workflow files
- Limit secret access to necessary workflows
- Rotate secrets regularly


## Troubleshooting

### Environment Variable Issues
```bash
# Check if variables are set
echo $TEST_AWS_ACCESS_KEY
echo $TEST_SSH_USERNAME

# Test environment variables are loaded
python -c "import os; print(os.environ.get('TEST_AWS_ACCESS_KEY', 'Not set'))"

# Check .env file loading
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.environ.get('TEST_AWS_ACCESS_KEY', 'Not set'))"
```

### GitHub Actions Issues
```bash
# Check workflow logs
# Go to Actions tab → Select workflow run → Check logs

# Test locally with environment variables
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
2. Update local environment variables in `.env` file
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
- Local environment variable usage logs
- GitHub Actions workflow logs  
- Cloud provider audit logs

## Support

For issues with credential setup:
1. Check the troubleshooting section above
2. Run `python scripts/test_real_world_credentials.py` for diagnostics
3. Review workflow logs in GitHub Actions
4. Verify environment variables are properly set and loaded
5. Ensure `.env` file is in the correct location and not committed to git