#!/usr/bin/env python3
"""
Setup script for Clustrix pricing monitoring and alerting system.

This script helps configure and start the pricing monitoring service with
validation rules and alerting capabilities.
"""

import os
import sys
import json
import logging
from typing import Dict, Any
from pathlib import Path
from datetime import datetime

# Add clustrix to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.pricing_clients.validation_alerts import (
    AlertConfig,
    PricingValidationEngine,
    PricingAlertManager,
    PricingMonitoringService,
    configure_monitoring_service,
)


def load_config_from_file(config_path: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Configuration file not found: {config_path}")
        return {}
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in configuration file: {e}")
        return {}


def create_sample_config():
    """Create a sample configuration file."""
    sample_config = {
        "alert_config": {
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_username": "your-email@gmail.com",
            "smtp_password": "your-app-password",
            "use_tls": True,
            "from_email": "your-email@gmail.com",
            "to_emails": ["alerts@yourcompany.com"],
            "webhook_url": "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK",
            "webhook_headers": {"Content-Type": "application/json"},
            "min_severity_email": "warning",
            "min_severity_webhook": "error",
            "max_alerts_per_hour": 10,
            "aggregate_similar_alerts": True,
            "aggregation_window_minutes": 60,
        },
        "validation_rules": {
            "price_bounds": {"enabled": True, "severity": "error"},
            "price_change": {"enabled": True, "severity": "warning"},
            "gpu_pricing": {"enabled": True, "severity": "warning"},
            "provider_consistency": {"enabled": True, "severity": "info"},
        },
        "monitoring": {
            "monitoring_interval_seconds": 300,
            "enable_background_monitoring": True,
        },
    }

    config_file = "pricing_monitoring_config.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f, indent=4)

    print(f"Sample configuration created: {config_file}")
    print(
        "Please edit this file with your actual settings before running the monitoring service."
    )

    return sample_config


def setup_logging():
    """Setup logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("pricing_monitoring.log"),
        ],
    )


def test_email_configuration(alert_config: AlertConfig):
    """Test email configuration."""
    print("Testing email configuration...")

    if not all(
        [alert_config.smtp_server, alert_config.from_email, alert_config.to_emails]
    ):
        print("❌ Email configuration incomplete")
        return False

    try:
        alert_manager = PricingAlertManager(alert_config)
        alert_manager.send_email_alert(
            subject="Clustrix Pricing Monitoring Test",
            body="This is a test email from Clustrix pricing monitoring system.",
            severity="info",
        )
        print("✅ Test email sent successfully")
        return True

    except Exception as e:
        print(f"❌ Email test failed: {e}")
        return False


def test_webhook_configuration(alert_config: AlertConfig):
    """Test webhook configuration."""
    print("Testing webhook configuration...")

    if not alert_config.webhook_url:
        print("❌ Webhook URL not configured")
        return False

    try:
        alert_manager = PricingAlertManager(alert_config)
        alert_manager.send_webhook_alert(
            payload={
                "message": "Test webhook from Clustrix pricing monitoring system",
                "alert_type": "test",
                "timestamp": "2024-01-01T12:00:00Z",
            },
            severity="info",
        )
        print("✅ Test webhook sent successfully")
        return True

    except Exception as e:
        print(f"❌ Webhook test failed: {e}")
        return False


def test_pricing_validation():
    """Test pricing validation system."""
    print("Testing pricing validation...")

    validation_engine = PricingValidationEngine()

    # Test cases
    test_cases = [
        ("aws", "t3.medium", "us-east-1", 0.0416, "Valid price"),
        ("aws", "t3.medium", "us-east-1", 0.0001, "Suspiciously low price"),
        ("aws", "t3.medium", "us-east-1", 100.0, "Suspiciously high price"),
        ("aws", "g4dn.xlarge", "us-east-1", 0.526, "Valid GPU price"),
        ("lambda", "gpu_1x_a10", "us-east-1", 0.75, "Valid Lambda price"),
    ]

    all_passed = True

    for provider, instance_type, region, price, description in test_cases:
        print(f"\nTesting: {description}")
        print(f"  {provider} {instance_type} in {region}: ${price:.4f}/hour")

        results = validation_engine.validate_price(
            provider, instance_type, region, price
        )

        failed_validations = [r for r in results if not r.passed]
        if failed_validations:
            print(f"  ❌ {len(failed_validations)} validation(s) failed:")
            for result in failed_validations:
                print(f"    - {result.rule_name} ({result.severity}): {result.message}")
            if (
                description == "Valid price"
                or description == "Valid GPU price"
                or description == "Valid Lambda price"
            ):
                all_passed = False
        else:
            print(f"  ✅ All validations passed")
            if "Suspiciously" in description:
                print(f"    ⚠️  Warning: Expected this test case to fail")
                all_passed = False

    return all_passed


def main():
    """Main setup function."""
    print("Clustrix Pricing Monitoring Setup")
    print("=" * 40)

    # Setup logging
    setup_logging()

    # Check for configuration file
    config_file = "pricing_monitoring_config.json"
    if not os.path.exists(config_file):
        print(f"Configuration file not found: {config_file}")
        print("Creating sample configuration...")
        create_sample_config()
        print("\nPlease edit the configuration file and run this script again.")
        return

    # Load configuration
    print(f"Loading configuration from {config_file}...")
    config = load_config_from_file(config_file)

    if not config:
        print("Failed to load configuration. Exiting.")
        return

    # Create alert configuration
    alert_config_data = config.get("alert_config", {})
    alert_config = AlertConfig(
        smtp_server=alert_config_data.get("smtp_server"),
        smtp_port=alert_config_data.get("smtp_port", 587),
        smtp_username=alert_config_data.get("smtp_username"),
        smtp_password=alert_config_data.get("smtp_password"),
        use_tls=alert_config_data.get("use_tls", True),
        from_email=alert_config_data.get("from_email"),
        to_emails=alert_config_data.get("to_emails", []),
        webhook_url=alert_config_data.get("webhook_url"),
        webhook_headers=alert_config_data.get("webhook_headers", {}),
        min_severity_email=alert_config_data.get("min_severity_email", "warning"),
        min_severity_webhook=alert_config_data.get("min_severity_webhook", "error"),
        max_alerts_per_hour=alert_config_data.get("max_alerts_per_hour", 10),
        aggregate_similar_alerts=alert_config_data.get(
            "aggregate_similar_alerts", True
        ),
        aggregation_window_minutes=alert_config_data.get(
            "aggregation_window_minutes", 60
        ),
    )

    # Configure monitoring service
    print("Configuring monitoring service...")
    monitoring_service = configure_monitoring_service(alert_config)

    # Configure validation rules
    validation_rules_config = config.get("validation_rules", {})
    for rule_name, rule_config in validation_rules_config.items():
        if rule_name in monitoring_service.validation_engine.rules:
            rule = monitoring_service.validation_engine.rules[rule_name]
            rule.enabled = rule_config.get("enabled", True)
            rule.severity = rule_config.get("severity", rule.severity)

    print("✅ Monitoring service configured")

    # Run tests
    print("\nRunning configuration tests...")

    # Test pricing validation
    validation_ok = test_pricing_validation()

    # Test email if configured
    email_ok = True
    if alert_config.smtp_server and alert_config.from_email:
        email_ok = test_email_configuration(alert_config)
    else:
        print("📧 Email not configured, skipping email test")

    # Test webhook if configured
    webhook_ok = True
    if alert_config.webhook_url:
        webhook_ok = test_webhook_configuration(alert_config)
    else:
        print("🔗 Webhook not configured, skipping webhook test")

    # Summary
    print("\nConfiguration Test Summary:")
    print(f"  Pricing Validation: {'✅' if validation_ok else '❌'}")
    print(f"  Email Alerts: {'✅' if email_ok else '❌'}")
    print(f"  Webhook Alerts: {'✅' if webhook_ok else '❌'}")

    if not all([validation_ok, email_ok, webhook_ok]):
        print("\n⚠️  Some tests failed. Please check your configuration.")
        return

    # Start monitoring service
    monitoring_config = config.get("monitoring", {})
    if monitoring_config.get("enable_background_monitoring", True):
        print("\nStarting background monitoring service...")
        monitoring_service.start_monitoring()

        monitoring_interval = monitoring_config.get("monitoring_interval_seconds", 300)
        print(
            f"✅ Monitoring service started (checking every {monitoring_interval} seconds)"
        )
        print("📋 Monitoring status:")

        status = monitoring_service.get_monitoring_status()
        print(f"  Active: {status['monitoring_active']}")
        print(f"  Validation Rules: {status['validation_rules_count']}")
        print(
            f"  Alert Manager: {'configured' if status['alert_manager_configured'] else 'not configured'}"
        )

        print("\nMonitoring service is now running in the background.")
        print("Check pricing_monitoring.log for detailed logs.")
        print("To stop the service, use Ctrl+C or kill the process.")

        try:
            # Keep the script running
            import time

            while True:
                time.sleep(60)
                # Print periodic status
                print(f"[{datetime.now()}] Monitoring active...")
        except KeyboardInterrupt:
            print("\nShutting down monitoring service...")
            monitoring_service.stop_monitoring()
            print("Monitoring service stopped.")
    else:
        print("\nBackground monitoring disabled in configuration.")
        print(
            "To enable background monitoring, set 'enable_background_monitoring': true in the config file."
        )


if __name__ == "__main__":
    main()
