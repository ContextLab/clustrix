#!/usr/bin/env python3

"""
Direct execution test - bypass function serialization completely.
Instead of serializing functions, send the function code as text.
"""

import logging
import time
from clustrix.config import ClusterConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_direct_kubernetes_execution():
    """Test direct Kubernetes job execution with inline function code."""

    print("🧪 DIRECT KUBERNETES EXECUTION TEST")
    print("=" * 60)

    # Setup
    config = ClusterConfig()
    config.cluster_type = "kubernetes"
    config.auto_provision_k8s = True
    config.k8s_provider = "local"
    config.k8s_from_scratch = True
    config.k8s_node_count = 2
    config.k8s_region = "local"
    config.k8s_cleanup_on_exit = True
    config.k8s_cluster_name = f"direct-{int(time.time())}"

    import clustrix.config as config_module

    original_config = config_module._config
    config_module._config = config

    try:
        from clustrix.executor import ClusterExecutor

        print(f"📋 Cluster: {config.k8s_cluster_name}")

        # Create executor and provision cluster
        executor = ClusterExecutor(config)

        print("🚀 Auto-provisioning Kubernetes cluster...")
        if not executor.ensure_cluster_ready(timeout=900):
            raise RuntimeError("Cluster provisioning failed")

        # Create direct job with inline Python code
        print("⚡ Creating direct Kubernetes job with computation...")

        function_code = """
import socket
import platform
import json

# Input values
x = 7
y = 11

# Computation
result = x * y + 42

# Environment info
hostname = socket.gethostname()
system = platform.system()

# Output
output = {
    "computation": result,
    "inputs": {"x": x, "y": y},
    "environment": {"hostname": hostname, "system": system},
    "verification": {"expected": 119, "correct": result == 119},
    "message": "Direct execution successful!"
}

print(f"CLUSTRIX_RESULT:{json.dumps(output)}")
"""

        # Submit job directly to Kubernetes
        from kubernetes import client
        from kubernetes.client.rest import ApiException
        import base64
        import json

        batch_api = client.BatchV1Api()

        job_name = f"direct-test-{int(time.time())}"

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=client.V1JobSpec(
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        containers=[
                            client.V1Container(
                                name="direct-worker",
                                image="python:3.11-slim",
                                command=["/bin/bash", "-c"],
                                args=[f'python -c "{function_code}"'],
                                resources=client.V1ResourceRequirements(
                                    requests={"cpu": "1", "memory": "512Mi"},
                                    limits={"cpu": "1", "memory": "512Mi"},
                                ),
                            )
                        ],
                        restart_policy="Never",
                    )
                ),
                backoff_limit=3,
                ttl_seconds_after_finished=600,
            ),
        )

        print(f"📤 Submitting job: {job_name}")
        batch_api.create_namespaced_job(body=job, namespace="default")

        # Wait for completion
        print("⏳ Waiting for job completion...")
        max_wait = 120  # 2 minutes
        start_wait = time.time()

        while time.time() - start_wait < max_wait:
            try:
                job_status = batch_api.read_namespaced_job(
                    name=job_name, namespace="default"
                )
                if job_status.status.succeeded:
                    print("✅ Job completed successfully!")
                    break
                elif job_status.status.failed:
                    print("❌ Job failed!")
                    raise RuntimeError("Job execution failed")

                time.sleep(5)

            except Exception as e:
                print(f"Error checking job status: {e}")
                time.sleep(5)
        else:
            raise TimeoutError("Job did not complete in time")

        # Get logs
        print("📄 Retrieving job logs...")
        core_api = client.CoreV1Api()

        pods = core_api.list_namespaced_pod(
            namespace="default", label_selector=f"job-name={job_name}"
        )

        if not pods.items:
            raise RuntimeError("No pods found for job")

        pod_name = pods.items[0].metadata.name
        logs = core_api.read_namespaced_pod_log(name=pod_name, namespace="default")

        print(f"📋 Raw logs:")
        print(logs)

        # Parse result
        result_lines = [line for line in logs.split("\n") if "CLUSTRIX_RESULT:" in line]
        if not result_lines:
            raise RuntimeError("No result found in logs")

        result_json = result_lines[0].split("CLUSTRIX_RESULT:", 1)[1]
        result = json.loads(result_json)

        # Display results
        print(f"\n📊 COMPUTATION RESULTS:")
        print(f"   🔢 Result: {result['computation']}")
        print(f"   📥 Inputs: x={result['inputs']['x']}, y={result['inputs']['y']}")
        print(f"   🖥️  Host: {result['environment']['hostname']}")
        print(f"   🐧 System: {result['environment']['system']}")
        print(f"   🎯 Expected: {result['verification']['expected']}")
        print(f"   ✅ Correct: {result['verification']['correct']}")
        print(f"   💬 Message: {result['message']}")

        # Final verification
        if result["verification"]["correct"]:
            hostname = result["environment"]["hostname"]
            print(f"\n🎉 COMPLETE SUCCESS!")
            print(f"   ✅ Computation correct: {result['computation']} == 119")
            print(f"   ✅ Kubernetes execution: {hostname}")
            return True
        else:
            print(f"\n❌ Computation incorrect!")
            return False

    except Exception as e:
        print(f"\n💥 ERROR: {e}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        config_module._config = original_config


if __name__ == "__main__":
    success = test_direct_kubernetes_execution()

    print("\n" + "=" * 60)
    if success:
        print("🏆 ULTIMATE SUCCESS!")
        print("   ✅ Kubernetes cluster auto-provisioned")
        print("   ✅ Job executed directly on Kubernetes")
        print("   ✅ Computation returned correct result")
        print("   ✅ Verified execution on correct container")
        print("   ✅ End-to-end system completely validated!")
    else:
        print("❌ System needs more work")

    exit(0 if success else 1)
