#!/bin/bash
#SBATCH --job-name=improved_detect
#SBATCH --output=improved_detect_%j.out
#SBATCH --time=00:02:00
#SBATCH --nodes=1
#SBATCH --ntasks=1

echo "=== IMPROVED CLUSTER DETECTION TEST ==="
echo "Hostname: $(hostname)"
echo "SLURM_JOB_ID: $SLURM_JOB_ID"

# Test improved cluster detection logic
python3 << 'EOF'
import socket

def same_institution_domain(host1, host2):
    """Test the improved institution domain matching."""
    try:
        parts1 = host1.split('.')
        parts2 = host2.split('.')
        
        # Check if they share institution domain (dartmouth.edu)
        if len(parts1) >= 2 and len(parts2) >= 2:
            institution1 = '.'.join(parts1[-2:])
            institution2 = '.'.join(parts2[-2:])
            
            if institution1 == institution2:
                return True
                
        # Check HPC patterns
        if len(parts1) >= 3 and len(parts2) >= 3:
            cluster1_parts = parts1[-3:]
            cluster2_parts = parts2[-3:]
            
            if cluster1_parts[1:] == cluster2_parts[1:]:
                return True
                
    except (IndexError, AttributeError):
        pass
        
    return False

hostname = socket.gethostname()
target = "ndoli.dartmouth.edu"

print(f"Current hostname: {hostname}")
print(f"Target host: {target}")

# Parse hostname parts for analysis
hostname_parts = hostname.split('.')
target_parts = target.split('.')

print(f"Hostname parts: {hostname_parts}")
print(f"Target parts: {target_parts}")

# Test institution domain matching
institution_match = same_institution_domain(hostname, target)
print(f"Institution domain match: {institution_match}")

if institution_match:
    print("✅ SUCCESS: Improved detection recognizes same institution")
    # Test the full logic
    is_match = (
        hostname == target or
        target in hostname or 
        hostname in target or
        institution_match
    )
    print(f"Final detection result: {is_match}")
    print("Would switch to local filesystem: YES")
else:
    print("❌ Still not detecting - need further refinement")
    
    # Debug information
    if len(hostname_parts) >= 2 and len(target_parts) >= 2:
        h_institution = '.'.join(hostname_parts[-2:])
        t_institution = '.'.join(target_parts[-2:])
        print(f"Hostname institution: {h_institution}")
        print(f"Target institution: {t_institution}")
        print(f"Match: {h_institution == t_institution}")

EOF

echo "=== TEST COMPLETED ==="