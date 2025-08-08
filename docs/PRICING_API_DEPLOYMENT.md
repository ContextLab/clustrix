# Production Deployment Guide: Cloud Provider Pricing APIs

This guide provides comprehensive instructions for deploying Clustrix's programmatic cloud provider pricing system in production environments.

## Overview

The Clustrix pricing system provides real-time pricing data from major cloud providers (AWS, Azure, GCP, Lambda Cloud) with automatic fallback to hardcoded pricing when APIs are unavailable.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Clustrix Application                     │
├─────────────────────────────────────────────────────────────┤
│                   Cost Monitors                            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐   │
│  │   AWS   │ │  Azure  │ │   GCP   │ │  Lambda Cloud   │   │
│  │Monitor  │ │ Monitor │ │ Monitor │ │    Monitor      │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
              │         │         │         │
              ▼         ▼         ▼         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Pricing Clients                           │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐   │
│  │   AWS   │ │  Azure  │ │   GCP   │ │  Lambda Cloud   │   │
│  │ Client  │ │ Client  │ │ Client  │ │    Client       │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
              │         │         │         │
              ▼         ▼         ▼         ▼
┌─────────────────────────────────────────────────────────────┐
│                 Cloud Provider APIs                        │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐   │
│  │AWS Pricing │Azure Retail│GCP Billing│Lambda Cloud  │   │
│  │    API     │Prices API │Catalog API│     API       │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

### System Requirements

- **Python**: 3.8+ (tested with 3.10, 3.12)
- **Memory**: Minimum 512MB RAM (recommended 1GB+)
- **Storage**: 100MB for cache and logs
- **Network**: Outbound HTTPS access to cloud provider APIs

### Required Dependencies

```bash
# Core dependencies
pip install clustrix[pricing]  # Once available

# Or install development version
pip install -e ".[dev]"

# Additional cloud provider SDKs (optional but recommended)
pip install boto3              # AWS API support
pip install google-cloud-billing  # GCP API support
```

## Configuration

### Environment Variables

Set the following environment variables for production deployment:

```bash
# === Clustrix Configuration ===
export CLUSTRIX_ENVIRONMENT=production
export CLUSTRIX_LOG_LEVEL=INFO
export CLUSTRIX_CACHE_DIR=/var/lib/clustrix/cache
export CLUSTRIX_CONFIG_DIR=/etc/clustrix

# === AWS Credentials ===
export AWS_ACCESS_KEY_ID="your-aws-access-key"
export AWS_SECRET_ACCESS_KEY="your-aws-secret-key"
export AWS_DEFAULT_REGION="us-east-1"

# === Azure Credentials ===
export AZURE_SUBSCRIPTION_ID="your-subscription-id"
export AZURE_TENANT_ID="your-tenant-id"
export AZURE_CLIENT_ID="your-client-id"
export AZURE_CLIENT_SECRET="your-client-secret"

# === GCP Credentials ===
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_APPLICATION_CREDENTIALS="/etc/clustrix/gcp-service-account.json"

# === Lambda Cloud Credentials ===
export LAMBDA_CLOUD_API_KEY="your-lambda-api-key"

# === Performance Tuning ===
export CLUSTRIX_PRICING_CACHE_TTL_HOURS=24
export CLUSTRIX_API_TIMEOUT_SECONDS=30
export CLUSTRIX_MAX_RETRY_ATTEMPTS=3
export CLUSTRIX_PRICING_FALLBACK_ENABLED=true
```

### Configuration File

Create `/etc/clustrix/clustrix.yml`:

```yaml
# Clustrix Production Configuration
pricing:
  # Cache settings
  cache_ttl_hours: 24
  cache_directory: "/var/lib/clustrix/cache"
  
  # API settings  
  api_timeout_seconds: 30
  max_retry_attempts: 3
  fallback_enabled: true
  
  # Provider configurations
  providers:
    aws:
      enabled: true
      regions: ["us-east-1", "us-west-2", "eu-west-1"]
      pricing_api_enabled: true
      
    azure:
      enabled: true
      regions: ["eastus", "westus2", "westeurope"]
      pricing_api_enabled: true
      
    gcp:
      enabled: true
      regions: ["us-central1", "us-west1", "europe-west1"]
      pricing_api_enabled: true
      
    lambda:
      enabled: true
      pricing_api_enabled: true
      api_endpoint: "https://cloud.lambdalabs.com/api/v1"

# Logging configuration
logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file: "/var/log/clustrix/pricing.log"
  max_bytes: 10485760  # 10MB
  backup_count: 5

# Monitoring
monitoring:
  metrics_enabled: true
  health_check_interval_seconds: 300
  pricing_staleness_alert_hours: 48
```

## Deployment Steps

### 1. System Setup

```bash
# Create system user
sudo useradd -r -s /bin/false clustrix

# Create directories
sudo mkdir -p /etc/clustrix
sudo mkdir -p /var/lib/clustrix/cache
sudo mkdir -p /var/log/clustrix

# Set permissions
sudo chown -R clustrix:clustrix /var/lib/clustrix
sudo chown -R clustrix:clustrix /var/log/clustrix
sudo chmod 755 /etc/clustrix
```

### 2. Install Clustrix

```bash
# Create virtual environment
sudo -u clustrix python3 -m venv /opt/clustrix/venv

# Activate and install
sudo -u clustrix /opt/clustrix/venv/bin/pip install -e ".[dev]"

# Verify installation
sudo -u clustrix /opt/clustrix/venv/bin/python -c "
from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.azure_pricing import AzurePricingClient
from clustrix.pricing_clients.gcp_pricing import GCPPricingClient
from clustrix.pricing_clients.lambda_pricing import LambdaPricingClient
print('Pricing clients imported successfully')
"
```

### 3. Configure Credentials

```bash
# Copy configuration file
sudo cp clustrix.yml /etc/clustrix/

# Set up GCP service account (if using GCP)
sudo cp gcp-service-account.json /etc/clustrix/
sudo chown clustrix:clustrix /etc/clustrix/gcp-service-account.json
sudo chmod 600 /etc/clustrix/gcp-service-account.json

# Set up environment file
sudo tee /etc/clustrix/environment > /dev/null << 'EOF'
CLUSTRIX_ENVIRONMENT=production
CLUSTRIX_CONFIG_DIR=/etc/clustrix
CLUSTRIX_CACHE_DIR=/var/lib/clustrix/cache
# Add your credentials here...
EOF

sudo chown clustrix:clustrix /etc/clustrix/environment
sudo chmod 600 /etc/clustrix/environment
```

### 4. Create Systemd Service

Create `/etc/systemd/system/clustrix-pricing.service`:

```ini
[Unit]
Description=Clustrix Pricing Service
After=network.target
Requires=network.target

[Service]
Type=notify
User=clustrix
Group=clustrix
WorkingDirectory=/opt/clustrix
ExecStart=/opt/clustrix/venv/bin/python -m clustrix.services.pricing_service
EnvironmentFile=/etc/clustrix/environment
Restart=always
RestartSec=10
KillMode=process
TimeoutStopSec=30

# Security settings
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/clustrix /var/log/clustrix
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

### 5. Start Service

```bash
# Reload systemd and start service
sudo systemctl daemon-reload
sudo systemctl enable clustrix-pricing
sudo systemctl start clustrix-pricing

# Check status
sudo systemctl status clustrix-pricing
```

## Production Usage

### Basic Usage

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor

# Initialize cost monitors (automatically loads production config)
aws_monitor = AWSCostMonitor()
azure_monitor = AzureCostMonitor()
gcp_monitor = GCPCostMonitor()
lambda_monitor = LambdaCostMonitor(use_pricing_api=True)

# Get cost estimates
aws_cost = aws_monitor.estimate_cost("t3.large", 8.0)  # 8 hours
azure_cost = azure_monitor.estimate_cost("Standard_D4s_v3", 8.0)
gcp_cost = gcp_monitor.estimate_cost("n1-standard-4", 8.0)
lambda_cost = lambda_monitor.estimate_cost("gpu_1x_a10", 4.0)  # 4 hours

print(f"AWS t3.large 8h: ${aws_cost.estimated_cost:.2f}")
print(f"Azure D4s_v3 8h: ${azure_cost.estimated_cost:.2f}")
print(f"GCP n1-standard-4 8h: ${gcp_cost.estimated_cost:.2f}")
print(f"Lambda A10 4h: ${lambda_cost.estimated_cost:.2f}")
```

### Advanced Usage

```python
# Get pricing with validation
from clustrix.pricing_clients.aws_pricing import AWSPricingClient

client = AWSPricingClient()
price = client.get_instance_pricing("m5.xlarge", "us-east-1", "Linux")

if price is None:
    print("Warning: Using fallback pricing")
else:
    print(f"Live API pricing: ${price:.4f}/hour")

# Batch pricing queries
pricing_info = client.get_all_pricing("us-east-1")
for instance_type, hourly_rate in pricing_info.items():
    print(f"{instance_type}: ${hourly_rate:.4f}/hour")
```

## Monitoring and Maintenance

### Health Checks

Create `/opt/clustrix/health_check.py`:

```python
#!/usr/bin/env python3
"""Production health check for Clustrix pricing system."""

import sys
import logging
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor

def health_check():
    """Run health check on all pricing providers."""
    monitors = {
        'aws': AWSCostMonitor(),
        'azure': AzureCostMonitor(),
        'gcp': GCPCostMonitor(),
        'lambda': LambdaCostMonitor(use_pricing_api=True)
    }
    
    health_status = {}
    overall_healthy = True
    
    for provider, monitor in monitors.items():
        try:
            # Test basic pricing functionality
            if provider == 'aws':
                result = monitor.estimate_cost('t3.micro', 1.0)
            elif provider == 'azure':
                result = monitor.estimate_cost('Standard_A1_v2', 1.0)
            elif provider == 'gcp':
                result = monitor.estimate_cost('n1-standard-1', 1.0)
            elif provider == 'lambda':
                result = monitor.estimate_cost('gpu_1x_a10', 1.0)
            
            if result and result.estimated_cost > 0:
                health_status[provider] = 'healthy'
                print(f"✅ {provider}: healthy (${result.estimated_cost:.4f}/hour)")
            else:
                health_status[provider] = 'unhealthy'
                overall_healthy = False
                print(f"❌ {provider}: unhealthy (no pricing data)")
                
        except Exception as e:
            health_status[provider] = 'error'
            overall_healthy = False
            print(f"❌ {provider}: error ({e})")
    
    if overall_healthy:
        print("✅ Overall system health: HEALTHY")
        return 0
    else:
        print("❌ Overall system health: UNHEALTHY")
        return 1

if __name__ == '__main__':
    sys.exit(health_check())
```

### Monitoring Script

Create `/opt/clustrix/monitor_pricing.py`:

```python
#!/usr/bin/env python3
"""Production monitoring for Clustrix pricing system."""

import time
import logging
import json
from datetime import datetime
from clustrix.pricing_clients.aws_pricing import AWSPricingClient
from clustrix.pricing_clients.azure_pricing import AzurePricingClient
from clustrix.pricing_clients.gcp_pricing import GCPPricingClient
from clustrix.pricing_clients.lambda_pricing import LambdaPricingClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def monitor_pricing_apis():
    """Monitor pricing API health and response times."""
    clients = {
        'aws': AWSPricingClient(),
        'azure': AzurePricingClient(),
        'gcp': GCPPricingClient(),
        'lambda': LambdaPricingClient()
    }
    
    # Authenticate Lambda client if credentials available
    import os
    lambda_api_key = os.getenv('LAMBDA_CLOUD_API_KEY')
    if lambda_api_key:
        clients['lambda'].authenticate(lambda_api_key)
    
    metrics = {
        'timestamp': datetime.now().isoformat(),
        'providers': {}
    }
    
    test_instances = {
        'aws': ('t3.small', 'us-east-1'),
        'azure': ('Standard_D2s_v3', 'eastus'),
        'gcp': ('n1-standard-1', 'us-central1'),
        'lambda': ('gpu_1x_a10', 'us-east-1')
    }
    
    for provider, client in clients.items():
        instance_type, region = test_instances[provider]
        
        start_time = time.time()
        try:
            if provider == 'aws':
                price = client.get_instance_pricing(instance_type, region, 'Linux')
            elif provider == 'azure':
                price = client.get_instance_pricing(instance_type, region, 'Linux')
            elif provider == 'gcp':
                price = client.get_instance_pricing(instance_type, region)
            elif provider == 'lambda':
                price = client.get_instance_pricing(instance_type, region)
            
            response_time = time.time() - start_time
            
            metrics['providers'][provider] = {
                'status': 'success' if price is not None else 'no_data',
                'response_time_seconds': round(response_time, 3),
                'price': price,
                'instance_type': instance_type,
                'region': region
            }
            
            logger.info(f"{provider}: ${price:.4f}/hour ({response_time:.3f}s)")
            
        except Exception as e:
            response_time = time.time() - start_time
            metrics['providers'][provider] = {
                'status': 'error',
                'response_time_seconds': round(response_time, 3),
                'error': str(e),
                'instance_type': instance_type,
                'region': region
            }
            logger.error(f"{provider}: error - {e}")
    
    # Write metrics to file for monitoring systems
    with open('/var/log/clustrix/pricing_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)
    
    return metrics

if __name__ == '__main__':
    while True:
        try:
            monitor_pricing_apis()
            time.sleep(300)  # Run every 5 minutes
        except KeyboardInterrupt:
            logger.info("Monitoring stopped")
            break
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
            time.sleep(60)  # Wait 1 minute on error
```

### Log Rotation

Create `/etc/logrotate.d/clustrix`:

```
/var/log/clustrix/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 clustrix clustrix
    postrotate
        systemctl reload clustrix-pricing > /dev/null 2>&1 || true
    endscript
}
```

## Security Considerations

### API Key Management

1. **Use environment variables** or secure key management systems
2. **Rotate keys regularly** (quarterly recommended)
3. **Monitor API usage** for unusual patterns
4. **Use least-privilege IAM policies** for cloud provider access

### Network Security

```bash
# Firewall rules (example with ufw)
sudo ufw allow out 443/tcp  # HTTPS for API calls
sudo ufw deny in 443/tcp    # No inbound HTTPS needed
```

### File Permissions

```bash
# Secure configuration files
sudo chmod 600 /etc/clustrix/environment
sudo chmod 600 /etc/clustrix/gcp-service-account.json
sudo chmod 644 /etc/clustrix/clustrix.yml

# Secure cache directory
sudo chmod 755 /var/lib/clustrix/cache
sudo chown -R clustrix:clustrix /var/lib/clustrix
```

## Performance Optimization

### Caching Strategy

- **Default TTL**: 24 hours for pricing data
- **Cache location**: `/var/lib/clustrix/cache`
- **Cache size**: Automatically managed, ~10MB typical
- **Cache invalidation**: Automatic on TTL expiry

### API Rate Limiting

- **AWS**: 100 requests/second (built-in throttling)
- **Azure**: No published limits (reasonable usage)  
- **GCP**: 300 requests/minute (quota-based)
- **Lambda Cloud**: 100 requests/minute (estimated)

### Memory Usage

- **Base memory**: ~50MB per pricing client
- **Cache memory**: ~1MB per 1000 cached prices
- **Recommended**: 1GB RAM for production

## Troubleshooting

### Common Issues

1. **"No pricing data available"**
   - Check API credentials
   - Verify network connectivity
   - Check API quotas/limits

2. **"Authentication failed"**
   - Verify credentials in environment variables
   - Check IAM permissions for cloud providers
   - Ensure service account keys are valid

3. **High response times**
   - Check network latency to cloud provider APIs
   - Consider regional API endpoints
   - Verify caching is working correctly

4. **Pricing discrepancies**
   - Check if using API vs fallback pricing
   - Verify instance type mappings
   - Check regional pricing differences

### Debugging Commands

```bash
# Check service status
sudo systemctl status clustrix-pricing

# View service logs
sudo journalctl -u clustrix-pricing -f

# Check pricing logs
sudo tail -f /var/log/clustrix/pricing.log

# Run health check
sudo -u clustrix /opt/clustrix/venv/bin/python /opt/clustrix/health_check.py

# Test individual provider
sudo -u clustrix /opt/clustrix/venv/bin/python -c "
from clustrix.cost_providers.aws import AWSCostMonitor
monitor = AWSCostMonitor()
result = monitor.estimate_cost('t3.micro', 1.0)
print(f'AWS pricing test: {result}')
"
```

## Backup and Recovery

### Configuration Backup

```bash
# Backup configuration
sudo tar -czf /backup/clustrix-config-$(date +%Y%m%d).tar.gz \
    /etc/clustrix/ \
    /var/lib/clustrix/cache/

# Restore configuration
sudo tar -xzf clustrix-config-20240101.tar.gz -C /
```

### Cache Management

```bash
# Clear pricing cache
sudo -u clustrix rm -rf /var/lib/clustrix/cache/*

# View cache contents
sudo -u clustrix ls -la /var/lib/clustrix/cache/
```

## Updates and Maintenance

### Updating Clustrix

```bash
# Stop service
sudo systemctl stop clustrix-pricing

# Update code
sudo -u clustrix /opt/clustrix/venv/bin/pip install -U clustrix[pricing]

# Run tests
sudo -u clustrix /opt/clustrix/venv/bin/python -m pytest tests/real_world/ -m real_world

# Restart service
sudo systemctl start clustrix-pricing
```

### Regular Maintenance Tasks

1. **Weekly**: Check logs for errors and warnings
2. **Monthly**: Verify pricing accuracy against cloud provider consoles
3. **Quarterly**: Rotate API keys and credentials
4. **Annually**: Review and update hardcoded fallback pricing

## Support and Monitoring

### Metrics to Monitor

- API response times
- Cache hit/miss ratios  
- Pricing data staleness
- Error rates by provider
- Memory and CPU usage

### Alerting Thresholds

- **API response time** > 30 seconds
- **Error rate** > 5%
- **Cache miss ratio** > 50%
- **Pricing data age** > 48 hours

### Getting Help

1. Check logs in `/var/log/clustrix/`
2. Run health check script
3. Review this deployment guide
4. Check GitHub issues for known problems

## Conclusion

This production deployment guide provides comprehensive instructions for running Clustrix's pricing system reliably in production. Regular monitoring and maintenance will ensure optimal performance and accurate pricing data.

For additional support or questions, refer to the project documentation or GitHub repository.