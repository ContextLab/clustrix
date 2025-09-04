"""
Comprehensive tests for AWS cluster operations.

This module tests lines 442-635 of clustrix/cloud_providers/aws.py focusing on:
- EKS/EC2 cluster lifecycle management
- Cluster CRUD operations
- Status monitoring
- Resource discovery and configuration

Uses boto3 stubber for mocking AWS API calls.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError
from botocore.stub import Stubber
import boto3
from datetime import datetime, timezone

from clustrix.cloud_providers.aws import AWSProvider


class TestAWSClusterOperations:
    """Test AWS cluster operations (lines 442-635)."""

    @pytest.fixture
    def provider(self):
        """Create authenticated AWSProvider instance with stubbed clients."""
        provider = AWSProvider()
        provider.authenticated = True
        provider.region = "us-east-1"

        # Create real boto3 clients for stubbing
        provider.ec2_client = boto3.client("ec2", region_name="us-east-1")
        provider.eks_client = boto3.client("eks", region_name="us-east-1")
        provider.iam_client = boto3.client("iam", region_name="us-east-1")

        provider.credentials = {
            "access_key_id": "test-access-key",
            "secret_access_key": "test-secret-key",
            "region": "us-east-1",
        }
        return provider

    @pytest.fixture
    def ec2_stubber(self, provider):
        """Create EC2 client stubber."""
        return Stubber(provider.ec2_client)

    @pytest.fixture
    def eks_stubber(self, provider):
        """Create EKS client stubber."""
        return Stubber(provider.eks_client)

    @pytest.fixture
    def iam_stubber(self, provider):
        """Create IAM client stubber."""
        return Stubber(provider.iam_client)

    def test_create_cluster_eks_success(
        self, provider, eks_stubber, ec2_stubber, iam_stubber
    ):
        """Test successful EKS cluster creation."""
        cluster_name = "test-eks-cluster"

        # Mock the create_eks_cluster method
        with patch.object(provider, "create_eks_cluster") as mock_create_eks:
            mock_create_eks.return_value = {
                "cluster_name": cluster_name,
                "cluster_type": "eks",
                "status": "CREATING",
                "endpoint": "",
                "arn": f"arn:aws:eks:us-east-1:123456789012:cluster/{cluster_name}",
            }

            result = provider.create_cluster(cluster_name, cluster_type="eks")

            assert result["cluster_name"] == cluster_name
            assert result["cluster_type"] == "eks"
            assert result["status"] == "CREATING"
            mock_create_eks.assert_called_once_with(cluster_name)

    def test_create_cluster_ec2_success(self, provider):
        """Test successful EC2 cluster creation."""
        instance_name = "test-ec2-cluster"

        with patch.object(provider, "create_ec2_instance") as mock_create_ec2:
            mock_create_ec2.return_value = {
                "instance_id": "i-1234567890abcdef0",
                "instance_name": instance_name,
                "cluster_type": "ec2",
                "state": "pending",
            }

            result = provider.create_cluster(instance_name, cluster_type="ec2")

            assert result["instance_name"] == instance_name
            assert result["cluster_type"] == "ec2"
            assert "instance_id" in result
            mock_create_ec2.assert_called_once_with(instance_name)

    def test_create_cluster_invalid_type(self, provider):
        """Test create_cluster with invalid cluster type."""
        with pytest.raises(ValueError, match="Unknown cluster type: invalid"):
            provider.create_cluster("test-cluster", cluster_type="invalid")

    def test_delete_cluster_eks_success(self, provider, eks_stubber):
        """Test successful EKS cluster deletion."""
        cluster_name = "test-eks-cluster"

        # Mock list_nodegroups response (no nodegroups)
        eks_stubber.add_response(
            "list_nodegroups", {"nodegroups": []}, {"clusterName": cluster_name}
        )

        # Mock delete_cluster response
        eks_stubber.add_response(
            "delete_cluster",
            {"cluster": {"name": cluster_name, "status": "DELETING"}},
            {"name": cluster_name},
        )

        eks_stubber.activate()

        result = provider.delete_cluster(cluster_name, cluster_type="eks")

        assert result is True
        eks_stubber.deactivate()

    def test_delete_cluster_eks_with_nodegroups(self, provider, eks_stubber):
        """Test EKS cluster deletion with existing nodegroups."""
        cluster_name = "test-eks-cluster"
        nodegroup_name = "test-nodegroup"

        # Mock list_nodegroups response (with nodegroups)
        eks_stubber.add_response(
            "list_nodegroups",
            {"nodegroups": [nodegroup_name]},
            {"clusterName": cluster_name},
        )

        # Mock delete_nodegroup response
        eks_stubber.add_response(
            "delete_nodegroup",
            {"nodegroup": {"nodegroupName": nodegroup_name, "status": "DELETING"}},
            {"clusterName": cluster_name, "nodegroupName": nodegroup_name},
        )

        # Mock delete_cluster response
        eks_stubber.add_response(
            "delete_cluster",
            {"cluster": {"name": cluster_name, "status": "DELETING"}},
            {"name": cluster_name},
        )

        eks_stubber.activate()

        result = provider.delete_cluster(cluster_name, cluster_type="eks")

        assert result is True
        eks_stubber.deactivate()

    def test_delete_cluster_eks_not_found(self, provider, eks_stubber):
        """Test EKS cluster deletion when cluster doesn't exist."""
        cluster_name = "nonexistent-cluster"

        # Mock ResourceNotFoundException
        eks_stubber.add_client_error(
            "list_nodegroups",
            service_error_code="ResourceNotFoundException",
            service_message="Cluster not found",
            expected_params={"clusterName": cluster_name},
        )

        eks_stubber.activate()

        result = provider.delete_cluster(cluster_name, cluster_type="eks")

        assert result is True  # Should return True for not found (idempotent)
        eks_stubber.deactivate()

    def test_delete_cluster_ec2_success(self, provider, ec2_stubber):
        """Test successful EC2 instance deletion."""
        instance_id = "i-1234567890abcdef0"

        # Mock terminate_instances response
        ec2_stubber.add_response(
            "terminate_instances",
            {
                "TerminatingInstances": [
                    {
                        "InstanceId": instance_id,
                        "CurrentState": {"Code": 32, "Name": "shutting-down"},
                        "PreviousState": {"Code": 16, "Name": "running"},
                    }
                ]
            },
            {"InstanceIds": [instance_id]},
        )

        ec2_stubber.activate()

        result = provider.delete_cluster(instance_id, cluster_type="ec2")

        assert result is True
        ec2_stubber.deactivate()

    def test_delete_cluster_invalid_type(self, provider):
        """Test delete_cluster with invalid cluster type."""
        with pytest.raises(ValueError, match="Unknown cluster type: invalid"):
            provider.delete_cluster("test-cluster", cluster_type="invalid")

    def test_delete_cluster_not_authenticated(self, provider):
        """Test delete_cluster when not authenticated."""
        provider.authenticated = False

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.delete_cluster("test-cluster")

    def test_get_cluster_status_eks_active(self, provider, eks_stubber):
        """Test getting status of active EKS cluster."""
        cluster_name = "test-eks-cluster"

        # Mock describe_cluster response
        cluster_data = {
            "cluster": {
                "name": cluster_name,
                "status": "ACTIVE",
                "endpoint": "https://ABC123.gr7.us-east-1.eks.amazonaws.com",
                "version": "1.27",
                "arn": f"arn:aws:eks:us-east-1:123456789012:cluster/{cluster_name}",
                "createdAt": datetime(2023, 1, 1, tzinfo=timezone.utc),
            }
        }

        eks_stubber.add_response(
            "describe_cluster", cluster_data, {"name": cluster_name}
        )

        # Mock list_nodegroups response
        eks_stubber.add_response(
            "list_nodegroups",
            {"nodegroups": ["test-nodegroup"]},
            {"clusterName": cluster_name},
        )

        # Mock describe_nodegroup response
        eks_stubber.add_response(
            "describe_nodegroup",
            {
                "nodegroup": {
                    "nodegroupName": "test-nodegroup",
                    "scalingConfig": {"desiredSize": 3},
                }
            },
            {"clusterName": cluster_name, "nodegroupName": "test-nodegroup"},
        )

        eks_stubber.activate()

        result = provider.get_cluster_status(cluster_name, cluster_type="eks")

        assert result["cluster_name"] == cluster_name
        assert result["status"] == "ACTIVE"
        assert result["endpoint"] == "https://ABC123.gr7.us-east-1.eks.amazonaws.com"
        assert result["version"] == "1.27"
        assert result["node_count"] == 3
        assert result["cluster_type"] == "eks"
        assert result["region"] == "us-east-1"

        eks_stubber.deactivate()

    def test_get_cluster_status_eks_not_found(self, provider, eks_stubber):
        """Test getting status of non-existent EKS cluster."""
        cluster_name = "nonexistent-cluster"

        # Mock ResourceNotFoundException
        eks_stubber.add_client_error(
            "describe_cluster",
            service_error_code="ResourceNotFoundException",
            service_message="Cluster not found",
            expected_params={"name": cluster_name},
        )

        eks_stubber.activate()

        result = provider.get_cluster_status(cluster_name, cluster_type="eks")

        assert result["cluster_name"] == cluster_name
        assert result["status"] == "NOT_FOUND"
        assert result["cluster_type"] == "eks"

        eks_stubber.deactivate()

    def test_get_cluster_status_ec2_running(self, provider, ec2_stubber):
        """Test getting status of running EC2 instance."""
        instance_id = "i-1234567890abcdef0"

        # Mock describe_instances response
        ec2_stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": instance_id,
                                "State": {"Name": "running", "Code": 16},
                            }
                        ]
                    }
                ]
            },
            {"InstanceIds": [instance_id]},
        )

        ec2_stubber.activate()

        result = provider.get_cluster_status(instance_id, cluster_type="ec2")

        assert result["instance_id"] == instance_id
        assert result["status"] == "running"
        assert result["cluster_type"] == "ec2"

        ec2_stubber.deactivate()

    def test_get_cluster_status_not_authenticated(self, provider):
        """Test get_cluster_status when not authenticated."""
        provider.authenticated = False

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.get_cluster_status("test-cluster")

    def test_list_clusters_success(self, provider, eks_stubber, ec2_stubber):
        """Test successful listing of all clusters."""
        # Mock EKS clusters
        eks_stubber.add_response(
            "list_clusters", {"clusters": ["eks-cluster-1", "eks-cluster-2"]}
        )

        # Mock EC2 instances
        ec2_stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-1234567890abcdef0",
                                "State": {"Name": "running"},
                                "Tags": [
                                    {"Key": "ManagedBy", "Value": "Clustrix"},
                                    {"Key": "Name", "Value": "ec2-cluster-1"},
                                ],
                            }
                        ]
                    }
                ]
            },
            {
                "Filters": [
                    {"Name": "tag:ManagedBy", "Values": ["Clustrix"]},
                    {"Name": "instance-state-name", "Values": ["running", "pending"]},
                ]
            },
        )

        eks_stubber.activate()
        ec2_stubber.activate()

        result = provider.list_clusters()

        assert len(result) == 3

        # Check EKS clusters
        eks_clusters = [c for c in result if c["type"] == "eks"]
        assert len(eks_clusters) == 2
        assert eks_clusters[0]["name"] == "eks-cluster-1"
        assert eks_clusters[1]["name"] == "eks-cluster-2"

        # Check EC2 clusters
        ec2_clusters = [c for c in result if c["type"] == "ec2"]
        assert len(ec2_clusters) == 1
        assert ec2_clusters[0]["name"] == "ec2-cluster-1"
        assert ec2_clusters[0]["instance_id"] == "i-1234567890abcdef0"
        assert ec2_clusters[0]["state"] == "running"

        eks_stubber.deactivate()
        ec2_stubber.deactivate()

    def test_list_clusters_no_clusters(self, provider, eks_stubber, ec2_stubber):
        """Test listing when no clusters exist."""
        # Mock empty responses
        eks_stubber.add_response("list_clusters", {"clusters": []})
        ec2_stubber.add_response(
            "describe_instances",
            {"Reservations": []},
            {
                "Filters": [
                    {"Name": "tag:ManagedBy", "Values": ["Clustrix"]},
                    {"Name": "instance-state-name", "Values": ["running", "pending"]},
                ]
            },
        )

        eks_stubber.activate()
        ec2_stubber.activate()

        result = provider.list_clusters()

        assert result == []

        eks_stubber.deactivate()
        ec2_stubber.deactivate()

    def test_list_clusters_api_errors(self, provider, eks_stubber, ec2_stubber):
        """Test list_clusters handles API errors gracefully."""
        # Mock EKS error
        eks_stubber.add_client_error(
            "list_clusters",
            service_error_code="AccessDeniedException",
            service_message="Access denied",
        )

        # Mock EC2 error
        ec2_stubber.add_client_error(
            "describe_instances",
            service_error_code="UnauthorizedOperation",
            service_message="Not authorized",
            expected_params={
                "Filters": [
                    {"Name": "tag:ManagedBy", "Values": ["Clustrix"]},
                    {"Name": "instance-state-name", "Values": ["running", "pending"]},
                ]
            },
        )

        eks_stubber.activate()
        ec2_stubber.activate()

        result = provider.list_clusters()

        assert result == []  # Should return empty list on errors

        eks_stubber.deactivate()
        ec2_stubber.deactivate()

    def test_list_clusters_not_authenticated(self, provider):
        """Test list_clusters when not authenticated."""
        provider.authenticated = False

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.list_clusters()

    def test_get_cluster_config_eks(self, provider):
        """Test getting Clustrix configuration for EKS cluster."""
        cluster_name = "test-eks-cluster"

        result = provider.get_cluster_config(cluster_name, cluster_type="eks")

        expected_config = {
            "name": f"AWS EKS - {cluster_name}",
            "cluster_type": "kubernetes",
            "cluster_host": f"{cluster_name}.eks.us-east-1.amazonaws.com",
            "cluster_port": 443,
            "k8s_namespace": "default",
            "k8s_image": "python:3.11",
            "default_cores": 2,
            "default_memory": "4GB",
            "cost_monitoring": True,
            "provider": "aws",
            "provider_config": {
                "cluster_name": cluster_name,
                "region": "us-east-1",
            },
        }

        assert result == expected_config

    def test_get_cluster_config_ec2(self, provider, ec2_stubber):
        """Test getting Clustrix configuration for EC2 cluster."""
        instance_id = "i-1234567890abcdef0"
        public_ip = "203.0.113.1"

        # Mock describe_instances response
        ec2_stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": instance_id,
                                "PublicIpAddress": public_ip,
                                "State": {"Name": "running"},
                            }
                        ]
                    }
                ]
            },
            {"InstanceIds": [instance_id]},
        )

        ec2_stubber.activate()

        result = provider.get_cluster_config(instance_id, cluster_type="ec2")

        expected_config = {
            "name": f"AWS EC2 - {instance_id}",
            "cluster_type": "ssh",
            "cluster_host": public_ip,
            "username": "ec2-user",
            "cluster_port": 22,
            "default_cores": 2,
            "default_memory": "4GB",
            "remote_work_dir": "/home/ec2-user/clustrix",
            "package_manager": "conda",
            "cost_monitoring": True,
            "provider": "aws",
        }

        # Check key fields (partial match since full implementation continues beyond visible lines)
        assert result["name"] == expected_config["name"]
        assert result["cluster_type"] == expected_config["cluster_type"]
        assert result["cluster_host"] == expected_config["cluster_host"]
        assert result["username"] == expected_config["username"]
        assert result["provider"] == expected_config["provider"]

        ec2_stubber.deactivate()

    def test_client_error_handling(self, provider, eks_stubber):
        """Test proper handling of AWS ClientError exceptions."""
        cluster_name = "test-cluster"

        # Mock client error that should be re-raised
        eks_stubber.add_client_error(
            "describe_cluster",
            service_error_code="AccessDeniedException",
            service_message="User not authorized to perform this action",
            expected_params={"name": cluster_name},
        )

        eks_stubber.activate()

        with pytest.raises(ClientError) as exc_info:
            provider.get_cluster_status(cluster_name, cluster_type="eks")

        assert exc_info.value.response["Error"]["Code"] == "AccessDeniedException"

        eks_stubber.deactivate()

    def test_rate_limiting_simulation(self, provider, eks_stubber):
        """Test handling of AWS rate limiting errors."""
        cluster_name = "test-cluster"

        # Mock throttling error
        eks_stubber.add_client_error(
            "describe_cluster",
            service_error_code="Throttling",
            service_message="Rate exceeded",
            expected_params={"name": cluster_name},
        )

        eks_stubber.activate()

        with pytest.raises(ClientError) as exc_info:
            provider.get_cluster_status(cluster_name, cluster_type="eks")

        assert exc_info.value.response["Error"]["Code"] == "Throttling"

        eks_stubber.deactivate()

    def test_create_eks_cluster_success(
        self, provider, eks_stubber, iam_stubber, ec2_stubber
    ):
        """Test successful EKS cluster creation with complete workflow."""
        cluster_name = "test-eks-cluster"

        # Mock _create_or_get_eks_cluster_role
        with patch.object(provider, "_create_or_get_eks_cluster_role") as mock_role:
            mock_role.return_value = "arn:aws:iam::123456789012:role/eks-service-role"

            # Mock _create_or_get_vpc_for_eks
            with patch.object(provider, "_create_or_get_vpc_for_eks") as mock_vpc:
                mock_vpc.return_value = {
                    "subnet_ids": ["subnet-12345", "subnet-67890"],
                    "security_group_ids": ["sg-12345"],
                }

                # Mock create_cluster response
                cluster_response = {
                    "cluster": {
                        "name": cluster_name,
                        "arn": f"arn:aws:eks:us-east-1:123456789012:cluster/{cluster_name}",
                        "status": "CREATING",
                        "endpoint": "",
                        "version": "1.27",
                        "createdAt": datetime(2023, 1, 1, tzinfo=timezone.utc),
                    }
                }

                eks_stubber.add_response(
                    "create_cluster",
                    cluster_response,
                    {
                        "name": cluster_name,
                        "version": "1.27",
                        "roleArn": "arn:aws:iam::123456789012:role/eks-service-role",
                        "resourcesVpcConfig": {
                            "subnetIds": ["subnet-12345", "subnet-67890"],
                            "securityGroupIds": ["sg-12345"],
                        },
                        "tags": {
                            "created_by": "clustrix",
                            "cluster_name": cluster_name,
                            "environment": "clustrix",
                        },
                    },
                )

                eks_stubber.activate()

                result = provider.create_eks_cluster(
                    cluster_name,
                    node_count=3,
                    instance_type="t3.large",
                    kubernetes_version="1.27",
                )

                assert result["cluster_name"] == cluster_name
                assert result["status"] == "CREATING"
                assert result["version"] == "1.27"
                assert "arn" in result

                mock_role.assert_called_once()
                mock_vpc.assert_called_once_with(cluster_name)

                eks_stubber.deactivate()

    def test_create_eks_cluster_not_authenticated(self, provider):
        """Test create_eks_cluster when not authenticated."""
        provider.authenticated = False

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.create_eks_cluster("test-cluster")

    def test_create_ec2_instance_with_ami(self, provider, ec2_stubber):
        """Test EC2 instance creation with specified AMI."""
        instance_name = "test-ec2-instance"
        ami_id = "ami-12345678"
        instance_type = "t3.medium"
        key_name = "my-key-pair"

        # Mock run_instances response
        ec2_stubber.add_response(
            "run_instances",
            {
                "Instances": [
                    {
                        "InstanceId": "i-1234567890abcdef0",
                        "State": {"Name": "pending", "Code": 0},
                        "InstanceType": instance_type,
                        "ImageId": ami_id,
                        "KeyName": key_name,
                    }
                ]
            },
            {
                "ImageId": ami_id,
                "InstanceType": instance_type,
                "MinCount": 1,
                "MaxCount": 1,
                "KeyName": key_name,
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": instance_name}],
                    }
                ],
            },
        )

        # Mock describe_instances response for final status check
        ec2_stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-1234567890abcdef0",
                                "State": {"Name": "running", "Code": 16},
                                "InstanceType": instance_type,
                                "ImageId": ami_id,
                                "PublicIpAddress": "203.0.113.1",
                                "PrivateIpAddress": "10.0.1.10",
                            }
                        ]
                    }
                ]
            },
            {"InstanceIds": ["i-1234567890abcdef0"]},
        )

        ec2_stubber.activate()

        # Mock the waiter to avoid internal describe_instances calls
        with patch.object(provider.ec2_client, "get_waiter") as mock_waiter:
            mock_waiter_instance = Mock()
            mock_waiter.return_value = mock_waiter_instance

            result = provider.create_ec2_instance(
                instance_name,
                instance_type=instance_type,
                ami_id=ami_id,
                key_name=key_name,
            )

            assert result["instance_id"] == "i-1234567890abcdef0"
            assert result["instance_name"] == instance_name
            assert result["state"] == "running"
            assert result["instance_type"] == instance_type
            assert result["public_ip"] == "203.0.113.1"
            assert result["private_ip"] == "10.0.1.10"

            # Verify waiter was called
            mock_waiter.assert_called_once_with("instance_running")
            mock_waiter_instance.wait.assert_called_once_with(
                InstanceIds=["i-1234567890abcdef0"]
            )

        ec2_stubber.deactivate()

    def test_create_ec2_instance_auto_ami(self, provider, ec2_stubber):
        """Test EC2 instance creation with automatic AMI selection."""
        instance_name = "test-ec2-instance"

        # Mock describe_images response (for AMI selection)
        ec2_stubber.add_response(
            "describe_images",
            {
                "Images": [
                    {
                        "ImageId": "ami-latest123",
                        "CreationDate": "2023-12-01T00:00:00.000Z",
                        "Name": "amzn2-ami-hvm-2.0.20231201.0-x86_64-gp2",
                    },
                    {
                        "ImageId": "ami-older456",
                        "CreationDate": "2023-11-01T00:00:00.000Z",
                        "Name": "amzn2-ami-hvm-2.0.20231101.0-x86_64-gp2",
                    },
                ]
            },
            {
                "Owners": ["amazon"],
                "Filters": [
                    {"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
                    {"Name": "state", "Values": ["available"]},
                ],
            },
        )

        # Mock run_instances response
        ec2_stubber.add_response(
            "run_instances",
            {
                "Instances": [
                    {
                        "InstanceId": "i-1234567890abcdef0",
                        "State": {"Name": "pending", "Code": 0},
                        "InstanceType": "t3.medium",
                        "ImageId": "ami-latest123",
                    }
                ]
            },
            {
                "ImageId": "ami-latest123",  # Should select the latest AMI
                "InstanceType": "t3.medium",
                "MinCount": 1,
                "MaxCount": 1,
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": instance_name}],
                    }
                ],
            },
        )

        # Mock describe_instances response for final status check
        ec2_stubber.add_response(
            "describe_instances",
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-1234567890abcdef0",
                                "State": {"Name": "running", "Code": 16},
                                "InstanceType": "t3.medium",
                                "ImageId": "ami-latest123",
                                "PublicIpAddress": "203.0.113.1",
                                "PrivateIpAddress": "10.0.1.10",
                            }
                        ]
                    }
                ]
            },
            {"InstanceIds": ["i-1234567890abcdef0"]},
        )

        ec2_stubber.activate()

        # Mock the waiter to avoid internal describe_instances calls
        with patch.object(provider.ec2_client, "get_waiter") as mock_waiter:
            mock_waiter_instance = Mock()
            mock_waiter.return_value = mock_waiter_instance

            result = provider.create_ec2_instance(instance_name)

            assert result["instance_id"] == "i-1234567890abcdef0"
            assert (
                result["public_ip"] == "203.0.113.1"
            )  # Should have public IP after waiter

            # Verify waiter was called
            mock_waiter.assert_called_once_with("instance_running")
            mock_waiter_instance.wait.assert_called_once_with(
                InstanceIds=["i-1234567890abcdef0"]
            )

        ec2_stubber.deactivate()

    def test_create_ec2_instance_not_authenticated(self, provider):
        """Test create_ec2_instance when not authenticated."""
        provider.authenticated = False

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.create_ec2_instance("test-instance")

    def test_create_ec2_instance_api_error(self, provider, ec2_stubber):
        """Test create_ec2_instance handles API errors."""
        instance_name = "test-instance"

        # Mock AMI selection
        ec2_stubber.add_response(
            "describe_images",
            {
                "Images": [
                    {"ImageId": "ami-12345", "CreationDate": "2023-01-01T00:00:00.000Z"}
                ]
            },
            {
                "Owners": ["amazon"],
                "Filters": [
                    {"Name": "name", "Values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
                    {"Name": "state", "Values": ["available"]},
                ],
            },
        )

        # Mock run_instances error
        ec2_stubber.add_client_error(
            "run_instances",
            service_error_code="InsufficientInstanceCapacity",
            service_message="Insufficient capacity",
            expected_params={
                "ImageId": "ami-12345",
                "InstanceType": "t3.medium",
                "MinCount": 1,
                "MaxCount": 1,
                "TagSpecifications": [
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": instance_name}],
                    }
                ],
            },
        )

        ec2_stubber.activate()

        with pytest.raises(ClientError) as exc_info:
            provider.create_ec2_instance(instance_name)

        assert (
            exc_info.value.response["Error"]["Code"] == "InsufficientInstanceCapacity"
        )

        ec2_stubber.deactivate()

    def test_delete_cluster_eks_api_error(self, provider, eks_stubber):
        """Test delete_cluster handles non-ResourceNotFound EKS API errors."""
        cluster_name = "test-cluster"

        # Mock list_nodegroups with access denied error (line 511 coverage)
        eks_stubber.add_client_error(
            "list_nodegroups",
            service_error_code="AccessDeniedException",
            service_message="Access denied",
            expected_params={"clusterName": cluster_name},
        )

        eks_stubber.activate()

        # Should return False due to general ClientError handling (lines 520-521)
        result = provider.delete_cluster(cluster_name, cluster_type="eks")

        assert result is False

        eks_stubber.deactivate()

    def test_delete_cluster_general_api_error(self, provider, ec2_stubber):
        """Test delete_cluster general ClientError handling (lines 520-521)."""
        instance_id = "i-1234567890abcdef0"

        # Mock terminate_instances error
        ec2_stubber.add_client_error(
            "terminate_instances",
            service_error_code="InvalidInstanceID.NotFound",
            service_message="Instance not found",
            expected_params={"InstanceIds": [instance_id]},
        )

        ec2_stubber.activate()

        result = provider.delete_cluster(instance_id, cluster_type="ec2")

        # Should return False on ClientError
        assert result is False

        ec2_stubber.deactivate()

    def test_get_cluster_status_invalid_type(self, provider):
        """Test get_cluster_status with invalid cluster type (line 587)."""
        with pytest.raises(ValueError, match="Unknown cluster type: invalid"):
            provider.get_cluster_status("test-cluster", cluster_type="invalid")
