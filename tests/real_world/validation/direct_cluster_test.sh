#!/bin/bash
#SBATCH --job-name=cluster_detect
#SBATCH --output=cluster_detect_%j.out
#SBATCH --time=00:02:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "=== CLUSTER DETECTION TEST ==="
echo "Hostname: $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Test basic cluster detection logic
python3 << 'EOF'
import socket
import os

hostname = socket.gethostname()
target = "ndoli.dartmouth.edu"

print(f"Current hostname: {hostname}")
print(f"Target host: {target}")

# Test our detection logic
is_match = (
    hostname == target or
    target in hostname or 
    hostname in target or
    (len(hostname.split(".")) > 1 and len(target.split(".")) > 1 and
     hostname.split(".")[1:] == target.split(".")[1:])
)

print(f"Detection result: {is_match}")
print(f"Would switch to local: {'YES' if is_match else 'NO'}")

# Test filesystem access
try:
    files = os.listdir(".")
    print(f"Filesystem test: SUCCESS ({len(files)} files)")
except Exception as e:
    print(f"Filesystem test: FAILED - {e}")

# Test shared filesystem access
shared_paths = ["/dartfs-hpc/rc/home/b/f002d6b", "/tmp"]
for path in shared_paths:
    try:
        if os.path.exists(path):
            items = os.listdir(path)
            print(f"Shared path {path}: ACCESSIBLE ({len(items)} items)")
        else:
            print(f"Shared path {path}: NOT FOUND")
    except Exception as e:
        print(f"Shared path {path}: ERROR - {e}")

print(f"Test Status: {'SUCCESS' if is_match else 'NEEDS_INVESTIGATION'}")
EOF

echo "=== TEST COMPLETED ==="