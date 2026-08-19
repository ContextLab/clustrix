#!/bin/bash
#SBATCH --job-name=shared_fs_test
#SBATCH --output=shared_fs_test_%j.out
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "Starting shared filesystem test on $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Set working directory for results
export CLUSTRIX_ORIGINAL_CWD=${CLUSTRIX_TEST_SLURM_REMOTE_DIR:?set CLUSTRIX_TEST_SLURM_REMOTE_DIR to a writable path on the cluster}/clustrix

# Extract and run
cd /tmp
mkdir clustrix_test_$$
cd clustrix_test_$$
unzip -q ${CLUSTRIX_TEST_SLURM_REMOTE_DIR:?set CLUSTRIX_TEST_SLURM_REMOTE_DIR to a writable path on the cluster}/clustrix/shared_fs_test_1594df9e2880ce58.zip
python3 execute.py

echo "Test completed"