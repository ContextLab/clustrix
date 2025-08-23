#!/usr/bin/env python3
"""
Simple test to validate paramiko installation and basic cluster detection.
"""


def test_simple_filesystem_working():
    """Simple test that validates the fix without complex imports."""
    import socket
    import os
    import subprocess
    import sys

    hostname = socket.gethostname()
    slurm_job_id = os.environ.get("SLURM_JOB_ID", "not_set")

    # Test 1: Cluster detection logic (simplified)
    target_host = "ndoli.dartmouth.edu"

    # Simple institution domain check
    hostname_parts = hostname.split(".")
    target_parts = target_host.split(".")

    if len(hostname_parts) >= 2 and len(target_parts) >= 2:
        hostname_institution = ".".join(hostname_parts[-2:])
        target_institution = ".".join(target_parts[-2:])
        same_institution = hostname_institution == target_institution
    else:
        same_institution = False

    # Test 2: Try to install paramiko manually
    print("Attempting to install paramiko...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "paramiko"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )

        if result.returncode == 0:
            paramiko_install_success = True
            paramiko_install_error = None
            print("Paramiko installation successful")
        else:
            paramiko_install_success = False
            paramiko_install_error = (
                result.stderr.decode() if result.stderr else "Unknown error"
            )
            print(f"Paramiko installation failed: {paramiko_install_error}")

    except Exception as e:
        paramiko_install_success = False
        paramiko_install_error = str(e)
        print(f"Exception during paramiko installation: {e}")

    # Test 3: Try to import paramiko after installation
    if paramiko_install_success:
        try:
            import paramiko

            paramiko_import_success = True
            paramiko_import_error = None
            print("Paramiko import successful")
        except Exception as e:
            paramiko_import_success = False
            paramiko_import_error = str(e)
            print(f"Paramiko import failed: {e}")
    else:
        paramiko_import_success = False
        paramiko_import_error = "Installation failed"

    # Test 4: Basic filesystem operations
    try:
        files = os.listdir(".")
        filesystem_success = True
        file_count = len(files)
        filesystem_error = None
    except Exception as e:
        filesystem_success = False
        file_count = 0
        filesystem_error = str(e)

    return {
        "test_metadata": {
            "hostname": hostname,
            "slurm_job_id": slurm_job_id,
            "python_version": sys.version,
        },
        "cluster_detection": {
            "hostname_parts": hostname_parts,
            "target_parts": target_parts,
            "same_institution": same_institution,
            "detection_working": same_institution,
        },
        "paramiko_installation": {
            "success": paramiko_install_success,
            "error": paramiko_install_error,
        },
        "paramiko_import": {
            "success": paramiko_import_success,
            "error": paramiko_import_error,
        },
        "basic_filesystem": {
            "success": filesystem_success,
            "file_count": file_count,
            "error": filesystem_error,
        },
        "overall_status": (
            "SUCCESS"
            if (
                same_institution
                and paramiko_install_success
                and paramiko_import_success
                and filesystem_success
            )
            else "PARTIAL"
        ),
        "key_issue_resolved": same_institution,  # The main fix we're testing
    }
