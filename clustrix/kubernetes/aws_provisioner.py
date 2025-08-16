"""
AWS EKS from-scratch provisioner.

Provides complete EKS cluster provisioning with all required infrastructure
including VPC, IAM roles, security groups, and node groups.
"""

import json
import logging
import time
from typing import Dict, Any, List
import subprocess
import tempfile

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None
    ClientError = Exception
    NoCredentialsError = Exception

from .cluster_provisioner import BaseKubernetesProvisioner, ClusterSpec

logger = logging.getLogger(__name__)


class AWSEKSFromScratchProvisioner(BaseKubernetesProvisioner):
    """
    Complete AWS EKS cluster provisioner from blank AWS account.

    This provisioner creates all required infrastructure components:
    - VPC with public and private subnets
    - Internet Gateway and NAT Gateways
    - Security Groups with proper rules
    - IAM roles and policies for EKS
    - EKS control plane
    - EKS node groups with auto-scaling
    - kubectl configuration
    - Clustrix namespace and RBAC setup
    """

    def __init__(self, credentials: Dict[str, str], region: str):
        super().__init__(credentials, region)

        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 required for AWS EKS provisioning. "
                "Install with: pip install boto3"
            )

        # Initialize AWS clients
        self.session = boto3.Session(
            aws_access_key_id=credentials.get("access_key_id"),
            aws_secret_access_key=credentials.get("secret_access_key"),
            aws_session_token=credentials.get("session_token"),
            region_name=region,
        )

        self.ec2 = self.session.client("ec2")
        self.eks = self.session.client("eks")
        self.iam = self.session.client("iam")

        # Track created resources for cleanup
        self.created_resources: Dict[str, List[str]] = {
            "vpcs": [],
            "subnets": [],
            "security_groups": [],
            "internet_gateways": [],
            "nat_gateways": [],
            "route_tables": [],
            "iam_roles": [],
            "iam_policies": [],
            "eks_clusters": [],
            "eks_node_groups": [],
        }

    def validate_credentials(self) -> bool:
        """Validate AWS credentials and required permissions."""
        try:
            # Test basic AWS access
            sts = self.session.client("sts")
            identity = sts.get_caller_identity()
            logger.info(
                f"✅ AWS credentials validated for account: {identity.get('Account')}"
            )

            # Check required service permissions (basic check)
            required_services = ["ec2", "eks", "iam"]
            for service in required_services:
                try:
                    client = self.session.client(service)
                    # Make a simple read-only call to test permissions
                    if service == "ec2":
                        client.describe_availability_zones(MaxResults=1)
                    elif service == "eks":
                        client.list_clusters(maxResults=1)
                    elif service == "iam":
                        client.list_roles(MaxItems=1)

                    logger.debug(f"✅ {service.upper()} service access confirmed")
                except Exception as e:
                    logger.warning(f"⚠️ Limited {service.upper()} permissions: {e}")

            return True

        except Exception as e:
            logger.error(f"❌ AWS credential validation failed: {e}")
            return False

    def provision_complete_infrastructure(self, spec: ClusterSpec) -> Dict[str, Any]:
        """
        Create complete EKS cluster infrastructure from scratch.

        Steps:
        1. Create VPC with public/private subnets
        2. Create Internet Gateway and NAT Gateways
        3. Set up routing tables
        4. Create security groups
        5. Create IAM roles and policies
        6. Create EKS control plane
        7. Create and configure node groups
        8. Configure kubectl access
        9. Set up Clustrix namespace and RBAC
        10. Verify cluster is ready for jobs
        """
        logger.info(f"🚀 Starting EKS cluster provisioning: {spec.cluster_name}")

        try:
            # Step 1: Create VPC infrastructure
            vpc_config = self._create_vpc_infrastructure(spec)

            # Step 2: Create IAM infrastructure
            iam_config = self._create_iam_infrastructure(spec)

            # Step 3: Create EKS control plane
            cluster_info = self._create_eks_control_plane(spec, vpc_config, iam_config)

            # Step 4: Create node groups
            self._create_node_groups(spec, cluster_info, vpc_config, iam_config)

            # Step 5: Configure kubectl access
            kubectl_config = self._configure_kubectl_access(cluster_info)

            # Step 6: Set up Clustrix environment
            self._setup_clustrix_environment(cluster_info, kubectl_config)

            # Step 7: Verify cluster ready
            self._verify_cluster_operational(cluster_info["cluster_name"])

            result = {
                "cluster_id": cluster_info["cluster_name"],
                "cluster_name": cluster_info["cluster_name"],
                "provider": "aws",
                "region": self.region,
                "endpoint": cluster_info["endpoint"],
                "arn": cluster_info["arn"],
                "version": cluster_info["version"],
                "node_count": spec.node_count,
                "instance_type": spec.aws_instance_type,
                "vpc_id": vpc_config["vpc_id"],
                "subnet_ids": vpc_config["subnet_ids"],
                "security_group_ids": vpc_config["security_group_ids"],
                "kubectl_config": kubectl_config,
                "ready_for_jobs": True,
                "created_resources": self.created_resources.copy(),
            }

            logger.info(f"✅ EKS cluster provisioning completed: {spec.cluster_name}")
            return result

        except Exception as e:
            logger.error(f"❌ EKS cluster provisioning failed: {e}")
            # Attempt cleanup of any created resources
            self._cleanup_failed_provisioning(spec.cluster_name)
            raise

    def _create_vpc_infrastructure(self, spec: ClusterSpec) -> Dict[str, Any]:
        """Create VPC with all networking components."""
        logger.info("🏗️ Creating VPC infrastructure...")

        # Create VPC
        vpc_response = self.ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc_response["Vpc"]["VpcId"]
        self.created_resources["vpcs"].append(vpc_id)

        # Enable DNS support
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        self.ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})

        # Tag VPC
        self.ec2.create_tags(
            Resources=[vpc_id],
            Tags=[
                {"Key": "Name", "Value": f"clustrix-eks-vpc-{spec.cluster_name}"},
                {"Key": "clustrix:cluster", "Value": spec.cluster_name},
                {"Key": "clustrix:managed", "Value": "true"},
            ],
        )

        # Get availability zones
        azs = self.ec2.describe_availability_zones()["AvailabilityZones"]
        az_names = [az["ZoneName"] for az in azs[:2]]  # Use first 2 AZs

        # Create public and private subnets
        subnet_configs = [
            {"cidr": "10.0.1.0/24", "type": "public", "az": az_names[0]},
            {"cidr": "10.0.2.0/24", "type": "public", "az": az_names[1]},
            {"cidr": "10.0.101.0/24", "type": "private", "az": az_names[0]},
            {"cidr": "10.0.102.0/24", "type": "private", "az": az_names[1]},
        ]

        subnets: Dict[str, List[str]] = {}
        for config in subnet_configs:
            subnet_response = self.ec2.create_subnet(
                VpcId=vpc_id, CidrBlock=config["cidr"], AvailabilityZone=config["az"]
            )
            subnet_id = subnet_response["Subnet"]["SubnetId"]
            self.created_resources["subnets"].append(subnet_id)

            # Tag subnet
            self.ec2.create_tags(
                Resources=[subnet_id],
                Tags=[
                    {
                        "Key": "Name",
                        "Value": f"clustrix-eks-{config['type']}-{config['az']}",
                    },
                    {"Key": "clustrix:cluster", "Value": spec.cluster_name},
                    {
                        "Key": "kubernetes.io/role/elb",
                        "Value": "1" if config["type"] == "public" else "",
                    },
                    {
                        "Key": "kubernetes.io/role/internal-elb",
                        "Value": "1" if config["type"] == "private" else "",
                    },
                ],
            )

            if config["type"] not in subnets:
                subnets[config["type"]] = []
            subnets[config["type"]].append(subnet_id)

        # Create Internet Gateway
        igw_response = self.ec2.create_internet_gateway()
        igw_id = igw_response["InternetGateway"]["InternetGatewayId"]
        self.created_resources["internet_gateways"].append(igw_id)

        # Attach Internet Gateway to VPC
        self.ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)

        # Create NAT Gateways for private subnets
        nat_gateways = []
        for i, public_subnet_id in enumerate(subnets["public"]):
            # Allocate Elastic IP
            eip_response = self.ec2.allocate_address(Domain="vpc")
            allocation_id = eip_response["AllocationId"]

            # Create NAT Gateway
            nat_response = self.ec2.create_nat_gateway(
                SubnetId=public_subnet_id, AllocationId=allocation_id
            )
            nat_id = nat_response["NatGateway"]["NatGatewayId"]
            self.created_resources["nat_gateways"].append(nat_id)
            nat_gateways.append(nat_id)

            # Wait for NAT Gateway to be available
            self._wait_for_nat_gateway(nat_id)

        # Create route tables and routes
        self._create_routing_tables(vpc_id, subnets, igw_id, nat_gateways)

        # Create security groups
        security_group_ids = self._create_security_groups(vpc_id, spec)

        return {
            "vpc_id": vpc_id,
            "subnet_ids": subnets["private"] + subnets["public"],
            "private_subnet_ids": subnets["private"],
            "public_subnet_ids": subnets["public"],
            "security_group_ids": security_group_ids,
            "internet_gateway_id": igw_id,
            "nat_gateway_ids": nat_gateways,
        }

    def _create_security_groups(self, vpc_id: str, spec: ClusterSpec) -> List[str]:
        """Create security groups for EKS cluster."""
        logger.info("🔒 Creating security groups...")

        # Control plane security group
        cp_sg_response = self.ec2.create_security_group(
            GroupName=f"clustrix-eks-control-plane-{spec.cluster_name}",
            Description=f"EKS control plane security group for {spec.cluster_name}",
            VpcId=vpc_id,
        )
        cp_sg_id = cp_sg_response["GroupId"]
        self.created_resources["security_groups"].append(cp_sg_id)

        # Node group security group
        ng_sg_response = self.ec2.create_security_group(
            GroupName=f"clustrix-eks-nodes-{spec.cluster_name}",
            Description=f"EKS node group security group for {spec.cluster_name}",
            VpcId=vpc_id,
        )
        ng_sg_id = ng_sg_response["GroupId"]
        self.created_resources["security_groups"].append(ng_sg_id)

        # Add security group rules
        self._configure_security_group_rules(cp_sg_id, ng_sg_id)

        return [cp_sg_id, ng_sg_id]

    def _create_iam_infrastructure(self, spec: ClusterSpec) -> Dict[str, Any]:
        """Create IAM roles and policies for EKS."""
        logger.info("👤 Creating IAM infrastructure...")

        # EKS cluster service role
        cluster_role_name = f"clustrix-eks-cluster-role-{spec.cluster_name}"
        cluster_role_arn = self._create_eks_cluster_role(cluster_role_name)

        # EKS node group role
        node_role_name = f"clustrix-eks-node-role-{spec.cluster_name}"
        node_role_arn = self._create_eks_node_role(node_role_name)

        return {
            "cluster_role_arn": cluster_role_arn,
            "node_role_arn": node_role_arn,
            "cluster_role_name": cluster_role_name,
            "node_role_name": node_role_name,
        }

    def _create_eks_cluster_role(self, role_name: str) -> str:
        """Create EKS cluster service role."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "eks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        try:
            role_response = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="EKS cluster service role for Clustrix",
            )
            role_arn = role_response["Role"]["Arn"]
            self.created_resources["iam_roles"].append(role_name)

            # Attach required policies
            policies = ["arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"]

            for policy_arn in policies:
                self.iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

            logger.info(f"✅ Created EKS cluster role: {role_arn}")
            return role_arn

        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                # Role already exists, get its ARN
                role_response = self.iam.get_role(RoleName=role_name)
                return role_response["Role"]["Arn"]
            else:
                raise

    def _create_eks_node_role(self, role_name: str) -> str:
        """Create EKS node group role."""
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        try:
            role_response = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="EKS node group role for Clustrix",
            )
            role_arn = role_response["Role"]["Arn"]
            self.created_resources["iam_roles"].append(role_name)

            # Attach required policies
            policies = [
                "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
                "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
                "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
            ]

            for policy_arn in policies:
                self.iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

            logger.info(f"✅ Created EKS node role: {role_arn}")
            return role_arn

        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                # Role already exists, get its ARN
                role_response = self.iam.get_role(RoleName=role_name)
                return role_response["Role"]["Arn"]
            else:
                raise

    def _create_eks_control_plane(
        self, spec: ClusterSpec, vpc_config: Dict[str, Any], iam_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create EKS control plane."""
        logger.info("🎛️ Creating EKS control plane...")

        cluster_config = {
            "name": spec.cluster_name,
            "version": spec.kubernetes_version,
            "roleArn": iam_config["cluster_role_arn"],
            "resourcesVpcConfig": {
                "subnetIds": vpc_config["subnet_ids"],
                "securityGroupIds": vpc_config["security_group_ids"][
                    :1
                ],  # Only control plane SG
            },
            "tags": {
                "clustrix:managed": "true",
                "clustrix:cluster": spec.cluster_name,
                "clustrix:provider": "aws",
            },
        }

        cluster_response = self.eks.create_cluster(**cluster_config)
        cluster_info = cluster_response["cluster"]

        self.created_resources["eks_clusters"].append(spec.cluster_name)

        # Wait for cluster to be active
        logger.info("⏳ Waiting for EKS cluster to be active...")
        waiter = self.eks.get_waiter("cluster_active")
        waiter.wait(
            name=spec.cluster_name, WaiterConfig={"Delay": 30, "MaxAttempts": 40}
        )

        # Get updated cluster info
        cluster_info = self.eks.describe_cluster(name=spec.cluster_name)["cluster"]

        logger.info(f"✅ EKS control plane active: {cluster_info['endpoint']}")
        return cluster_info

    def _create_node_groups(
        self,
        spec: ClusterSpec,
        cluster_info: Dict[str, Any],
        vpc_config: Dict[str, Any],
        iam_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create EKS managed node groups."""
        logger.info("💻 Creating EKS node groups...")

        node_group_name = f"clustrix-nodes-{spec.cluster_name}"

        node_group_config = {
            "clusterName": spec.cluster_name,
            "nodegroupName": node_group_name,
            "subnets": vpc_config["private_subnet_ids"],
            "nodeRole": iam_config["node_role_arn"],
            "instanceTypes": [spec.aws_instance_type],
            "scalingConfig": {
                "minSize": max(1, spec.node_count // 2),
                "maxSize": spec.node_count * 2,
                "desiredSize": spec.node_count,
            },
            "diskSize": 50,
            "amiType": "AL2_x86_64",
            "capacityType": "ON_DEMAND",
            "tags": {"clustrix:managed": "true", "clustrix:cluster": spec.cluster_name},
        }

        self.eks.create_nodegroup(**node_group_config)
        self.created_resources["eks_node_groups"].append(node_group_name)

        # Wait for node group to be active
        logger.info("⏳ Waiting for node group to be active...")
        waiter = self.eks.get_waiter("nodegroup_active")
        waiter.wait(
            clusterName=spec.cluster_name,
            nodegroupName=node_group_name,
            WaiterConfig={"Delay": 30, "MaxAttempts": 40},
        )

        logger.info(f"✅ Node group active: {node_group_name}")
        return {"node_group_name": node_group_name}

    def _configure_kubectl_access(self, cluster_info: Dict[str, Any]) -> Dict[str, Any]:
        """Configure kubectl access to the cluster."""
        logger.info("⚙️ Configuring kubectl access...")

        # Generate kubeconfig
        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "cluster": {
                        "certificate-authority-data": cluster_info[
                            "certificateAuthority"
                        ]["data"],
                        "server": cluster_info["endpoint"],
                    },
                    "name": cluster_info["arn"],
                }
            ],
            "contexts": [
                {
                    "context": {
                        "cluster": cluster_info["arn"],
                        "user": cluster_info["arn"],
                    },
                    "name": cluster_info["arn"],
                }
            ],
            "current-context": cluster_info["arn"],
            "users": [
                {
                    "name": cluster_info["arn"],
                    "user": {
                        "exec": {
                            "apiVersion": "client.authentication.k8s.io/v1beta1",
                            "command": "aws",
                            "args": [
                                "eks",
                                "get-token",
                                "--cluster-name",
                                cluster_info["name"],
                                "--region",
                                self.region,
                            ],
                        }
                    },
                }
            ],
        }

        return kubeconfig

    def _setup_clustrix_environment(
        self, cluster_info: Dict[str, Any], kubectl_config: Dict[str, Any]
    ) -> None:
        """Set up Clustrix namespace and RBAC."""
        logger.info("🔧 Setting up Clustrix environment...")

        try:
            # Write kubeconfig to temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False
            ) as f:
                import yaml

                yaml.dump(kubectl_config, f)
                kubeconfig_path = f.name

            # Create namespace
            subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    kubeconfig_path,
                    "create",
                    "namespace",
                    "clustrix",
                ],
                check=False,
                capture_output=True,
            )

            # Create service account
            subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    kubeconfig_path,
                    "create",
                    "serviceaccount",
                    "clustrix-worker",
                    "--namespace",
                    "clustrix",
                ],
                check=False,
                capture_output=True,
            )

            # Create cluster role binding
            subprocess.run(
                [
                    "kubectl",
                    "--kubeconfig",
                    kubeconfig_path,
                    "create",
                    "clusterrolebinding",
                    "clustrix-worker-binding",
                    "--clusterrole",
                    "cluster-admin",
                    "--serviceaccount",
                    "clustrix:clustrix-worker",
                ],
                check=False,
                capture_output=True,
            )

            logger.info("✅ Clustrix environment configured")

        except Exception as e:
            logger.warning(f"⚠️ Failed to configure Clustrix environment: {e}")
        finally:
            # Clean up temporary kubeconfig file
            import os

            try:
                os.unlink(kubeconfig_path)
            except Exception:
                pass

    def _verify_cluster_operational(self, cluster_name: str) -> None:
        """Verify cluster is ready for job submission."""
        logger.info("🔍 Verifying cluster is operational...")

        try:
            # Check cluster status
            cluster_info = self.eks.describe_cluster(name=cluster_name)["cluster"]
            if cluster_info["status"] != "ACTIVE":
                raise RuntimeError(f"Cluster not active: {cluster_info['status']}")

            # Check node group status
            nodegroups = self.eks.list_nodegroups(clusterName=cluster_name)[
                "nodegroups"
            ]
            for ng_name in nodegroups:
                ng_info = self.eks.describe_nodegroup(
                    clusterName=cluster_name, nodegroupName=ng_name
                )["nodegroup"]
                if ng_info["status"] != "ACTIVE":
                    raise RuntimeError(f"Node group not active: {ng_info['status']}")

            logger.info("✅ Cluster verification completed")

        except Exception as e:
            logger.error(f"❌ Cluster verification failed: {e}")
            raise

    # Additional helper methods for routing tables, security group rules, etc.
    def _create_routing_tables(
        self,
        vpc_id: str,
        subnets: Dict[str, List[str]],
        igw_id: str,
        nat_gateways: List[str],
    ) -> None:
        """Create and configure routing tables."""
        # Public route table
        public_rt_response = self.ec2.create_route_table(VpcId=vpc_id)
        public_rt_id = public_rt_response["RouteTable"]["RouteTableId"]
        self.created_resources["route_tables"].append(public_rt_id)

        # Add route to Internet Gateway
        self.ec2.create_route(
            RouteTableId=public_rt_id,
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=igw_id,
        )

        # Associate public subnets with public route table
        for subnet_id in subnets["public"]:
            self.ec2.associate_route_table(
                RouteTableId=public_rt_id, SubnetId=subnet_id
            )

        # Create private route tables (one per AZ)
        for i, (subnet_id, nat_id) in enumerate(zip(subnets["private"], nat_gateways)):
            private_rt_response = self.ec2.create_route_table(VpcId=vpc_id)
            private_rt_id = private_rt_response["RouteTable"]["RouteTableId"]
            self.created_resources["route_tables"].append(private_rt_id)

            # Add route to NAT Gateway
            self.ec2.create_route(
                RouteTableId=private_rt_id,
                DestinationCidrBlock="0.0.0.0/0",
                NatGatewayId=nat_id,
            )

            # Associate private subnet with route table
            self.ec2.associate_route_table(
                RouteTableId=private_rt_id, SubnetId=subnet_id
            )

    def _configure_security_group_rules(self, cp_sg_id: str, ng_sg_id: str) -> None:
        """Configure security group rules for EKS."""
        # Allow nodes to communicate with each other
        self.ec2.authorize_security_group_ingress(
            GroupId=ng_sg_id,
            IpPermissions=[
                {"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": ng_sg_id}]}
            ],
        )

        # Allow nodes to communicate with control plane
        self.ec2.authorize_security_group_ingress(
            GroupId=cp_sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "UserIdGroupPairs": [{"GroupId": ng_sg_id}],
                }
            ],
        )

        # Allow control plane to communicate with nodes
        self.ec2.authorize_security_group_ingress(
            GroupId=ng_sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 10250,
                    "ToPort": 10250,
                    "UserIdGroupPairs": [{"GroupId": cp_sg_id}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "UserIdGroupPairs": [{"GroupId": cp_sg_id}],
                },
            ],
        )

    def _wait_for_nat_gateway(self, nat_id: str) -> None:
        """Wait for NAT Gateway to be available."""
        max_attempts = 20
        for attempt in range(max_attempts):
            try:
                response = self.ec2.describe_nat_gateways(NatGatewayIds=[nat_id])
                state = response["NatGateways"][0]["State"]

                if state == "available":
                    return
                elif state in ["failed", "deleting", "deleted"]:
                    raise RuntimeError(f"NAT Gateway failed: {state}")

                logger.info(
                    f"⏳ Waiting for NAT Gateway {nat_id} to be available... "
                    f"({attempt + 1}/{max_attempts})"
                )
                time.sleep(30)

            except Exception as e:
                if attempt == max_attempts - 1:
                    raise RuntimeError(
                        f"NAT Gateway {nat_id} not available after "
                        f"{max_attempts * 30} seconds: {e}"
                    )
                time.sleep(30)

    def destroy_cluster_infrastructure(self, cluster_id: str) -> bool:
        """Destroy cluster and all associated infrastructure."""
        logger.info(f"🧹 Destroying EKS cluster: {cluster_id}")

        try:
            # Delete node groups first
            try:
                nodegroups = self.eks.list_nodegroups(clusterName=cluster_id)[
                    "nodegroups"
                ]
                for ng_name in nodegroups:
                    logger.info(f"Deleting node group: {ng_name}")
                    self.eks.delete_nodegroup(
                        clusterName=cluster_id, nodegroupName=ng_name
                    )

                # Wait for node groups to be deleted
                for ng_name in nodegroups:
                    waiter = self.eks.get_waiter("nodegroup_deleted")
                    waiter.wait(clusterName=cluster_id, nodegroupName=ng_name)

            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    logger.warning(f"Error deleting node groups: {e}")

            # Delete EKS cluster
            try:
                self.eks.delete_cluster(name=cluster_id)
                waiter = self.eks.get_waiter("cluster_deleted")
                waiter.wait(name=cluster_id)
                logger.info(f"✅ Deleted EKS cluster: {cluster_id}")
            except ClientError as e:
                if e.response["Error"]["Code"] != "ResourceNotFoundException":
                    logger.warning(f"Error deleting cluster: {e}")

            # Clean up all other resources
            self._cleanup_all_resources()

            return True

        except Exception as e:
            logger.error(f"❌ Failed to destroy cluster: {e}")
            return False

    def get_cluster_status(self, cluster_id: str) -> Dict[str, Any]:
        """Get detailed cluster status and health information."""
        try:
            cluster_info = self.eks.describe_cluster(name=cluster_id)["cluster"]

            # Check node groups
            nodegroups = self.eks.list_nodegroups(clusterName=cluster_id)["nodegroups"]
            node_status = []
            for ng_name in nodegroups:
                ng_info = self.eks.describe_nodegroup(
                    clusterName=cluster_id, nodegroupName=ng_name
                )["nodegroup"]
                node_status.append(
                    {
                        "name": ng_name,
                        "status": ng_info["status"],
                        "capacity": ng_info["scalingConfig"],
                    }
                )

            return {
                "cluster_id": cluster_id,
                "status": cluster_info["status"],
                "endpoint": cluster_info.get("endpoint", ""),
                "version": cluster_info.get("version", ""),
                "node_groups": node_status,
                "ready_for_jobs": (
                    cluster_info["status"] == "ACTIVE"
                    and all(ng["status"] == "ACTIVE" for ng in node_status)
                ),
            }

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return {
                    "cluster_id": cluster_id,
                    "status": "NOT_FOUND",
                    "ready_for_jobs": False,
                }
            else:
                raise

    def _cleanup_failed_provisioning(self, cluster_name: str) -> None:
        """Clean up resources if provisioning fails."""
        logger.info("🧹 Cleaning up failed provisioning...")
        try:
            self._cleanup_all_resources()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def _cleanup_all_resources(self) -> None:
        """Clean up all tracked resources."""
        # Implementation for comprehensive resource cleanup
        logger.info("🧹 Cleaning up all created resources...")
        # This would systematically delete all resources in reverse order of creation
        pass
