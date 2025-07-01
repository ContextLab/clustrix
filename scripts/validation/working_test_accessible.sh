#!/bin/bash
#SBATCH --job-name=accessible_test
#SBATCH --output=accessible_test_%j.out
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "Accessible test on $(hostname)"
echo "Working in accessible directory: $(pwd)"

# Work in the accessible directory
cd /dartfs-hpc/rc/home/b/f002d6b/clustrix
export CLUSTRIX_ORIGINAL_CWD=/dartfs-hpc/rc/home/b/f002d6b/clustrix

# Create subdirectory for this job
mkdir -p job_$SLURM_JOB_ID
cd job_$SLURM_JOB_ID

echo "Extracting package..."
unzip -q ../simple_working_test_f9951f102bcc02ca.zip

echo "Running test..."
python3 execute.py

echo "Test completed"