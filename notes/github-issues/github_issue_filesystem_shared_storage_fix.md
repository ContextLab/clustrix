# GitHub Issue Update: Critical Filesystem Fix for Shared Storage

## Issue: Filesystem Functions Incorrectly Using SSH on Shared Storage Clusters

### **Problem Discovery**
During SLURM validation testing, filesystem operations are failing with "No authentication methods available" error. Investigation reveals this is **NOT** an authentication issue but a **fundamental design problem**.

### **Root Cause Analysis**
The `ClusterFilesystem` class incorrectly attempts SSH connections from SLURM compute nodes to head nodes, even when they share the same filesystem (NFS/Lustre). On clusters like ndoli.dartmouth.edu:

- **Current (Wrong)**: `cluster_ls("/shared/data")` → SSH from compute node to head node → "No authentication methods available"
- **Should Be (Right)**: `cluster_ls("/shared/data")` → Direct filesystem access → Works immediately

### **Real-World Impact**
This blocks **primary HPC use cases**:
```python
@cluster(cores=16)
def analyze_large_dataset():
    # Currently fails - but SHOULD work on shared storage
    files = cluster_find("*.h5", "/dartfs-hpc/rc/lab/datasets/")
    for file in files:
        data = cluster_stat(file)  # Get file size before processing
        if data.size > 1e9:  # Process large files differently
            result = process_large_file(file)
    return results
```

### **Evidence from SLURM Job 5230373**
```json
{
  "function_name": "test_filesystem_integration",
  "status": "SUCCESS",
  "result": {
    "filesystem_test": "FAILED", 
    "error": "No authentication methods available"
  }
}
```

**Key Insight**: The job successfully executes and paramiko is installed, but filesystem operations fail because they're trying SSH instead of direct access.

### **Priority Escalation: HIGH → CRITICAL**
This changes from "enhancement" to **critical bug** because:
1. **Shared filesystems are standard** on all major HPC clusters
2. **Primary use case blocked**: Large dataset analysis workflows  
3. **Performance impact**: SSH overhead vs direct filesystem access
4. **User expectations**: Core documented functionality appears broken

### **Technical Solution Required**
Implement **cluster detection logic** in `ClusterFilesystem`:

```python
class ClusterFilesystem:
    def __init__(self, config):
        self.config = config
        if self._running_on_target_cluster():
            self.use_local_operations = True  # Direct filesystem access
        else:
            self.use_ssh = True  # SSH for external access
    
    def _running_on_target_cluster(self):
        """Detect if we're already running on the target cluster."""
        import socket
        current_host = socket.gethostname()
        return (self.config.cluster_host in current_host or 
                current_host in self.config.cluster_host)
```

### **Validation Plan**
1. **Test shared filesystem access** from SLURM compute nodes
2. **Implement cluster detection** in ClusterFilesystem class  
3. **Add local filesystem operations** for when running on target cluster
4. **Validate with real SLURM jobs** using filesystem functions
5. **Verify performance improvement** (direct access vs SSH)

### **Expected Outcome**
After fix, this should work seamlessly:
```python
@cluster(cores=16, cluster_host="ndoli.dartmouth.edu")
def genomics_pipeline():
    # Will use direct filesystem access - no SSH needed
    files = cluster_find("*.fastq", "/dartfs-hpc/rc/lab/datasets/") ✅
    large_files = [f for f in files if cluster_stat(f).size > 1e9] ✅
    return process_files(large_files) ✅
```

### **Implementation Status**
- [x] Problem identified and documented
- [x] Root cause analysis complete  
- [x] Solution architecture defined
- [ ] **Next**: Implement cluster detection logic
- [ ] **Next**: Add local filesystem operations
- [ ] **Next**: SLURM validation testing

**Commit Reference**: fab944c (Dependency resolution system complete - now adding critical filesystem fix)

---

**This fix will make clustrix fully production-ready for real HPC workflows with large shared datasets.**