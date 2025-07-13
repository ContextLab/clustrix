#!/usr/bin/env python3
"""
Check remote execution logs to see what's failing in VENV2.
"""

import sys
import os
sys.path.insert(0, '/Users/jmanning/clustrix')

from tests.real_world import TestCredentials
import paramiko

def check_remote_logs():
    """Check the remote execution logs to see what's failing."""
    print("🔍 Checking remote execution logs...")
    
    creds = TestCredentials()
    tensor01_creds = creds.get_tensor01_credentials()
    
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(
            hostname=tensor01_creds['host'],
            username=tensor01_creds['username'],
            password=tensor01_creds['password'],
            port=int(tensor01_creds.get('port', 22)),
            timeout=30
        )
        
        # Find the most recent job directories
        stdin, stdout, stderr = ssh_client.exec_command('ls -t /tmp/clustrix_tensor01/ | head -5')
        recent_jobs = stdout.read().decode().strip().split('\n')
        
        print(f"Recent job directories:")
        for job in recent_jobs:
            if job.strip():
                print(f"  {job}")
        
        # Check the most recent job that likely failed
        if recent_jobs and recent_jobs[0].strip():
            latest_job = recent_jobs[0].strip()
            job_path = f"/tmp/clustrix_tensor01/{latest_job}"
            
            print(f"\n🔍 Analyzing job: {latest_job}")
            
            # Check if result files exist
            stdin, stdout, stderr = ssh_client.exec_command(f'ls -la {job_path}/')
            job_contents = stdout.read().decode().strip()
            print(f"\nJob directory contents:")
            print(job_contents)
            
            # Check for result files specifically
            stdin, stdout, stderr = ssh_client.exec_command(f'ls -la {job_path}/*result*.pkl 2>/dev/null || echo "No result files found"')
            result_files = stdout.read().decode().strip()
            print(f"\nResult files:")
            print(result_files)
            
            # Check for error files
            stdin, stdout, stderr = ssh_client.exec_command(f'ls -la {job_path}/*error*.pkl 2>/dev/null || echo "No error files found"')
            error_files = stdout.read().decode().strip()
            print(f"\nError files:")
            print(error_files)
            
            # Check for log files
            stdin, stdout, stderr = ssh_client.exec_command(f'find {job_path} -name "*.log" -o -name "*.out" -o -name "*.err"')
            log_files = stdout.read().decode().strip()
            if log_files:
                print(f"\nLog files found:")
                for log_file in log_files.split('\n'):
                    if log_file.strip():
                        print(f"  {log_file}")
                        # Show the content of each log file
                        stdin, stdout, stderr = ssh_client.exec_command(f'cat {log_file}')
                        log_content = stdout.read().decode().strip()
                        if log_content:
                            print(f"    Content of {log_file}:")
                            print(f"    {log_content}")
                        print()
            
            # Check the job script that was executed
            stdin, stdout, stderr = ssh_client.exec_command(f'find {job_path} -name "job_script.sh" -o -name "*.sh"')
            script_files = stdout.read().decode().strip()
            if script_files:
                print(f"\nJob script files:")
                for script_file in script_files.split('\n'):
                    if script_file.strip():
                        print(f"  {script_file}")
                        stdin, stdout, stderr = ssh_client.exec_command(f'cat {script_file}')
                        script_content = stdout.read().decode().strip()
                        if script_content:
                            print(f"    Content of {script_file}:")
                            print(f"    {script_content}")
                        print()
            
            # Check if there are any Python error outputs
            stdin, stdout, stderr = ssh_client.exec_command(f'find {job_path} -name "*.py" -exec echo "=== {{}} ===" \\; -exec cat {{}} \\; 2>/dev/null | head -100')
            python_files = stdout.read().decode().strip()
            if python_files:
                print(f"\nPython files in job directory:")
                print(python_files[:2000])  # Limit output
            
            # Try to manually run the execution to see what happens
            print(f"\n🔍 Attempting manual execution simulation...")
            
            # Check if the venv directories exist and are functional
            stdin, stdout, stderr = ssh_client.exec_command(f'ls -la {job_path}/venv*/ 2>/dev/null || echo "No venv directories"')
            venv_dirs = stdout.read().decode().strip()
            print(f"\nVirtual environment directories:")
            print(venv_dirs)
            
            # Check if we can run python in venv2
            stdin, stdout, stderr = ssh_client.exec_command(f'find {job_path} -name "clustrix_venv2*" -type d')
            venv2_dirs = stdout.read().decode().strip()
            if venv2_dirs:
                venv2_dir = venv2_dirs.split('\n')[0].strip()
                print(f"\nFound VENV2 directory: {venv2_dir}")
                
                # Check if python works in venv2
                python_path = f"{venv2_dir}/bin/python"
                stdin, stdout, stderr = ssh_client.exec_command(f'{python_path} --version 2>&1 || echo "Python in venv2 failed"')
                python_version = stdout.read().decode().strip()
                print(f"VENV2 Python version: {python_version}")
                
                # Check if torch is available in venv2
                stdin, stdout, stderr = ssh_client.exec_command(f'{python_path} -c "import torch; print(f\\"PyTorch version: {{torch.__version__}}\\"); print(f\\"CUDA available: {{torch.cuda.is_available()}}\\"); print(f\\"GPU count: {{torch.cuda.device_count()}}\\")" 2>&1')
                torch_check = stdout.read().decode().strip()
                print(f"VENV2 PyTorch check:")
                print(f"  {torch_check}")
        
        ssh_client.close()
        
    except Exception as e:
        print(f"❌ Failed to check remote logs: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_remote_logs()