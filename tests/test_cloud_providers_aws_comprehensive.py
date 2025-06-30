"""
Comprehensive AWS provider tests following Cloud Control API patterns.

These tests aim to achieve high coverage by testing both mocked scenarios
and real API interactions when credentials are available.

Design follows AWS Cloud Control API patterns:
- Standardized CRUD-L operations (Create, Read, Update, Delete, List)
- Consistent error handling across resource types
- Request tracking and status monitoring
- Graceful fallback when credentials unavailable
"""

import os
import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from clustrix.cloud_providers.aws import AWSProvider


class TestAWSProviderComprehensive:
    """Comprehensive AWS provider tests with real API testing capability."""

    @pytest.fixture
    def provider(self):
        """Create AWSProvider instance."""
        return AWSProvider()

    @pytest.fixture
    def authenticated_provider(self):
        """Create authenticated AWSProvider instance."""
        provider = AWSProvider()
        provider.authenticated = True
        provider.region = "us-east-1"
        provider.ec2_client = Mock()
        provider.eks_client = Mock()
        provider.iam_client = Mock()
        provider.credentials = {
            "access_key_id": "test-access-key",
            "secret_access_key": "test-secret-key",
            "region": "us-east-1",
        }
        return provider

    @pytest.fixture
    def real_aws_credentials(self):
        """Check for real AWS credentials in environment or test config."""
        access_key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

        if access_key and secret_key:
            return {
                "access_key_id": access_key,
                "secret_access_key": secret_key,
                "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                "session_token": os.environ.get("AWS_SESSION_TOKEN"),
            }
        return None

    def test_initialization_comprehensive(self, provider):
        """Test comprehensive provider initialization."""
        assert provider.ec2_client is None
        assert provider.eks_client is None
        assert provider.iam_client is None
        assert provider.region == "us-east-1"
        assert not provider.authenticated
        assert hasattr(provider, "credentials")
        assert provider.credentials == {}

    @pytest.mark.skipif(
        not os.environ.get("AWS_ACCESS_KEY_ID"),
        reason="Real AWS credentials not available",
    )
    def test_real_authentication(self, provider, real_aws_credentials):
        """Test authentication with real AWS credentials if available."""
        if not real_aws_credentials:
            pytest.skip("No real AWS credentials available")

        result = provider.authenticate(**real_aws_credentials)

        if result:
            assert provider.authenticated is True
            assert provider.ec2_client is not None
            assert provider.eks_client is not None
            assert provider.iam_client is not None

            # Test that we can actually make a call
            try:
                user_info = provider.iam_client.get_user()
                assert "User" in user_info or "UserName" in str(user_info)
            except Exception as e:
                # Some test accounts might not have get_user permission
                assert "AccessDenied" in str(e) or "NoSuchEntity" in str(e)

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_authentication_error_scenarios(self, mock_boto3, provider):
        """Test comprehensive authentication error scenarios."""
        from clustrix.cloud_providers.aws import ClientError, NoCredentialsError

        # Test missing credentials
        result = provider.authenticate()
        assert result is False

        result = provider.authenticate(access_key_id="key-only")
        assert result is False

        # Test NoCredentialsError
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session
        mock_iam_client = Mock()
        mock_session.client.return_value = mock_iam_client
        mock_iam_client.get_user.side_effect = NoCredentialsError()

        result = provider.authenticate(
            access_key_id="test-key", secret_access_key="test-secret"
        )
        assert result is False

        # Test various ClientError scenarios
        error_codes = ["AccessDenied", "InvalidUserID.NotFound", "TokenRefreshRequired"]
        for error_code in error_codes:
            mock_iam_client.get_user.side_effect = ClientError(
                {"Error": {"Code": error_code, "Message": f"Test {error_code}"}},
                "GetUser",
            )
            result = provider.authenticate(
                access_key_id="test-key", secret_access_key="test-secret"
            )
            assert result is False

    def test_eks_cluster_role_operations(self, authenticated_provider):
        """Test EKS cluster role creation and retrieval following CRUD patterns."""
        from clustrix.cloud_providers.aws import ClientError

        # Test getting existing role (Read operation)
        authenticated_provider.iam_client.get_role.return_value = {
            "Role": {"Arn": "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"}
        }

        result = authenticated_provider._create_or_get_eks_cluster_role()
        assert result == "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"

        # Test creating new role when it doesn't exist (Create operation)
        authenticated_provider.iam_client.get_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity"}}, "GetRole"
        )
        authenticated_provider.iam_client.create_role.return_value = {
            "Role": {"Arn": "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"}
        }

        result = authenticated_provider._create_or_get_eks_cluster_role()
        assert result == "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"

        # Verify policy attachment was called
        authenticated_provider.iam_client.attach_role_policy.assert_called()

    def test_vpc_operations_comprehensive(self, authenticated_provider):
        """Test VPC operations following AWS resource management patterns."""
        cluster_name = "test-cluster"

        # Mock existing VPC scenario
        authenticated_provider.ec2_client.describe_vpcs.return_value = {
            "Vpcs": [
                {
                    "VpcId": "vpc-12345678",
                    "CidrBlock": "10.0.0.0/16",
                    "State": "available",
                }
            ]
        }

        # Mock subnet creation
        authenticated_provider.ec2_client.describe_subnets.return_value = {
            "Subnets": []
        }
        authenticated_provider.ec2_client.create_subnet.return_value = {
            "Subnet": {"SubnetId": "subnet-12345678"}
        }

        # Mock security group creation
        authenticated_provider.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": []
        }
        authenticated_provider.ec2_client.create_security_group.return_value = {
            "GroupId": "sg-12345678"
        }

        vpc_config = authenticated_provider._create_or_get_vpc_for_eks(cluster_name)

        assert vpc_config["vpc_id"] == "vpc-12345678"
        assert "subnet_ids" in vpc_config
        assert "security_group_ids" in vpc_config

    def test_eks_cluster_lifecycle(self, authenticated_provider):
        """Test complete EKS cluster lifecycle (Create, Read, Update, Delete)."""
        cluster_name = "test-eks-cluster"

        # Mock cluster creation
        authenticated_provider._create_or_get_eks_cluster_role = Mock(
            return_value="arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"
        )
        authenticated_provider._create_or_get_vpc_for_eks = Mock(
            return_value={
                "vpc_id": "vpc-12345678",
                "subnet_ids": ["subnet-1", "subnet-2"],
                "security_group_ids": ["sg-12345678"],
            }
        )

        authenticated_provider.eks_client.create_cluster.return_value = {
            "cluster": {
                "name": cluster_name,
                "status": "CREATING",
                "endpoint": "",
                "arn": f"arn:aws:eks:us-east-1:123456789012:cluster/{cluster_name}",
                "version": "1.27",
                "createdAt": datetime.now(timezone.utc),
            }
        }

        # Test Create operation
        result = authenticated_provider.create_eks_cluster(
            cluster_name, node_count=3, instance_type="t3.medium"
        )

        assert result["cluster_name"] == cluster_name
        assert result["status"] == "CREATING"
        assert result["node_count"] == 3
        assert result["instance_type"] == "t3.medium"

        # Test Read operation (get status)
        authenticated_provider.eks_client.describe_cluster.return_value = {
            "cluster": {
                "name": cluster_name,
                "status": "ACTIVE",
                "endpoint": "https://test.eks.amazonaws.com",
                "version": "1.27",
                "arn": f"arn:aws:eks:us-east-1:123456789012:cluster/{cluster_name}",
                "createdAt": datetime.now(timezone.utc),
            }
        }
        authenticated_provider.eks_client.list_nodegroups.return_value = {
            "nodegroups": ["test-nodegroup"]
        }
        authenticated_provider.eks_client.describe_nodegroup.return_value = {
            "nodegroup": {"scalingConfig": {"desiredSize": 3}}
        }

        status = authenticated_provider.get_cluster_status(cluster_name, "eks")
        assert status["status"] == "ACTIVE"
        assert status["node_count"] == 3

        # Test Delete operation
        authenticated_provider.eks_client.list_nodegroups.return_value = {
            "nodegroups": ["test-nodegroup"]
        }

        result = authenticated_provider.delete_cluster(cluster_name, "eks")
        assert result is True

        # Verify deletion calls
        authenticated_provider.eks_client.delete_nodegroup.assert_called()
        authenticated_provider.eks_client.delete_cluster.assert_called_with(
            name=cluster_name
        )

    def test_ec2_instance_lifecycle(self, authenticated_provider):
        """Test complete EC2 instance lifecycle (Create, Read, Delete)."""
        instance_name = "test-ec2-instance"

        # Mock AMI lookup
        authenticated_provider.ec2_client.describe_images.return_value = {
            "Images": [
                {"ImageId": "ami-12345678", "CreationDate": "2023-01-01T00:00:00.000Z"}
            ]
        }

        # Mock instance creation
        authenticated_provider.ec2_client.run_instances.return_value = {
            "Instances": [
                {
                    "InstanceId": "i-12345678",
                    "State": {"Name": "pending"},
                    "InstanceType": "t3.medium",
                }
            ]
        }

        # Mock waiter
        mock_waiter = Mock()
        authenticated_provider.ec2_client.get_waiter.return_value = mock_waiter

        # Mock updated instance info
        authenticated_provider.ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-12345678",
                            "State": {"Name": "running"},
                            "PublicIpAddress": "1.2.3.4",
                            "PrivateIpAddress": "10.0.1.100",
                            "InstanceType": "t3.medium",
                        }
                    ]
                }
            ]
        }

        # Test Create operation
        result = authenticated_provider.create_ec2_instance(
            instance_name, instance_type="t3.medium"
        )

        assert result["instance_id"] == "i-12345678"
        assert result["instance_name"] == instance_name
        assert result["public_ip"] == "1.2.3.4"
        assert result["state"] == "running"

        # Test Read operation (get status)
        status = authenticated_provider.get_cluster_status("i-12345678", "ec2")
        assert status["instance_id"] == "i-12345678"
        assert status["status"] == "running"

        # Test Delete operation
        result = authenticated_provider.delete_cluster("i-12345678", "ec2")
        assert result is True
        authenticated_provider.ec2_client.terminate_instances.assert_called_with(
            InstanceIds=["i-12345678"]
        )

    def test_list_operations(self, authenticated_provider):
        """Test List operations for both EKS and EC2 resources."""
        # Mock EKS cluster listing
        authenticated_provider.eks_client.list_clusters.return_value = {
            "clusters": ["cluster-1", "cluster-2"]
        }

        # Mock EC2 instance listing
        authenticated_provider.ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-12345678",
                            "State": {"Name": "running"},
                            "Tags": [{"Key": "Name", "Value": "test-instance"}],
                        }
                    ]
                }
            ]
        }

        clusters = authenticated_provider.list_clusters()

        assert len(clusters) >= 2  # At least EKS clusters
        eks_clusters = [c for c in clusters if c["type"] == "eks"]
        assert len(eks_clusters) == 2

    def test_configuration_generation(self, authenticated_provider):
        """Test Clustrix configuration generation for AWS resources."""
        # Test EKS configuration
        eks_config = authenticated_provider.get_cluster_config("test-cluster", "eks")

        assert eks_config["cluster_type"] == "kubernetes"
        assert "AWS EKS" in eks_config["name"]
        assert eks_config["k8s_namespace"] == "default"
        assert eks_config["provider"] == "aws"
        assert "cluster_name" in eks_config["provider_config"]

        # Test EC2 configuration
        authenticated_provider.ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-12345678",
                            "PublicIpAddress": "1.2.3.4",
                            "State": {"Name": "running"},
                        }
                    ]
                }
            ]
        }

        ec2_config = authenticated_provider.get_cluster_config("i-12345678", "ec2")

        assert ec2_config["cluster_type"] == "ssh"
        assert "AWS EC2" in ec2_config["name"]
        assert ec2_config["cluster_host"] == "1.2.3.4"
        assert ec2_config["username"] == "ec2-user"
        assert ec2_config["provider"] == "aws"

    def test_cost_estimation_comprehensive(self, provider):
        """Test comprehensive cost estimation scenarios."""
        # Test EKS cost calculation
        eks_cost = provider.estimate_cost(
            cluster_type="eks", instance_type="c5.xlarge", node_count=5, hours=24
        )

        expected_control_plane = 0.10 * 24  # $0.10/hour for 24 hours
        expected_nodes = (
            0.170 * 5 * 24
        )  # $0.170/hour per c5.xlarge * 5 nodes * 24 hours
        expected_total = expected_control_plane + expected_nodes

        assert eks_cost["control_plane"] == expected_control_plane
        assert eks_cost["nodes"] == expected_nodes
        assert eks_cost["total"] == expected_total

        # Test EC2 cost calculation
        ec2_cost = provider.estimate_cost(
            cluster_type="ec2", instance_type="m5.2xlarge", hours=168  # 1 week
        )

        # m5.2xlarge not in default pricing, should use default
        expected_total = 0.10 * 168
        assert ec2_cost["total"] == expected_total

        # Test with known instance type
        ec2_cost_known = provider.estimate_cost(
            cluster_type="ec2", instance_type="t3.large", hours=12
        )

        expected_total_known = 0.0832 * 12  # t3.large price
        assert ec2_cost_known["total"] == expected_total_known

    def test_region_and_instance_operations(self, provider):
        """Test region and instance type operations."""
        # Test when not authenticated
        regions = provider.get_available_regions()
        assert "us-east-1" in regions
        assert "us-west-2" in regions
        assert isinstance(regions, list)

        instance_types = provider.get_available_instance_types()
        assert "t3.micro" in instance_types
        assert "c5.large" in instance_types
        assert isinstance(instance_types, list)

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_region_and_instance_operations_authenticated(self, mock_boto3, provider):
        """Test region and instance operations when authenticated."""
        # Setup authentication
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session
        mock_ec2_client = Mock()
        mock_iam_client = Mock()
        mock_session.client.side_effect = lambda service: {
            "ec2": mock_ec2_client,
            "iam": mock_iam_client,
        }.get(service, Mock())
        mock_iam_client.get_user.return_value = {"User": {"UserName": "test"}}

        provider.authenticate(access_key_id="test", secret_access_key="test")

        # Mock region listing
        mock_ec2_client.describe_regions.return_value = {
            "Regions": [
                {"RegionName": "us-east-1"},
                {"RegionName": "us-west-2"},
                {"RegionName": "eu-west-1"},
                {"RegionName": "ap-northeast-1"},
            ]
        }

        regions = provider.get_available_regions()
        assert "us-east-1" in regions
        assert "us-west-2" in regions
        # Priority regions should come first
        assert regions.index("us-east-1") < regions.index("ap-northeast-1")

        # Mock instance type listing
        mock_ec2_client.describe_instance_type_offerings.return_value = {
            "InstanceTypeOfferings": [
                {"InstanceType": "t3.micro"},
                {"InstanceType": "t3.small"},
                {"InstanceType": "t3.medium"},
                {"InstanceType": "c5.large"},
                {"InstanceType": "c5.xlarge"},
                {"InstanceType": "m5.large"},
            ]
        }

        instance_types = provider.get_available_instance_types()
        assert "t3.micro" in instance_types
        assert "c5.large" in instance_types
        assert len(instance_types) <= 30  # Should be limited

    def test_error_handling_comprehensive(self, authenticated_provider):
        """Test comprehensive error handling scenarios."""
        from clustrix.cloud_providers.aws import ClientError

        # Test cluster operations with unauthenticated provider
        provider = AWSProvider()

        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_eks_cluster("test")

        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.create_ec2_instance("test")

        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.get_cluster_status("test")

        with pytest.raises(RuntimeError, match="Not authenticated"):
            provider.list_clusters()

        # Test EKS cluster not found
        authenticated_provider.eks_client.describe_cluster.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DescribeCluster"
        )

        status = authenticated_provider.get_cluster_status("nonexistent", "eks")
        assert status["status"] == "NOT_FOUND"

        # Test successful deletion of non-existent cluster
        authenticated_provider.eks_client.list_nodegroups.return_value = {
            "nodegroups": []
        }
        authenticated_provider.eks_client.delete_cluster.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DeleteCluster"
        )

        result = authenticated_provider.delete_cluster("nonexistent", "eks")
        assert result is True  # Should succeed for non-existent resources

    def test_subnet_and_security_group_operations(self, authenticated_provider):
        """Test subnet and security group creation following AWS best practices."""
        vpc_id = "vpc-12345678"
        cluster_name = "test-cluster"

        # Test subnet creation
        authenticated_provider.ec2_client.describe_subnets.return_value = {
            "Subnets": []
        }
        authenticated_provider.ec2_client.create_subnet.side_effect = [
            {"Subnet": {"SubnetId": "subnet-1"}},
            {"Subnet": {"SubnetId": "subnet-2"}},
        ]

        subnet_ids = authenticated_provider._create_eks_subnets(vpc_id, cluster_name)
        assert len(subnet_ids) == 2
        assert "subnet-1" in subnet_ids
        assert "subnet-2" in subnet_ids

        # Verify proper tagging
        tag_calls = authenticated_provider.ec2_client.create_tags.call_args_list
        assert len(tag_calls) >= 2  # Should tag both subnets

        # Test security group creation
        authenticated_provider.ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": []
        }
        authenticated_provider.ec2_client.create_security_group.return_value = {
            "GroupId": "sg-12345678"
        }

        sg_ids = authenticated_provider._create_eks_security_groups(
            vpc_id, cluster_name
        )
        assert len(sg_ids) == 1
        assert "sg-12345678" in sg_ids

    def test_cluster_type_validation(self, authenticated_provider):
        """Test cluster type validation across all operations."""
        # Test invalid cluster types
        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.create_cluster("test", cluster_type="invalid")

        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.delete_cluster("test", cluster_type="invalid")

        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.get_cluster_status("test", cluster_type="invalid")

        with pytest.raises(ValueError, match="Unknown cluster type"):
            authenticated_provider.get_cluster_config("test", cluster_type="invalid")

    def test_credential_validation_edge_cases(self, authenticated_provider):
        """Test credential validation edge cases."""
        # Test when IAM client is None
        authenticated_provider.iam_client = None
        assert authenticated_provider.validate_credentials() is False

        # Test when not authenticated
        authenticated_provider.authenticated = False
        assert authenticated_provider.validate_credentials() is False

    @pytest.mark.integration
    @pytest.mark.skipif(
        not os.environ.get("AWS_ACCESS_KEY_ID"),
        reason="Real AWS credentials required for integration test",
    )
    def test_integration_with_real_aws(self, provider, real_aws_credentials):
        """Integration test with real AWS services when credentials available."""
        if not real_aws_credentials:
            pytest.skip("No real AWS credentials available")

        # Authenticate with real credentials
        result = provider.authenticate(**real_aws_credentials)
        if not result:
            pytest.skip("Authentication failed with provided credentials")

        # Test listing real regions
        regions = provider.get_available_regions()
        assert len(regions) > 10  # AWS has many regions
        assert "us-east-1" in regions

        # Test listing real instance types
        instance_types = provider.get_available_instance_types()
        assert len(instance_types) > 10
        assert any(t.startswith("t3.") for t in instance_types)

        # Test credential validation
        assert provider.validate_credentials() is True

        # Test listing existing clusters (should not fail)
        clusters = provider.list_clusters()
        assert isinstance(clusters, list)  # Should return a list, even if empty
