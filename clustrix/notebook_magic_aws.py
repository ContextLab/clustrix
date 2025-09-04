"""
AWS cloud provider notebook magic integration for Clustrix.

This module provides AWS-specific configuration widgets and functionality
for use in Jupyter notebooks with Clustrix.
"""

import logging
from typing import Dict, List, Optional, Any

try:
    import ipywidgets as widgets  # type: ignore

    IPYTHON_AVAILABLE = True
except ImportError:
    IPYTHON_AVAILABLE = False
    from .notebook_magic_mocks import widgets

try:
    import boto3  # type: ignore
    from botocore.exceptions import ClientError, NoCredentialsError  # type: ignore

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None
    ClientError = Exception
    NoCredentialsError = Exception

from .cloud_providers.aws import AWSProvider

logger = logging.getLogger(__name__)


def configure_aws_credentials(
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    region: str = "us-east-1",
    session_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Configure AWS credentials for Clustrix.

    Args:
        access_key_id: AWS access key ID
        secret_access_key: AWS secret access key
        region: AWS region (default: us-east-1)
        session_token: Optional session token for temporary credentials

    Returns:
        Dict with credential configuration status
    """
    if not BOTO3_AVAILABLE:
        return {
            "success": False,
            "error": "boto3 is not installed. Install with: pip install boto3",
            "credentials_valid": False,
        }

    try:
        # Create AWS provider instance
        provider = AWSProvider()

        # Build credentials dict
        credentials = {
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "region": region,
        }
        if session_token:
            credentials["session_token"] = session_token

        # Test authentication
        auth_success = provider.authenticate(**credentials)

        if auth_success:
            return {
                "success": True,
                "message": f"Successfully configured AWS credentials for region {region}",
                "credentials_valid": True,
                "region": region,
                "provider": "aws",
            }
        else:
            return {
                "success": False,
                "error": "Failed to authenticate with provided AWS credentials",
                "credentials_valid": False,
            }

    except Exception as e:
        logger.error(f"Error configuring AWS credentials: {e}")
        return {
            "success": False,
            "error": f"Error configuring AWS credentials: {str(e)}",
            "credentials_valid": False,
        }


def validate_aws_connection(
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    region: str = "us-east-1",
    session_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate AWS connection and credentials.

    Args:
        access_key_id: AWS access key ID
        secret_access_key: AWS secret access key
        region: AWS region
        session_token: Optional session token

    Returns:
        Dict with validation results
    """
    if not BOTO3_AVAILABLE:
        return {"valid": False, "error": "boto3 is not installed", "details": {}}

    try:
        # Create session and test connection
        session_kwargs = {
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region,
        }
        if session_token:
            session_kwargs["aws_session_token"] = session_token

        session = boto3.Session(**session_kwargs)

        # Test with STS to get caller identity
        sts_client = session.client("sts")
        caller_identity = sts_client.get_caller_identity()

        # Get available EC2 instance types for region validation
        ec2_client = session.client("ec2")
        instance_offerings = ec2_client.describe_instance_type_offerings(
            LocationType="region", Filters=[{"Name": "location", "Values": [region]}]
        )

        return {
            "valid": True,
            "message": "AWS connection validated successfully",
            "details": {
                "account_id": caller_identity.get("Account"),
                "user_id": caller_identity.get("UserId"),
                "arn": caller_identity.get("Arn"),
                "region": region,
                "available_instance_types": len(
                    instance_offerings.get("InstanceTypeOfferings", [])
                ),
            },
        }

    except NoCredentialsError:
        return {
            "valid": False,
            "error": "Invalid or missing AWS credentials",
            "details": {},
        }
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        return {
            "valid": False,
            "error": f"AWS API error ({error_code}): {str(e)}",
            "details": {"error_code": error_code},
        }
    except Exception as e:
        return {
            "valid": False,
            "error": f"Connection validation failed: {str(e)}",
            "details": {},
        }


def list_aws_resources(
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    region: str = "us-east-1",
    session_token: Optional[str] = None,
    resource_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    List available AWS resources.

    Args:
        access_key_id: AWS access key ID
        secret_access_key: AWS secret access key
        region: AWS region
        session_token: Optional session token
        resource_types: List of resource types to query (defaults to common types)

    Returns:
        Dict with discovered resources
    """
    if not BOTO3_AVAILABLE:
        return {"success": False, "error": "boto3 is not installed", "resources": {}}

    if resource_types is None:
        resource_types = ["ec2_instances", "eks_clusters", "vpc"]

    try:
        session_kwargs = {
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region,
        }
        if session_token:
            session_kwargs["aws_session_token"] = session_token

        session = boto3.Session(**session_kwargs)
        resources = {}

        # List EC2 instances
        if "ec2_instances" in resource_types:
            try:
                ec2_client = session.client("ec2")
                response = ec2_client.describe_instances()
                instances = []
                for reservation in response.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        instances.append(
                            {
                                "instance_id": instance.get("InstanceId"),
                                "state": instance.get("State", {}).get("Name"),
                                "type": instance.get("InstanceType"),
                                "public_ip": instance.get("PublicIpAddress"),
                                "private_ip": instance.get("PrivateIpAddress"),
                            }
                        )
                resources["ec2_instances"] = instances
            except Exception as e:
                resources["ec2_instances"] = [{"error": str(e)}]

        # List EKS clusters
        if "eks_clusters" in resource_types:
            try:
                eks_client = session.client("eks")
                response = eks_client.list_clusters()
                clusters = []
                for cluster_name in response.get("clusters", []):
                    cluster_details = eks_client.describe_cluster(name=cluster_name)
                    cluster = cluster_details.get("cluster", {})
                    clusters.append(
                        {
                            "name": cluster_name,
                            "status": cluster.get("status"),
                            "version": cluster.get("version"),
                            "endpoint": cluster.get("endpoint"),
                        }
                    )
                resources["eks_clusters"] = clusters
            except Exception as e:
                resources["eks_clusters"] = [{"error": str(e)}]

        # List VPCs
        if "vpc" in resource_types:
            try:
                ec2_client = session.client("ec2")
                response = ec2_client.describe_vpcs()
                vpcs = []
                for vpc in response.get("Vpcs", []):
                    vpcs.append(
                        {
                            "vpc_id": vpc.get("VpcId"),
                            "cidr_block": vpc.get("CidrBlock"),
                            "state": vpc.get("State"),
                            "is_default": vpc.get("IsDefault", False),
                        }
                    )
                resources["vpcs"] = vpcs
            except Exception as e:
                resources["vpcs"] = [{"error": str(e)}]

        return {"success": True, "region": region, "resources": resources}

    except Exception as e:
        logger.error(f"Error listing AWS resources: {e}")
        return {
            "success": False,
            "error": f"Failed to list AWS resources: {str(e)}",
            "resources": {},
        }


def get_aws_regions() -> List[str]:
    """
    Get list of available AWS regions.

    Returns:
        List of AWS region names
    """
    if not BOTO3_AVAILABLE:
        # Return common regions if boto3 not available
        return [
            "us-east-1",
            "us-west-1",
            "us-west-2",
            "eu-west-1",
            "eu-central-1",
            "ap-southeast-1",
            "ap-northeast-1",
        ]

    try:
        # Use default session to get regions (no credentials needed for this)
        ec2 = boto3.client("ec2", region_name="us-east-1")
        response = ec2.describe_regions()
        regions = [region["RegionName"] for region in response["Regions"]]
        return sorted(regions)
    except Exception:
        # Fallback to common regions
        return [
            "us-east-1",
            "us-west-1",
            "us-west-2",
            "eu-west-1",
            "eu-central-1",
            "ap-southeast-1",
            "ap-northeast-1",
        ]


def get_aws_instance_types(region: str = "us-east-1") -> List[str]:
    """
    Get available EC2 instance types for a region.

    Args:
        region: AWS region

    Returns:
        List of instance type names
    """
    if not BOTO3_AVAILABLE:
        return [
            "t3.micro",
            "t3.small",
            "t3.medium",
            "t3.large",
            "t3.xlarge",
            "c5.large",
            "c5.xlarge",
            "c5.2xlarge",
            "m5.large",
            "m5.xlarge",
            "m5.2xlarge",
        ]

    try:
        ec2 = boto3.client("ec2", region_name=region)
        response = ec2.describe_instance_type_offerings(
            LocationType="region", Filters=[{"Name": "location", "Values": [region]}]
        )
        instance_types = [
            offering["InstanceType"] for offering in response["InstanceTypeOfferings"]
        ]

        # Filter to common families and sort
        common_families = ["t3", "t2", "c5", "c4", "m5", "m4", "r5", "r4"]
        filtered_types = []

        for family in common_families:
            family_types = [t for t in instance_types if t.startswith(family + ".")]
            filtered_types.extend(sorted(family_types)[:6])  # Limit per family

        return filtered_types[:30]  # Limit total results

    except Exception:
        return [
            "t3.micro",
            "t3.small",
            "t3.medium",
            "t3.large",
            "t3.xlarge",
            "c5.large",
            "c5.xlarge",
            "c5.2xlarge",
            "m5.large",
            "m5.xlarge",
            "m5.2xlarge",
        ]


def create_aws_config_widget() -> widgets.Widget:
    """
    Create AWS configuration widget for Jupyter notebooks.

    Returns:
        IPython widget for AWS configuration
    """
    if not IPYTHON_AVAILABLE:
        raise ImportError("IPython and ipywidgets are required")

    # Style configuration
    style = {"description_width": "150px"}
    layout = widgets.Layout(width="400px")

    # Create input widgets
    access_key_widget = widgets.Text(
        description="Access Key ID:",
        style=style,
        layout=layout,
        placeholder="Enter AWS access key ID",
    )

    secret_key_widget = widgets.Password(
        description="Secret Key:",
        style=style,
        layout=layout,
        placeholder="Enter AWS secret access key",
    )

    region_widget = widgets.Dropdown(
        description="Region:",
        options=get_aws_regions(),
        value="us-east-1",
        style=style,
        layout=layout,
    )

    session_token_widget = widgets.Password(
        description="Session Token:",
        style=style,
        layout=layout,
        placeholder="Optional session token",
    )

    # Test connection button
    test_button = widgets.Button(
        description="Test Connection",
        button_style="primary",
        layout=widgets.Layout(width="150px"),
    )

    # Output area
    output = widgets.Output()

    def on_test_click(button):
        """Handle test connection button click."""
        with output:
            output.clear_output()
            print("Testing AWS connection...")

            result = validate_aws_connection(
                access_key_id=access_key_widget.value or None,
                secret_access_key=secret_key_widget.value or None,
                region=region_widget.value,
                session_token=session_token_widget.value or None,
            )

            if result["valid"]:
                print("✅ Connection successful!")
                if result.get("details"):
                    print(f"Account: {result['details'].get('account_id', 'N/A')}")
                    print(f"Region: {result['details'].get('region', 'N/A')}")
            else:
                print(f"❌ Connection failed: {result.get('error', 'Unknown error')}")

    test_button.on_click(on_test_click)

    # Create layout
    return widgets.VBox(
        [
            widgets.HTML("<h3>AWS Configuration</h3>"),
            access_key_widget,
            secret_key_widget,
            region_widget,
            session_token_widget,
            test_button,
            output,
        ]
    )
