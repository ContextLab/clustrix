"""
Comprehensive AWS provisioning tests for infrastructure, cost management, and cleanup.

Tests focus on:
- AWS EKS infrastructure provisioning workflow
- Network and security configuration
- Cost estimation and resource discovery
- Resource cleanup verification
- Error handling and edge cases

Uses boto3 stubber for predictable AWS API responses without real API calls.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from botocore.stub import Stubber
from botocore.exceptions import ClientError

# Test imports
from clustrix.kubernetes.aws_provisioner import AWSEKSFromScratchProvisioner
from clustrix.kubernetes.cluster_provisioner import ClusterSpec
from clustrix.cost_providers.aws import AWSCostMonitor
from clustrix.cloud_providers.aws import AWSProvider


class TestAWSInfrastructureProvisioning:
    """Tests for comprehensive AWS infrastructure provisioning."""

    @pytest.fixture
    def cluster_spec(self):
        """Create a standard cluster specification for testing."""
        spec = ClusterSpec(
            provider="aws",
            cluster_name="test-cluster",
            region="us-east-1",
            node_count=3,
            kubernetes_version="1.27",
            aws_instance_type="t3.medium",
        )
        return spec

    @pytest.fixture
    def aws_credentials(self):
        """Mock AWS credentials for testing."""
        return {
            "access_key_id": "test-access-key-123",
            "secret_access_key": "test-secret-key-456",
            "session_token": "test-session-token-789",
            "region": "us-east-1",
        }

    @pytest.fixture
    def provisioner(self, aws_credentials):
        """Create AWSEKSFromScratchProvisioner with mocked clients."""
        with patch("clustrix.kubernetes.aws_provisioner.BOTO3_AVAILABLE", True), patch(
            "clustrix.kubernetes.aws_provisioner.boto3"
        ):

            provisioner = AWSEKSFromScratchProvisioner(
                credentials=aws_credentials, region="us-east-1"
            )

            # Mock all AWS service clients
            provisioner.ec2 = Mock()
            provisioner.eks = Mock()
            provisioner.iam = Mock()
            provisioner.session = Mock()

            return provisioner

    def test_validate_credentials_success(self, provisioner):
        """Test successful credential validation."""

        # Mock client creation to return our pre-configured mocks
        def mock_client(service):
            if service == "sts":
                sts_client = Mock()
                sts_client.get_caller_identity.return_value = {
                    "Account": "123456789012",
                    "UserId": "test-user-id",
                    "Arn": "arn:aws:iam::123456789012:user/test-user",
                }
                return sts_client
            elif service == "ec2":
                ec2_client = Mock()
                ec2_client.describe_availability_zones.return_value = {
                    "AvailabilityZones": [{"ZoneName": "us-east-1a"}]
                }
                return ec2_client
            elif service == "eks":
                eks_client = Mock()
                eks_client.list_clusters.return_value = {"clusters": []}
                return eks_client
            elif service == "iam":
                iam_client = Mock()
                iam_client.list_roles.return_value = {"Roles": []}
                return iam_client
            return Mock()

        provisioner.session.client.side_effect = mock_client

        result = provisioner.validate_credentials()

        assert result is True
        # Verify session.client was called for all required services
        expected_calls = ["sts", "ec2", "eks", "iam"]
        actual_calls = [
            call[0][0] for call in provisioner.session.client.call_args_list
        ]
        for service in expected_calls:
            assert service in actual_calls

    def test_validate_credentials_failure(self, provisioner):
        """Test credential validation failure scenarios."""

        # Mock STS client that raises authentication error
        def mock_client(service):
            if service == "sts":
                sts_client = Mock()
                sts_client.get_caller_identity.side_effect = ClientError(
                    {
                        "Error": {
                            "Code": "InvalidUserID.NotFound",
                            "Message": "Invalid credentials",
                        }
                    },
                    "GetCallerIdentity",
                )
                return sts_client
            return Mock()

        provisioner.session.client.side_effect = mock_client

        result = provisioner.validate_credentials()

        assert result is False

    def test_provision_complete_infrastructure_workflow(
        self, provisioner, cluster_spec
    ):
        """Test complete end-to-end infrastructure provisioning workflow."""
        # Mock all sub-methods for infrastructure provisioning
        vpc_config = {
            "vpc_id": "vpc-12345678",
            "subnet_ids": ["subnet-1", "subnet-2", "subnet-3", "subnet-4"],
            "private_subnet_ids": ["subnet-3", "subnet-4"],
            "public_subnet_ids": ["subnet-1", "subnet-2"],
            "security_group_ids": ["sg-12345678", "sg-87654321"],
            "internet_gateway_id": "igw-12345678",
            "nat_gateway_ids": ["nat-12345678", "nat-87654321"],
        }

        iam_config = {
            "cluster_role_arn": "arn:aws:iam::123456789012:role/eks-cluster-role",
            "node_role_arn": "arn:aws:iam::123456789012:role/eks-node-role",
            "cluster_role_name": "eks-cluster-role",
            "node_role_name": "eks-node-role",
        }

        cluster_info = {
            "cluster_name": "test-cluster",
            "arn": "arn:aws:eks:us-east-1:123456789012:cluster/test-cluster",
            "endpoint": "https://test.eks.amazonaws.com",
            "version": "1.27",
        }

        provisioner._create_vpc_infrastructure = Mock(return_value=vpc_config)
        provisioner._create_iam_infrastructure = Mock(return_value=iam_config)
        provisioner._create_eks_control_plane = Mock(return_value=cluster_info)
        provisioner._create_node_groups = Mock(
            return_value={"node_group_name": "test-nodes"}
        )
        provisioner._configure_kubectl_access = Mock(
            return_value={"kubeconfig": "test-config"}
        )
        provisioner._setup_clustrix_environment = Mock()
        provisioner._verify_cluster_operational = Mock()

        result = provisioner.provision_complete_infrastructure(cluster_spec)

        # Verify workflow order and results
        provisioner._create_vpc_infrastructure.assert_called_once_with(cluster_spec)
        provisioner._create_iam_infrastructure.assert_called_once_with(cluster_spec)
        provisioner._create_eks_control_plane.assert_called_once_with(
            cluster_spec, vpc_config, iam_config
        )
        provisioner._create_node_groups.assert_called_once_with(
            cluster_spec, cluster_info, vpc_config, iam_config
        )
        provisioner._configure_kubectl_access.assert_called_once_with(cluster_info)
        provisioner._setup_clustrix_environment.assert_called_once()
        provisioner._verify_cluster_operational.assert_called_once_with("test-cluster")

        # Verify result structure
        assert result["cluster_name"] == "test-cluster"
        assert result["provider"] == "aws"
        assert result["vpc_id"] == "vpc-12345678"
        assert result["ready_for_jobs"] is True

    def test_create_vpc_infrastructure(self, provisioner, cluster_spec):
        """Test VPC infrastructure creation with all network components."""
        # Mock VPC creation
        provisioner.ec2.create_vpc.return_value = {"Vpc": {"VpcId": "vpc-12345678"}}

        # Mock availability zones
        provisioner.ec2.describe_availability_zones.return_value = {
            "AvailabilityZones": [
                {"ZoneName": "us-east-1a"},
                {"ZoneName": "us-east-1b"},
                {"ZoneName": "us-east-1c"},
            ]
        }

        # Mock subnet creation
        provisioner.ec2.create_subnet.side_effect = [
            {"Subnet": {"SubnetId": "subnet-public-1"}},
            {"Subnet": {"SubnetId": "subnet-public-2"}},
            {"Subnet": {"SubnetId": "subnet-private-1"}},
            {"Subnet": {"SubnetId": "subnet-private-2"}},
        ]

        # Mock Internet Gateway
        provisioner.ec2.create_internet_gateway.return_value = {
            "InternetGateway": {"InternetGatewayId": "igw-12345678"}
        }

        # Mock Elastic IP allocation
        provisioner.ec2.allocate_address.side_effect = [
            {"AllocationId": "eipalloc-12345678"},
            {"AllocationId": "eipalloc-87654321"},
        ]

        # Mock NAT Gateway creation
        provisioner.ec2.create_nat_gateway.side_effect = [
            {"NatGateway": {"NatGatewayId": "nat-12345678"}},
            {"NatGateway": {"NatGatewayId": "nat-87654321"}},
        ]

        # Mock NAT Gateway status check
        provisioner._wait_for_nat_gateway = Mock()

        # Mock routing and security group creation
        provisioner._create_routing_tables = Mock()
        provisioner._create_security_groups = Mock(
            return_value=["sg-12345678", "sg-87654321"]
        )

        result = provisioner._create_vpc_infrastructure(cluster_spec)

        # Verify VPC creation
        provisioner.ec2.create_vpc.assert_called_once_with(CidrBlock="10.0.0.0/16")
        provisioner.ec2.modify_vpc_attribute.assert_called()

        # Verify subnet creation (4 subnets: 2 public, 2 private)
        assert provisioner.ec2.create_subnet.call_count == 4

        # Verify NAT Gateway creation (2 for high availability)
        assert provisioner.ec2.create_nat_gateway.call_count == 2

        # Verify result structure
        assert result["vpc_id"] == "vpc-12345678"
        assert len(result["subnet_ids"]) == 4
        assert len(result["private_subnet_ids"]) == 2
        assert len(result["public_subnet_ids"]) == 2
        assert result["internet_gateway_id"] == "igw-12345678"
        assert len(result["nat_gateway_ids"]) == 2

    def test_create_routing_tables(self, provisioner):
        """Test routing table creation for public and private subnets."""
        vpc_id = "vpc-12345678"
        subnets = {
            "public": ["subnet-public-1", "subnet-public-2"],
            "private": ["subnet-private-1", "subnet-private-2"],
        }
        igw_id = "igw-12345678"
        nat_gateways = ["nat-12345678", "nat-87654321"]

        # Mock route table creation
        provisioner.ec2.create_route_table.side_effect = [
            {"RouteTable": {"RouteTableId": "rt-public-12345678"}},
            {"RouteTable": {"RouteTableId": "rt-private-12345678"}},
            {"RouteTable": {"RouteTableId": "rt-private-87654321"}},
        ]

        provisioner._create_routing_tables(vpc_id, subnets, igw_id, nat_gateways)

        # Verify route table creation (1 public + 2 private for HA)
        assert provisioner.ec2.create_route_table.call_count == 3

        # Verify internet gateway route for public table
        provisioner.ec2.create_route.assert_any_call(
            RouteTableId="rt-public-12345678",
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=igw_id,
        )

        # Verify NAT gateway routes for private tables
        provisioner.ec2.create_route.assert_any_call(
            RouteTableId="rt-private-12345678",
            DestinationCidrBlock="0.0.0.0/0",
            NatGatewayId="nat-12345678",
        )

    def test_configure_security_group_rules(self, provisioner):
        """Test security group rule configuration for EKS cluster."""
        cp_sg_id = "sg-control-plane"
        ng_sg_id = "sg-node-group"

        provisioner._configure_security_group_rules(cp_sg_id, ng_sg_id)

        # Verify security group rule creation calls
        assert provisioner.ec2.authorize_security_group_ingress.call_count == 3

        # Check node-to-node communication rule
        call_args = provisioner.ec2.authorize_security_group_ingress.call_args_list
        node_rule_call = next(
            call for call in call_args if call[1]["GroupId"] == ng_sg_id
        )
        assert node_rule_call[1]["IpPermissions"][0]["IpProtocol"] == "-1"

    def test_create_iam_infrastructure(self, provisioner, cluster_spec):
        """Test IAM roles and policies creation for EKS."""
        # Mock cluster role creation
        provisioner._create_eks_cluster_role = Mock(
            return_value="arn:aws:iam::123456789012:role/clustrix-eks-cluster-role-test-cluster"
        )

        # Mock node role creation
        provisioner._create_eks_node_role = Mock(
            return_value="arn:aws:iam::123456789012:role/clustrix-eks-node-role-test-cluster"
        )

        result = provisioner._create_iam_infrastructure(cluster_spec)

        provisioner._create_eks_cluster_role.assert_called_once_with(
            "clustrix-eks-cluster-role-test-cluster"
        )
        provisioner._create_eks_node_role.assert_called_once_with(
            "clustrix-eks-node-role-test-cluster"
        )

        assert (
            result["cluster_role_arn"]
            == "arn:aws:iam::123456789012:role/clustrix-eks-cluster-role-test-cluster"
        )
        assert (
            result["node_role_arn"]
            == "arn:aws:iam::123456789012:role/clustrix-eks-node-role-test-cluster"
        )

    def test_create_eks_cluster_role(self, provisioner):
        """Test EKS cluster service role creation."""
        role_name = "test-cluster-role"

        # Mock successful role creation
        provisioner.iam.create_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/{role_name}"}
        }

        result = provisioner._create_eks_cluster_role(role_name)

        # Verify role creation with correct trust policy
        provisioner.iam.create_role.assert_called_once()
        call_args = provisioner.iam.create_role.call_args[1]
        assert call_args["RoleName"] == role_name

        trust_policy = json.loads(call_args["AssumeRolePolicyDocument"])
        assert (
            trust_policy["Statement"][0]["Principal"]["Service"] == "eks.amazonaws.com"
        )

        # Verify policy attachment
        provisioner.iam.attach_role_policy.assert_called_once_with(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
        )

        assert result == f"arn:aws:iam::123456789012:role/{role_name}"

    def test_create_eks_node_role(self, provisioner):
        """Test EKS node group role creation."""
        role_name = "test-node-role"

        # Mock successful role creation
        provisioner.iam.create_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/{role_name}"}
        }

        result = provisioner._create_eks_node_role(role_name)

        # Verify role creation with correct trust policy
        provisioner.iam.create_role.assert_called_once()
        call_args = provisioner.iam.create_role.call_args[1]
        assert call_args["RoleName"] == role_name

        trust_policy = json.loads(call_args["AssumeRolePolicyDocument"])
        assert (
            trust_policy["Statement"][0]["Principal"]["Service"] == "ec2.amazonaws.com"
        )

        # Verify all required policy attachments
        assert provisioner.iam.attach_role_policy.call_count == 3

        expected_policies = [
            "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
            "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
            "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        ]

        attached_policies = [
            call[1]["PolicyArn"]
            for call in provisioner.iam.attach_role_policy.call_args_list
        ]
        for policy in expected_policies:
            assert policy in attached_policies

    def test_create_eks_control_plane(self, provisioner, cluster_spec):
        """Test EKS control plane creation and configuration."""
        vpc_config = {
            "subnet_ids": ["subnet-1", "subnet-2"],
            "security_group_ids": ["sg-control-plane", "sg-nodes"],
        }
        iam_config = {"cluster_role_arn": "arn:aws:iam::123456789012:role/cluster-role"}

        # Mock cluster creation
        provisioner.eks.create_cluster.return_value = {
            "cluster": {
                "name": "test-cluster",
                "status": "CREATING",
                "arn": "arn:aws:eks:us-east-1:123456789012:cluster/test-cluster",
                "endpoint": "",
                "version": "1.27",
            }
        }

        # Mock waiter
        mock_waiter = Mock()
        provisioner.eks.get_waiter.return_value = mock_waiter

        # Mock final cluster status
        provisioner.eks.describe_cluster.return_value = {
            "cluster": {
                "name": "test-cluster",
                "status": "ACTIVE",
                "arn": "arn:aws:eks:us-east-1:123456789012:cluster/test-cluster",
                "endpoint": "https://test.eks.amazonaws.com",
                "version": "1.27",
            }
        }

        result = provisioner._create_eks_control_plane(
            cluster_spec, vpc_config, iam_config
        )

        # Verify cluster creation call
        provisioner.eks.create_cluster.assert_called_once()
        create_args = provisioner.eks.create_cluster.call_args[1]
        assert create_args["name"] == "test-cluster"
        assert create_args["roleArn"] == "arn:aws:iam::123456789012:role/cluster-role"
        assert create_args["resourcesVpcConfig"]["subnetIds"] == [
            "subnet-1",
            "subnet-2",
        ]

        # Verify waiter usage
        mock_waiter.wait.assert_called_once()

        assert result["name"] == "test-cluster"
        assert result["status"] == "ACTIVE"

    def test_create_node_groups(self, provisioner, cluster_spec):
        """Test EKS managed node group creation."""
        cluster_info = {"name": "test-cluster"}
        vpc_config = {"private_subnet_ids": ["subnet-private-1", "subnet-private-2"]}
        iam_config = {"node_role_arn": "arn:aws:iam::123456789012:role/node-role"}

        # Mock waiter
        mock_waiter = Mock()
        provisioner.eks.get_waiter.return_value = mock_waiter

        result = provisioner._create_node_groups(
            cluster_spec, cluster_info, vpc_config, iam_config
        )

        # Verify node group creation
        provisioner.eks.create_nodegroup.assert_called_once()
        create_args = provisioner.eks.create_nodegroup.call_args[1]

        assert create_args["clusterName"] == "test-cluster"
        assert create_args["nodegroupName"] == "clustrix-nodes-test-cluster"
        assert create_args["subnets"] == ["subnet-private-1", "subnet-private-2"]
        assert create_args["nodeRole"] == "arn:aws:iam::123456789012:role/node-role"
        assert create_args["instanceTypes"] == ["t3.medium"]
        assert create_args["scalingConfig"]["desiredSize"] == 3

        # Verify waiter usage
        mock_waiter.wait.assert_called_once()

        assert result["node_group_name"] == "clustrix-nodes-test-cluster"

    def test_destroy_cluster_infrastructure(self, provisioner):
        """Test comprehensive cluster infrastructure destruction."""
        cluster_id = "test-cluster"

        # Mock node group listing and deletion
        provisioner.eks.list_nodegroups.return_value = {
            "nodegroups": ["nodegroup-1", "nodegroup-2"]
        }

        # Mock waiters
        mock_waiter = Mock()
        provisioner.eks.get_waiter.return_value = mock_waiter

        # Mock resource cleanup
        provisioner._cleanup_all_resources = Mock()

        result = provisioner.destroy_cluster_infrastructure(cluster_id)

        # Verify node group deletion
        assert provisioner.eks.delete_nodegroup.call_count == 2
        provisioner.eks.delete_nodegroup.assert_any_call(
            clusterName=cluster_id, nodegroupName="nodegroup-1"
        )
        provisioner.eks.delete_nodegroup.assert_any_call(
            clusterName=cluster_id, nodegroupName="nodegroup-2"
        )

        # Verify cluster deletion
        provisioner.eks.delete_cluster.assert_called_once_with(name=cluster_id)

        # Verify cleanup
        provisioner._cleanup_all_resources.assert_called_once()

        assert result is True

    def test_get_cluster_status(self, provisioner):
        """Test cluster status retrieval with detailed information."""
        cluster_id = "test-cluster"

        # Mock cluster information
        provisioner.eks.describe_cluster.return_value = {
            "cluster": {
                "name": cluster_id,
                "status": "ACTIVE",
                "endpoint": "https://test.eks.amazonaws.com",
                "version": "1.27",
            }
        }

        # Mock node group information
        provisioner.eks.list_nodegroups.return_value = {"nodegroups": ["nodegroup-1"]}
        provisioner.eks.describe_nodegroup.return_value = {
            "nodegroup": {
                "status": "ACTIVE",
                "scalingConfig": {"minSize": 1, "maxSize": 5, "desiredSize": 3},
            }
        }

        result = provisioner.get_cluster_status(cluster_id)

        assert result["cluster_id"] == cluster_id
        assert result["status"] == "ACTIVE"
        assert result["endpoint"] == "https://test.eks.amazonaws.com"
        assert result["ready_for_jobs"] is True
        assert len(result["node_groups"]) == 1
        assert result["node_groups"][0]["name"] == "nodegroup-1"
        assert result["node_groups"][0]["status"] == "ACTIVE"

    def test_provisioning_failure_cleanup(self, provisioner, cluster_spec):
        """Test cleanup when provisioning fails."""
        # Mock VPC creation success
        provisioner._create_vpc_infrastructure = Mock(
            return_value={"vpc_id": "vpc-12345678", "subnet_ids": ["subnet-1"]}
        )

        # Mock IAM creation success
        provisioner._create_iam_infrastructure = Mock(
            return_value={
                "cluster_role_arn": "arn:aws:iam::123456789012:role/cluster-role"
            }
        )

        # Mock EKS control plane creation failure
        provisioner._create_eks_control_plane = Mock(
            side_effect=Exception("Control plane creation failed")
        )

        # Mock cleanup
        provisioner._cleanup_failed_provisioning = Mock()

        with pytest.raises(Exception, match="Control plane creation failed"):
            provisioner.provision_complete_infrastructure(cluster_spec)

        # Verify cleanup was called
        provisioner._cleanup_failed_provisioning.assert_called_once_with("test-cluster")


class TestAWSCostEstimation:
    """Tests for AWS cost estimation and resource discovery."""

    @pytest.fixture
    def aws_provider(self):
        """Create AWSProvider for cost estimation tests."""
        return AWSProvider()

    @pytest.fixture
    def cost_monitor(self):
        """Create AWSCostMonitor for cost tracking tests."""
        return AWSCostMonitor(region="us-east-1", use_pricing_api=False)

    def test_estimate_cost_eks_cluster(self, aws_provider):
        """Test EKS cluster cost estimation."""
        result = aws_provider.estimate_cost(
            cluster_type="eks", instance_type="c5.xlarge", node_count=4, hours=24
        )

        # EKS control plane: $0.10/hour * 24 hours = $2.40
        expected_control_plane = 0.10 * 24
        # c5.xlarge nodes: $0.170/hour * 4 nodes * 24 hours = $16.32
        expected_nodes = 0.170 * 4 * 24
        expected_total = expected_control_plane + expected_nodes

        assert result["control_plane"] == expected_control_plane
        assert result["nodes"] == expected_nodes
        assert result["total"] == expected_total

    def test_estimate_cost_ec2_instance(self, aws_provider):
        """Test EC2 instance cost estimation."""
        result = aws_provider.estimate_cost(
            cluster_type="ec2", instance_type="t3.large", hours=48
        )

        # t3.large: $0.0832/hour * 48 hours = $3.9936
        expected_total = 0.0832 * 48

        assert result["instance"] == expected_total
        assert result["total"] == expected_total

    def test_estimate_cost_unknown_instance_type(self, aws_provider):
        """Test cost estimation with unknown instance type falls back to default."""
        result = aws_provider.estimate_cost(
            cluster_type="ec2", instance_type="unknown.instance.type", hours=10
        )

        # Should use default price $0.10/hour
        expected_total = 0.10 * 10
        assert result["total"] == expected_total

    def test_get_available_instance_types_unauthenticated(self, aws_provider):
        """Test instance type listing when not authenticated."""
        instance_types = aws_provider.get_available_instance_types()

        assert isinstance(instance_types, list)
        assert len(instance_types) > 0
        assert "t3.micro" in instance_types
        assert "t3.medium" in instance_types
        assert "c5.large" in instance_types
        assert "m5.large" in instance_types

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_get_available_instance_types_authenticated(self, mock_boto3, aws_provider):
        """Test instance type listing when authenticated."""
        # Setup authentication
        mock_session = Mock()
        mock_boto3.Session.return_value = mock_session
        mock_ec2_client = Mock()
        mock_iam_client = Mock()
        mock_session.client.side_effect = lambda service: {
            "ec2": mock_ec2_client,
            "iam": mock_iam_client,
        }.get(service, Mock())

        # Mock credentials validation
        mock_iam_client.get_user.return_value = {"User": {"UserName": "test"}}

        # Mock instance type response
        mock_ec2_client.describe_instance_type_offerings.return_value = {
            "InstanceTypeOfferings": [
                {"InstanceType": "t3.micro"},
                {"InstanceType": "t3.small"},
                {"InstanceType": "t3.medium"},
                {"InstanceType": "t3.large"},
                {"InstanceType": "c5.large"},
                {"InstanceType": "c5.xlarge"},
                {"InstanceType": "c5.2xlarge"},
                {"InstanceType": "m5.large"},
                {"InstanceType": "m5.xlarge"},
                {"InstanceType": "r5.large"},
                {"InstanceType": "r5.xlarge"},
            ]
        }

        aws_provider.authenticate(access_key_id="test", secret_access_key="test")
        instance_types = aws_provider.get_available_instance_types()

        assert isinstance(instance_types, list)
        assert len(instance_types) <= 30  # Should be limited
        assert "t3.micro" in instance_types
        assert "c5.large" in instance_types
        assert "m5.large" in instance_types

    def test_get_available_instance_types_different_region(self, aws_provider):
        """Test instance type listing for different regions."""
        # Mock authentication
        aws_provider.authenticated = True
        aws_provider.region = "us-east-1"
        aws_provider.credentials = {
            "access_key_id": "test",
            "secret_access_key": "test",
        }

        with patch("clustrix.cloud_providers.aws.boto3") as mock_boto3:
            # Mock session and client for different region
            mock_session = Mock()
            mock_boto3.Session.return_value = mock_session
            mock_ec2_client = Mock()
            mock_session.client.return_value = mock_ec2_client

            mock_ec2_client.describe_instance_type_offerings.return_value = {
                "InstanceTypeOfferings": [
                    {"InstanceType": "t3.micro"},
                    {"InstanceType": "t3.small"},
                    {"InstanceType": "c5.large"},
                ]
            }

            instance_types = aws_provider.get_available_instance_types("eu-west-1")

            # Verify session was created for different region
            mock_boto3.Session.assert_called_with(
                aws_access_key_id="test",
                aws_secret_access_key="test",
                aws_session_token=None,
                region_name="eu-west-1",
            )

            assert "t3.micro" in instance_types
            assert "c5.large" in instance_types

    def test_get_available_regions_unauthenticated(self, aws_provider):
        """Test region listing when not authenticated."""
        regions = aws_provider.get_available_regions()

        assert isinstance(regions, list)
        assert len(regions) > 0
        assert "us-east-1" in regions
        assert "us-west-2" in regions
        assert "eu-west-1" in regions

    @patch("clustrix.cloud_providers.aws.BOTO3_AVAILABLE", True)
    @patch("clustrix.cloud_providers.aws.boto3")
    def test_get_available_regions_authenticated(self, mock_boto3, aws_provider):
        """Test region listing when authenticated."""
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

        # Mock regions response
        mock_ec2_client.describe_regions.return_value = {
            "Regions": [
                {"RegionName": "us-east-1"},
                {"RegionName": "us-west-1"},
                {"RegionName": "us-west-2"},
                {"RegionName": "eu-west-1"},
                {"RegionName": "eu-central-1"},
                {"RegionName": "ap-southeast-1"},
                {"RegionName": "ap-northeast-1"},
                {"RegionName": "ap-south-1"},
            ]
        }

        aws_provider.authenticate(access_key_id="test", secret_access_key="test")
        regions = aws_provider.get_available_regions()

        assert isinstance(regions, list)
        assert len(regions) >= 7
        # Priority regions should come first
        assert regions.index("us-east-1") < regions.index("ap-south-1")

    def test_cost_monitor_estimate_cost_on_demand(self, cost_monitor):
        """Test cost estimation for on-demand instances."""
        estimate = cost_monitor.estimate_cost("c5.xlarge", 12.5, use_spot=False)

        # c5.xlarge: $0.17/hour * 12.5 hours = $2.125
        expected_cost = 0.17 * 12.5

        assert estimate.instance_type == "c5.xlarge (On-Demand)"
        assert estimate.hourly_rate == 0.17
        assert estimate.hours_used == 12.5
        assert estimate.estimated_cost == expected_cost
        assert estimate.currency == "USD"
        assert estimate.pricing_source == "hardcoded"

    def test_cost_monitor_estimate_cost_spot(self, cost_monitor):
        """Test cost estimation for spot instances."""
        estimate = cost_monitor.estimate_cost("c5.xlarge", 10.0, use_spot=True)

        # c5.xlarge spot: $0.17/hour * 0.65 (35% discount) * 10 hours = $1.105
        expected_hourly_rate = 0.17 * 0.65
        expected_cost = expected_hourly_rate * 10.0

        assert estimate.instance_type == "c5.xlarge (Spot)"
        assert abs(estimate.hourly_rate - expected_hourly_rate) < 0.001
        assert estimate.estimated_cost == expected_cost
        assert "Spot pricing is estimated" in estimate.pricing_warning

    def test_cost_monitor_gpu_instance_pricing(self, cost_monitor):
        """Test cost estimation for GPU instances."""
        estimate = cost_monitor.estimate_cost("p3.2xlarge", 4.0)

        # p3.2xlarge: $3.06/hour * 4 hours = $12.24
        expected_cost = 3.06 * 4.0

        assert estimate.hourly_rate == 3.06
        assert estimate.estimated_cost == expected_cost

    def test_cost_monitor_get_pricing_info(self, cost_monitor):
        """Test retrieval of pricing information."""
        pricing = cost_monitor.get_pricing_info()

        assert isinstance(pricing, dict)
        assert "t3.micro" in pricing
        assert "c5.xlarge" in pricing
        assert "p3.2xlarge" in pricing
        assert pricing["t3.micro"] == 0.0104
        assert pricing["c5.xlarge"] == 0.17

    def test_cost_monitor_get_spot_pricing_info(self, cost_monitor):
        """Test retrieval of spot pricing estimates."""
        spot_pricing = cost_monitor.get_spot_pricing_info()

        assert isinstance(spot_pricing, dict)
        assert "t3.micro" in spot_pricing
        assert "c5.xlarge" in spot_pricing

        # Verify spot discount applied (c5 family gets ~35% discount)
        on_demand_c5 = 0.17
        expected_spot_c5 = on_demand_c5 * 0.65
        assert abs(spot_pricing["c5.xlarge"] - expected_spot_c5) < 0.001

    def test_cost_monitor_batch_cost_estimation(self, cost_monitor):
        """Test cost estimation for AWS Batch workloads."""
        result = cost_monitor.estimate_batch_cost(
            job_queue="test-queue",
            compute_environment="test-env",
            estimated_jobs=100,
            avg_job_duration_hours=0.5,
        )

        # 100 jobs * 0.5 hours * $0.085/hour (c5.large default) = $4.25
        expected_cost = 100 * 0.5 * 0.085

        assert result["estimated_jobs"] == 100
        assert result["total_compute_hours"] == 50.0
        assert result["estimated_cost"] == expected_cost
        assert result["cost_per_job"] == expected_cost / 100
        assert len(result["recommendations"]) > 0

    def test_cost_monitor_region_pricing_comparison(self, cost_monitor):
        """Test pricing comparison across AWS regions."""
        comparison = cost_monitor.get_region_pricing_comparison("t3.medium")

        assert isinstance(comparison, dict)
        assert "us-east-1" in comparison
        assert "eu-west-1" in comparison
        assert "ap-northeast-1" in comparison

        # Verify regional pricing differences
        us_east_price = comparison["us-east-1"]["on_demand_hourly"]
        tokyo_price = comparison["ap-northeast-1"]["on_demand_hourly"]

        # Tokyo should be more expensive (1.2x multiplier)
        assert tokyo_price > us_east_price
        assert abs(tokyo_price - (us_east_price * 1.2)) < 0.001


class TestAWSResourceCleanup:
    """Tests for AWS resource cleanup verification."""

    @pytest.fixture
    def provisioner(self, aws_credentials):
        """Create provisioner for cleanup tests."""
        with patch("clustrix.kubernetes.aws_provisioner.BOTO3_AVAILABLE", True), patch(
            "clustrix.kubernetes.aws_provisioner.boto3"
        ):

            provisioner = AWSEKSFromScratchProvisioner(
                credentials=aws_credentials, region="us-east-1"
            )

            provisioner.ec2 = Mock()
            provisioner.eks = Mock()
            provisioner.iam = Mock()

            # Initialize with some tracked resources
            provisioner.created_resources = {
                "vpcs": ["vpc-12345678"],
                "subnets": ["subnet-1", "subnet-2"],
                "security_groups": ["sg-12345678"],
                "internet_gateways": ["igw-12345678"],
                "nat_gateways": ["nat-12345678"],
                "route_tables": ["rt-12345678"],
                "iam_roles": ["test-cluster-role", "test-node-role"],
                "eks_clusters": ["test-cluster"],
                "eks_node_groups": ["test-nodegroup"],
            }

            return provisioner

    @pytest.fixture
    def aws_credentials(self):
        """Mock AWS credentials."""
        return {
            "access_key_id": "test-access-key",
            "secret_access_key": "test-secret-key",
        }

    def test_destroy_cluster_with_resource_not_found(self, provisioner):
        """Test cluster destruction when some resources don't exist."""
        cluster_id = "test-cluster"

        # Mock node group listing
        provisioner.eks.list_nodegroups.return_value = {
            "nodegroups": ["test-nodegroup"]
        }

        # Mock node group deletion with not found error
        provisioner.eks.delete_nodegroup.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DeleteNodegroup"
        )

        # Mock cluster deletion success
        mock_waiter = Mock()
        provisioner.eks.get_waiter.return_value = mock_waiter

        # Mock cleanup
        provisioner._cleanup_all_resources = Mock()

        result = provisioner.destroy_cluster_infrastructure(cluster_id)

        # Should succeed even with missing resources
        assert result is True
        provisioner._cleanup_all_resources.assert_called_once()

    def test_destroy_cluster_handles_waiter_timeout(self, provisioner):
        """Test cluster destruction handles waiter timeouts gracefully."""
        cluster_id = "test-cluster"

        provisioner.eks.list_nodegroups.return_value = {
            "nodegroups": ["test-nodegroup"]
        }

        # Mock waiter timeout
        mock_waiter = Mock()
        mock_waiter.wait.side_effect = Exception("Waiter timeout")
        provisioner.eks.get_waiter.return_value = mock_waiter

        provisioner._cleanup_all_resources = Mock()

        result = provisioner.destroy_cluster_infrastructure(cluster_id)

        # Should still succeed and attempt cleanup
        assert result is True
        provisioner._cleanup_all_resources.assert_called_once()

    def test_resource_tracking_during_provisioning(self, provisioner):
        """Test that resources are properly tracked during provisioning."""
        # Simulate VPC creation
        provisioner.ec2.create_vpc.return_value = {"Vpc": {"VpcId": "vpc-new123"}}

        # Call method that should track VPC
        vpc_response = provisioner.ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc_response["Vpc"]["VpcId"]
        provisioner.created_resources["vpcs"].append(vpc_id)

        assert "vpc-new123" in provisioner.created_resources["vpcs"]

    def test_failed_provisioning_cleanup(self, provisioner):
        """Test cleanup is called when provisioning fails."""
        cluster_name = "test-cluster"

        # Mock cleanup method
        provisioner._cleanup_all_resources = Mock()

        provisioner._cleanup_failed_provisioning(cluster_name)

        provisioner._cleanup_all_resources.assert_called_once()

    def test_iam_role_already_exists_handling(self, provisioner):
        """Test handling of existing IAM roles during creation."""
        role_name = "existing-role"

        # Mock role already exists error
        provisioner.iam.create_role.side_effect = ClientError(
            {"Error": {"Code": "EntityAlreadyExists"}}, "CreateRole"
        )

        # Mock getting existing role
        provisioner.iam.get_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/{role_name}"}
        }

        result = provisioner._create_eks_cluster_role(role_name)

        # Should get existing role ARN
        assert result == f"arn:aws:iam::123456789012:role/{role_name}"
        provisioner.iam.get_role.assert_called_once_with(RoleName=role_name)

    def test_cluster_status_resource_not_found(self, provisioner):
        """Test cluster status when cluster doesn't exist."""
        cluster_id = "nonexistent-cluster"

        provisioner.eks.describe_cluster.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DescribeCluster"
        )

        result = provisioner.get_cluster_status(cluster_id)

        assert result["cluster_id"] == cluster_id
        assert result["status"] == "NOT_FOUND"
        assert result["ready_for_jobs"] is False

    def test_nat_gateway_wait_failure(self, provisioner):
        """Test NAT gateway wait handling failures."""
        nat_id = "nat-12345678"

        # Mock NAT gateway in failed state
        provisioner.ec2.describe_nat_gateways.return_value = {
            "NatGateways": [{"State": "failed"}]
        }

        with pytest.raises(RuntimeError, match="NAT Gateway failed: failed"):
            provisioner._wait_for_nat_gateway(nat_id)

    def test_nat_gateway_wait_timeout(self, provisioner):
        """Test NAT gateway wait timeout handling."""
        nat_id = "nat-timeout"

        # Mock NAT gateway stuck in pending state
        provisioner.ec2.describe_nat_gateways.return_value = {
            "NatGateways": [{"State": "pending"}]
        }

        with pytest.raises(RuntimeError, match="NAT Gateway .* not available after"):
            provisioner._wait_for_nat_gateway(nat_id)

    def test_verify_cluster_operational_success(self, provisioner):
        """Test cluster operational verification success."""
        cluster_name = "test-cluster"

        # Mock cluster status
        provisioner.eks.describe_cluster.return_value = {
            "cluster": {"status": "ACTIVE"}
        }

        # Mock node group status
        provisioner.eks.list_nodegroups.return_value = {"nodegroups": ["nodegroup-1"]}
        provisioner.eks.describe_nodegroup.return_value = {
            "nodegroup": {"status": "ACTIVE"}
        }

        # Should not raise exception
        provisioner._verify_cluster_operational(cluster_name)

    def test_verify_cluster_operational_failure(self, provisioner):
        """Test cluster operational verification failure."""
        cluster_name = "unhealthy-cluster"

        # Mock cluster not active
        provisioner.eks.describe_cluster.return_value = {
            "cluster": {"status": "FAILED"}
        }

        with pytest.raises(RuntimeError, match="Cluster not active: FAILED"):
            provisioner._verify_cluster_operational(cluster_name)

    def test_kubectl_config_generation(self, provisioner):
        """Test kubectl configuration generation."""
        cluster_info = {
            "name": "test-cluster",
            "arn": "arn:aws:eks:us-east-1:123456789012:cluster/test-cluster",
            "endpoint": "https://test.eks.amazonaws.com",
            "certificateAuthority": {"data": "LS0tLS1CRUdJTi..."},
        }

        result = provisioner._configure_kubectl_access(cluster_info)

        assert result["apiVersion"] == "v1"
        assert result["kind"] == "Config"
        assert len(result["clusters"]) == 1
        assert (
            result["clusters"][0]["cluster"]["server"]
            == "https://test.eks.amazonaws.com"
        )
        assert result["current-context"] == cluster_info["arn"]

    def test_setup_clustrix_environment_failure_handling(self, provisioner):
        """Test Clustrix environment setup handles kubectl failures gracefully."""
        cluster_info = {"name": "test-cluster", "arn": "test-arn"}
        kubectl_config = {"test": "config"}

        with patch(
            "clustrix.kubernetes.aws_provisioner.subprocess.run"
        ) as mock_run, patch(
            "clustrix.kubernetes.aws_provisioner.tempfile.NamedTemporaryFile"
        ) as mock_temp, patch(
            "clustrix.kubernetes.aws_provisioner.yaml.dump"
        ) as mock_yaml_dump:

            # Mock temporary file
            mock_file = Mock()
            mock_file.name = "/tmp/test-kubeconfig.yaml"
            mock_temp.return_value.__enter__.return_value = mock_file

            # Mock subprocess failure (but continue)
            mock_run.return_value.returncode = 1

            # Should not raise exception even if kubectl commands fail
            provisioner._setup_clustrix_environment(cluster_info, kubectl_config)

            # Verify attempts were made
            assert mock_run.call_count == 3  # namespace, serviceaccount, rolebinding


class TestAWSErrorHandling:
    """Tests for comprehensive AWS error handling scenarios."""

    @pytest.fixture
    def provisioner(self, aws_credentials):
        """Create provisioner for error handling tests."""
        with patch("clustrix.kubernetes.aws_provisioner.BOTO3_AVAILABLE", True), patch(
            "clustrix.kubernetes.aws_provisioner.boto3"
        ):

            provisioner = AWSEKSFromScratchProvisioner(
                credentials=aws_credentials, region="us-east-1"
            )

            provisioner.ec2 = Mock()
            provisioner.eks = Mock()
            provisioner.iam = Mock()
            provisioner.session = Mock()

            return provisioner

    @pytest.fixture
    def aws_credentials(self):
        """Mock AWS credentials."""
        return {
            "access_key_id": "test-access-key",
            "secret_access_key": "test-secret-key",
        }

    def test_api_rate_limit_handling(self, provisioner):
        """Test handling of AWS API rate limiting."""
        # Mock throttling error
        provisioner.ec2.create_vpc.side_effect = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}}, "CreateVpc"
        )

        with pytest.raises(ClientError) as exc_info:
            provisioner.ec2.create_vpc(CidrBlock="10.0.0.0/16")

        assert exc_info.value.response["Error"]["Code"] == "Throttling"

    def test_permission_denied_handling(self, provisioner):
        """Test handling of permission denied errors."""
        # Mock access denied
        provisioner.iam.create_role.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "User is not authorized"}},
            "CreateRole",
        )

        with pytest.raises(ClientError) as exc_info:
            provisioner.iam.create_role(
                RoleName="test-role",
                AssumeRolePolicyDocument=json.dumps({"test": "policy"}),
            )

        assert exc_info.value.response["Error"]["Code"] == "AccessDenied"

    def test_resource_limit_exceeded(self, provisioner):
        """Test handling of AWS resource limits."""
        # Mock service quota exceeded
        provisioner.eks.create_cluster.side_effect = ClientError(
            {
                "Error": {
                    "Code": "LimitExceededException",
                    "Message": "Maximum number of clusters reached",
                }
            },
            "CreateCluster",
        )

        with pytest.raises(ClientError) as exc_info:
            provisioner.eks.create_cluster(
                name="test-cluster",
                version="1.27",
                roleArn="arn:aws:iam::123456789012:role/test-role",
            )

        assert exc_info.value.response["Error"]["Code"] == "LimitExceededException"

    def test_invalid_parameter_handling(self, provisioner):
        """Test handling of invalid parameters."""
        # Mock invalid parameter error
        provisioner.ec2.create_subnet.side_effect = ClientError(
            {
                "Error": {
                    "Code": "InvalidParameterValue",
                    "Message": "Invalid CIDR block",
                }
            },
            "CreateSubnet",
        )

        with pytest.raises(ClientError) as exc_info:
            provisioner.ec2.create_subnet(
                VpcId="vpc-12345678", CidrBlock="invalid-cidr"
            )

        assert exc_info.value.response["Error"]["Code"] == "InvalidParameterValue"

    def test_dependency_violation_handling(self, provisioner):
        """Test handling of resource dependency violations."""
        # Mock dependency violation (e.g., trying to delete VPC with resources)
        provisioner.ec2.delete_vpc.side_effect = ClientError(
            {
                "Error": {
                    "Code": "DependencyViolation",
                    "Message": "VPC has dependencies and cannot be deleted",
                }
            },
            "DeleteVpc",
        )

        with pytest.raises(ClientError) as exc_info:
            provisioner.ec2.delete_vpc(VpcId="vpc-12345678")

        assert exc_info.value.response["Error"]["Code"] == "DependencyViolation"
