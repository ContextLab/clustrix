#!/bin/bash
#SBATCH --job-name=final_fs_test
#SBATCH --output=final_fs_test_%j.out
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "=== FINAL FILESYSTEM TEST ==="
echo "Hostname: $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Set working directory for results
export CLUSTRIX_ORIGINAL_CWD=/dartfs-hpc/rc/home/b/f002d6b/clustrix

# Extract and run package
cd /tmp
mkdir final_fs_test_$$
cd final_fs_test_$$
echo "Extracting package..."
unzip -q /dartfs-hpc/rc/home/b/f002d6b/clustrix/complete_fs_test_dea40bd7ce96d519.zip

echo "Running filesystem test..."
python3 execute.py

echo "=== TEST COMPLETED ==="