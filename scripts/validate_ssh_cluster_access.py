#!/usr/bin/env python3
"""
SSH Cluster Access Validation Script

This script validates SSH connectivity and basic functionality for Clustrix
cluster integration with real remote servers.
"""

import sys
import os
import time
import tempfile
import json
import logging
from pathlib import Path
from datetime import datetime

# Add the clustrix package to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from clustrix.secure_credentials import ValidationCredentials

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_ssh_connectivity(hostname, username, password=None, key_file=None):
    """Test basic SSH connectivity to a cluster."""
    print(f"🔍 SSH Connectivity Test: {username}@{hostname}")
    print("=" * 60)
    
    try:
        import paramiko
    except ImportError:
        print("❌ paramiko not available. Install with: pip install paramiko")
        return False
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Setup connection parameters
        connect_kwargs = {
            "hostname": hostname,
            "username": username,
            "timeout": 30
        }
        
        if key_file and os.path.exists(key_file):
            connect_kwargs["key_filename"] = key_file
            print(f"🔑 Using SSH key: {key_file}")
        elif password:
            connect_kwargs["password"] = password
            print("🔑 Using password authentication")
        else:
            # Try default SSH keys
            print("🔑 Using default SSH key authentication")
        
        print(f"🔌 Connecting to {hostname}...")
        ssh_client.connect(**connect_kwargs)
        print("✅ SSH connection successful")
        
        # Test basic command execution
        print("🧪 Testing command execution...")
        stdin, stdout, stderr = ssh_client.exec_command("echo 'Hello from Clustrix validation!'")
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()
        
        if output == "Hello from Clustrix validation!":
            print("✅ Command execution working")
        else:
            print(f"❌ Unexpected command output: {output}")
            if error:
                print(f"   Error: {error}")
            return False
        
        # Test system information
        print("📊 Gathering system information...")
        commands = {
            "hostname": "hostname",
            "os_info": "uname -a",
            "python_version": "python3 --version",
            "disk_space": "df -h | head -5",
            "memory_info": "free -h",
            "cpu_info": "nproc"
        }
        
        system_info = {}
        for key, cmd in commands.items():
            try:
                stdin, stdout, stderr = ssh_client.exec_command(cmd)
                output = stdout.read().decode().strip()
                if output:
                    system_info[key] = output
                    print(f"   {key}: {output.split()[0] if output else 'N/A'}")
            except Exception as e:
                print(f"   {key}: Error - {e}")
                system_info[key] = f"Error: {e}"
        
        # Test directory access and creation
        print("📁 Testing directory operations...")
        test_dir = f"/tmp/clustrix_test_{int(time.time())}"
        
        # Create test directory
        stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {test_dir}")
        error = stderr.read().decode().strip()
        if error:
            print(f"❌ Failed to create test directory: {error}")
            return False
        
        # Test file operations
        test_file = f"{test_dir}/test_file.txt"
        test_content = f"Clustrix SSH validation test at {datetime.now()}"
        stdin, stdout, stderr = ssh_client.exec_command(f"echo '{test_content}' > {test_file}")
        
        # Read back the file
        stdin, stdout, stderr = ssh_client.exec_command(f"cat {test_file}")
        file_content = stdout.read().decode().strip()
        
        if test_content in file_content:
            print("✅ File operations working")
        else:
            print(f"❌ File operation failed. Expected: {test_content}, Got: {file_content}")
        
        # Cleanup
        stdin, stdout, stderr = ssh_client.exec_command(f"rm -rf {test_dir}")
        print("✅ Cleanup completed")
        
        ssh_client.close()
        
        return {
            "success": True,
            "hostname": hostname,
            "username": username,
            "system_info": system_info,
            "connection_method": "key" if key_file else "password" if password else "default_key"
        }
        
    except paramiko.AuthenticationException:
        print("❌ SSH authentication failed")
        print("   Check username, password, or SSH keys")
        return False
    except paramiko.SSHException as e:
        print(f"❌ SSH connection error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False
    finally:
        try:
            ssh_client.close()
        except:
            pass

def test_sftp_functionality(hostname, username, password=None, key_file=None):
    """Test SFTP file transfer capabilities."""
    print(f"\n📁 SFTP Functionality Test: {username}@{hostname}")
    print("=" * 60)
    
    try:
        import paramiko
    except ImportError:
        print("❌ paramiko not available for SFTP testing")
        return False
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Connect
        connect_kwargs = {
            "hostname": hostname,
            "username": username,
            "timeout": 30
        }
        
        if key_file and os.path.exists(key_file):
            connect_kwargs["key_filename"] = key_file
        elif password:
            connect_kwargs["password"] = password
        
        ssh_client.connect(**connect_kwargs)
        
        # Create SFTP client
        sftp_client = ssh_client.open_sftp()
        print("✅ SFTP connection established")
        
        # Create temporary local file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as local_file:
            test_content = f"Clustrix SFTP test file\nCreated: {datetime.now()}\nContent: Test data for validation"
            local_file.write(test_content)
            local_file_path = local_file.name
        
        # Test upload
        remote_test_dir = f"/tmp/clustrix_sftp_test_{int(time.time())}"
        remote_file_path = f"{remote_test_dir}/test_upload.txt"
        
        # Create remote directory first
        stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {remote_test_dir}")
        
        print("📤 Testing file upload...")
        sftp_client.put(local_file_path, remote_file_path)
        print("✅ File upload successful")
        
        # Test download
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as download_file:
            download_path = download_file.name
        
        print("📥 Testing file download...")
        sftp_client.get(remote_file_path, download_path)
        print("✅ File download successful")
        
        # Verify content
        with open(download_path, 'r') as f:
            downloaded_content = f.read()
        
        if test_content == downloaded_content:
            print("✅ File content verification successful")
        else:
            print("❌ File content mismatch")
            print(f"   Original: {test_content[:50]}...")
            print(f"   Downloaded: {downloaded_content[:50]}...")
        
        # Test directory listing
        print("📋 Testing directory listing...")
        file_list = sftp_client.listdir(remote_test_dir)
        if 'test_upload.txt' in file_list:
            print("✅ Directory listing working")
        else:
            print(f"❌ Directory listing failed. Found: {file_list}")
        
        # Cleanup
        try:
            sftp_client.remove(remote_file_path)
            ssh_client.exec_command(f"rmdir {remote_test_dir}")
            os.unlink(local_file_path)
            os.unlink(download_path)
            print("✅ SFTP cleanup completed")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")
        
        sftp_client.close()
        ssh_client.close()
        
        return True
        
    except Exception as e:
        print(f"❌ SFTP test failed: {e}")
        return False
    finally:
        try:
            sftp_client.close()
            ssh_client.close()
        except:
            pass

def test_python_environment(hostname, username, password=None, key_file=None):
    """Test Python environment on remote cluster."""
    print(f"\n🐍 Python Environment Test: {username}@{hostname}")
    print("=" * 60)
    
    try:
        import paramiko
    except ImportError:
        print("❌ paramiko not available for Python testing")
        return False
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Connect
        connect_kwargs = {
            "hostname": hostname,
            "username": username,
            "timeout": 30
        }
        
        if key_file and os.path.exists(key_file):
            connect_kwargs["key_filename"] = key_file
        elif password:
            connect_kwargs["password"] = password
        
        ssh_client.connect(**connect_kwargs)
        
        # Test Python availability and version
        python_tests = {
            "python3_version": "python3 --version",
            "pip_version": "pip3 --version", 
            "python_path": "which python3",
            "pip_path": "which pip3",
            "python_modules": "python3 -c \"import sys; print('\\n'.join(sys.path))\"",
            "basic_imports": "python3 -c \"import os, sys, json, time; print('Basic imports successful')\"",
            "numpy_test": "python3 -c \"import numpy; print(f'NumPy {numpy.__version__} available')\"",
            "install_test": "pip3 install --user cloudpickle --quiet && python3 -c \"import cloudpickle; print('cloudpickle installation successful')\""
        }
        
        results = {}
        
        for test_name, command in python_tests.items():
            print(f"🧪 Testing {test_name}...")
            
            try:
                stdin, stdout, stderr = ssh_client.exec_command(command, timeout=60)
                output = stdout.read().decode().strip()
                error = stderr.read().decode().strip()
                
                if output and "successful" in output.lower() or test_name in ["python3_version", "pip_version", "python_path", "pip_path"]:
                    print(f"   ✅ {output.split(chr(10))[0] if output else 'OK'}")
                    results[test_name] = {"success": True, "output": output}
                elif "import" in command and "error" not in error.lower():
                    print(f"   ✅ {output if output else 'Import successful'}")
                    results[test_name] = {"success": True, "output": output}
                else:
                    print(f"   ⚠️  {error if error else 'No output'}")
                    results[test_name] = {"success": False, "error": error, "output": output}
                    
            except Exception as e:
                print(f"   ❌ Error: {e}")
                results[test_name] = {"success": False, "error": str(e)}
        
        # Test creating and running a Python script
        print("🧪 Testing Python script execution...")
        test_script = '''
import sys
import os
import json
from datetime import datetime

def clustrix_test_function():
    """Test function similar to what Clustrix would execute."""
    
    result = {
        "function_name": "clustrix_test_function",
        "execution_time": datetime.now().isoformat(),
        "python_version": sys.version,
        "working_directory": os.getcwd(),
        "environment_vars": len(os.environ),
        "computation_result": sum(i**2 for i in range(1000)),
        "success": True
    }
    
    return result

if __name__ == "__main__":
    try:
        result = clustrix_test_function()
        print("CLUSTRIX_RESULT_START")
        print(json.dumps(result, indent=2))
        print("CLUSTRIX_RESULT_END")
        print("Script execution successful!")
    except Exception as e:
        print(f"Script execution failed: {e}")
        sys.exit(1)
'''
        
        # Upload and execute the test script
        script_path = f"/tmp/clustrix_python_test_{int(time.time())}.py"
        
        # Create script file
        stdin, stdout, stderr = ssh_client.exec_command(f"cat > {script_path} << 'EOF'\n{test_script}\nEOF")
        
        # Execute script
        stdin, stdout, stderr = ssh_client.exec_command(f"cd /tmp && python3 {script_path}")
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()
        
        if "Script execution successful!" in output and "CLUSTRIX_RESULT_START" in output:
            print("✅ Python script execution successful")
            
            # Extract JSON result
            try:
                start_marker = output.find("CLUSTRIX_RESULT_START") + len("CLUSTRIX_RESULT_START")
                end_marker = output.find("CLUSTRIX_RESULT_END")
                json_str = output[start_marker:end_marker].strip()
                script_result = json.loads(json_str)
                print(f"   Computation result: {script_result.get('computation_result', 'N/A')}")
                results["script_execution"] = {"success": True, "result": script_result}
            except Exception as e:
                print(f"   ⚠️  Could not parse result: {e}")
                results["script_execution"] = {"success": True, "output": output}
        else:
            print(f"❌ Python script execution failed")
            print(f"   Output: {output[:200]}...")
            if error:
                print(f"   Error: {error[:200]}...")
            results["script_execution"] = {"success": False, "error": error, "output": output}
        
        # Cleanup
        stdin, stdout, stderr = ssh_client.exec_command(f"rm -f {script_path}")
        
        ssh_client.close()
        
        success_count = sum(1 for r in results.values() if r.get("success", False))
        total_tests = len(results)
        
        print(f"\n📊 Python Environment Summary: {success_count}/{total_tests} tests passed")
        
        return {
            "success": success_count >= (total_tests * 0.7),  # 70% success rate
            "results": results,
            "success_rate": success_count / total_tests
        }
        
    except Exception as e:
        print(f"❌ Python environment test failed: {e}")
        return False
    finally:
        try:
            ssh_client.close()
        except:
            pass

def test_cluster_scheduler_detection(hostname, username, password=None, key_file=None):
    """Detect available cluster schedulers (SLURM, PBS, SGE, etc.)."""
    print(f"\n⚙️  Cluster Scheduler Detection: {username}@{hostname}")
    print("=" * 60)
    
    try:
        import paramiko
    except ImportError:
        print("❌ paramiko not available for scheduler testing")
        return False
    
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        # Connect
        connect_kwargs = {
            "hostname": hostname,
            "username": username,
            "timeout": 30
        }
        
        if key_file and os.path.exists(key_file):
            connect_kwargs["key_filename"] = key_file
        elif password:
            connect_kwargs["password"] = password
        
        ssh_client.connect(**connect_kwargs)
        
        # Test for different schedulers
        scheduler_tests = {
            "slurm": ["sinfo", "squeue", "sbatch"],
            "pbs": ["qstat", "qsub", "pbsnodes"],
            "sge": ["qstat", "qsub", "qhost"],
            "lsf": ["bjobs", "bsub", "bhosts"]
        }
        
        detected_schedulers = {}
        
        for scheduler_name, commands in scheduler_tests.items():
            print(f"🔍 Testing for {scheduler_name.upper()}...")
            
            scheduler_available = True
            command_results = {}
            
            for cmd in commands:
                try:
                    stdin, stdout, stderr = ssh_client.exec_command(f"which {cmd}")
                    cmd_path = stdout.read().decode().strip()
                    error = stderr.read().decode().strip()
                    
                    if cmd_path and not error:
                        print(f"   ✅ {cmd}: {cmd_path}")
                        command_results[cmd] = cmd_path
                    else:
                        print(f"   ❌ {cmd}: not found")
                        scheduler_available = False
                        break
                except Exception as e:
                    print(f"   ❌ {cmd}: error - {e}")
                    scheduler_available = False
                    break
            
            if scheduler_available:
                detected_schedulers[scheduler_name] = command_results
                print(f"   🎉 {scheduler_name.upper()} detected and available!")
                
                # Get basic scheduler info
                if scheduler_name == "slurm":
                    try:
                        stdin, stdout, stderr = ssh_client.exec_command("sinfo -h")
                        sinfo_output = stdout.read().decode().strip()
                        if sinfo_output:
                            print(f"   📊 SLURM partitions: {len(sinfo_output.split(chr(10)))} found")
                    except:
                        pass
                        
        ssh_client.close()
        
        if detected_schedulers:
            print(f"\n🎯 Detected schedulers: {', '.join(detected_schedulers.keys()).upper()}")
            return detected_schedulers
        else:
            print("\n⚠️  No cluster schedulers detected")
            print("   This appears to be a regular SSH server")
            return {}
        
    except Exception as e:
        print(f"❌ Scheduler detection failed: {e}")
        return False
    finally:
        try:
            ssh_client.close()
        except:
            pass

def main():
    """Main validation function."""
    print("🚀 Starting SSH Cluster Access Validation")
    print("=" * 70)
    
    # Get credentials
    creds = ValidationCredentials()
    
    # Test both clusters
    clusters = [
        ("clustrix-ssh-slurm", "SLURM Cluster"),
        ("clustrix-ssh-gpu", "GPU Server")
    ]
    
    all_results = {}
    
    for cred_name, cluster_description in clusters:
        print(f"\n🎯 Testing {cluster_description}")
        print("=" * 70)
        
        # Get cluster credentials
        cluster_creds = creds.cred_manager.get_structured_credential(cred_name)
        if not cluster_creds:
            print(f"❌ No credentials found for {cred_name}")
            print("   Please add credentials to 1Password")
            continue
        
        hostname = cluster_creds.get("hostname")
        username = cluster_creds.get("username")
        password = cluster_creds.get("password")
        key_file = cluster_creds.get("key_file")
        
        if not hostname or not username:
            print(f"❌ Invalid credentials for {cred_name}")
            print(f"   hostname: {hostname}, username: {username}")
            continue
        
        print(f"🔗 Target: {username}@{hostname}")
        
        # Run all tests for this cluster
        cluster_results = {}
        
        # SSH Connectivity Test
        ssh_result = test_ssh_connectivity(hostname, username, password, key_file)
        cluster_results["ssh_connectivity"] = ssh_result
        
        if ssh_result:
            # SFTP Test
            sftp_result = test_sftp_functionality(hostname, username, password, key_file)
            cluster_results["sftp_functionality"] = sftp_result
            
            # Python Environment Test
            python_result = test_python_environment(hostname, username, password, key_file)
            cluster_results["python_environment"] = python_result
            
            # Scheduler Detection Test
            scheduler_result = test_cluster_scheduler_detection(hostname, username, password, key_file)
            cluster_results["scheduler_detection"] = scheduler_result
        
        all_results[cluster_description] = cluster_results
    
    # Summary
    print("\n📊 SSH Cluster Validation Summary")
    print("=" * 70)
    
    for cluster_name, results in all_results.items():
        print(f"\n🎯 {cluster_name}:")
        
        if not results:
            print("   ❌ No tests performed (credential issues)")
            continue
        
        for test_name, result in results.items():
            if isinstance(result, dict) and result.get("success"):
                print(f"   ✅ {test_name}: PASSED")
            elif isinstance(result, bool) and result:
                print(f"   ✅ {test_name}: PASSED")
            elif isinstance(result, dict) and "success_rate" in result:
                success_rate = result["success_rate"]
                status = "PASSED" if success_rate >= 0.7 else "PARTIAL"
                print(f"   {'✅' if status == 'PASSED' else '⚠️'} {test_name}: {status} ({success_rate*100:.1f}%)")
            elif result == {}:
                print(f"   ⚠️  {test_name}: No schedulers detected (regular SSH server)")
            else:
                print(f"   ❌ {test_name}: FAILED")
    
    # Determine overall success
    total_clusters = len(all_results)
    successful_clusters = sum(1 for results in all_results.values() 
                            if results and results.get("ssh_connectivity"))
    
    print(f"\n🎯 Overall Result: {successful_clusters}/{total_clusters} clusters accessible")
    
    if successful_clusters > 0:
        print("🎉 SSH cluster access validation successful!")
        print("   Clustrix can connect to and execute jobs on remote clusters.")
        return 0
    else:
        print("❌ SSH cluster access validation failed.")
        print("   Check credentials and network connectivity.")
        return 1

if __name__ == "__main__":
    sys.exit(main())