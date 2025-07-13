#!/usr/bin/env python3
"""
Check what Python versions are available on tensor01.
"""

import sys
import os
sys.path.insert(0, '/Users/jmanning/clustrix')

from tests.real_world import TestCredentials
import paramiko

def check_python_versions():
    """Check available Python versions on tensor01."""
    print("🔍 Checking available Python versions on tensor01...")
    
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
        
        python_candidates = [
            "python3.12", "python3.11", "python3.10", "python3.9", 
            "python3.8", "python3.7", "python3.6", "python3", "python"
        ]
        
        print("Available Python versions:")
        available_versions = []
        
        for python_cmd in python_candidates:
            test_cmd = f"{python_cmd} -c 'import sys; print(sys.version)' 2>/dev/null"
            stdin, stdout, stderr = ssh_client.exec_command(test_cmd)
            version_output = stdout.read().decode().strip()
            
            if version_output and "Python" in version_output:
                available_versions.append((python_cmd, version_output))
                print(f"  ✅ {python_cmd}: {version_output}")
            else:
                print(f"  ❌ {python_cmd}: Not available")
        
        # Check conda Python versions
        print(f"\nChecking conda environments:")
        stdin, stdout, stderr = ssh_client.exec_command('conda info --envs 2>/dev/null')
        conda_envs = stdout.read().decode().strip()
        if conda_envs:
            print("Conda environments:")
            print(conda_envs)
        else:
            print("  Conda not available or no environments")
        
        # Check if conda can create Python 3.9
        stdin, stdout, stderr = ssh_client.exec_command('conda search python=3.9 2>/dev/null | head -5')
        conda_python = stdout.read().decode().strip()
        if conda_python:
            print(f"\nConda Python 3.9 availability:")
            print(conda_python[:500])
        
        ssh_client.close()
        
        return available_versions
        
    except Exception as e:
        print(f"❌ Failed to check Python versions: {e}")
        return []

if __name__ == "__main__":
    versions = check_python_versions()
    
    if versions:
        print(f"\n🎯 Best candidates for VENV1 (serialization):")
        for cmd, version in versions:
            if any(v in version for v in ["3.8", "3.9", "3.10", "3.11", "3.12"]):
                print(f"  ✅ {cmd}: {version[:50]}...")
        
        print(f"\n📋 Recommended strategy:")
        print(f"  - Use newer Python (3.8+) for VENV1 to handle modern serialization")
        print(f"  - Use Python 3.9+ via conda for VENV2 with full GPU support")
    else:
        print("❌ No Python versions found")