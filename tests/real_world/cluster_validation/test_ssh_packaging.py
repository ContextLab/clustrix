#!/usr/bin/env python3
"""
SSH cluster packaging test script.

This script tests the packaging system on the SSH cluster (tensor01.dartmouth.edu)
by creating packages, uploading them, and executing them directly via SSH.
"""

import os
import sys
import json
import tempfile
import time
from datetime import datetime

# Add clustrix to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clustrix.config import ClusterConfig
from clustrix.file_packaging import package_function_for_execution
import paramiko


class SSHPackagingValidator:
    """Validates packaging system on SSH cluster."""

    def __init__(self):
        self.ssh_config = ClusterConfig(
            cluster_type="ssh",
            cluster_host="tensor01.dartmouth.edu",
            username="f002d6b",
            remote_work_dir="/home/f002d6b/clustrix_test",
        )

        self.ssh_client = None
        self.test_results = []

    def setup_ssh_connection(self):
        """Set up SSH connection."""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.ssh_client.connect(
                hostname=self.ssh_config.cluster_host,
                username=self.ssh_config.username,
                timeout=10,
            )
            print("✅ SSH connection established")
            return True
        except Exception as e:
            print(f"❌ SSH connection failed: {e}")
            return False

    def run_remote_command(self, command, timeout=60):
        """Run command on remote host."""
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(
                command, timeout=timeout
            )

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
            try:
                sftp.mkdir(remote_dir)
            except:
                pass

            sftp.put(local_path, remote_path)
            sftp.close()
            return True
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            return False

    def create_test_functions(self):
        """Create test functions for SSH validation."""

        def test_basic_ssh():
            """Basic test for SSH execution."""
            import os
            import sys
            import socket

            return {
                "hostname": socket.gethostname(),
                "python_version": sys.version.split()[0],
                "current_directory": os.getcwd(),
                "user": os.environ.get("USER", "unknown"),
                "test": "basic_ssh_success",
            }

        def test_filesystem_ssh(config):
            """Test filesystem operations on SSH."""
            from clustrix import cluster_ls, cluster_exists, cluster_count_files

            try:
                files = cluster_ls(".", config)
                py_files = cluster_count_files(".", "*.py", config)
                has_bashrc = cluster_exists(".bashrc", config)

                return {
                    "total_files": len(files),
                    "python_files": py_files,
                    "has_bashrc": has_bashrc,
                    "sample_files": files[:5],
                    "test": "filesystem_ssh_success",
                }
            except Exception as e:
                return {"test": "filesystem_ssh_failed", "error": str(e)}

        def test_local_functions_ssh():
            """Test local function dependencies on SSH."""

            def local_helper(x):
                return x * 3 + 7

            def string_helper(s):
                return s.lower().replace(" ", "-")

            result1 = local_helper(10)
            result2 = string_helper("Hello SSH World")

            return {
                "numeric_result": result1,
                "string_result": result2,
                "test": "local_functions_ssh_success",
            }

        # Add helpers to the function
        def local_helper(x):
            return x * 3 + 7

        def string_helper(s):
            return s.lower().replace(" ", "-")

        test_local_functions_ssh.__globals__["local_helper"] = local_helper
        test_local_functions_ssh.__globals__["string_helper"] = string_helper

        def test_error_handling_ssh():
            """Test error handling on SSH."""
            try:
                # Test some operations
                import tempfile
                import os

                # Create temp file
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
                    f.write("test data for ssh")
                    temp_file = f.name

                # Read it back
                with open(temp_file, "r") as f:
                    content = f.read()

                # Clean up
                os.unlink(temp_file)

                return {
                    "file_content": content.strip(),
                    "test": "error_handling_ssh_success",
                }

            except Exception as e:
                return {"test": "error_handling_ssh_failed", "error": str(e)}

        return {
            "basic": test_basic_ssh,
            "filesystem": test_filesystem_ssh,
            "local_functions": test_local_functions_ssh,
            "error_handling": test_error_handling_ssh,
        }

    def create_execution_script(self, package_path, test_name, has_config_arg=False):
        """Create execution script for SSH cluster."""

        script_content = f'''#!/usr/bin/env python3
"""
SSH execution script for {test_name}
"""

import sys
import os
import json
import zipfile
import tempfile
import traceback

def main():
    print("Starting SSH execution test: {test_name}")
    print(f"Python: {{sys.version}}")
    print(f"Working dir: {{os.getcwd()}}")
    print(f"Hostname: {{os.environ.get('HOSTNAME', 'unknown')}}")
    
    package_path = "{package_path}"
    
    if not os.path.exists(package_path):
        print(f"ERROR: Package not found: {{package_path}}")
        return 1
    
    print(f"Package found: {{package_path}} ({{os.path.getsize(package_path)}} bytes)")
    
    # Extract package
    temp_dir = tempfile.mkdtemp(prefix="clustrix_ssh_")
    print(f"Extracting to: {{temp_dir}}")
    
    try:
        with zipfile.ZipFile(package_path, 'r') as zf:
            zf.extractall(temp_dir)
            print(f"Extracted: {{zf.namelist()}}")
        
        # Change to package directory
        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        sys.path.insert(0, temp_dir)
        
        # Load metadata
        with open("metadata.json", "r") as f:
            metadata = json.load(f)
        
        function_name = metadata["function_info"]["name"]
        print(f"Executing function: {{function_name}}")
        
        # Set up config if needed
        config = None
        if {str(has_config_arg).lower()}:
            with open("cluster_config.json", "r") as f:
                config_data = json.load(f)
            
            class Config:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
            
            config = Config(**config_data)
        
        # Set up filesystem if needed
        if metadata["dependencies"]["requires_cluster_filesystem"]:
            print("Setting up filesystem functions...")
            try:
                exec(open("clustrix_filesystem.py").read(), globals())
                from filesystem_utils import setup_filesystem_functions
                fs_functions = setup_filesystem_functions(config)
                globals().update(fs_functions)
                print("Filesystem functions ready")
            except Exception as e:
                print(f"Warning: Filesystem setup failed: {{e}}")
        
        # Execute function
        function_source = metadata["function_info"]["source"]
        exec(function_source, globals())
        
        func = globals()[function_name]
        
        # Call function
        if config is not None:
            result = func(config)
        else:
            result = func()
        
        print("Function executed successfully!")
        print("Result:")
        print(json.dumps(result, indent=2, default=str))
        
        # Save result
        result_file = f"{{old_cwd}}/ssh_result_{test_name}.json"
        with open(result_file, "w") as f:
            json.dump({{
                "test_name": "{test_name}",
                "status": "SUCCESS",
                "result": result,
                "metadata": {{
                    "hostname": os.environ.get("HOSTNAME", "unknown"),
                    "timestamp": __import__("datetime").datetime.now().isoformat()
                }}
            }}, f, indent=2, default=str)
        
        print(f"Result saved to: {{result_file}}")
        return 0
        
    except Exception as e:
        print(f"ERROR: {{e}}")
        traceback.print_exc()
        
        error_file = f"{{old_cwd}}/ssh_error_{test_name}.json"
        with open(error_file, "w") as f:
            json.dump({{
                "test_name": "{test_name}",
                "status": "ERROR",
                "error": str(e),
                "traceback": traceback.format_exc()
            }}, f, indent=2)
        
        return 1
    
    finally:
        os.chdir(old_cwd)
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
'''

        return script_content

    def test_ssh_execution(self, test_name, test_func):
        """Test execution of a packaged function on SSH cluster."""
        print(f"\n🔌 Testing SSH execution: {test_name}")

        try:
            # Determine if function needs config
            needs_config = test_name == "filesystem"

            # Create package
            if needs_config:
                package_info = package_function_for_execution(
                    test_func, self.ssh_config, func_args=(self.ssh_config,)
                )
            else:
                package_info = package_function_for_execution(
                    test_func, self.ssh_config
                )

            print(f"  📦 Package created: {package_info.package_id}")

            # Upload package
            remote_package = f"{self.ssh_config.remote_work_dir}/packages/pkg_{test_name}_{package_info.package_id}.zip"

            if not self.upload_file(package_info.package_path, remote_package):
                self.test_results.append(
                    {
                        "test": test_name,
                        "status": "FAILED",
                        "error": "Package upload failed",
                    }
                )
                return False

            print(f"  ⬆️  Package uploaded")

            # Create and upload execution script
            exec_script = self.create_execution_script(
                remote_package, test_name, needs_config
            )

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(exec_script)
                local_script = f.name

            remote_script = (
                f"{self.ssh_config.remote_work_dir}/scripts/exec_{test_name}.py"
            )

            if not self.upload_file(local_script, remote_script):
                os.unlink(local_script)
                self.test_results.append(
                    {
                        "test": test_name,
                        "status": "FAILED",
                        "error": "Script upload failed",
                    }
                )
                return False

            print(f"  ⬆️  Execution script uploaded")

            # Execute on remote
            exec_command = f"cd {self.ssh_config.remote_work_dir} && python3 scripts/exec_{test_name}.py"
            exit_code, stdout, stderr = self.run_remote_command(exec_command)

            print(f"  🏃 Execution completed (exit code: {exit_code})")

            if stdout:
                print(
                    f"  📄 Output: {stdout[:200]}{'...' if len(stdout) > 200 else ''}"
                )

            if stderr:
                print(f"  ⚠️  Stderr: {stderr}")

            # Download result file
            result_file = (
                f"{self.ssh_config.remote_work_dir}/ssh_result_{test_name}.json"
            )

            try:
                sftp = self.ssh_client.open_sftp()
                local_result = tempfile.mktemp(suffix=".json")
                sftp.get(result_file, local_result)

                with open(local_result, "r") as f:
                    result_data = json.load(f)

                self.test_results.append(result_data)
                print(f"  ✅ Test completed: {result_data.get('status', 'unknown')}")

                os.unlink(local_result)
                sftp.close()

            except Exception as e:
                print(f"  ⚠️  Could not download result: {e}")
                self.test_results.append(
                    {
                        "test": test_name,
                        "status": "UNKNOWN",
                        "error": f"Result download failed: {e}",
                    }
                )

            # Cleanup
            os.unlink(local_script)
            return True

        except Exception as e:
            print(f"  ❌ Test failed: {e}")
            self.test_results.append(
                {"test": test_name, "status": "FAILED", "error": str(e)}
            )
            return False

    def setup_remote_directories(self):
        """Set up remote directories."""
        dirs = [
            f"{self.ssh_config.remote_work_dir}",
            f"{self.ssh_config.remote_work_dir}/packages",
            f"{self.ssh_config.remote_work_dir}/scripts",
        ]

        for directory in dirs:
            self.run_remote_command(f"mkdir -p {directory}")

        print(f"✅ Remote directories set up")

    def run_ssh_validation(self):
        """Run complete SSH validation suite."""
        print("🚀 Starting SSH Packaging Validation")
        print("=" * 50)

        if not self.setup_ssh_connection():
            return False

        try:
            self.setup_remote_directories()

            test_functions = self.create_test_functions()

            print(f"\n📤 Running {len(test_functions)} SSH tests...")

            for test_name, test_func in test_functions.items():
                success = self.test_ssh_execution(test_name, test_func)
                if not success:
                    print(f"  ❌ {test_name} failed")
                else:
                    print(f"  ✅ {test_name} completed")

            self.generate_ssh_report()
            return True

        except Exception as e:
            print(f"❌ SSH validation failed: {e}")
            return False
        finally:
            if self.ssh_client:
                self.ssh_client.close()

    def generate_ssh_report(self):
        """Generate SSH validation report."""
        print("\n" + "=" * 50)
        print("📊 SSH Packaging Validation Report")
        print("=" * 50)

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
            test_name = result.get("test_name", result.get("test", "unknown"))
            status = result.get("status", "unknown")

            if status == "SUCCESS":
                print(f"  ✅ {test_name}: SUCCESS")
                if "result" in result and isinstance(result["result"], dict):
                    test_type = result["result"].get("test", "unknown")
                    print(f"     Result type: {test_type}")
            else:
                print(f"  ❌ {test_name}: {status}")
                if "error" in result:
                    print(f"     Error: {result['error']}")

        # Save report
        report_file = (
            f"ssh_validation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        try:
            with open(report_file, "w") as f:
                json.dump(
                    {
                        "summary": {
                            "total": total,
                            "successful": successful,
                            "failed": failed,
                            "success_rate": (
                                (successful / total * 100) if total > 0 else 0
                            ),
                        },
                        "results": self.test_results,
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                    default=str,
                )

            print(f"\n📄 Report saved to: {report_file}")
        except Exception as e:
            print(f"⚠️  Could not save report: {e}")


if __name__ == "__main__":
    validator = SSHPackagingValidator()
    success = validator.run_ssh_validation()

    if success:
        print("\n✅ SSH packaging validation completed!")
    else:
        print("\n❌ SSH packaging validation failed!")

    sys.exit(0 if success else 1)
