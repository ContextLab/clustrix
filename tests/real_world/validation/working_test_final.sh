#!/bin/bash
#SBATCH --job-name=working_test
#SBATCH --output=working_test_%j.out
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "Working test on $(hostname)"
export CLUSTRIX_ORIGINAL_CWD=${CLUSTRIX_TEST_SLURM_REMOTE_DIR:?set CLUSTRIX_TEST_SLURM_REMOTE_DIR to a writable path on the cluster}/clustrix
cd /tmp && mkdir working_test_$$
cd working_test_$$
unzip -q ${CLUSTRIX_TEST_SLURM_REMOTE_DIR:?set CLUSTRIX_TEST_SLURM_REMOTE_DIR to a writable path on the cluster}/clustrix/simple_working_test_f9951f102bcc02ca.zip
python3 execute.py
echo "Working test completed"