#!/usr/bin/env python3
"""
SLURM test for shared filesystem fix.

This script creates a packaged function that tests the shared filesystem
detection and operations on the SLURM cluster.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clustrix.config import ClusterConfig
from clustrix.file_packaging import package_function_for_execution
from clustrix.secure_credentials import ValidationCredentials
import tempfile
import zipfile
import json
import paramiko

def test_shared_filesystem_on_slurm(config):
    """
    Function to be executed on SLURM cluster to test shared filesystem operations.
    This function will be packaged and executed remotely.
    """
    import socket
    import os
    import json
    from clustrix.filesystem import ClusterFilesystem, cluster_ls, cluster_exists
    
    # Get execution environment info
    hostname = socket.gethostname()
    
    # Test 1: Direct filesystem access (should work on shared storage)
    try:
        files = os.listdir(".")
        direct_access_success = True
        direct_file_count = len(files)
    except Exception as e:
        direct_access_success = False
        direct_file_count = 0
        direct_error = str(e)
    
    # Test 2: ClusterFilesystem with cluster detection
    try:
        fs = ClusterFilesystem(config)
        
        # Check if cluster detection worked
        detected_as_local = (fs.config.cluster_type == "local")
        
        # Test filesystem operations through ClusterFilesystem
        fs_files = fs.ls(".")
        fs_ls_success = True
        fs_file_count = len(fs_files)
        
        # Test exists operation
        fs_exists_result = fs.exists(".")
        fs_exists_success = True
        
    except Exception as e:
        detected_as_local = False
        fs_ls_success = False
        fs_file_count = 0
        fs_exists_result = False
        fs_exists_success = False
        fs_error = str(e)
    
    # Test 3: Convenience functions
    try:
        convenience_files = cluster_ls(".", config)
        convenience_success = True
        convenience_file_count = len(convenience_files)
        
        convenience_exists = cluster_exists(".", config)
        
    except Exception as e:
        convenience_success = False
        convenience_file_count = 0
        convenience_exists = False
        convenience_error = str(e)
    
    # Test 4: Try to access shared directories (if available)
    shared_dirs_test = {}
    test_paths = [
        "/dartfs-hpc/rc/home/b/f002d6b/",
        "/dartfs-hpc/rc/lab/",
        "/tmp"
    ]
    
    for test_path in test_paths:
        try:
            if os.path.exists(test_path):
                items = os.listdir(test_path)
                shared_dirs_test[test_path] = {
                    "accessible": True,
                    "item_count": len(items)
                }
            else:
                shared_dirs_test[test_path] = {
                    "accessible": False,
                    "reason": "Path does not exist"
                }
        except Exception as e:
            shared_dirs_test[test_path] = {
                "accessible": False,
                "reason": str(e)
            }
    
    return {
        "hostname": hostname,
        "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
        "direct_filesystem_access": {
            "success": direct_access_success,
            "file_count": direct_file_count,
            "error": direct_error if not direct_access_success else None
        },
        "cluster_filesystem": {
            "detected_as_local": detected_as_local,
            "ls_success": fs_ls_success,
            "file_count": fs_file_count,
            "exists_success": fs_exists_success,
            "exists_result": fs_exists_result,
            "error": fs_error if not fs_ls_success else None
        },
        "convenience_functions": {
            "success": convenience_success,
            "file_count": convenience_file_count,
            "exists_result": convenience_exists,
            "error": convenience_error if not convenience_success else None
        },
        "shared_directories": shared_dirs_test,
        "filesystem_test_status": "SUCCESS" if (
            direct_access_success and 
            detected_as_local and 
            fs_ls_success and 
            convenience_success
        ) else "FAILED"
    }

def main():
    print("🚀 Testing Shared Filesystem Fix on SLURM Cluster")
    print("=" * 60)
    
    # Get SSH credentials
    val_creds = ValidationCredentials()
    ssh_creds = val_creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")
    
    if not ssh_creds:
        print("❌ Could not get SSH credentials")
        return
    
    # Create cluster config
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host=ssh_creds.get("hostname", "ndoli.dartmouth.edu"),
        username=ssh_creds.get("username", "f002d6b"),
        password=ssh_creds.get("password"),
        remote_work_dir="/dartfs-hpc/rc/home/b/f002d6b/clustrix/shared_fs_tests"
    )
    
    print(f"📋 Cluster: {config.cluster_host}")
    print(f"👤 User: {config.username}")
    
    # Package the test function
    print("\n📦 Creating test package...")
    package_info = package_function_for_execution(
        test_shared_filesystem_on_slurm,
        config,
        func_args=(config,)
    )
    
    print(f"📏 Package size: {package_info.size_bytes:,} bytes")
    print(f"🆔 Package ID: {package_info.package_id}")
    
    # Connect to cluster and submit job
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh_client.connect(
            hostname=config.cluster_host,
            username=config.username,
            password=config.password,
            timeout=30
        )
        print(f"✅ Connected to {config.cluster_host}")
        
        # Set up remote directories
        sftp = ssh_client.open_sftp()
        
        # Create test directory
        try:
            sftp.mkdir(config.remote_work_dir)
        except:
            pass  # Directory might already exist
        
        try:
            sftp.mkdir(f"{config.remote_work_dir}/packages")
        except:
            pass
        
        # Upload package
        remote_package_path = f"{config.remote_work_dir}/packages/shared_fs_test_{package_info.package_id}.zip"
        print(f"⬆️  Uploading package to {remote_package_path}")
        sftp.put(package_info.package_path, remote_package_path)
        
        # Create execution script
        execution_script = f"""#!/bin/bash
#SBATCH --job-name=shared_fs_test
#SBATCH --output=shared_fs_test_%j.out
#SBATCH --error=shared_fs_test_%j.err
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1

echo "🚀 Starting shared filesystem test on $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Set original working directory for result files
export CLUSTRIX_ORIGINAL_CWD="{config.remote_work_dir}"

# Create temporary execution directory
EXEC_DIR="/tmp/clustrix_shared_fs_test_${{SLURM_JOB_ID}}"
mkdir -p "$EXEC_DIR"
cd "$EXEC_DIR"

# Extract package
echo "📦 Extracting package..."
unzip -q "{remote_package_path}"

# Execute the test
echo "🧪 Running shared filesystem test..."
python3 execute.py

echo "✅ Shared filesystem test completed"
"""
        
        # Upload execution script
        script_path = f"{config.remote_work_dir}/shared_fs_test.sh"
        sftp.putfo(sftp.file(script_path, 'w'), execution_script.encode())
        
        # Make script executable
        stdin, stdout, stderr = ssh_client.exec_command(f"chmod +x {script_path}")
        
        # Submit SLURM job
        print("🎯 Submitting SLURM job...")
        stdin, stdout, stderr = ssh_client.exec_command(f"cd {config.remote_work_dir} && sbatch shared_fs_test.sh")
        
        submit_output = stdout.read().decode().strip()
        submit_error = stderr.read().decode().strip()
        
        if submit_error:
            print(f"❌ Submit error: {submit_error}")
            return
        
        # Extract job ID
        if "Submitted batch job" in submit_output:
            job_id = submit_output.split()[-1]
            print(f"✅ Job submitted with ID: {job_id}")
            
            # Wait for job to complete
            print("⏳ Waiting for job to complete...")
            import time
            
            for _ in range(30):  # Wait up to 5 minutes
                stdin, stdout, stderr = ssh_client.exec_command(f"squeue -j {job_id}")
                queue_output = stdout.read().decode()
                
                if job_id not in queue_output:
                    print("✅ Job completed")
                    break
                    
                print(".", end="", flush=True)
                time.sleep(10)
            else:
                print("\n⏰ Timeout waiting for job completion")
            
            # Check for results
            print("\n📊 Checking for results...")
            stdin, stdout, stderr = ssh_client.exec_command(f"find {config.remote_work_dir} -name 'result_*_{job_id}.json'")
            result_files = stdout.read().decode().strip().split('\n')
            
            for result_file in result_files:
                if result_file.strip():
                    print(f"\n📄 Result file: {result_file}")
                    stdin, stdout, stderr = ssh_client.exec_command(f"cat {result_file}")
                    result_content = stdout.read().decode()
                    
                    try:
                        result_data = json.loads(result_content)
                        print(json.dumps(result_data, indent=2))
                        
                        # Save result locally
                        local_result_file = f"shared_filesystem_test_result_{job_id}.json"
                        with open(local_result_file, 'w') as f:
                            json.dump(result_data, f, indent=2)
                        print(f"💾 Result saved locally to: {local_result_file}")
                        
                    except json.JSONDecodeError:
                        print("Raw output:")
                        print(result_content)
            
            # Check job output files
            print(f"\n📋 Job output files:")
            stdin, stdout, stderr = ssh_client.exec_command(f"ls -la {config.remote_work_dir}/shared_fs_test_{job_id}.*")
            output_files = stdout.read().decode()
            print(output_files)
            
        else:
            print(f"❌ Job submission failed: {submit_output}")
        
        sftp.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        ssh_client.close()

if __name__ == "__main__":
    main()