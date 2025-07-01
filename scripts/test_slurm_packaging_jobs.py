#!/usr/bin/env python3
"""
SLURM-specific packaging test script that actually submits jobs.

This script creates packaged functions, uploads them to the SLURM cluster,
and submits actual SLURM jobs to test the packaging system end-to-end.
"""

import os
import sys
import json
import tempfile
import time
import subprocess
from pathlib import Path
from datetime import datetime

# Add clustrix to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clustrix.config import ClusterConfig
from clustrix.file_packaging import package_function_for_execution
from clustrix.secure_credentials import ValidationCredentials
import paramiko


class SlurmPackagingValidator:
    """Validates packaging system with real SLURM job submissions."""
    
    def __init__(self):
        self.val_creds = ValidationCredentials()
        self.slurm_config = ClusterConfig(
            cluster_type="slurm",
            cluster_host="ndoli.dartmouth.edu",
            username="f002d6b",
            remote_work_dir="/dartfs-hpc/rc/home/b/f002d6b/clustrix",
            module_loads=["python"],
            environment_variables={"OMP_NUM_THREADS": "1"}
        )
        
        self.ssh_client = None
        self.remote_test_dir = "/dartfs-hpc/rc/home/b/f002d6b/clustrix/packaging_tests"
        self.job_ids = []
        
    def setup_ssh_connection(self):
        """Set up SSH connection to SLURM cluster."""
        try:
            print("🔐 Retrieving SSH credentials from 1Password...")
            
            # Get SSH credentials from 1Password
            ssh_creds = self.val_creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")
            if not ssh_creds:
                print("❌ Could not retrieve SSH credentials from 1Password")
                return False
            
            print("✅ SSH credentials retrieved successfully")
            
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            hostname = ssh_creds.get("hostname", self.slurm_config.cluster_host)
            username = ssh_creds.get("username", self.slurm_config.username)
            password = ssh_creds.get("password")
            private_key = ssh_creds.get("private_key")
            
            print(f"🔌 Connecting to {username}@{hostname}...")
            
            # Try key-based authentication first if private key is available
            if private_key:
                try:
                    from io import StringIO
                    key_file = StringIO(private_key)
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                    
                    self.ssh_client.connect(
                        hostname=hostname,
                        username=username,
                        pkey=pkey,
                        timeout=30
                    )
                    print("✅ SSH connection established with private key")
                    return True
                except Exception as e:
                    print(f"⚠️  Private key auth failed: {e}")
                    
            # Fall back to password authentication
            if password:
                try:
                    self.ssh_client.connect(
                        hostname=hostname,
                        username=username,
                        password=password,
                        timeout=30
                    )
                    print("✅ SSH connection established with password")
                    return True
                except Exception as e:
                    print(f"❌ Password auth failed: {e}")
                    return False
            
            print("❌ No valid authentication method available")
            return False
                
        except Exception as e:
            print(f"❌ SSH connection failed: {e}")
            return False
    
    def run_remote_command(self, command, timeout=30):
        """Run a command on the remote cluster."""
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command, timeout=timeout)
            
            stdout_data = stdout.read().decode().strip()
            stderr_data = stderr.read().decode().strip()
            exit_code = stdout.channel.recv_exit_status()
            
            return exit_code, stdout_data, stderr_data
        except Exception as e:
            return -1, "", str(e)
    
    def upload_file(self, local_path, remote_path):
        """Upload a file to the remote cluster."""
        try:
            sftp = self.ssh_client.open_sftp()
            
            # Create remote directory if needed
            remote_dir = os.path.dirname(remote_path)
            try:
                sftp.mkdir(remote_dir)
            except:
                pass  # Directory might already exist
            
            sftp.put(local_path, remote_path)
            sftp.close()
            return True
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            return False
    
    def create_test_functions(self):
        """Create test functions for SLURM validation."""
        
        def test_basic_execution():
            """Basic test to verify execution environment."""
            import os
            import sys
            import socket
            
            result = {
                "hostname": socket.gethostname(),
                "python_version": sys.version.split()[0],
                "python_executable": sys.executable,
                "current_directory": os.getcwd(),
                "environment_vars": {
                    "SLURM_JOB_ID": os.environ.get("SLURM_JOB_ID", "not_set"),
                    "SLURM_PROCID": os.environ.get("SLURM_PROCID", "not_set"),
                    "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "not_set")
                },
                "test_status": "SUCCESS"
            }
            
            return result
        
        def test_filesystem_integration(config):
            """Test cluster filesystem operations."""
            from clustrix import cluster_ls, cluster_exists, cluster_count_files
            
            try:
                # Test basic filesystem operations
                files = cluster_ls(".", config)
                
                # Count Python files
                py_count = cluster_count_files(".", "*.py", config)
                
                # Check for common files
                has_readme = cluster_exists("README.md", config)
                
                result = {
                    "total_files": len(files),
                    "python_files": py_count,
                    "has_readme": has_readme,
                    "sample_files": files[:3] if files else [],
                    "filesystem_test": "SUCCESS"
                }
                
                return result
                
            except Exception as e:
                return {
                    "filesystem_test": "FAILED",
                    "error": str(e)
                }
        
        def test_local_dependencies():
            """Test local function dependencies."""
            
            def helper_function(x):
                """Local helper function."""
                return x * 2 + 10
            
            def another_helper(text):
                """Another local helper."""
                return text.upper().replace(" ", "_")
            
            # Use the helpers
            number_result = helper_function(25)
            string_result = another_helper("hello slurm world")
            
            return {
                "number_result": number_result,
                "string_result": string_result,
                "local_dependencies_test": "SUCCESS"
            }
        
        # Add helpers to the local dependencies function
        def helper_function(x):
            return x * 2 + 10
        
        def another_helper(text):
            return text.upper().replace(" ", "_")
        
        test_local_dependencies.__globals__['helper_function'] = helper_function
        test_local_dependencies.__globals__['another_helper'] = another_helper
        
        def test_complex_scenario(config):
            """Complex test combining multiple features."""
            import os
            import json
            from clustrix import cluster_find, cluster_stat
            
            def calculate_score(files):
                """Local calculation function."""
                return len(files) * 3.14159
            
            try:
                # Find Python files
                python_files = cluster_find("*.py", ".", config)
                
                # Get stats for first few files
                file_stats = []
                for py_file in python_files[:3]:
                    try:
                        stat_info = cluster_stat(py_file, config)
                        file_stats.append({
                            "file": py_file,
                            "size": stat_info.size,
                            "is_dir": stat_info.is_dir
                        })
                    except Exception as e:
                        file_stats.append({
                            "file": py_file,
                            "error": str(e)
                        })
                
                # Calculate a score using local function
                score = calculate_score(python_files)
                
                result = {
                    "python_files_found": len(python_files),
                    "file_stats": file_stats,
                    "calculated_score": score,
                    "hostname": os.environ.get("HOSTNAME", "unknown"),
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_in_slurm"),
                    "complex_test": "SUCCESS"
                }
                
                return json.dumps(result, indent=2)
                
            except Exception as e:
                return json.dumps({
                    "complex_test": "FAILED",
                    "error": str(e)
                })
        
        # Add helper to complex scenario
        def calculate_score(files):
            return len(files) * 3.14159
        
        test_complex_scenario.__globals__['calculate_score'] = calculate_score
        
        return {
            "basic": test_basic_execution,
            "filesystem": test_filesystem_integration,
            "local_deps": test_local_dependencies,
            "complex": test_complex_scenario
        }
    
    def create_execution_script(self, package_path, test_name, has_config_arg=False):
        """Create a script that will execute the packaged function."""
        
        script_content = f'''#!/usr/bin/env python3
"""
Execution script for packaged function: {test_name}
"""

import sys
import os
import json
import zipfile
import tempfile
import traceback
from pathlib import Path

def main():
    print(f"Starting execution of {test_name}")
    print(f"Python version: {{sys.version}}")
    print(f"Working directory: {{os.getcwd()}}")
    print(f"SLURM Job ID: {{os.environ.get('SLURM_JOB_ID', 'not_set')}}")
    
    package_path = "{package_path}"
    
    if not os.path.exists(package_path):
        print(f"ERROR: Package file not found: {{package_path}}")
        return 1
    
    print(f"Found package: {{package_path}}")
    print(f"Package size: {{os.path.getsize(package_path)}} bytes")
    
    # Extract package to temporary directory
    temp_dir = tempfile.mkdtemp(prefix="clustrix_exec_")
    print(f"Extracting to: {{temp_dir}}")
    
    try:
        with zipfile.ZipFile(package_path, 'r') as zf:
            zf.extractall(temp_dir)
            print(f"Extracted files: {{zf.namelist()}}")
        
        # Change to the package directory
        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        
        # Add to Python path
        sys.path.insert(0, temp_dir)
        
        # Load metadata
        with open("metadata.json", "r") as f:
            metadata = json.load(f)
        
        print(f"Function: {{metadata['function_info']['name']}}")
        
        # Load cluster config if needed
        config = None
        if {str(has_config_arg)}:
            with open("cluster_config.json", "r") as f:
                config_data = json.load(f)
            
            # Create a simple config object
            class Config:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
            
            config = Config(**config_data)
        
        # Set up filesystem functions if needed
        if metadata["dependencies"]["requires_cluster_filesystem"]:
            print("Setting up cluster filesystem functions...")
            try:
                # Install required packages if not available
                try:
                    import paramiko
                except ImportError:
                    print("Installing paramiko...")
                    import subprocess
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
                    import paramiko
                
                # Set up the clustrix module with config
                import clustrix
                clustrix._set_global_config(config)
                print("Clustrix module configured successfully")
            except Exception as e:
                print(f"Warning: Could not set up clustrix module: {{e}}")
        
        # Execute the function
        print("Executing function...")
        function_source = metadata["function_info"]["source"]
        exec(function_source, globals())
        
        function_name = metadata["function_info"]["name"]
        func = globals()[function_name]
        
        # Call function with appropriate arguments
        if config is not None:
            result = func(config)
        else:
            result = func()
        
        print("Function executed successfully!")
        print("Result:")
        print(json.dumps(result, indent=2, default=str))
        
        # Save result
        result_file = f"{{old_cwd}}/result_{test_name}_" + os.environ.get("SLURM_JOB_ID", "unknown") + ".json"
        with open(result_file, "w") as f:
            json.dump({{
                "test_name": "{test_name}",
                "status": "SUCCESS",
                "result": result,
                "metadata": {{
                    "hostname": os.environ.get("HOSTNAME", "unknown"),
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
                    "timestamp": __import__("datetime").datetime.now().isoformat()
                }}
            }}, f, indent=2, default=str)
        
        print(f"Result saved to: {{result_file}}")
        return 0
        
    except Exception as e:
        print(f"ERROR: Function execution failed: {{e}}")
        traceback.print_exc()
        
        # Save error
        error_file = f"{{old_cwd}}/error_{test_name}_" + os.environ.get("SLURM_JOB_ID", "unknown") + ".json"
        with open(error_file, "w") as f:
            json.dump({{
                "test_name": "{test_name}",
                "status": "ERROR", 
                "error": str(e),
                "traceback": traceback.format_exc(),
                "metadata": {{
                    "hostname": os.environ.get("HOSTNAME", "unknown"),
                    "slurm_job_id": os.environ.get("SLURM_JOB_ID", "not_set"),
                    "timestamp": __import__("datetime").datetime.now().isoformat()
                }}
            }}, f, indent=2)
        
        return 1
    
    finally:
        # Cleanup
        os.chdir(old_cwd)
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
'''
        
        return script_content
    
    def create_slurm_script(self, test_name, execution_script_path):
        """Create SLURM job submission script."""
        
        script_content = f'''#!/bin/bash
#SBATCH --job-name=clustrix_pkg_{test_name}
#SBATCH --output={self.remote_test_dir}/logs/clustrix_pkg_{test_name}_%j.out
#SBATCH --error={self.remote_test_dir}/logs/clustrix_pkg_{test_name}_%j.err
#SBATCH --time=00:10:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2GB

echo "Starting SLURM job for clustrix packaging test: {test_name}"
echo "Job ID: $SLURM_JOB_ID"
echo "Hostname: $HOSTNAME"
echo "Date: $(date)"

# Load required modules
module load python

# Set environment variables  
export OMP_NUM_THREADS=1

# Change to test directory
cd {self.remote_test_dir}

# Make sure logs directory exists
mkdir -p logs

echo "Environment setup complete"
echo "Python version: $(python3 --version)"
echo "Current directory: $(pwd)"

# Run the execution script
echo "Running execution script..."
python3 {execution_script_path}

echo "SLURM job completed"
'''
        
        return script_content
    
    def submit_test_job(self, test_name, test_func):
        """Submit a single test job to SLURM."""
        print(f"\n🎯 Submitting test job: {test_name}")
        
        try:
            # Determine if function needs config argument
            needs_config = test_name in ["filesystem", "complex"]
            
            # Create package
            if needs_config:
                package_info = package_function_for_execution(
                    test_func, self.slurm_config, func_args=(self.slurm_config,)
                )
            else:
                package_info = package_function_for_execution(test_func, self.slurm_config)
            
            print(f"  📦 Package created: {package_info.package_id}")
            print(f"  📏 Package size: {package_info.size_bytes:,} bytes")
            
            # Upload package to cluster
            remote_package_path = f"{self.remote_test_dir}/packages/package_{test_name}_{package_info.package_id}.zip"
            
            if self.upload_file(package_info.package_path, remote_package_path):
                print(f"  ⬆️  Package uploaded to: {remote_package_path}")
            else:
                print(f"  ❌ Failed to upload package")
                return None
            
            # The package already contains an execute.py script with dependency resolution
            # Create a simple wrapper script that extracts and runs it
            wrapper_script = f'''#!/usr/bin/env python3
"""
Wrapper script for packaged function execution with dependency resolution.
"""

import os
import sys
import zipfile
import tempfile
import subprocess

def main():
    print(f"Starting execution wrapper for {test_name}")
    print(f"Package location: {remote_package_path}")
    
    # Save the original working directory for result files
    original_cwd = os.getcwd()
    print(f"Original working directory: {{original_cwd}}")
    
    # Extract package to temporary directory
    temp_dir = tempfile.mkdtemp(prefix="clustrix_exec_{test_name}_")
    print(f"Extracting package to: {{temp_dir}}")
    
    try:
        with zipfile.ZipFile("{remote_package_path}", 'r') as zf:
            zf.extractall(temp_dir)
            print(f"Extracted files: {{zf.namelist()}}")
        
        # Change to the package directory
        os.chdir(temp_dir)
        
        # Set environment variable for original working directory
        os.environ["CLUSTRIX_ORIGINAL_CWD"] = original_cwd
        
        # Run the packaged execution script
        print("Running packaged execution script with dependency resolution...")
        result = subprocess.run([sys.executable, "execute.py"], 
                              capture_output=False, text=True)
        
        print(f"Execution completed with exit code: {{result.returncode}}")
        return result.returncode
        
    except Exception as e:
        print(f"Execution failed: {{e}}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Cleanup is handled by the execute.py script
        pass

if __name__ == "__main__":
    sys.exit(main())
'''
            
            # Save wrapper script locally and upload
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(wrapper_script)
                local_exec_script = f.name
            
            remote_exec_script = f"{self.remote_test_dir}/scripts/execute_{test_name}.py"
            
            if self.upload_file(local_exec_script, remote_exec_script):
                print(f"  ⬆️  Wrapper script uploaded")
            else:
                print(f"  ❌ Failed to upload wrapper script")
                return None
            
            # Create SLURM script
            slurm_script = self.create_slurm_script(test_name, remote_exec_script)
            
            # Save SLURM script locally and upload
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                f.write(slurm_script)
                local_slurm_script = f.name
            
            remote_slurm_script = f"{self.remote_test_dir}/scripts/slurm_{test_name}.sh"
            
            if self.upload_file(local_slurm_script, remote_slurm_script):
                print(f"  ⬆️  SLURM script uploaded")
            else:
                print(f"  ❌ Failed to upload SLURM script")
                return None
            
            # Submit job
            submit_command = f"cd {self.remote_test_dir} && sbatch scripts/slurm_{test_name}.sh"
            exit_code, stdout, stderr = self.run_remote_command(submit_command)
            
            if exit_code == 0 and "Submitted batch job" in stdout:
                job_id = stdout.split()[-1]
                self.job_ids.append((job_id, test_name))
                print(f"  ✅ Job submitted with ID: {job_id}")
                return job_id
            else:
                print(f"  ❌ Job submission failed: {stderr}")
                return None
                
        except Exception as e:
            print(f"  ❌ Test submission failed: {e}")
            return None
        finally:
            # Clean up temporary files
            try:
                os.unlink(local_exec_script)
                os.unlink(local_slurm_script)
            except:
                pass
    
    def setup_remote_directories(self):
        """Set up required directories on the remote cluster."""
        directories = [
            f"{self.remote_test_dir}",
            f"{self.remote_test_dir}/packages",
            f"{self.remote_test_dir}/scripts", 
            f"{self.remote_test_dir}/logs",
            f"{self.remote_test_dir}/results"
        ]
        
        for directory in directories:
            self.run_remote_command(f"mkdir -p {directory}")
        
        print(f"✅ Remote directories set up in {self.remote_test_dir}")
    
    def monitor_jobs(self, timeout=300):
        """Monitor submitted jobs until completion."""
        print(f"\n👀 Monitoring {len(self.job_ids)} submitted jobs...")
        
        start_time = time.time()
        completed_jobs = []
        
        while len(completed_jobs) < len(self.job_ids) and (time.time() - start_time) < timeout:
            for job_id, test_name in self.job_ids:
                if (job_id, test_name) in completed_jobs:
                    continue
                
                # Check job status
                exit_code, stdout, stderr = self.run_remote_command(f"squeue -j {job_id} --noheader")
                
                if exit_code != 0 or not stdout.strip():
                    # Job is no longer in queue (completed or failed)
                    completed_jobs.append((job_id, test_name))
                    print(f"  ✅ Job {job_id} ({test_name}) completed")
            
            if len(completed_jobs) < len(self.job_ids):
                time.sleep(10)  # Wait before checking again
        
        if len(completed_jobs) == len(self.job_ids):
            print(f"✅ All jobs completed")
        else:
            remaining = len(self.job_ids) - len(completed_jobs)
            print(f"⚠️  Timeout reached, {remaining} jobs still running")
        
        return completed_jobs
    
    def collect_results(self):
        """Collect and analyze job results."""
        print(f"\n📊 Collecting job results...")
        
        results = {}
        
        # List result files
        exit_code, stdout, stderr = self.run_remote_command(f"find {self.remote_test_dir} -name 'result_*.json' -o -name 'error_*.json'")
        
        if exit_code == 0 and stdout:
            result_files = stdout.strip().split('\n')
            
            for result_file in result_files:
                if not result_file.strip():
                    continue
                
                # Download result file
                try:
                    sftp = self.ssh_client.open_sftp()
                    
                    local_temp_file = tempfile.mktemp(suffix='.json')
                    sftp.get(result_file, local_temp_file)
                    
                    # Read result
                    with open(local_temp_file, 'r') as f:
                        result_data = json.load(f)
                    
                    test_name = result_data.get('test_name', 'unknown')
                    status = result_data.get('status', 'unknown')
                    
                    results[f"{test_name}_{result_data.get('metadata', {}).get('slurm_job_id', 'unknown')}"] = result_data
                    
                    print(f"  📄 {test_name}: {status}")
                    
                    # Clean up
                    os.unlink(local_temp_file)
                    sftp.close()
                    
                except Exception as e:
                    print(f"  ❌ Failed to download {result_file}: {e}")
        
        return results
    
    def run_validation_suite(self):
        """Run the complete SLURM packaging validation suite."""
        print("🚀 Starting SLURM Packaging Validation Suite")
        print("=" * 60)
        
        # Setup SSH connection
        if not self.setup_ssh_connection():
            print("❌ Cannot continue without SSH connection")
            return False
        
        try:
            # Setup remote directories
            self.setup_remote_directories()
            
            # Create test functions
            test_functions = self.create_test_functions()
            
            # Submit all test jobs
            print(f"\n📤 Submitting {len(test_functions)} test jobs...")
            
            for test_name, test_func in test_functions.items():
                job_id = self.submit_test_job(test_name, test_func)
                if job_id:
                    print(f"  ✅ {test_name}: Job {job_id}")
                else:
                    print(f"  ❌ {test_name}: Submission failed")
            
            if not self.job_ids:
                print("❌ No jobs were successfully submitted")
                return False
            
            # Monitor jobs
            completed_jobs = self.monitor_jobs()
            
            # Collect results
            results = self.collect_results()
            
            # Generate report
            self.generate_validation_report(results)
            
            return True
            
        except Exception as e:
            print(f"❌ Validation suite failed: {e}")
            return False
        finally:
            if self.ssh_client:
                self.ssh_client.close()
    
    def generate_validation_report(self, results):
        """Generate validation report."""
        print("\n" + "=" * 60)
        print("📊 SLURM Packaging Validation Report")
        print("=" * 60)
        
        total_tests = len(results)
        successful_tests = len([r for r in results.values() if r.get('status') == 'SUCCESS'])
        failed_tests = total_tests - successful_tests
        
        print(f"Total Tests: {total_tests}")
        print(f"Successful: {successful_tests} ✅")
        print(f"Failed: {failed_tests} ❌")
        
        if total_tests > 0:
            success_rate = (successful_tests / total_tests) * 100
            print(f"Success Rate: {success_rate:.1f}%")
        
        print("\nDetailed Results:")
        for test_id, result in results.items():
            status = result.get('status', 'UNKNOWN')
            test_name = result.get('test_name', 'unknown')
            
            if status == 'SUCCESS':
                print(f"  ✅ {test_name}: SUCCESS")
                if 'result' in result:
                    # Show a summary of the result
                    result_data = result['result']
                    if isinstance(result_data, dict):
                        key_info = [f"{k}: {v}" for k, v in list(result_data.items())[:3]]
                        print(f"     Result: {', '.join(key_info)}")
            else:
                print(f"  ❌ {test_name}: {status}")
                if 'error' in result:
                    print(f"     Error: {result['error']}")
        
        # Save report
        report_file = f"slurm_validation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(report_file, 'w') as f:
                json.dump({
                    "summary": {
                        "total_tests": total_tests,
                        "successful": successful_tests,
                        "failed": failed_tests,
                        "success_rate": (successful_tests / total_tests * 100) if total_tests > 0 else 0
                    },
                    "results": results,
                    "job_ids": self.job_ids,
                    "timestamp": datetime.now().isoformat()
                }, f, indent=2, default=str)
            
            print(f"\n📄 Validation report saved to: {report_file}")
        except Exception as e:
            print(f"⚠️  Could not save report: {e}")


if __name__ == "__main__":
    validator = SlurmPackagingValidator()
    success = validator.run_validation_suite()
    
    if success:
        print("\n✅ SLURM packaging validation completed successfully!")
    else:
        print("\n❌ SLURM packaging validation failed!")
    
    sys.exit(0 if success else 1)