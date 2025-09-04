"""Comprehensive tests for AWS core provider functionality."""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.stub import Stubber

from clustrix.cloud_providers.aws import AWSProvider


class TestAWSProviderCore:
    """Test AWS provider core functionality - Stream 1 scope (lines 1-441)."""

    @pytest.fixture
    def provider(self):
        """Create AWSProvider instance."""
        return AWSProvider()

    @pytest.fixture
    def mock_credentials(self):
        """Mock AWS credentials."""
        return {
            "access_key_id": "AKIATEST123456789",
            "secret_access_key": "test-secret-key-abcdefg",
            "region": "us-west-2",
            "session_token": "test-session-token",
        }

    @pytest.fixture
    def stubbed_provider(self, mock_credentials):
        """Create AWSProvider with stubbed boto3 clients."""
        with patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True):
            with patch("clustrix.cloud_providers.aws.boto3") as mock_boto3:
                provider = AWSProvider()

                # Create mock session and clients
                mock_session = Mock()
                mock_boto3.Session.return_value = mock_session

                mock_ec2_client = Mock()
                mock_eks_client = Mock()
                mock_iam_client = Mock()
                mock_sts_client = Mock()

                mock_session.client.side_effect = lambda service: {
                    "ec2": mock_ec2_client,
                    "eks": mock_eks_client,
                    "iam": mock_iam_client,
                    "sts": mock_sts_client,
                }[service]

                # Set up stubbers
                provider.ec2_stubber = Stubber(mock_ec2_client)
                provider.eks_stubber = Stubber(mock_eks_client)
                provider.iam_stubber = Stubber(mock_iam_client)
                provider.sts_stubber = Stubber(mock_sts_client)

                return (
                    provider,
                    mock_session,
                    mock_ec2_client,
                    mock_eks_client,
                    mock_iam_client,
                    mock_sts_client,
                )

    def test_initialization(self, provider):
        """Test provider initialization."""
        assert provider.ec2_client is None
        assert provider.eks_client is None
        assert provider.iam_client is None
        assert provider.region == "us-east-1"
        assert not provider.authenticated
        assert provider.credentials == {}  # CloudProvider initializes with empty dict

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_authenticate_success_with_credentials(
        self, mock_boto3, provider, mock_credentials
    ):
        """Test successful authentication with provided credentials."""
        # Mock session and clients
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session

        mock_ec2_client = Mock()
        mock_eks_client = Mock()
        mock_iam_client = Mock()
        mock_sts_client = Mock()

        mock_session.client.side_effect = lambda service: {
            "ec2": mock_ec2_client,
            "eks": mock_eks_client,
            "iam": mock_iam_client,
            "sts": mock_sts_client,
        }[service]

        # Mock successful STS call
        mock_sts_client.get_caller_identity.return_value = {
            "UserId": "AIDACKCEVSQ6C2EXAMPLE",
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/testuser",
        }

        # Test authentication
        result = provider.authenticate(**mock_credentials)

        assert result is True
        assert provider.authenticated is True
        assert provider.region == "us-west-2"
        assert provider.ec2_client == mock_ec2_client
        assert provider.eks_client == mock_eks_client
        assert provider.iam_client == mock_iam_client
        assert provider.credentials["access_key_id"] == "AKIATEST123456789"
        assert provider.credentials["secret_access_key"] == "test-secret-key-abcdefg"
        assert provider.credentials["session_token"] == "test-session-token"

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_authenticate_success_without_session_token(self, mock_boto3, provider):
        """Test successful authentication without session token."""
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session

        mock_sts_client = Mock()
        mock_session.client.return_value = mock_sts_client
        mock_sts_client.get_caller_identity.return_value = {"Account": "123456789012"}

        credentials = {
            "access_key_id": "AKIATEST123456789",
            "secret_access_key": "test-secret-key",
            "region": "eu-west-1",
        }

        result = provider.authenticate(**credentials)

        assert result is True
        assert provider.authenticated is True
        assert provider.region == "eu-west-1"
        assert "session_token" not in provider.credentials

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_authenticate_with_credential_manager(self, mock_boto3, provider):
        """Test authentication using credential manager."""
        provider.get_credentials_from_manager = Mock(
            return_value={
                "access_key_id": "AKIATEST123456789",
                "secret_access_key": "test-secret-key-from-manager",
                "region": "us-east-1",
            }
        )

        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session

        mock_sts_client = Mock()
        mock_session.client.return_value = mock_sts_client
        mock_sts_client.get_caller_identity.return_value = {"Account": "123456789012"}

        # Provide incomplete credentials
        result = provider.authenticate(access_key_id="AKIATEST123456789")

        assert result is True
        assert provider.authenticated is True
        assert (
            provider.credentials["secret_access_key"] == "test-secret-key-from-manager"
        )

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", False)
    def test_authenticate_boto3_not_available(self, provider, mock_credentials):
        """Test authentication failure when boto3 not available."""
        result = provider.authenticate(**mock_credentials)

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    def test_authenticate_missing_credentials(self, provider):
        """Test authentication failure with missing credentials."""
        # Mock credential manager to return None
        provider.get_credentials_from_manager = Mock(return_value=None)

        result = provider.authenticate()

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_authenticate_invalid_credentials(
        self, mock_boto3, provider, mock_credentials
    ):
        """Test authentication failure with invalid credentials."""
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session

        mock_sts_client = Mock()
        mock_session.client.return_value = mock_sts_client
        mock_sts_client.get_caller_identity.side_effect = NoCredentialsError()

        result = provider.authenticate(**mock_credentials)

        assert result is False
        assert not provider.authenticated

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_authenticate_client_error(self, mock_boto3, provider, mock_credentials):
        """Test authentication failure with client error."""
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session

        mock_sts_client = Mock()
        mock_session.client.return_value = mock_sts_client
        mock_sts_client.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "InvalidUserID.NotFound", "Message": "Invalid user"}},
            "GetCallerIdentity",
        )

        result = provider.authenticate(**mock_credentials)

        assert result is False
        assert not provider.authenticated

    def test_validate_credentials_not_authenticated(self, provider):
        """Test credential validation when not authenticated."""
        result = provider.validate_credentials()
        assert result is False

    def test_validate_credentials_no_iam_client(self, provider):
        """Test credential validation without IAM client."""
        provider.authenticated = True
        result = provider.validate_credentials()
        assert result is False

    def test_validate_credentials_success(self, provider):
        """Test successful credential validation."""
        provider.authenticated = True
        provider.iam_client = Mock()
        provider.iam_client.get_user.return_value = {"User": {"UserName": "testuser"}}

        result = provider.validate_credentials()
        assert result is True

    def test_validate_credentials_failure(self, provider):
        """Test credential validation failure."""
        provider.authenticated = True
        provider.iam_client = Mock()
        provider.iam_client.get_user.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}, "GetUser"
        )

        result = provider.validate_credentials()
        assert result is False

    def test_create_or_get_eks_cluster_role_existing(self, stubbed_provider):
        """Test getting existing EKS cluster role."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.iam_client = iam_client

        # Mock existing role response
        existing_role_arn = "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"
        iam_client.get_role.return_value = {"Role": {"Arn": existing_role_arn}}

        result = provider._create_or_get_eks_cluster_role()

        assert result == existing_role_arn
        iam_client.get_role.assert_called_once_with(
            RoleName="clustrix-eks-cluster-role"
        )

    def test_create_or_get_eks_cluster_role_create_new(self, stubbed_provider):
        """Test creating new EKS cluster role."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.iam_client = iam_client

        # Mock role doesn't exist, then create
        iam_client.get_role.side_effect = ClientError(
            {"Error": {"Code": "NoSuchEntity", "Message": "Role not found"}}, "GetRole"
        )

        new_role_arn = "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role"
        iam_client.create_role.return_value = {"Role": {"Arn": new_role_arn}}

        result = provider._create_or_get_eks_cluster_role()

        assert result == new_role_arn
        iam_client.create_role.assert_called_once()
        iam_client.attach_role_policy.assert_called_once_with(
            RoleName="clustrix-eks-cluster-role",
            PolicyArn="arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
        )

    def test_create_or_get_vpc_for_eks_existing(self, stubbed_provider):
        """Test getting existing VPC for EKS."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client
        provider.region = "us-west-2"

        cluster_name = "test-cluster"
        existing_vpc_id = "vpc-12345678"

        # Mock existing VPC
        ec2_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": existing_vpc_id}]}

        # Mock subnet and security group creation
        provider._create_eks_subnets = Mock(return_value=["subnet-123", "subnet-456"])
        provider._create_eks_security_groups = Mock(return_value=["sg-123"])

        result = provider._create_or_get_vpc_for_eks(cluster_name)

        assert result["vpc_id"] == existing_vpc_id
        assert result["subnet_ids"] == ["subnet-123", "subnet-456"]
        assert result["security_group_ids"] == ["sg-123"]

        ec2_client.describe_vpcs.assert_called_once()

    def test_create_or_get_vpc_for_eks_create_new(self, stubbed_provider):
        """Test creating new VPC for EKS."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client
        provider.region = "us-west-2"

        cluster_name = "test-cluster"
        new_vpc_id = "vpc-87654321"

        # Mock no existing VPC, then create new
        ec2_client.describe_vpcs.return_value = {"Vpcs": []}
        ec2_client.create_vpc.return_value = {"Vpc": {"VpcId": new_vpc_id}}

        # Mock subnet and security group creation
        provider._create_eks_subnets = Mock(return_value=["subnet-789", "subnet-012"])
        provider._create_eks_security_groups = Mock(return_value=["sg-456"])

        result = provider._create_or_get_vpc_for_eks(cluster_name)

        assert result["vpc_id"] == new_vpc_id
        assert result["subnet_ids"] == ["subnet-789", "subnet-012"]
        assert result["security_group_ids"] == ["sg-456"]

        ec2_client.create_vpc.assert_called_once_with(CidrBlock="10.0.0.0/16")
        ec2_client.create_tags.assert_called_once()

    def test_create_or_get_vpc_for_eks_client_error(self, stubbed_provider):
        """Test VPC creation with client error."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client

        cluster_name = "test-cluster"

        ec2_client.describe_vpcs.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "Not authorized"}},
            "DescribeVpcs",
        )

        with pytest.raises(ClientError):
            provider._create_or_get_vpc_for_eks(cluster_name)

    def test_create_eks_subnets_existing(self, stubbed_provider):
        """Test getting existing EKS subnets."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client
        provider.region = "us-west-2"

        vpc_id = "vpc-12345678"
        cluster_name = "test-cluster"
        existing_subnet_ids = ["subnet-111", "subnet-222"]

        # Mock existing subnets
        ec2_client.describe_subnets.side_effect = [
            {"Subnets": [{"SubnetId": existing_subnet_ids[0]}]},
            {"Subnets": [{"SubnetId": existing_subnet_ids[1]}]},
        ]

        result = provider._create_eks_subnets(vpc_id, cluster_name)

        assert result == existing_subnet_ids
        assert ec2_client.describe_subnets.call_count == 2

    def test_create_eks_subnets_create_new(self, stubbed_provider):
        """Test creating new EKS subnets."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client
        provider.region = "us-west-2"

        vpc_id = "vpc-12345678"
        cluster_name = "test-cluster"
        new_subnet_ids = ["subnet-new1", "subnet-new2"]

        # Mock no existing subnets, then create new
        ec2_client.describe_subnets.return_value = {"Subnets": []}
        ec2_client.create_subnet.side_effect = [
            {"Subnet": {"SubnetId": new_subnet_ids[0]}},
            {"Subnet": {"SubnetId": new_subnet_ids[1]}},
        ]

        result = provider._create_eks_subnets(vpc_id, cluster_name)

        assert result == new_subnet_ids
        assert ec2_client.create_subnet.call_count == 2
        assert ec2_client.create_tags.call_count == 2

    def test_create_eks_security_groups_existing(self, stubbed_provider):
        """Test getting existing EKS security group."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client

        vpc_id = "vpc-12345678"
        cluster_name = "test-cluster"
        existing_sg_id = "sg-existing123"

        # Mock existing security group
        ec2_client.describe_security_groups.return_value = {
            "SecurityGroups": [{"GroupId": existing_sg_id}]
        }

        result = provider._create_eks_security_groups(vpc_id, cluster_name)

        assert result == [existing_sg_id]
        ec2_client.describe_security_groups.assert_called_once()

    def test_create_eks_security_groups_create_new(self, stubbed_provider):
        """Test creating new EKS security group."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client

        vpc_id = "vpc-12345678"
        cluster_name = "test-cluster"
        new_sg_id = "sg-new456"

        # Mock no existing security group, then create new
        ec2_client.describe_security_groups.return_value = {"SecurityGroups": []}
        ec2_client.create_security_group.return_value = {"GroupId": new_sg_id}

        result = provider._create_eks_security_groups(vpc_id, cluster_name)

        assert result == [new_sg_id]
        ec2_client.create_security_group.assert_called_once()
        ec2_client.create_tags.assert_called_once()

    def test_create_eks_cluster_success(self, stubbed_provider):
        """Test successful EKS cluster creation."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.eks_client = eks_client
        provider.region = "us-west-2"

        cluster_name = "test-cluster"
        cluster_arn = f"arn:aws:eks:us-west-2:123456789012:cluster/{cluster_name}"
        cluster_endpoint = f"https://{cluster_name}.eks.us-west-2.amazonaws.com"

        # Mock dependencies
        provider._create_or_get_eks_cluster_role = Mock(
            return_value="arn:aws:iam::123456789012:role/eks-role"
        )
        provider._create_or_get_vpc_for_eks = Mock(
            return_value={
                "vpc_id": "vpc-123",
                "subnet_ids": ["subnet-1", "subnet-2"],
                "security_group_ids": ["sg-1"],
            }
        )

        # Mock EKS cluster creation
        eks_client.create_cluster.return_value = {
            "cluster": {
                "name": cluster_name,
                "arn": cluster_arn,
                "status": "CREATING",
                "endpoint": cluster_endpoint,
                "version": "1.27",
                "createdAt": datetime.now(timezone.utc),
            }
        }

        result = provider.create_eks_cluster(
            cluster_name=cluster_name,
            node_count=3,
            instance_type="t3.large",
            kubernetes_version="1.27",
        )

        assert result["cluster_name"] == cluster_name
        assert result["status"] == "CREATING"
        assert result["endpoint"] == cluster_endpoint
        assert result["arn"] == cluster_arn
        assert result["version"] == "1.27"
        assert result["node_count"] == 3
        assert result["instance_type"] == "t3.large"
        assert result["region"] == "us-west-2"

    def test_create_eks_cluster_not_authenticated(self, stubbed_provider):
        """Test EKS cluster creation without authentication."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.create_eks_cluster("test-cluster")

    def test_create_eks_cluster_client_error(self, stubbed_provider):
        """Test EKS cluster creation with client error."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.eks_client = eks_client

        # Mock dependencies
        provider._create_or_get_eks_cluster_role = Mock(
            return_value="arn:aws:iam::123456789012:role/eks-role"
        )
        provider._create_or_get_vpc_for_eks = Mock(
            return_value={
                "vpc_id": "vpc-123",
                "subnet_ids": ["subnet-1", "subnet-2"],
                "security_group_ids": ["sg-1"],
            }
        )

        # Mock EKS client error
        eks_client.create_cluster.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ResourceInUseException",
                    "Message": "Cluster already exists",
                }
            },
            "CreateCluster",
        )

        with pytest.raises(ClientError):
            provider.create_eks_cluster("test-cluster")

    def test_create_ec2_instance_success_default_ami(self, stubbed_provider):
        """Test successful EC2 instance creation with default AMI."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client
        provider.region = "us-west-2"

        instance_name = "test-instance"
        instance_id = "i-1234567890abcdef0"

        # Mock AMI lookup
        ec2_client.describe_images.return_value = {
            "Images": [
                {"ImageId": "ami-12345678", "CreationDate": "2023-01-01T00:00:00.000Z"}
            ]
        }

        # Mock instance creation
        ec2_client.run_instances.return_value = {
            "Instances": [{"InstanceId": instance_id, "State": {"Name": "pending"}}]
        }

        # Mock waiter and instance description
        waiter_mock = Mock()
        ec2_client.get_waiter.return_value = waiter_mock

        ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": instance_id,
                            "PublicIpAddress": "1.2.3.4",
                            "PrivateIpAddress": "10.0.1.100",
                            "State": {"Name": "running"},
                        }
                    ]
                }
            ]
        }

        result = provider.create_ec2_instance(
            instance_name=instance_name, instance_type="t3.large"
        )

        assert result["instance_id"] == instance_id
        assert result["instance_name"] == instance_name
        assert result["public_ip"] == "1.2.3.4"
        assert result["private_ip"] == "10.0.1.100"
        assert result["instance_type"] == "t3.large"
        assert result["state"] == "running"
        assert result["region"] == "us-west-2"

        waiter_mock.wait.assert_called_once_with(InstanceIds=[instance_id])

    def test_create_ec2_instance_success_custom_ami(self, stubbed_provider):
        """Test successful EC2 instance creation with custom AMI."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client

        instance_name = "test-instance"
        instance_id = "i-1234567890abcdef0"
        custom_ami = "ami-custom123"
        key_name = "my-key-pair"

        # Mock instance creation
        ec2_client.run_instances.return_value = {
            "Instances": [{"InstanceId": instance_id, "State": {"Name": "pending"}}]
        }

        # Mock waiter and instance description
        waiter_mock = Mock()
        ec2_client.get_waiter.return_value = waiter_mock

        ec2_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": instance_id,
                            "PublicIpAddress": "5.6.7.8",
                            "PrivateIpAddress": "10.0.1.200",
                            "State": {"Name": "running"},
                        }
                    ]
                }
            ]
        }

        result = provider.create_ec2_instance(
            instance_name=instance_name,
            instance_type="m5.xlarge",
            ami_id=custom_ami,
            key_name=key_name,
        )

        assert result["instance_id"] == instance_id
        assert result["public_ip"] == "5.6.7.8"

        # Verify AMI lookup was not called
        ec2_client.describe_images.assert_not_called()

        # Verify run_instances called with custom AMI and key
        ec2_client.run_instances.assert_called_once_with(
            ImageId=custom_ami,
            InstanceType="m5.xlarge",
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

    def test_create_ec2_instance_not_authenticated(self, stubbed_provider):
        """Test EC2 instance creation without authentication."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )

        with pytest.raises(RuntimeError, match="Not authenticated with AWS"):
            provider.create_ec2_instance("test-instance")

    def test_create_ec2_instance_client_error(self, stubbed_provider):
        """Test EC2 instance creation with client error."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.ec2_client = ec2_client

        # Mock client error
        ec2_client.describe_images.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "Not authorized"}},
            "DescribeImages",
        )

        with pytest.raises(ClientError):
            provider.create_ec2_instance("test-instance")

    def test_rate_limiting_error_handling(self, stubbed_provider):
        """Test handling of AWS API rate limiting."""
        provider, session, ec2_client, eks_client, iam_client, sts_client = (
            stubbed_provider
        )
        provider.authenticated = True
        provider.iam_client = iam_client

        # Mock rate limiting error on both get_role and create_role
        throttling_error = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}}, "GetRole"
        )
        iam_client.get_role.side_effect = throttling_error
        iam_client.create_role.side_effect = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}}, "CreateRole"
        )

        with pytest.raises(ClientError) as exc_info:
            provider._create_or_get_eks_cluster_role()

        assert exc_info.value.response["Error"]["Code"] == "Throttling"

    def test_credential_expiration_handling(self, provider):
        """Test handling of expired credentials."""
        provider.authenticated = True
        provider.iam_client = Mock()

        # Mock credential expiration error
        provider.iam_client.get_user.side_effect = ClientError(
            {"Error": {"Code": "TokenRefreshRequired", "Message": "Token expired"}},
            "GetUser",
        )

        result = provider.validate_credentials()
        assert result is False
