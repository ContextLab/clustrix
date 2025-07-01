#!/usr/bin/env python3
"""
SSH cluster packaging test script using 1Password credentials.

This script tests the packaging system on SSH clusters using credentials
stored securely in 1Password.
"""

import os
import sys
import json
import tempfile
import time
import paramiko
from datetime import datetime
from io import StringIO

# Add clustrix to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clustrix.config import ClusterConfig
from clustrix.file_packaging import package_function_for_execution
from clustrix.secure_credentials import ValidationCredentials


class SSHPackagingValidator:
    """Validates packaging system on SSH cluster using 1Password credentials."""
    
    def __init__(self):
        self.val_creds = ValidationCredentials()
        self.ssh_client = None
        self.test_results = []
        self.cluster_config = None
    
    def get_ssh_credentials(self):
        """Get SSH credentials from 1Password."""
        print("🔐 Retrieving SSH credentials from 1Password...")
        
        # Try to get SSH SLURM credentials
        ssh_creds = self.val_creds.cred_manager.get_structured_credential("clustrix-ssh-slurm")
        
        if ssh_creds:
            print("✅ SSH credentials retrieved successfully")
            return ssh_creds
        else:
            print("❌ Failed to retrieve SSH credentials")
            return None
    
    def setup_ssh_connection(self, ssh_creds):
        """Set up SSH connection using 1Password credentials."""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            hostname = ssh_creds.get("hostname", "ndoli.dartmouth.edu")
            username = ssh_creds.get("username", "f002d6b")
            password = ssh_creds.get("password")
            private_key = ssh_creds.get("private_key")
            
            print(f"🔌 Connecting to {username}@{hostname}...")
            
            # Try key-based auth if private key is provided
            if private_key:
                try:
                    # Parse private key from string
                    key_file = StringIO(private_key)
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                    
                    self.ssh_client.connect(
                        hostname=hostname,
                        username=username,
                        pkey=pkey,
                        timeout=30
                    )
                    print("✅ SSH connection established with private key")
                except Exception as e:
                    print(f"⚠️  Private key auth failed: {e}")
                    # Fall back to password
                    if password:
                        self.ssh_client.connect(
                            hostname=hostname,
                            username=username,
                            password=password,
                            timeout=30
                        )
                        print("✅ SSH connection established with password")
            elif password:
                # Use password auth
                self.ssh_client.connect(
                    hostname=hostname,
                    username=username,
                    password=password,
                    timeout=30
                )
                print("✅ SSH connection established with password")
            else:
                print("❌ No authentication method available")
                return False
            
            # Set up cluster config for the connected host
            # Use the correct home directory for SLURM cluster
            if hostname == "ndoli.dartmouth.edu":
                remote_work_dir = f"/dartfs-hpc/rc/home/b/{username}/clustrix_test"
            else:
                remote_work_dir = f"/home/{username}/clustrix_test"
                
            self.cluster_config = ClusterConfig(
                cluster_type="ssh",
                cluster_host=hostname,
                username=username,
                remote_work_dir=remote_work_dir
            )
            
            return True
            
        except Exception as e:
            print(f"❌ SSH connection failed: {e}")
            return False
    
    def run_remote_command(self, command, timeout=60):
        """Run command on remote host."""
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command, timeout=timeout)
            
            stdout_data = stdout.read().decode().strip()
            stderr_data = stderr.read().decode().strip()
            exit_code = stdout.channel.recv_exit_status()
            
            return exit_code, stdout_data, stderr_data
        except Exception as e:
            return -1, "", str(e)
    
    def upload_file(self, local_path, remote_path):
        """Upload file to remote host."""
        try:
            sftp = self.ssh_client.open_sftp()
            
            # Create remote directory if needed
            remote_dir = os.path.dirname(remote_path)
            print(f"  🗂️  Creating remote directory: {remote_dir}")
            try:
                sftp.mkdir(remote_dir)
                print(f"  ✅ Directory created: {remote_dir}")
            except Exception as mkdir_e:
                print(f"  ⚠️  Directory creation failed (might exist): {mkdir_e}")
                # Check if directory exists
                try:
                    sftp.stat(remote_dir)
                    print(f"  ✅ Directory exists: {remote_dir}")
                except:
                    print(f"  ❌ Directory does not exist and cannot be created: {remote_dir}")
                    sftp.close()
                    return False
            
            print(f"  📤 Uploading {local_path} to {remote_path}")
            sftp.put(local_path, remote_path)
            print(f"  ✅ Upload successful")
            sftp.close()
            return True
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            return False
    
    def create_test_functions(self):
        """Create test functions for SSH validation."""
        
        def test_basic_execution():
            """Basic test to verify execution environment."""
            import os
            import sys
            import socket
            
            return {
                "hostname": socket.gethostname(),
                "python_version": sys.version.split()[0],
                "current_directory": os.getcwd(),
                "user": os.environ.get("USER", "unknown"),
                "test": "basic_execution_success"
            }
        
        def test_filesystem_operations(config):
            """Test cluster filesystem operations."""
            from clustrix import cluster_ls, cluster_exists, cluster_count_files
            
            try:
                files = cluster_ls(".", config)
                py_files = cluster_count_files(".", "*.py", config)
                
                return {
                    "total_files": len(files),
                    "python_files": py_files,
                    "sample_files": files[:5],
                    "test": "filesystem_operations_success"
                }
            except Exception as e:
                return {
                    "test": "filesystem_operations_failed",
                    "error": str(e)
                }
        
        def test_complex_scenario(config):
            """Complex test with multiple dependencies."""
            import os
            import json
            from clustrix import cluster_find, cluster_stat
            
            def process_data(data):
                """Local helper function."""
                return sum(data) * 1.5
            
            try:
                # Find Python files
                python_files = cluster_find("*.py", ".", config)
                
                # Process some data
                numbers = [1, 2, 3, 4, 5]
                result = process_data(numbers)
                
                return {
                    "python_files_found": len(python_files),
                    "calculation_result": result,
                    "hostname": os.environ.get("HOSTNAME", "unknown"),
                    "test": "complex_scenario_success"
                }
            except Exception as e:
                return {
                    "test": "complex_scenario_failed",
                    "error": str(e)
                }
        
        # Add helper to complex scenario
        def process_data(data):
            return sum(data) * 1.5
        
        test_complex_scenario.__globals__['process_data'] = process_data
        
        return {
            "basic": test_basic_execution,
            "filesystem": test_filesystem_operations,
            "complex": test_complex_scenario
        }
    
    def test_package_execution(self, test_name, test_func):
        """Test execution of a packaged function."""
        print(f"\n📦 Testing package execution: {test_name}")
        
        try:
            # Determine if function needs config
            needs_config = test_name in ["filesystem", "complex"]
            
            # Create package
            if needs_config:
                package_info = package_function_for_execution(
                    test_func, self.cluster_config, func_args=(self.cluster_config,)
                )
            else:
                package_info = package_function_for_execution(test_func, self.cluster_config)
            
            print(f"  ✅ Package created: {package_info.package_id}")
            print(f"  📏 Size: {package_info.size_bytes:,} bytes")
            
            # Create simple execution script
            remote_test_dir = f"{self.cluster_config.remote_work_dir}/test_{test_name}"
            
            # Create test directory
            self.run_remote_command(f"mkdir -p {remote_test_dir}")
            
            # Upload package
            remote_package = f"{remote_test_dir}/package.zip"
            if not self.upload_file(package_info.package_path, remote_package):
                raise Exception("Failed to upload package")
            
            print(f"  ⬆️  Package uploaded")
            
            # Create execution script as a file
            if needs_config:
                config_setup = """
    # Load cluster config
    with open("cluster_config.json", "r") as f:
        config_data = json.load(f)

    class Config:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    config = Config(**config_data)
    
    # Set up clustrix module if filesystem functions are needed
    try:
        import clustrix
        clustrix._set_global_config(config)
        print("Clustrix module configured")
    except:
        pass"""
                func_call = 'result = func(config)'
            else:
                config_setup = ""
                func_call = 'result = func()'
            
            exec_script_content = f"""#!/usr/bin/env python3
import zipfile
import json
import sys
import os

try:
    # Extract package
    with zipfile.ZipFile('package.zip', 'r') as zf:
        zf.extractall('.')

    # Load metadata
    with open('metadata.json', 'r') as f:
        metadata = json.load(f)

    # Execute function
    exec(metadata['function_info']['source'])
    func = globals()[metadata['function_info']['name']]
{config_setup}
    
    # Call function
    {func_call}
    
    print('RESULT_JSON:' + json.dumps(result, default=str))
except Exception as e:
    print('ERROR_JSON:' + json.dumps({{'error': str(e)}}, default=str))
"""
            
            # Write execution script to temporary file and upload it
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(exec_script_content)
                local_exec_script = f.name
            
            remote_exec_script = f"{remote_test_dir}/exec_{test_name}.py"
            
            if not self.upload_file(local_exec_script, remote_exec_script):
                os.unlink(local_exec_script)
                raise Exception("Failed to upload execution script")
            
            os.unlink(local_exec_script)
            
            # Run the execution script
            exec_command = f"cd {remote_test_dir} && python3 exec_{test_name}.py"
            print(f"  🏃 Running: {exec_command}")
            exit_code, stdout, stderr = self.run_remote_command(exec_command, timeout=30)
            
            print(f"  📊 Exit code: {exit_code}")
            print(f"  📄 Stdout: {stdout[:500]}")
            print(f"  ⚠️  Stderr: {stderr[:500]}")
            
            # Parse result
            if "RESULT_JSON:" in stdout:
                result_json = stdout.split("RESULT_JSON:")[1].strip()
                result = json.loads(result_json)
                
                self.test_results.append({
                    "test": test_name,
                    "status": "SUCCESS",
                    "result": result
                })
                print(f"  ✅ Test passed")
                
            elif "ERROR_JSON:" in stdout:
                error_json = stdout.split("ERROR_JSON:")[1].strip()
                error = json.loads(error_json)
                
                self.test_results.append({
                    "test": test_name,
                    "status": "FAILED",
                    "error": error.get("error", "Unknown error")
                })
                print(f"  ❌ Test failed: {error.get('error')}")
                
            else:
                self.test_results.append({
                    "test": test_name,
                    "status": "ERROR",
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code
                })
                print(f"  ❌ Unexpected output")
                if stderr:
                    print(f"     Stderr: {stderr}")
            
            # Cleanup
            self.run_remote_command(f"rm -rf {remote_test_dir}")
            
        except Exception as e:
            print(f"  ❌ Test failed: {e}")
            self.test_results.append({
                "test": test_name,
                "status": "ERROR",
                "error": str(e)
            })
    
    def run_validation_suite(self):
        """Run the complete validation suite."""
        print("🚀 Starting SSH Packaging Validation with 1Password")
        print("=" * 60)
        
        # Get credentials
        ssh_creds = self.get_ssh_credentials()
        if not ssh_creds:
            print("❌ Cannot continue without credentials")
            return False
        
        # Connect
        if not self.setup_ssh_connection(ssh_creds):
            print("❌ Cannot continue without SSH connection")
            return False
        
        try:
            # Check connection
            exit_code, stdout, stderr = self.run_remote_command("echo 'Connection test' && hostname && pwd")
            print(f"\n📍 Connected to: {stdout}")
            
            # Setup remote directories
            self.setup_remote_directories()
            
            # Run tests
            test_functions = self.create_test_functions()
            
            for test_name, test_func in test_functions.items():
                self.test_package_execution(test_name, test_func)
            
            # Generate report
            self.generate_report()
            
            return True
            
        except Exception as e:
            print(f"❌ Validation failed: {e}")
            return False
        finally:
            if self.ssh_client:
                self.ssh_client.close()
    
    def setup_remote_directories(self):
        """Set up remote directories."""
        dirs = [
            self.cluster_config.remote_work_dir,
            f"{self.cluster_config.remote_work_dir}/test_basic",
            f"{self.cluster_config.remote_work_dir}/test_filesystem", 
            f"{self.cluster_config.remote_work_dir}/test_complex"
        ]
        
        for directory in dirs:
            self.run_remote_command(f"mkdir -p {directory}")
        
        print(f"✅ Remote directories set up")
    
    def generate_report(self):
        """Generate validation report."""
        print("\n" + "=" * 60)
        print("📊 SSH Packaging Validation Report")
        print("=" * 60)
        
        total = len(self.test_results)
        successful = len([r for r in self.test_results if r.get("status") == "SUCCESS"])
        failed = total - successful
        
        print(f"Total Tests: {total}")
        print(f"Successful: {successful} ✅")
        print(f"Failed: {failed} ❌")
        
        if total > 0:
            print(f"Success Rate: {(successful/total*100):.1f}%")
        
        print("\nDetailed Results:")
        for result in self.test_results:
            test_name = result.get("test", "unknown")
            status = result.get("status", "unknown")
            
            if status == "SUCCESS":
                print(f"  ✅ {test_name}: SUCCESS")
                if "result" in result:
                    result_data = result["result"]
                    if isinstance(result_data, dict):
                        test_type = result_data.get("test", "unknown")
                        print(f"     Result: {test_type}")
            else:
                print(f"  ❌ {test_name}: {status}")
                if "error" in result:
                    print(f"     Error: {result['error']}")
        
        # Save report
        report_file = f"ssh_1password_validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(report_file, 'w') as f:
                json.dump({
                    "summary": {
                        "total": total,
                        "successful": successful,
                        "failed": failed,
                        "success_rate": (successful/total*100) if total > 0 else 0
                    },
                    "results": self.test_results,
                    "timestamp": datetime.now().isoformat()
                }, f, indent=2, default=str)
            
            print(f"\n📄 Report saved to: {report_file}")
        except Exception as e:
            print(f"⚠️  Could not save report: {e}")


if __name__ == "__main__":
    validator = SSHPackagingValidator()
    success = validator.run_validation_suite()
    
    if success:
        print("\n✅ SSH packaging validation completed!")
    else:
        print("\n❌ SSH packaging validation failed!")
    
    sys.exit(0 if success else 1)