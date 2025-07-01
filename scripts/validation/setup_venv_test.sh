#!/bin/bash
#SBATCH --job-name=setup_venv
#SBATCH --output=setup_venv_%j.out
#SBATCH --time=00:10:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "=== PYTHON VENV SETUP TEST ==="
echo "Hostname: $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Work in accessible directory
cd /dartfs-hpc/rc/home/b/f002d6b/clustrix

echo "1. Default Python version:"
python3 --version

echo "2. Loading Python module:"
module load python
python3 --version

echo "3. Creating virtual environment:"
python3 -m venv clustrix_venv

echo "4. Activating virtual environment:"
source clustrix_venv/bin/activate
python3 --version
which python3

echo "5. Installing paramiko in venv:"
pip install paramiko

echo "6. Testing paramiko import:"
python3 -c "import paramiko; print('Paramiko version:', paramiko.__version__)"

echo "7. Installing additional packages:"
pip install requests

echo "8. Testing environment:"
python3 -c "
import paramiko
import requests
import socket
print('Success: All packages working')
print('Hostname:', socket.gethostname())
print('Python:', __import__('sys').version)
"

echo "=== VENV SETUP COMPLETED ==="