"""AWS cloud provider integration for Clustrix."""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None
    ClientError = Exception
    NoCredentialsError = Exception

from .base import CloudProvider
from . import PROVIDERS

logger = logging.getLogger(__name__)


class AWSProvider(CloudProvider):
    """AWS cloud provider implementation."""

    def __init__(self):
        """Initialize AWS provider."""
        super().__init__()
        self.ec2_client = None
        self.eks_client = None
        self.iam_client = None
        self.region = "us-east-1"

    def authenticate(self, **credentials) -> bool:
        """
        Authenticate with AWS.

        Args:
            **credentials: AWS credentials including:
                - access_key_id: AWS access key ID
                - secret_access_key: AWS secret access key
                - region: AWS region (default: us-east-1)
                - session_token: Optional session token for temporary credentials

        Returns:
            bool: True if authentication successful
        """
        access_key_id = credentials.get("access_key_id")
        secret_access_key = credentials.get("secret_access_key")
        region = credentials.get("region", "us-east-1")
        session_token = credentials.get("session_token")

        if not access_key_id or not secret_access_key:
            logger.error("access_key_id and secret_access_key are required")
            return False
        if not BOTO3_AVAILABLE:
            logger.error("boto3 is not installed. Install with: pip install boto3")
            return False

        try:
            # Create session with provided credentials
            session = boto3.Session(
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                aws_session_token=session_token,
                region_name=region,
            )

            # Initialize clients
            self.ec2_client = session.client("ec2")
            self.eks_client = session.client("eks")
            self.iam_client = session.client("iam")

            # Test credentials by making a simple API call
            self.iam_client.get_user()

            self.region = region
            self.credentials = {
                "access_key_id": access_key_id,
                "secret_access_key": secret_access_key,
                "region": region,
            }
            if session_token:
                self.credentials["session_token"] = session_token

            self.authenticated = True
            logger.info(f"Successfully authenticated with AWS in region {region}")
            return True

        except NoCredentialsError:
            logger.error("Invalid AWS credentials")
            return False
        except ClientError as e:
            logger.error(f"AWS authentication failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during AWS authentication: {e}")
            return False

    def validate_credentials(self) -> bool:
        """Validate current AWS credentials."""
        if not self.authenticated or not self.iam_client:
            return False

        try:
            self.iam_client.get_user()
            return True
        except Exception:
            return False

    def create_eks_cluster(
        self,
        cluster_name: str,
        node_count: int = 2,
        instance_type: str = "t3.medium",
        kubernetes_version: str = "1.27",
    ) -> Dict[str, Any]:
        """
        Create an EKS cluster.

        Args:
            cluster_name: Name for the EKS cluster
            node_count: Number of worker nodes
            instance_type: EC2 instance type for nodes
            kubernetes_version: Kubernetes version

        Returns:
            Dict with cluster information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with AWS")

        try:
            # This is a simplified version - real implementation would need:
            # 1. VPC creation/selection
            # 2. IAM role creation
            # 3. Security group setup
            # 4. Node group creation

            logger.info(f"Creating EKS cluster '{cluster_name}'...")

            # For now, return a placeholder
            # TODO: Implement full EKS creation
            return {
                "cluster_name": cluster_name,
                "status": "CREATING",
                "endpoint": f"https://{cluster_name}.eks.{self.region}.amazonaws.com",
                "node_count": node_count,
                "instance_type": instance_type,
                "region": self.region,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except ClientError as e:
            logger.error(f"Failed to create EKS cluster: {e}")
            raise

    def create_ec2_instance(
        self,
        instance_name: str,
        instance_type: str = "t3.medium",
        ami_id: Optional[str] = None,
        key_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create an EC2 instance.

        Args:
            instance_name: Name tag for the instance
            instance_type: EC2 instance type
            ami_id: AMI ID (uses Amazon Linux 2 if not specified)
            key_name: SSH key pair name

        Returns:
            Dict with instance information
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with AWS")

        try:
            # Get default AMI if not specified
            if not ami_id:
                # Get latest Amazon Linux 2 AMI
                response = self.ec2_client.describe_images(
                    Owners=["amazon"],
                    Filters=[
                        {"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
                        {"Name": "state", "Values": ["available"]},
                    ],
                )
                ami_id = sorted(
                    response["Images"], key=lambda x: x["CreationDate"], reverse=True
                )[0]["ImageId"]

            # Create instance
            response = self.ec2_client.run_instances(
                ImageId=ami_id,
                InstanceType=instance_type,
                MinCount=1,
                MaxCount=1,
                KeyName=key_name,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": instance_name}],
                    }
                ],
            )

            instance = response["Instances"][0]
            instance_id = instance["InstanceId"]

            # Wait for instance to get public IP
            waiter = self.ec2_client.get_waiter("instance_running")
            waiter.wait(InstanceIds=[instance_id])

            # Get updated instance info
            response = self.ec2_client.describe_instances(InstanceIds=[instance_id])
            instance = response["Reservations"][0]["Instances"][0]

            return {
                "instance_id": instance_id,
                "instance_name": instance_name,
                "public_ip": instance.get("PublicIpAddress", ""),
                "private_ip": instance.get("PrivateIpAddress", ""),
                "instance_type": instance_type,
                "state": instance["State"]["Name"],
                "region": self.region,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        except ClientError as e:
            logger.error(f"Failed to create EC2 instance: {e}")
            raise

    def create_cluster(
        self, cluster_name: str, cluster_type: str = "eks", **kwargs
    ) -> Dict[str, Any]:
        """
        Create a cluster (EKS or EC2).

        Args:
            cluster_name: Name for the cluster
            cluster_type: Type of cluster ('eks' or 'ec2')
            **kwargs: Additional parameters for cluster creation

        Returns:
            Dict with cluster information
        """
        if cluster_type == "eks":
            return self.create_eks_cluster(cluster_name, **kwargs)
        elif cluster_type == "ec2":
            return self.create_ec2_instance(cluster_name, **kwargs)
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def delete_cluster(
        self, cluster_identifier: str, cluster_type: str = "eks"
    ) -> bool:
        """
        Delete a cluster.

        Args:
            cluster_identifier: Cluster name or instance ID
            cluster_type: Type of cluster ('eks' or 'ec2')

        Returns:
            bool: True if deletion successful
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with AWS")

        try:
            if cluster_type == "eks":
                # TODO: Implement EKS deletion
                logger.info(f"Deleting EKS cluster '{cluster_identifier}'...")
                return True
            elif cluster_type == "ec2":
                self.ec2_client.terminate_instances(InstanceIds=[cluster_identifier])
                logger.info(f"Terminated EC2 instance '{cluster_identifier}'")
                return True
            else:
                raise ValueError(f"Unknown cluster type: {cluster_type}")

        except ClientError as e:
            logger.error(f"Failed to delete cluster: {e}")
            return False

    def get_cluster_status(
        self, cluster_identifier: str, cluster_type: str = "eks"
    ) -> Dict[str, Any]:
        """Get status of a cluster."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with AWS")

        try:
            if cluster_type == "eks":
                # TODO: Implement real EKS status check
                return {
                    "cluster_name": cluster_identifier,
                    "status": "ACTIVE",
                    "cluster_type": "eks",
                }
            elif cluster_type == "ec2":
                response = self.ec2_client.describe_instances(
                    InstanceIds=[cluster_identifier]
                )
                instance = response["Reservations"][0]["Instances"][0]
                return {
                    "instance_id": cluster_identifier,
                    "status": instance["State"]["Name"],
                    "cluster_type": "ec2",
                }
            else:
                raise ValueError(f"Unknown cluster type: {cluster_type}")
        except ClientError as e:
            logger.error(f"Failed to get cluster status: {e}")
            raise

    def list_clusters(self) -> List[Dict[str, Any]]:
        """List all clusters."""
        if not self.authenticated:
            raise RuntimeError("Not authenticated with AWS")

        clusters = []

        # List EKS clusters
        try:
            response = self.eks_client.list_clusters()
            for cluster_name in response.get("clusters", []):
                clusters.append(
                    {"name": cluster_name, "type": "eks", "region": self.region}
                )
        except ClientError:
            pass

        # List EC2 instances tagged as Clustrix
        try:
            response = self.ec2_client.describe_instances(
                Filters=[
                    {"Name": "tag:ManagedBy", "Values": ["Clustrix"]},
                    {"Name": "instance-state-name", "Values": ["running", "pending"]},
                ]
            )
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    name = next(
                        (
                            tag["Value"]
                            for tag in instance.get("Tags", [])
                            if tag["Key"] == "Name"
                        ),
                        instance["InstanceId"],
                    )
                    clusters.append(
                        {
                            "name": name,
                            "instance_id": instance["InstanceId"],
                            "type": "ec2",
                            "region": self.region,
                            "state": instance["State"]["Name"],
                        }
                    )
        except ClientError:
            pass

        return clusters

    def get_cluster_config(
        self, cluster_identifier: str, cluster_type: str = "eks"
    ) -> Dict[str, Any]:
        """
        Get Clustrix configuration for a cluster.

        Args:
            cluster_identifier: Cluster name or instance ID
            cluster_type: Type of cluster ('eks' or 'ec2')

        Returns:
            Dict with Clustrix configuration
        """
        if cluster_type == "eks":
            return {
                "name": f"AWS EKS - {cluster_identifier}",
                "cluster_type": "kubernetes",
                "cluster_host": f"{cluster_identifier}.eks.{self.region}.amazonaws.com",
                "cluster_port": 443,
                "k8s_namespace": "default",
                "k8s_image": "python:3.11",
                "default_cores": 2,
                "default_memory": "4GB",
                "cost_monitoring": True,
                "provider": "aws",
                "provider_config": {
                    "cluster_name": cluster_identifier,
                    "region": self.region,
                },
            }
        elif cluster_type == "ec2":
            # Get instance details
            response = self.ec2_client.describe_instances(
                InstanceIds=[cluster_identifier]
            )
            instance = response["Reservations"][0]["Instances"][0]

            return {
                "name": f"AWS EC2 - {cluster_identifier}",
                "cluster_type": "ssh",
                "cluster_host": instance.get("PublicIpAddress", ""),
                "username": "ec2-user",  # Default for Amazon Linux
                "cluster_port": 22,
                "default_cores": 2,  # Would need to map instance type to cores
                "default_memory": "4GB",  # Would need to map instance type to memory
                "remote_work_dir": "/home/ec2-user/clustrix",
                "package_manager": "conda",
                "cost_monitoring": True,
                "provider": "aws",
                "provider_config": {
                    "instance_id": cluster_identifier,
                    "region": self.region,
                },
            }
        else:
            raise ValueError(f"Unknown cluster type: {cluster_type}")

    def estimate_cost(self, **kwargs) -> Dict[str, float]:
        """
        Estimate AWS costs.

        Args:
            **kwargs: AWS cost parameters including:
                - cluster_type: Type of cluster ('eks' or 'ec2')
                - instance_type: EC2 instance type
                - node_count: Number of nodes (for EKS)
                - hours: Number of hours

        Returns:
            Dict with cost breakdown
        """
        cluster_type = kwargs.get("cluster_type", "eks")
        instance_type = kwargs.get("instance_type", "t3.medium")
        node_count = kwargs.get("node_count", 2)
        hours = kwargs.get("hours", 1)
        # Simplified pricing - real implementation would use AWS Pricing API
        instance_prices = {
            "t3.micro": 0.0104,
            "t3.small": 0.0208,
            "t3.medium": 0.0416,
            "t3.large": 0.0832,
            "m5.large": 0.096,
            "m5.xlarge": 0.192,
            "c5.large": 0.085,
            "c5.xlarge": 0.170,
        }

        base_price = instance_prices.get(instance_type, 0.10)  # Default price

        if cluster_type == "eks":
            # EKS charges $0.10 per hour for the control plane
            control_plane_cost = 0.10 * hours
            node_cost = base_price * node_count * hours
            total = control_plane_cost + node_cost

            return {
                "control_plane": control_plane_cost,
                "nodes": node_cost,
                "total": total,
            }
        else:  # ec2
            total = base_price * hours
            return {
                "instance": total,
                "total": total,
            }

    def get_available_instance_types(self, region: Optional[str] = None) -> List[str]:
        """
        Get available EC2 instance types for the specified region.

        Args:
            region: AWS region to query (uses current region if not specified)

        Returns:
            List of available instance type names
        """
        if not self.authenticated:
            # Return a default list if not authenticated
            return [
                "t3.micro",
                "t3.small",
                "t3.medium",
                "t3.large",
                "t3.xlarge",
                "c5.large",
                "c5.xlarge",
                "c5.2xlarge",
                "c5.4xlarge",
                "m5.large",
                "m5.xlarge",
                "m5.2xlarge",
                "m5.4xlarge",
                "r5.large",
                "r5.xlarge",
                "r5.2xlarge",
            ]

        try:
            # Use specified region or current region
            query_region = region or self.region

            # Create EC2 client for the specified region if different
            if region and region != self.region:
                session = boto3.Session(
                    aws_access_key_id=self.credentials.get("access_key_id"),
                    aws_secret_access_key=self.credentials.get("secret_access_key"),
                    aws_session_token=self.credentials.get("session_token"),
                    region_name=region,
                )
                ec2_client = session.client("ec2")
            else:
                ec2_client = self.ec2_client

            # Get instance type offerings for the region
            response = ec2_client.describe_instance_type_offerings(
                LocationType="region",
                Filters=[{"Name": "location", "Values": [query_region]}],
            )

            # Extract instance type names and sort them
            instance_types = [
                offering["InstanceType"]
                for offering in response["InstanceTypeOfferings"]
            ]
            instance_types.sort()

            # Filter to common instance families for better UX
            common_families = ["t3", "t2", "c5", "c4", "m5", "m4", "r5", "r4"]
            filtered_types = []

            for family in common_families:
                family_types = [t for t in instance_types if t.startswith(family + ".")]
                filtered_types.extend(family_types[:6])  # Limit to 6 sizes per family

            return filtered_types[:30]  # Limit total to 30 for better UX

        except Exception as e:
            logger.warning(
                f"Failed to fetch instance types for region {query_region}: {e}"
            )
            # Return default list on error
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

    def get_available_regions(self) -> List[str]:
        """
        Get available AWS regions.

        Returns:
            List of available AWS region names
        """
        if not self.authenticated:
            # Return common regions if not authenticated
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
            # Get all available regions
            response = self.ec2_client.describe_regions()
            regions = [region["RegionName"] for region in response["Regions"]]
            regions.sort()

            # Prioritize common regions
            priority_regions = [
                "us-east-1",
                "us-west-1",
                "us-west-2",
                "eu-west-1",
                "eu-central-1",
                "ap-southeast-1",
                "ap-northeast-1",
            ]

            # Put priority regions first, then others
            sorted_regions = []
            for region in priority_regions:
                if region in regions:
                    sorted_regions.append(region)
                    regions.remove(region)

            sorted_regions.extend(regions)
            return sorted_regions

        except Exception as e:
            logger.warning(f"Failed to fetch AWS regions: {e}")
            return [
                "us-east-1",
                "us-west-1",
                "us-west-2",
                "eu-west-1",
                "eu-central-1",
                "ap-southeast-1",
                "ap-northeast-1",
            ]


# Register the provider
if BOTO3_AVAILABLE:
    PROVIDERS["aws"] = AWSProvider
