#!/bin/bash
#SBATCH --job-name=test_venv
#SBATCH --output=test_venv_%j.out
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "=== TESTING WITH PROPER VENV ==="
echo "Hostname: $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Work in accessible directory
cd /dartfs-hpc/rc/home/b/f002d6b/clustrix
export CLUSTRIX_ORIGINAL_CWD=/dartfs-hpc/rc/home/b/f002d6b/clustrix

echo "1. Loading Python module:"
module load python
python3 --version

echo "2. Activating venv:"
source clustrix_venv/bin/activate
python3 --version
which python3

echo "3. Testing paramiko import:"
python3 -c "import paramiko; print('Paramiko working:', paramiko.__version__)"

echo "4. Creating job directory and extracting package:"
mkdir -p job_$SLURM_JOB_ID
cd job_$SLURM_JOB_ID
unzip -q ../simple_working_test_f9951f102bcc02ca.zip

echo "5. Running filesystem test with proper environment:"
python3 execute.py

echo "=== VENV TEST COMPLETED ==="