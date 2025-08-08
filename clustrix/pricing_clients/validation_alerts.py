"""
Pricing data validation and alerting system.

This module provides comprehensive validation of pricing data and alerting
capabilities for production deployments.
"""

import logging
import time
import json
import smtplib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict, deque
import threading
import requests

from .resilience import PricingDataValidator, get_global_pricing_validator
from .performance_monitor import get_global_performance_monitor

logger = logging.getLogger(__name__)


@dataclass
class ValidationRule:
    """Pricing validation rule configuration."""

    name: str
    description: str
    validator_func: Callable
    severity: str = "warning"  # info, warning, error, critical
    enabled: bool = True


@dataclass
class ValidationResult:
    """Result of a pricing validation check."""

    rule_name: str
    provider: str
    instance_type: str
    region: str
    price: Optional[float]
    passed: bool
    message: str
    severity: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertConfig:
    """Configuration for pricing alerts."""

    # Email settings
    smtp_server: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    use_tls: bool = True
    from_email: Optional[str] = None
    to_emails: List[str] = field(default_factory=list)

    # Webhook settings
    webhook_url: Optional[str] = None
    webhook_headers: Dict[str, str] = field(default_factory=dict)

    # Alert thresholds
    min_severity_email: str = "warning"
    min_severity_webhook: str = "error"
    max_alerts_per_hour: int = 10

    # Aggregation settings
    aggregate_similar_alerts: bool = True
    aggregation_window_minutes: int = 60


class PricingValidationEngine:
    """Engine for validating pricing data against various rules."""

    def __init__(self, validator: Optional[PricingDataValidator] = None):
        """Initialize validation engine."""
        self.validator = validator or get_global_pricing_validator()
        self.rules: Dict[str, ValidationRule] = {}
        self.validation_history: deque = deque(maxlen=10000)

        # Setup default validation rules
        self._setup_default_rules()

    def _setup_default_rules(self):
        """Setup default validation rules."""

        def price_bounds_check(
            provider: str,
            instance_type: str,
            region: str,
            price: Optional[float],
            **kwargs,
        ) -> Tuple[bool, str]:
            """Check if price is within reasonable bounds."""
            if price is None:
                return False, "Price is None"

            if price <= 0:
                return False, f"Price ${price:.4f} is not positive"

            if price < 0.001:
                return False, f"Price ${price:.4f} is suspiciously low (< $0.001/hour)"

            if price > 100.0:
                return False, f"Price ${price:.4f} is suspiciously high (> $100/hour)"

            return True, f"Price ${price:.4f} is within reasonable bounds"

        def price_change_check(
            provider: str,
            instance_type: str,
            region: str,
            price: Optional[float],
            **kwargs,
        ) -> Tuple[bool, str]:
            """Check for dramatic price changes."""
            if price is None:
                return False, "Price is None"

            # Check against historical pricing
            key = f"{provider}:{instance_type}:{region}"
            if key in self.validator.historical_prices:
                historical_price = self.validator.historical_prices[key]

                if historical_price > 0:
                    change_percent = (
                        abs(price - historical_price) / historical_price * 100
                    )

                    if change_percent > 200:  # More than 200% change
                        return False, (
                            f"Price change of {change_percent:.1f}% "
                            f"(${historical_price:.4f} → ${price:.4f}) exceeds threshold"
                        )

            return True, "Price change within acceptable range"

        def gpu_pricing_check(
            provider: str,
            instance_type: str,
            region: str,
            price: Optional[float],
            **kwargs,
        ) -> Tuple[bool, str]:
            """Check GPU instance pricing reasonableness."""
            if price is None:
                return True, "No price to validate"

            # Identify GPU instances by name patterns
            gpu_patterns = ["gpu", "p2", "p3", "p4", "g4", "g5", "nc", "nd", "nv"]
            is_gpu = any(pattern in instance_type.lower() for pattern in gpu_patterns)

            if is_gpu:
                if price < 0.50:
                    return False, f"GPU instance ${price:.4f}/hour is suspiciously low"

                if price > 50.0:
                    return False, f"GPU instance ${price:.4f}/hour is suspiciously high"

            return True, "GPU pricing within expected range"

        def provider_consistency_check(
            provider: str,
            instance_type: str,
            region: str,
            price: Optional[float],
            **kwargs,
        ) -> Tuple[bool, str]:
            """Check consistency with provider's typical pricing patterns."""
            if price is None:
                return True, "No price to validate"

            # Provider-specific checks
            if provider.lower() == "lambda" and price < 0.40:
                return (
                    False,
                    f"Lambda Cloud price ${price:.4f}/hour is below minimum expected",
                )

            if provider.lower() == "aws":
                # Check for micro instances
                if "micro" in instance_type.lower() and price > 0.02:
                    return (
                        False,
                        f"AWS micro instance ${price:.4f}/hour is too expensive",
                    )

            return True, "Provider pricing pattern is consistent"

        # Register default rules
        self.add_validation_rule(
            ValidationRule(
                name="price_bounds",
                description="Check if pricing is within reasonable bounds",
                validator_func=price_bounds_check,
                severity="error",
            )
        )

        self.add_validation_rule(
            ValidationRule(
                name="price_change",
                description="Check for dramatic price changes",
                validator_func=price_change_check,
                severity="warning",
            )
        )

        self.add_validation_rule(
            ValidationRule(
                name="gpu_pricing",
                description="Validate GPU instance pricing",
                validator_func=gpu_pricing_check,
                severity="warning",
            )
        )

        self.add_validation_rule(
            ValidationRule(
                name="provider_consistency",
                description="Check provider-specific pricing consistency",
                validator_func=provider_consistency_check,
                severity="info",
            )
        )

    def add_validation_rule(self, rule: ValidationRule):
        """Add a custom validation rule."""
        self.rules[rule.name] = rule
        logger.info(f"Added validation rule: {rule.name}")

    def remove_validation_rule(self, rule_name: str):
        """Remove a validation rule."""
        if rule_name in self.rules:
            del self.rules[rule_name]
            logger.info(f"Removed validation rule: {rule_name}")

    def validate_price(
        self,
        provider: str,
        instance_type: str,
        region: str,
        price: Optional[float],
        **metadata,
    ) -> List[ValidationResult]:
        """Validate a price against all enabled rules."""
        results = []

        for rule_name, rule in self.rules.items():
            if not rule.enabled:
                continue

            try:
                passed, message = rule.validator_func(
                    provider=provider,
                    instance_type=instance_type,
                    region=region,
                    price=price,
                    **metadata,
                )

                result = ValidationResult(
                    rule_name=rule_name,
                    provider=provider,
                    instance_type=instance_type,
                    region=region,
                    price=price,
                    passed=passed,
                    message=message,
                    severity=rule.severity,
                    metadata=metadata,
                )

                results.append(result)
                self.validation_history.append(result)

            except Exception as e:
                logger.error(f"Validation rule {rule_name} failed: {e}")
                error_result = ValidationResult(
                    rule_name=rule_name,
                    provider=provider,
                    instance_type=instance_type,
                    region=region,
                    price=price,
                    passed=False,
                    message=f"Validation rule error: {e}",
                    severity="error",
                    metadata=metadata,
                )
                results.append(error_result)
                self.validation_history.append(error_result)

        return results

    def get_validation_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get summary of validation results."""
        cutoff_time = datetime.now() - timedelta(hours=hours)

        recent_results = [
            r for r in self.validation_history if r.timestamp >= cutoff_time
        ]

        if not recent_results:
            return {"message": "No validation results in specified time period"}

        # Count results by rule and severity
        rule_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"passed": 0, "failed": 0}
        )
        severity_stats: Dict[str, int] = defaultdict(int)
        provider_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"passed": 0, "failed": 0}
        )

        for result in recent_results:
            if result.passed:
                rule_stats[result.rule_name]["passed"] += 1
                provider_stats[result.provider]["passed"] += 1
            else:
                rule_stats[result.rule_name]["failed"] += 1
                provider_stats[result.provider]["failed"] += 1
                severity_stats[result.severity] += 1

        return {
            "time_period_hours": hours,
            "total_validations": len(recent_results),
            "rule_statistics": dict(rule_stats),
            "severity_statistics": dict(severity_stats),
            "provider_statistics": dict(provider_stats),
            "overall_pass_rate": (
                (
                    sum(p["passed"] for p in provider_stats.values())
                    / len(recent_results)
                )
                if recent_results
                else 0
            ),
        }


class PricingAlertManager:
    """Manager for pricing-related alerts and notifications."""

    def __init__(self, config: AlertConfig):
        """Initialize alert manager."""
        self.config = config
        self.alert_history: deque = deque(maxlen=1000)
        self.alert_counts: Dict[str, int] = defaultdict(int)
        self.last_alert_reset = datetime.now()

        # Aggregated alerts
        self.aggregated_alerts: Dict[str, List[ValidationResult]] = defaultdict(list)
        self.last_aggregation_send = datetime.now()

        # Severity levels (higher number = more severe)
        self.severity_levels = {"info": 1, "warning": 2, "error": 3, "critical": 4}

    def should_send_alert(self, severity: str, alert_type: str) -> bool:
        """Check if alert should be sent based on configuration."""

        # Reset hourly alert counts
        now = datetime.now()
        if (now - self.last_alert_reset).total_seconds() >= 3600:  # 1 hour
            self.alert_counts.clear()
            self.last_alert_reset = now

        # Check alert rate limiting
        current_count = self.alert_counts[alert_type]
        if current_count >= self.config.max_alerts_per_hour:
            logger.warning(f"Alert rate limit reached for {alert_type}")
            return False

        return True

    def send_email_alert(self, subject: str, body: str, severity: str = "warning"):
        """Send email alert."""
        if (
            not self.config.smtp_server
            or not self.config.from_email
            or not self.config.to_emails
        ):
            logger.debug("Email configuration not complete, skipping email alert")
            return

        # Check severity threshold
        min_level = self.severity_levels.get(self.config.min_severity_email, 2)
        alert_level = self.severity_levels.get(severity, 1)

        if alert_level < min_level:
            logger.debug(f"Alert severity {severity} below email threshold")
            return

        try:
            msg = MIMEMultipart()
            msg["From"] = self.config.from_email
            msg["To"] = ", ".join(self.config.to_emails)
            msg["Subject"] = f"[{severity.upper()}] {subject}"

            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.config.smtp_server, self.config.smtp_port) as server:
                if self.config.use_tls:
                    server.starttls()

                if self.config.smtp_username and self.config.smtp_password:
                    server.login(self.config.smtp_username, self.config.smtp_password)

                server.send_message(msg)

            logger.info(f"Email alert sent: {subject}")

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")

    def send_webhook_alert(self, payload: Dict[str, Any], severity: str = "warning"):
        """Send webhook alert."""
        if not self.config.webhook_url:
            logger.debug("Webhook URL not configured, skipping webhook alert")
            return

        # Check severity threshold
        min_level = self.severity_levels.get(self.config.min_severity_webhook, 3)
        alert_level = self.severity_levels.get(severity, 1)

        if alert_level < min_level:
            logger.debug(f"Alert severity {severity} below webhook threshold")
            return

        try:
            headers = {
                "Content-Type": "application/json",
                **self.config.webhook_headers,
            }

            response = requests.post(
                self.config.webhook_url, json=payload, headers=headers, timeout=30
            )

            response.raise_for_status()
            logger.info("Webhook alert sent successfully")

        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")

    def handle_validation_results(self, results: List[ValidationResult]):
        """Handle validation results and send alerts if needed."""
        failed_results = [r for r in results if not r.passed]

        if not failed_results:
            return

        if self.config.aggregate_similar_alerts:
            # Add to aggregated alerts
            for result in failed_results:
                key = f"{result.rule_name}:{result.severity}"
                self.aggregated_alerts[key].append(result)
        else:
            # Send individual alerts immediately
            for result in failed_results:
                self._send_individual_alert(result)

    def _send_individual_alert(self, result: ValidationResult):
        """Send individual alert for a validation result."""
        alert_type = f"{result.rule_name}_{result.provider}"

        if not self.should_send_alert(result.severity, alert_type):
            return

        subject = f"Pricing Validation Failed: {result.rule_name}"
        body = f"""
Pricing validation alert from Clustrix

Rule: {result.rule_name}
Provider: {result.provider}
Instance Type: {result.instance_type}
Region: {result.region}
Price: ${result.price:.4f} if result.price else 'None'
Message: {result.message}
Severity: {result.severity}
Timestamp: {result.timestamp}

Metadata: {json.dumps(result.metadata, indent=2)}
"""

        # Send email
        self.send_email_alert(subject, body, result.severity)

        # Send webhook
        webhook_payload = {
            "alert_type": "pricing_validation",
            "rule_name": result.rule_name,
            "provider": result.provider,
            "instance_type": result.instance_type,
            "region": result.region,
            "price": result.price,
            "message": result.message,
            "severity": result.severity,
            "timestamp": result.timestamp.isoformat(),
            "metadata": result.metadata,
        }

        self.send_webhook_alert(webhook_payload, result.severity)

        # Record alert
        self.alert_counts[alert_type] += 1
        self.alert_history.append(
            {
                "timestamp": datetime.now(),
                "alert_type": alert_type,
                "severity": result.severity,
                "result": result,
            }
        )

    def send_aggregated_alerts(self):
        """Send aggregated alerts."""
        now = datetime.now()
        time_since_last = (
            now - self.last_aggregation_send
        ).total_seconds() / 60  # minutes

        if time_since_last < self.config.aggregation_window_minutes:
            return

        if not self.aggregated_alerts:
            return

        # Group alerts by severity
        severity_groups = defaultdict(list)
        for key, results in self.aggregated_alerts.items():
            rule_name, severity = key.split(":", 1)
            severity_groups[severity].extend(results)

        # Send aggregated email
        total_alerts = sum(len(results) for results in self.aggregated_alerts.values())
        subject = f"Pricing Validation Summary: {total_alerts} alerts"

        body = f"""
Pricing Validation Alert Summary
Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}
Time Window: {self.config.aggregation_window_minutes} minutes

Total Alerts: {total_alerts}

"""

        for severity in ["critical", "error", "warning", "info"]:
            if severity not in severity_groups:
                continue

            results = severity_groups[severity]
            body += f"\n{severity.upper()} ALERTS ({len(results)}):\n"
            body += "-" * 40 + "\n"

            # Group by rule and provider
            rule_counts = defaultdict(int)
            provider_counts = defaultdict(int)

            for result in results:
                rule_counts[result.rule_name] += 1
                provider_counts[result.provider] += 1

            body += "By Rule:\n"
            for rule, count in rule_counts.items():
                body += f"  {rule}: {count}\n"

            body += "By Provider:\n"
            for provider, count in provider_counts.items():
                body += f"  {provider}: {count}\n"

            body += "\nRecent Examples:\n"
            for result in results[:3]:  # Show first 3 examples
                body += (
                    f"  {result.provider} {result.instance_type}: {result.message}\n"
                )

            if len(results) > 3:
                body += f"  ... and {len(results) - 3} more\n"
            body += "\n"

        # Determine overall severity
        if severity_groups["critical"]:
            overall_severity = "critical"
        elif severity_groups["error"]:
            overall_severity = "error"
        elif severity_groups["warning"]:
            overall_severity = "warning"
        else:
            overall_severity = "info"

        # Send email
        self.send_email_alert(subject, body, overall_severity)

        # Send webhook
        webhook_payload = {
            "alert_type": "pricing_validation_summary",
            "total_alerts": total_alerts,
            "time_window_minutes": self.config.aggregation_window_minutes,
            "severity_breakdown": {
                s: len(results) for s, results in severity_groups.items()
            },
            "timestamp": now.isoformat(),
            "overall_severity": overall_severity,
        }

        self.send_webhook_alert(webhook_payload, overall_severity)

        # Clear aggregated alerts
        self.aggregated_alerts.clear()
        self.last_aggregation_send = now

        logger.info(f"Sent aggregated alert summary: {total_alerts} alerts")


class PricingMonitoringService:
    """Complete pricing monitoring service with validation and alerting."""

    def __init__(
        self,
        validation_engine: Optional[PricingValidationEngine] = None,
        alert_manager: Optional[PricingAlertManager] = None,
    ):
        """Initialize monitoring service."""
        self.validation_engine = validation_engine or PricingValidationEngine()
        self.alert_manager = alert_manager
        self.performance_monitor = get_global_performance_monitor()

        self.monitoring_active = False
        self.monitoring_thread = None
        self.monitoring_interval = 300  # 5 minutes

    def validate_and_alert(
        self,
        provider: str,
        instance_type: str,
        region: str,
        price: Optional[float],
        **metadata,
    ) -> List[ValidationResult]:
        """Validate pricing and send alerts if needed."""

        # Run validation
        results = self.validation_engine.validate_price(
            provider, instance_type, region, price, **metadata
        )

        # Send alerts for failures
        if self.alert_manager:
            self.alert_manager.handle_validation_results(results)

        return results

    def start_monitoring(self):
        """Start background monitoring service."""
        if self.monitoring_active:
            logger.warning("Monitoring service already active")
            return

        self.monitoring_active = True
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop, daemon=True
        )
        self.monitoring_thread.start()

        logger.info("Pricing monitoring service started")

    def stop_monitoring(self):
        """Stop background monitoring service."""
        self.monitoring_active = False

        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=10)

        logger.info("Pricing monitoring service stopped")

    def _monitoring_loop(self):
        """Main monitoring loop."""
        while self.monitoring_active:
            try:
                # Send aggregated alerts
                if self.alert_manager:
                    self.alert_manager.send_aggregated_alerts()

                # Monitor system health
                self._check_system_health()

                # Sleep until next check
                time.sleep(self.monitoring_interval)

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(60)  # Sleep 1 minute on error

    def _check_system_health(self):
        """Check overall system health."""
        try:
            # Get performance summary
            perf_summary = self.performance_monitor.get_performance_summary(hours=1)

            # Check for performance issues
            if perf_summary.get("error_rate", 0) > 0.10:  # 10% error rate
                if self.alert_manager:
                    subject = "High Pricing API Error Rate"
                    body = f"""
High error rate detected in pricing system:

Error Rate: {perf_summary['error_rate']:.2%}
Total Requests: {perf_summary.get('total_requests', 0)}
Time Period: 1 hour

Provider Breakdown:
{json.dumps(perf_summary.get('provider_summary', {}), indent=2)}
"""
                    self.alert_manager.send_email_alert(subject, body, "warning")

            # Check for slow response times
            avg_response_time = perf_summary.get("average_response_time", 0)
            if avg_response_time > 30.0:  # 30 seconds
                if self.alert_manager:
                    subject = "Slow Pricing API Response Times"
                    body = f"""
Slow response times detected in pricing system:

Average Response Time: {avg_response_time:.2f} seconds
Total Requests: {perf_summary.get('total_requests', 0)}
Time Period: 1 hour

This may indicate API throttling or network issues.
"""
                    self.alert_manager.send_email_alert(subject, body, "info")

        except Exception as e:
            logger.error(f"Error checking system health: {e}")

    def get_monitoring_status(self) -> Dict[str, Any]:
        """Get current monitoring status."""
        return {
            "monitoring_active": self.monitoring_active,
            "monitoring_interval_seconds": self.monitoring_interval,
            "validation_rules_count": len(self.validation_engine.rules),
            "alert_manager_configured": self.alert_manager is not None,
            "validation_summary": self.validation_engine.get_validation_summary(),
            "performance_summary": self.performance_monitor.get_performance_summary(),
            "alert_history_count": (
                len(self.alert_manager.alert_history) if self.alert_manager else 0
            ),
        }


# Global monitoring service instance
_global_monitoring_service = None


def get_global_monitoring_service() -> PricingMonitoringService:
    """Get the global monitoring service instance."""
    global _global_monitoring_service
    if _global_monitoring_service is None:
        _global_monitoring_service = PricingMonitoringService()
    return _global_monitoring_service


def configure_monitoring_service(
    alert_config: Optional[AlertConfig] = None,
) -> PricingMonitoringService:
    """Configure the global monitoring service with alert settings."""
    global _global_monitoring_service

    validation_engine = PricingValidationEngine()
    alert_manager = PricingAlertManager(alert_config) if alert_config else None

    _global_monitoring_service = PricingMonitoringService(
        validation_engine, alert_manager
    )

    return _global_monitoring_service
