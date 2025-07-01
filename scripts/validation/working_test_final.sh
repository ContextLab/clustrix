#!/bin/bash
#SBATCH --job-name=working_test
#SBATCH --output=working_test_%j.out
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "Working test on $(hostname)"
export CLUSTRIX_ORIGINAL_CWD=/dartfs-hpc/rc/home/b/f002d6b/clustrix
cd /tmp && mkdir working_test_$$
cd working_test_$$
unzip -q /dartfs-hpc/rc/home/b/f002d6b/clustrix/simple_working_test_f9951f102bcc02ca.zip
python3 execute.py
echo "Working test completed"