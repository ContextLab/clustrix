#!/usr/bin/env python3
"""
Setup script for local test infrastructure.

This script sets up real infrastructure for testing clustrix without
requiring cloud resources or external clusters.
"""

import subprocess
import time
import sys
import os
from pathlib import Path
import yaml
import json


class TestInfrastructureSetup:
    """Manage test infrastructure setup and teardown."""

    def __init__(self):
        self.infrastructure_dir = Path(__file__).parent
        self.docker_compose_file = self.infrastructure_dir / "docker-compose.yml"
        self.kind_config_file = self.infrastructure_dir / "kind-config.yaml"

    def check_dependencies(self):
        """Check if required tools are installed."""
        dependencies = {
            "docker": ["docker", "--version"],
            "docker-compose": ["docker-compose", "--version"],
            "kind": ["kind", "--version"],
            "kubectl": ["kubectl", "version", "--client"],
        }

        missing = []
        for tool, command in dependencies.items():
            try:
                subprocess.run(command, capture_output=True, check=True)
                print(f"✅ {tool} is installed")
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(f"❌ {tool} is not installed")
                missing.append(tool)

        if missing:
            print("\n⚠️  Missing dependencies:")
            for tool in missing:
                print(f"  - {tool}")
            print("\nPlease install missing dependencies and try again.")
            return False

        return True

    def create_kind_config(self):
        """Create Kind cluster configuration."""
        kind_config = {
            "kind": "Cluster",
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "nodes": [
                {
                    "role": "control-plane",
                    "kubeadmConfigPatches": [
                        """
kind: InitConfiguration
nodeRegistration:
  kubeletExtraArgs:
    node-labels: "clustrix-test=true"
"""
                    ],
                    "extraPortMappings": [
                        {"containerPort": 30000, "hostPort": 30000, "protocol": "TCP"},
                        {"containerPort": 30001, "hostPort": 30001, "protocol": "TCP"},
                    ],
                },
                {
                    "role": "worker",
                    "kubeadmConfigPatches": [
                        """
kind: JoinConfiguration
nodeRegistration:
  kubeletExtraArgs:
    node-labels: "clustrix-test=true,workload=compute"
"""
                    ],
                },
                {
                    "role": "worker",
                    "kubeadmConfigPatches": [
                        """
kind: JoinConfiguration
nodeRegistration:
  kubeletExtraArgs:
    node-labels: "clustrix-test=true,workload=gpu"
"""
                    ],
                },
            ],
            "networking": {
                "podSubnet": "10.244.0.0/16",
                "serviceSubnet": "10.96.0.0/12",
            },
        }

        with open(self.kind_config_file, "w") as f:
            yaml.dump(kind_config, f)

        print(f"✅ Created Kind configuration at {self.kind_config_file}")

    def setup_kubernetes(self):
        """Setup local Kubernetes cluster using Kind."""
        print("\n🚀 Setting up Kubernetes cluster...")

        # Check if cluster already exists
        result = subprocess.run(
            ["kind", "get", "clusters"], capture_output=True, text=True
        )

        if "clustrix-test" in result.stdout:
            print("ℹ️  Cluster 'clustrix-test' already exists")
            return True

        # Create Kind configuration
        self.create_kind_config()

        # Create cluster
        try:
            subprocess.run(
                [
                    "kind",
                    "create",
                    "cluster",
                    "--name",
                    "clustrix-test",
                    "--config",
                    str(self.kind_config_file),
                    "--wait",
                    "5m",
                ],
                check=True,
            )
            print("✅ Kubernetes cluster created successfully")

            # Install basic resources
            self.setup_k8s_resources()

            return True

        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to create Kubernetes cluster: {e}")
            return False

    def setup_k8s_resources(self):
        """Setup basic Kubernetes resources for testing."""
        print("📦 Setting up Kubernetes resources...")

        # Create test namespace
        namespace_yaml = """
apiVersion: v1
kind: Namespace
metadata:
  name: clustrix-test
  labels:
    name: clustrix-test
"""

        # Apply namespace
        subprocess.run(
            ["kubectl", "apply", "-f", "-"], input=namespace_yaml.encode(), check=True
        )

        # Create service account
        sa_yaml = """
apiVersion: v1
kind: ServiceAccount
metadata:
  name: clustrix-test-sa
  namespace: clustrix-test
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: clustrix-test-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: ServiceAccount
  name: clustrix-test-sa
  namespace: clustrix-test
"""

        subprocess.run(
            ["kubectl", "apply", "-f", "-"], input=sa_yaml.encode(), check=True
        )

        print("✅ Kubernetes resources created")

    def setup_docker_services(self):
        """Setup Docker services using docker-compose."""
        print("\n🚀 Starting Docker services...")

        # Generate SSH keys if they don't exist
        self.generate_ssh_keys()

        try:
            # Start services
            subprocess.run(
                ["docker-compose", "-f", str(self.docker_compose_file), "up", "-d"],
                check=True,
                cwd=self.infrastructure_dir,
            )

            print("✅ Docker services started")

            # Wait for services to be healthy
            self.wait_for_services()

            return True

        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to start Docker services: {e}")
            return False

    def generate_ssh_keys(self):
        """Generate SSH keys for testing."""
        keys_dir = self.infrastructure_dir / "test_keys"
        keys_dir.mkdir(exist_ok=True)

        key_file = keys_dir / "id_rsa"
        if not key_file.exists():
            print("🔑 Generating SSH keys for testing...")
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "rsa",
                    "-b",
                    "2048",
                    "-f",
                    str(key_file),
                    "-N",
                    "",
                ],
                check=True,
            )

            # Copy public key to authorized_keys
            pub_key = key_file.with_suffix(".pub")
            authorized_keys = keys_dir / "authorized_keys"
            authorized_keys.write_text(pub_key.read_text())

            print("✅ SSH keys generated")

    def wait_for_services(self):
        """Wait for all services to be healthy."""
        print("⏳ Waiting for services to be ready...")

        services = {
            "MinIO": ("http://localhost:9000/minio/health/live", 30),
            "PostgreSQL": ("pg_isready -h localhost -p 5432 -U clustrix", 30),
            "Redis": ("redis-cli -h localhost -p 6379 ping", 20),
            "SSH": ("ssh -p 2222 testuser@localhost echo test", 30),
        }

        for service, (check_cmd, timeout) in services.items():
            start = time.time()
            while time.time() - start < timeout:
                try:
                    if "http" in check_cmd:
                        import requests

                        response = requests.get(check_cmd, timeout=2)
                        if response.status_code == 200:
                            print(f"  ✅ {service} is ready")
                            break
                    else:
                        result = subprocess.run(
                            (
                                check_cmd.split()
                                if not "|" in check_cmd
                                else ["bash", "-c", check_cmd]
                            ),
                            capture_output=True,
                            timeout=2,
                        )
                        if result.returncode == 0:
                            print(f"  ✅ {service} is ready")
                            break
                except:
                    pass

                time.sleep(2)
            else:
                print(f"  ⚠️  {service} health check timed out")

    def create_test_config(self):
        """Create test configuration file."""
        config = {
            "infrastructure": {
                "kubernetes": {
                    "context": "kind-clustrix-test",
                    "namespace": "clustrix-test",
                },
                "ssh": {
                    "host": "localhost",
                    "port": 2222,
                    "username": "testuser",
                    "password": "testpass",
                    "key_file": str(self.infrastructure_dir / "test_keys" / "id_rsa"),
                },
                "minio": {
                    "endpoint": "localhost:9000",
                    "access_key": "minioadmin",
                    "secret_key": "minioadmin",
                    "buckets": ["test-bucket", "results-bucket"],
                },
                "postgres": {
                    "host": "localhost",
                    "port": 5432,
                    "database": "clustrix_test",
                    "username": "clustrix",
                    "password": "testpass",
                },
                "redis": {"host": "localhost", "port": 6379},
            }
        }

        config_file = self.infrastructure_dir / "test_infrastructure.json"
        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)

        print(f"\n✅ Test configuration saved to {config_file}")

        # Also create environment file
        env_file = self.infrastructure_dir / "test.env"
        with open(env_file, "w") as f:
            f.write("# Test Infrastructure Environment Variables\n")
            f.write("export KUBECONFIG=$HOME/.kube/config\n")
            f.write("export TEST_SSH_HOST=localhost\n")
            f.write("export TEST_SSH_PORT=2222\n")
            f.write("export TEST_SSH_USER=testuser\n")
            f.write("export TEST_SSH_PASS=testpass\n")
            f.write(f"export TEST_SSH_KEY={self.infrastructure_dir}/test_keys/id_rsa\n")
            f.write("export TEST_MINIO_ENDPOINT=localhost:9000\n")
            f.write("export TEST_MINIO_ACCESS_KEY=minioadmin\n")
            f.write("export TEST_MINIO_SECRET_KEY=minioadmin\n")
            f.write("export TEST_POSTGRES_HOST=localhost\n")
            f.write("export TEST_POSTGRES_PORT=5432\n")
            f.write("export TEST_POSTGRES_DB=clustrix_test\n")
            f.write("export TEST_POSTGRES_USER=clustrix\n")
            f.write("export TEST_POSTGRES_PASS=testpass\n")
            f.write("export TEST_REDIS_HOST=localhost\n")
            f.write("export TEST_REDIS_PORT=6379\n")

        print(f"✅ Environment file saved to {env_file}")
        print(f"\nTo use the test environment, run:")
        print(f"  source {env_file}")

    def setup(self):
        """Setup complete test infrastructure."""
        print("🔧 Setting up Clustrix test infrastructure...")

        # Check dependencies
        if not self.check_dependencies():
            return False

        # Setup Kubernetes
        if not self.setup_kubernetes():
            print("⚠️  Kubernetes setup failed, continuing with other services...")

        # Setup Docker services
        if not self.setup_docker_services():
            return False

        # Create test configuration
        self.create_test_config()

        print("\n✨ Test infrastructure setup complete!")
        print("\nServices available:")
        print("  • Kubernetes: kubectl --context kind-clustrix-test")
        print("  • SSH Server: ssh -p 2222 testuser@localhost")
        print("  • MinIO (S3): http://localhost:9001 (admin/admin)")
        print("  • PostgreSQL: psql -h localhost -U clustrix clustrix_test")
        print("  • Redis: redis-cli -h localhost")

        return True

    def teardown(self):
        """Teardown test infrastructure."""
        print("🧹 Tearing down test infrastructure...")

        # Stop Docker services
        try:
            subprocess.run(
                ["docker-compose", "-f", str(self.docker_compose_file), "down", "-v"],
                check=True,
                cwd=self.infrastructure_dir,
            )
            print("✅ Docker services stopped")
        except:
            print("⚠️  Failed to stop Docker services")

        # Delete Kind cluster
        try:
            subprocess.run(
                ["kind", "delete", "cluster", "--name", "clustrix-test"], check=True
            )
            print("✅ Kubernetes cluster deleted")
        except:
            print("⚠️  Failed to delete Kubernetes cluster")

        print("✨ Teardown complete")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Setup local test infrastructure for Clustrix"
    )
    parser.add_argument(
        "action", choices=["setup", "teardown", "status"], help="Action to perform"
    )

    args = parser.parse_args()

    setup = TestInfrastructureSetup()

    if args.action == "setup":
        sys.exit(0 if setup.setup() else 1)
    elif args.action == "teardown":
        setup.teardown()
        sys.exit(0)
    elif args.action == "status":
        # Check status of services
        print("🔍 Checking infrastructure status...")
        # Implementation would check each service
        sys.exit(0)


if __name__ == "__main__":
    main()
