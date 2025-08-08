# Clustrix Pricing System User Guide

This guide provides practical examples and common use cases for Clustrix's cloud provider pricing system.

## Quick Start

### Installation

```bash
# Install Clustrix with pricing support
pip install -e ".[dev]"

# Optional: Install cloud provider SDKs for enhanced functionality
pip install boto3                    # AWS
pip install google-cloud-billing    # GCP
```

### Basic Setup

```python
from clustrix.cost_providers.aws import AWSCostMonitor

# Initialize cost monitor (uses environment variables for credentials)
monitor = AWSCostMonitor()

# Get cost estimate
cost = monitor.estimate_cost("t3.medium", 8.0)  # 8 hours
print(f"Cost: ${cost.estimated_cost:.2f}")
```

### Environment Configuration

Set up credentials via environment variables:

```bash
# AWS
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"

# Azure
export AZURE_SUBSCRIPTION_ID="your-subscription"
export AZURE_TENANT_ID="your-tenant"
export AZURE_CLIENT_ID="your-client-id"
export AZURE_CLIENT_SECRET="your-secret"

# GCP
export GOOGLE_CLOUD_PROJECT="your-project"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"

# Lambda Cloud
export LAMBDA_CLOUD_API_KEY="your-api-key"
```

## Common Use Cases

### 1. Development Cost Estimation

Estimate costs for development workloads across different providers.

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor

def estimate_development_costs():
    """Compare development costs across providers."""
    
    # Development workload: 8 hours/day, 5 days/week
    daily_hours = 8
    weekly_days = 5
    monthly_hours = daily_hours * weekly_days * 4  # ~160 hours/month
    
    providers = {
        'AWS': (AWSCostMonitor(), 't3.medium'),
        'Azure': (AzureCostMonitor(), 'Standard_D2s_v3'),
        'GCP': (GCPCostMonitor(), 'n1-standard-2')
    }
    
    results = {}
    for name, (monitor, instance_type) in providers.items():
        try:
            cost_estimate = monitor.estimate_cost(instance_type, monthly_hours)
            results[name] = {
                'monthly_cost': cost_estimate.estimated_cost,
                'hourly_rate': cost_estimate.hourly_rate,
                'instance_type': instance_type,
                'pricing_source': cost_estimate.pricing_source
            }
        except Exception as e:
            print(f"Error getting {name} pricing: {e}")
            results[name] = {'error': str(e)}
    
    # Display results
    print("Development Workload Cost Comparison (Monthly)")
    print("=" * 50)
    
    valid_results = {k: v for k, v in results.items() if 'error' not in v}
    if valid_results:
        # Sort by cost
        sorted_results = sorted(valid_results.items(), key=lambda x: x[1]['monthly_cost'])
        
        for provider, data in sorted_results:
            print(f"{provider:8} ${data['monthly_cost']:7.2f} | "
                  f"${data['hourly_rate']:6.4f}/hr | "
                  f"{data['instance_type']:18} | "
                  f"{data['pricing_source']}")
        
        # Cost difference analysis
        cheapest = sorted_results[0][1]['monthly_cost']
        most_expensive = sorted_results[-1][1]['monthly_cost']
        savings = most_expensive - cheapest
        
        print(f"\nPotential monthly savings: ${savings:.2f}")
        print(f"Annual savings: ${savings * 12:.2f}")
    
    return results

# Run the comparison
results = estimate_development_costs()
```

### 2. GPU Training Cost Analysis

Compare GPU costs for machine learning workloads.

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor  
from clustrix.cost_providers.gcp import GCPCostMonitor
from clustrix.cost_providers.lambda_cloud import LambdaCostMonitor

def analyze_gpu_training_costs():
    """Analyze GPU costs for ML training workloads."""
    
    # Training scenarios
    scenarios = {
        'Short Training (4 hours)': 4,
        'Medium Training (12 hours)': 12, 
        'Long Training (48 hours)': 48,
        'Weekend Project (72 hours)': 72
    }
    
    # GPU instance mappings
    gpu_instances = {
        'AWS': (AWSCostMonitor(), 'g4dn.xlarge'),  # T4 GPU
        'Azure': (AzureCostMonitor(), 'Standard_NC6s_v3'),  # V100 GPU
        'GCP': (GCPCostMonitor(), 'n1-standard-4-t4'),  # T4 GPU  
        'Lambda Cloud': (LambdaCostMonitor(use_pricing_api=True), 'gpu_1x_a10')  # A10 GPU
    }
    
    print("GPU Training Cost Analysis")
    print("=" * 60)
    
    for scenario, hours in scenarios.items():
        print(f"\n{scenario}:")
        print("-" * 40)
        
        scenario_results = {}
        
        for provider, (monitor, instance_type) in gpu_instances.items():
            try:
                cost_estimate = monitor.estimate_cost(instance_type, hours)
                scenario_results[provider] = cost_estimate.estimated_cost
                
                print(f"{provider:12} ${cost_estimate.estimated_cost:7.2f} | "
                      f"${cost_estimate.hourly_rate:6.3f}/hr | "
                      f"{instance_type}")
                      
            except Exception as e:
                print(f"{provider:12} Error: {e}")
        
        # Show cost comparison
        if len(scenario_results) > 1:
            cheapest = min(scenario_results.items(), key=lambda x: x[1])
            most_expensive = max(scenario_results.items(), key=lambda x: x[1])
            
            if cheapest[1] != most_expensive[1]:
                savings = most_expensive[1] - cheapest[1]
                savings_percent = (savings / most_expensive[1]) * 100
                
                print(f"  → Cheapest: {cheapest[0]} (${cheapest[1]:.2f})")
                print(f"  → Savings: ${savings:.2f} ({savings_percent:.1f}%)")

# Run GPU analysis
analyze_gpu_training_costs()
```

### 3. Batch Processing Cost Optimization

Optimize costs for batch processing workloads.

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
from clustrix.cost_providers.gcp import GCPCostMonitor
from datetime import datetime, timedelta

def optimize_batch_processing_costs():
    """Find optimal instance types for batch processing."""
    
    # Define batch processing requirements
    job_requirements = {
        'cpu_intensive': {
            'aws': ['c5.large', 'c5.xlarge', 'c5.2xlarge', 'c5.4xlarge'],
            'azure': ['Standard_F2s_v2', 'Standard_F4s_v2', 'Standard_F8s_v2', 'Standard_F16s_v2'],
            'gcp': ['c2-standard-4', 'c2-standard-8', 'c2-standard-16', 'c2-standard-30']
        },
        'memory_intensive': {
            'aws': ['r5.large', 'r5.xlarge', 'r5.2xlarge', 'r5.4xlarge'],
            'azure': ['Standard_E4s_v3', 'Standard_E8s_v3', 'Standard_E16s_v3', 'Standard_E32s_v3'],
            'gcp': ['n1-highmem-4', 'n1-highmem-8', 'n1-highmem-16', 'n1-highmem-32']
        },
        'balanced': {
            'aws': ['m5.large', 'm5.xlarge', 'm5.2xlarge', 'm5.4xlarge'],
            'azure': ['Standard_D4s_v3', 'Standard_D8s_v3', 'Standard_D16s_v3', 'Standard_D32s_v3'],
            'gcp': ['n1-standard-4', 'n1-standard-8', 'n1-standard-16', 'n1-standard-32']
        }
    }
    
    # Job duration scenarios  
    durations = [1, 4, 8, 24, 72]  # hours
    
    monitors = {
        'aws': AWSCostMonitor(),
        'azure': AzureCostMonitor(),
        'gcp': GCPCostMonitor()
    }
    
    print("Batch Processing Cost Optimization")
    print("=" * 50)
    
    for workload_type, provider_instances in job_requirements.items():
        print(f"\n{workload_type.replace('_', ' ').title()} Workload")
        print("-" * 30)
        
        # Test each duration
        for duration in durations:
            print(f"\n{duration} hour job:")
            
            best_options = []
            
            for provider, instance_types in provider_instances.items():
                if provider not in monitors:
                    continue
                    
                monitor = monitors[provider]
                provider_best = None
                provider_best_cost = float('inf')
                
                for instance_type in instance_types:
                    try:
                        cost_estimate = monitor.estimate_cost(instance_type, duration)
                        total_cost = cost_estimate.estimated_cost
                        
                        if total_cost < provider_best_cost:
                            provider_best = (instance_type, total_cost, cost_estimate.hourly_rate)
                            provider_best_cost = total_cost
                            
                    except Exception as e:
                        continue
                
                if provider_best:
                    best_options.append((provider, *provider_best))
            
            # Sort by total cost
            best_options.sort(key=lambda x: x[2])
            
            for provider, instance_type, total_cost, hourly_rate in best_options[:3]:  # Top 3
                print(f"  {provider:6} {instance_type:20} ${total_cost:7.2f} (${hourly_rate:.4f}/hr)")
            
            if len(best_options) > 1:
                savings = best_options[-1][2] - best_options[0][2]
                print(f"    → Potential savings: ${savings:.2f}")

# Run batch optimization
optimize_batch_processing_costs()
```

### 4. Monthly Budget Planning

Plan and monitor monthly cloud spending.

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cost_providers.azure import AzureCostMonitor
import json
from datetime import datetime

def create_monthly_budget_plan():
    """Create a monthly budget plan for cloud resources."""
    
    # Define monthly usage patterns
    monthly_workloads = {
        'development_team': {
            'description': '5 developers, 8 hours/day, 22 working days',
            'instances': [
                {'type': 't3.medium', 'count': 5, 'hours_per_day': 8, 'days_per_month': 22}
            ]
        },
        'ci_cd_pipeline': {
            'description': 'CI/CD builds, ~4 hours/day average',
            'instances': [
                {'type': 'c5.large', 'count': 2, 'hours_per_day': 4, 'days_per_month': 30}
            ]
        },
        'data_processing': {
            'description': 'Weekly batch jobs, memory intensive',
            'instances': [
                {'type': 'r5.2xlarge', 'count': 1, 'hours_per_day': 12, 'days_per_month': 4}
            ]
        },
        'ml_training': {
            'description': 'GPU training, 2 sessions per week',
            'instances': [
                {'type': 'g4dn.xlarge', 'count': 1, 'hours_per_day': 6, 'days_per_month': 8}
            ]
        }
    }
    
    providers = {
        'AWS': AWSCostMonitor(),
        'Azure': AzureCostMonitor()
    }
    
    # Instance type mappings
    instance_mappings = {
        'Azure': {
            't3.medium': 'Standard_D2s_v3',
            'c5.large': 'Standard_F4s_v2', 
            'r5.2xlarge': 'Standard_E16s_v3',
            'g4dn.xlarge': 'Standard_NC6s_v3'
        }
    }
    
    budget_analysis = {}
    
    for provider_name, monitor in providers.items():
        print(f"\n{provider_name} Monthly Budget Analysis")
        print("=" * 40)
        
        total_monthly_cost = 0
        provider_breakdown = {}
        
        for workload, config in monthly_workloads.items():
            workload_cost = 0
            workload_details = []
            
            for instance_config in config['instances']:
                # Map instance type for non-AWS providers
                instance_type = instance_config['type']
                if provider_name in instance_mappings:
                    instance_type = instance_mappings[provider_name].get(instance_type, instance_type)
                
                # Calculate monthly hours
                monthly_hours = (instance_config['count'] * 
                               instance_config['hours_per_day'] * 
                               instance_config['days_per_month'])
                
                try:
                    cost_estimate = monitor.estimate_cost(instance_type, monthly_hours)
                    instance_monthly_cost = cost_estimate.estimated_cost
                    workload_cost += instance_monthly_cost
                    
                    workload_details.append({
                        'instance_type': instance_type,
                        'count': instance_config['count'],
                        'monthly_hours': monthly_hours,
                        'hourly_rate': cost_estimate.hourly_rate,
                        'monthly_cost': instance_monthly_cost
                    })
                    
                except Exception as e:
                    print(f"  Error pricing {instance_type}: {e}")
                    continue
            
            total_monthly_cost += workload_cost
            provider_breakdown[workload] = {
                'description': config['description'],
                'cost': workload_cost,
                'details': workload_details
            }
            
            print(f"\n{workload.replace('_', ' ').title()}:")
            print(f"  {config['description']}")
            print(f"  Monthly cost: ${workload_cost:.2f}")
            
            for detail in workload_details:
                print(f"    {detail['instance_type']}: {detail['count']}x × "
                      f"{detail['monthly_hours']:.0f}h = ${detail['monthly_cost']:.2f}")
        
        print(f"\nTotal Monthly Cost: ${total_monthly_cost:.2f}")
        print(f"Annual Estimate: ${total_monthly_cost * 12:.2f}")
        
        # Cost breakdown by workload
        if provider_breakdown:
            print(f"\nCost Breakdown:")
            sorted_workloads = sorted(provider_breakdown.items(), 
                                    key=lambda x: x[1]['cost'], reverse=True)
            
            for workload, data in sorted_workloads:
                percentage = (data['cost'] / total_monthly_cost * 100) if total_monthly_cost > 0 else 0
                print(f"  {workload.replace('_', ' ').title()}: "
                      f"${data['cost']:.2f} ({percentage:.1f}%)")
        
        budget_analysis[provider_name] = {
            'total_monthly_cost': total_monthly_cost,
            'workload_breakdown': provider_breakdown,
            'generated_at': datetime.now().isoformat()
        }
    
    # Compare providers
    if len(budget_analysis) > 1:
        print(f"\nProvider Cost Comparison:")
        print("-" * 25)
        
        provider_costs = {name: data['total_monthly_cost'] 
                         for name, data in budget_analysis.items()}
        sorted_providers = sorted(provider_costs.items(), key=lambda x: x[1])
        
        for provider, cost in sorted_providers:
            print(f"  {provider}: ${cost:.2f}/month")
        
        if len(sorted_providers) > 1:
            cheapest = sorted_providers[0]
            most_expensive = sorted_providers[-1]
            monthly_savings = most_expensive[1] - cheapest[1]
            annual_savings = monthly_savings * 12
            
            print(f"\nPotential Savings:")
            print(f"  Monthly: ${monthly_savings:.2f}")
            print(f"  Annual: ${annual_savings:.2f}")
    
    # Save budget analysis
    with open('monthly_budget_analysis.json', 'w') as f:
        json.dump(budget_analysis, f, indent=2)
    
    print(f"\nBudget analysis saved to 'monthly_budget_analysis.json'")
    
    return budget_analysis

# Generate budget plan
budget_plan = create_monthly_budget_plan()
```

### 5. Cost Alerting and Monitoring

Set up automated cost monitoring and alerts.

```python
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.pricing_clients.performance_monitor import get_global_performance_monitor
from clustrix.pricing_clients.resilience import get_global_health_checker
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import json

class CostAlertingSystem:
    """Automated cost alerting and monitoring system."""
    
    def __init__(self, email_config=None):
        self.monitors = {
            'aws': AWSCostMonitor(use_pricing_api=True)
        }
        self.email_config = email_config
        self.performance_monitor = get_global_performance_monitor()
        self.health_checker = get_global_health_checker()
        
        # Alert thresholds
        self.cost_thresholds = {
            'daily_limit': 50.0,      # $50/day
            'monthly_limit': 1000.0,  # $1000/month
            'hourly_spike': 10.0      # $10/hour spike
        }
        
        # Performance thresholds
        self.performance_thresholds = {
            'error_rate': 0.05,       # 5% error rate
            'response_time': 30.0,    # 30 seconds
            'cache_hit_rate': 0.70    # 70% cache hit rate
        }
    
    def check_cost_thresholds(self, workloads):
        """Check if any cost thresholds are exceeded."""
        alerts = []
        
        for provider, monitor in self.monitors.items():
            total_daily_cost = 0
            
            for workload in workloads:
                try:
                    cost_estimate = monitor.estimate_cost(
                        workload['instance_type'], 
                        workload['daily_hours']
                    )
                    workload_cost = cost_estimate.estimated_cost
                    total_daily_cost += workload_cost
                    
                    # Check hourly spike threshold
                    hourly_cost = cost_estimate.hourly_rate
                    if hourly_cost > self.cost_thresholds['hourly_spike']:
                        alerts.append({
                            'type': 'cost_spike',
                            'provider': provider,
                            'workload': workload['name'],
                            'instance_type': workload['instance_type'],
                            'hourly_cost': hourly_cost,
                            'threshold': self.cost_thresholds['hourly_spike'],
                            'severity': 'warning'
                        })
                
                except Exception as e:
                    alerts.append({
                        'type': 'pricing_error',
                        'provider': provider,
                        'workload': workload['name'],
                        'error': str(e),
                        'severity': 'error'
                    })
            
            # Check daily limit
            if total_daily_cost > self.cost_thresholds['daily_limit']:
                alerts.append({
                    'type': 'daily_limit_exceeded',
                    'provider': provider,
                    'daily_cost': total_daily_cost,
                    'threshold': self.cost_thresholds['daily_limit'],
                    'severity': 'critical'
                })
            
            # Check monthly projection
            projected_monthly = total_daily_cost * 30
            if projected_monthly > self.cost_thresholds['monthly_limit']:
                alerts.append({
                    'type': 'monthly_projection_exceeded',
                    'provider': provider,
                    'projected_monthly': projected_monthly,
                    'threshold': self.cost_thresholds['monthly_limit'],
                    'severity': 'warning'
                })
        
        return alerts
    
    def check_performance_health(self):
        """Check system performance and health."""
        alerts = []
        
        # Get performance summary
        summary = self.performance_monitor.get_performance_summary(hours=1)
        
        # Check error rate
        if summary.get('error_rate', 0) > self.performance_thresholds['error_rate']:
            alerts.append({
                'type': 'high_error_rate',
                'error_rate': summary['error_rate'],
                'threshold': self.performance_thresholds['error_rate'],
                'severity': 'warning'
            })
        
        # Check response time
        avg_response_time = summary.get('average_response_time', 0)
        if avg_response_time > self.performance_thresholds['response_time']:
            alerts.append({
                'type': 'slow_response_time',
                'response_time': avg_response_time,
                'threshold': self.performance_thresholds['response_time'],
                'severity': 'warning'
            })
        
        # Check cache hit rate
        cache_hit_rate = summary.get('cache_hit_rate', 1.0)
        if cache_hit_rate < self.performance_thresholds['cache_hit_rate']:
            alerts.append({
                'type': 'low_cache_hit_rate',
                'cache_hit_rate': cache_hit_rate,
                'threshold': self.performance_thresholds['cache_hit_rate'],
                'severity': 'info'
            })
        
        # Check overall health
        overall_health = self.health_checker.get_overall_health()
        if overall_health['overall_status'] != 'healthy':
            alerts.append({
                'type': 'system_unhealthy',
                'healthy_services': overall_health['healthy_services'],
                'total_services': overall_health['total_services'],
                'health_percentage': overall_health['health_percentage'],
                'severity': 'critical' if overall_health['healthy_services'] == 0 else 'warning'
            })
        
        return alerts
    
    def send_alert_email(self, alerts):
        """Send alert email if configured."""
        if not self.email_config or not alerts:
            return
        
        # Group alerts by severity
        critical = [a for a in alerts if a.get('severity') == 'critical']
        warnings = [a for a in alerts if a.get('severity') == 'warning']
        info = [a for a in alerts if a.get('severity') == 'info']
        errors = [a for a in alerts if a.get('severity') == 'error']
        
        # Create email content
        subject = f"Clustrix Cost Alert - {len(alerts)} alerts"
        if critical:
            subject = f"CRITICAL: {subject}"
        
        body = f"""
Clustrix Cost Monitoring Alert Report
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Summary: {len(alerts)} total alerts
- Critical: {len(critical)}
- Warnings: {len(warnings)} 
- Errors: {len(errors)}
- Info: {len(info)}

"""
        
        # Add alert details
        for severity, alert_list in [('CRITICAL', critical), ('WARNING', warnings), 
                                   ('ERROR', errors), ('INFO', info)]:
            if alert_list:
                body += f"\n{severity} ALERTS:\n"
                body += "-" * 20 + "\n"
                
                for alert in alert_list:
                    body += f"Type: {alert['type']}\n"
                    for key, value in alert.items():
                        if key not in ['type', 'severity']:
                            body += f"  {key}: {value}\n"
                    body += "\n"
        
        # Send email
        try:
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.email_config['from']
            msg['To'] = self.email_config['to']
            
            with smtplib.SMTP(self.email_config['smtp_server'], 
                            self.email_config['smtp_port']) as server:
                if self.email_config.get('use_tls'):
                    server.starttls()
                if self.email_config.get('username'):
                    server.login(self.email_config['username'], 
                               self.email_config['password'])
                server.send_message(msg)
                
            print(f"Alert email sent: {subject}")
            
        except Exception as e:
            print(f"Failed to send alert email: {e}")
    
    def run_monitoring_check(self, workloads):
        """Run complete monitoring check."""
        print(f"Running cost monitoring check at {datetime.now()}")
        
        # Check costs
        cost_alerts = self.check_cost_thresholds(workloads)
        
        # Check performance  
        performance_alerts = self.check_performance_health()
        
        # Combine alerts
        all_alerts = cost_alerts + performance_alerts
        
        # Log alerts
        if all_alerts:
            print(f"Found {len(all_alerts)} alerts:")
            for alert in all_alerts:
                severity = alert.get('severity', 'info').upper()
                alert_type = alert.get('type', 'unknown')
                print(f"  [{severity}] {alert_type}")
        else:
            print("No alerts - system healthy")
        
        # Send email if configured
        self.send_alert_email(all_alerts)
        
        # Save alert log
        alert_log = {
            'timestamp': datetime.now().isoformat(),
            'alerts': all_alerts,
            'alert_count': len(all_alerts)
        }
        
        with open(f"cost_alert_log_{datetime.now().strftime('%Y%m%d')}.json", 'a') as f:
            f.write(json.dumps(alert_log) + "\n")
        
        return all_alerts

# Example usage
def setup_cost_monitoring():
    """Set up automated cost monitoring."""
    
    # Define your workloads
    workloads = [
        {
            'name': 'development',
            'instance_type': 't3.medium',
            'daily_hours': 8
        },
        {
            'name': 'ci_cd',
            'instance_type': 'c5.large', 
            'daily_hours': 4
        },
        {
            'name': 'data_processing',
            'instance_type': 'r5.xlarge',
            'daily_hours': 2
        }
    ]
    
    # Email configuration (optional)
    email_config = {
        'smtp_server': 'smtp.gmail.com',
        'smtp_port': 587,
        'use_tls': True,
        'username': 'your-email@gmail.com',
        'password': 'your-app-password',  # Use app password for Gmail
        'from': 'your-email@gmail.com',
        'to': 'alerts@yourcompany.com'
    }
    
    # Initialize alerting system
    alerting = CostAlertingSystem(email_config)
    
    # Run monitoring check
    alerts = alerting.run_monitoring_check(workloads)
    
    return alerts

# Run monitoring
alerts = setup_cost_monitoring()
```

## Advanced Features

### Custom Pricing Sources

Add custom pricing sources or override existing ones:

```python
from clustrix.pricing_clients.base import BasePricingClient

class CustomPricingClient(BasePricingClient):
    """Custom pricing client for internal pricing data."""
    
    def __init__(self):
        self.custom_prices = {
            't3.micro': 0.0104,
            't3.small': 0.0208,
            't3.medium': 0.0416
        }
    
    def authenticate(self, **credentials):
        return True
    
    def get_instance_pricing(self, instance_type, region, **kwargs):
        return self.custom_prices.get(instance_type)
    
    def get_all_pricing(self, region, **kwargs):
        return self.custom_prices.copy()

# Use custom pricing client
custom_client = CustomPricingClient()
price = custom_client.get_instance_pricing("t3.small", "us-east-1")
```

### Integration with CI/CD

Add cost estimation to your CI/CD pipeline:

```python
# cost_check.py - CI/CD cost validation script
import sys
import json
from clustrix.cost_providers.aws import AWSCostMonitor

def validate_deployment_cost():
    """Validate deployment cost against budget."""
    
    # Load deployment configuration
    with open('deployment_config.json', 'r') as f:
        config = json.load(f)
    
    monitor = AWSCostMonitor()
    total_estimated_cost = 0
    
    for resource in config.get('resources', []):
        instance_type = resource['instance_type']
        count = resource['count'] 
        daily_hours = resource.get('daily_hours', 24)  # Default to always on
        
        cost_estimate = monitor.estimate_cost(instance_type, daily_hours)
        resource_cost = cost_estimate.estimated_cost * count
        total_estimated_cost += resource_cost
        
        print(f"{instance_type} x{count}: ${resource_cost:.2f}/day")
    
    monthly_estimate = total_estimated_cost * 30
    
    print(f"\nTotal estimated cost:")
    print(f"Daily: ${total_estimated_cost:.2f}")
    print(f"Monthly: ${monthly_estimate:.2f}")
    
    # Check against budget
    budget_limit = config.get('budget_limit', 1000)  # Default $1000/month
    
    if monthly_estimate > budget_limit:
        print(f"\nERROR: Estimated monthly cost ${monthly_estimate:.2f} exceeds budget limit ${budget_limit:.2f}")
        sys.exit(1)
    
    print(f"\nPASS: Deployment within budget (${monthly_estimate:.2f} < ${budget_limit:.2f})")
    return 0

if __name__ == '__main__':
    validate_deployment_cost()
```

This completes the comprehensive user guide with practical examples for common use cases of Clustrix's pricing system.