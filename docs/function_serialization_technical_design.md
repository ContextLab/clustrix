# Function Serialization and Dependency Management Technical Design

**Issue Reference**: GitHub Issue #64  
**Status**: Design Phase  
**Author**: Claude Code  
**Date**: 2025-07-01  

## Executive Summary

This document outlines a comprehensive solution to replace Clustrix's current pickle-based function serialization with a dependency packaging approach that enables reliable remote execution of locally-defined functions with their complete dependency context. Additionally, it introduces unified cluster filesystem utilities to support development-to-production workflows with datasets.

## Problem Analysis

### Current Limitations

1. **Pickle Serialization Failures**: Functions defined in `__main__` module (scripts, notebooks) cannot be pickled/unpickled
2. **Dependency Isolation**: Only pip-installable packages are handled; local modules and files are ignored
3. **Context Loss**: Functions lose access to their original execution environment
4. **User Experience**: Forces unintuitive code restructuring
5. **Dataset Workflow Gap**: No support for transitioning from local development data to cluster production data

### Error Pattern
```python
# This fails with current approach
def my_analysis(data):
    return process_data(data)  # process_data defined locally

@cluster(cores=4)
def run_analysis():
    return my_analysis(load_data())  # Fails: Can't pickle my_analysis
```

### Dataset Workflow Problem
```python
# Local development
local_files = os.listdir("./small_dataset/")

@cluster(cores=8)
def process_data():
    # No way to list files on cluster filesystem
    # Must hardcode filenames or use different code paths
```

## Proposed Solution: Unified Architecture

The solution consists of two complementary components:

1. **Dependency Packaging System**: Handle function serialization and local dependency management
2. **Unified Filesystem Utilities**: Provide consistent filesystem operations across local and remote environments

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Clustrix Unified Interface                    │
├─────────────────────────────────────────────────────────────────────────┤
│  Function Execution                    │  Filesystem Operations           │
│  ┌─────────────────┐                  │  ┌─────────────────┐            │
│  │ Dependency      │                  │  │ cluster_ls()    │            │
│  │ Packaging       │                  │  │ cluster_find()  │            │
│  │ System          │                  │  │ cluster_stat()  │            │
│  └─────────────────┘                  │  └─────────────────┘            │
├─────────────────────────────────────────────────────────────────────────┤
│                        Config-Driven Execution                          │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ Local Config    │    │ SSH Config      │    │ K8s Config      │     │
│  │ cluster_type:   │    │ cluster_type:   │    │ cluster_type:   │     │
│  │ "local"         │    │ "slurm"         │    │ "kubernetes"    │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Part I: Unified Filesystem Utilities

### Core Design Principle

All filesystem operations use the same interface regardless of execution context, with behavior determined by the `ClusterConfig` object:

```python
def cluster_ls(path: str, config: ClusterConfig) -> List[str]:
    """List directory contents locally or remotely based on config."""
    
def cluster_find(pattern: str, path: str, config: ClusterConfig) -> List[str]:
    """Find files matching pattern locally or remotely based on config."""
    
def cluster_stat(path: str, config: ClusterConfig) -> FileInfo:
    """Get file information locally or remotely based on config."""
```

### Filesystem Operations Module

#### 1.1 Core Operations
```python
class ClusterFilesystem:
    """Unified filesystem operations for local and remote clusters."""
    
    def __init__(self, config: ClusterConfig):
        self.config = config
        self._ssh_client = None
    
    def ls(self, path: str) -> List[str]:
        """List directory contents."""
        if self.config.cluster_type == "local":
            return self._local_ls(path)
        else:
            return self._remote_ls(path)
    
    def find(self, pattern: str, path: str = ".") -> List[str]:
        """Find files matching pattern."""
        if self.config.cluster_type == "local":
            return self._local_find(pattern, path)
        else:
            return self._remote_find(pattern, path)
    
    def stat(self, path: str) -> FileInfo:
        """Get file/directory information."""
        if self.config.cluster_type == "local":
            return self._local_stat(path)
        else:
            return self._remote_stat(path)
    
    def exists(self, path: str) -> bool:
        """Check if file/directory exists."""
        if self.config.cluster_type == "local":
            return self._local_exists(path)
        else:
            return self._remote_exists(path)
    
    def isdir(self, path: str) -> bool:
        """Check if path is a directory."""
        if self.config.cluster_type == "local":
            return self._local_isdir(path)
        else:
            return self._remote_isdir(path)
    
    def isfile(self, path: str) -> bool:
        """Check if path is a file."""
        if self.config.cluster_type == "local":
            return self._local_isfile(path)
        else:
            return self._remote_isfile(path)
    
    def glob(self, pattern: str, path: str = ".") -> List[str]:
        """Pattern matching for files."""
        if self.config.cluster_type == "local":
            return self._local_glob(pattern, path)
        else:
            return self._remote_glob(pattern, path)
    
    def du(self, path: str) -> DiskUsage:
        """Get directory usage information."""
        if self.config.cluster_type == "local":
            return self._local_du(path)
        else:
            return self._remote_du(path)
    
    def count_files(self, path: str, pattern: str = "*") -> int:
        """Count files in directory matching pattern."""
        if self.config.cluster_type == "local":
            return self._local_count_files(path, pattern)
        else:
            return self._remote_count_files(path, pattern)
```

#### 1.2 Local Implementation
```python
def _local_ls(self, path: str) -> List[str]:
    """Local directory listing."""
    full_path = os.path.join(self.config.local_work_dir or os.getcwd(), path)
    return os.listdir(full_path)

def _local_find(self, pattern: str, path: str) -> List[str]:
    """Local file finding."""
    import glob
    full_path = os.path.join(self.config.local_work_dir or os.getcwd(), path)
    search_pattern = os.path.join(full_path, "**", pattern)
    return glob.glob(search_pattern, recursive=True)

def _local_stat(self, path: str) -> FileInfo:
    """Local file stat."""
    full_path = os.path.join(self.config.local_work_dir or os.getcwd(), path)
    stat = os.stat(full_path)
    return FileInfo(
        size=stat.st_size,
        modified=stat.st_mtime,
        is_dir=os.path.isdir(full_path),
        permissions=oct(stat.st_mode)[-3:]
    )

def _local_glob(self, pattern: str, path: str) -> List[str]:
    """Local glob pattern matching."""
    import glob
    full_path = os.path.join(self.config.local_work_dir or os.getcwd(), path)
    search_pattern = os.path.join(full_path, pattern)
    return glob.glob(search_pattern)

def _local_du(self, path: str) -> DiskUsage:
    """Local disk usage."""
    full_path = os.path.join(self.config.local_work_dir or os.getcwd(), path)
    total_size = 0
    file_count = 0
    
    for dirpath, dirnames, filenames in os.walk(full_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            total_size += os.path.getsize(filepath)
            file_count += 1
    
    return DiskUsage(total_bytes=total_size, file_count=file_count)
```

#### 1.3 Remote Implementation  
```python
def _remote_ls(self, path: str) -> List[str]:
    """Remote directory listing via SSH."""
    ssh_client = self._get_ssh_client()
    full_path = os.path.join(self.config.remote_work_dir, path)
    
    stdin, stdout, stderr = ssh_client.exec_command(f"ls -1 {full_path}")
    output = stdout.read().decode().strip()
    
    if output:
        return output.split('\n')
    return []

def _remote_find(self, pattern: str, path: str) -> List[str]:
    """Remote file finding via SSH."""
    ssh_client = self._get_ssh_client()
    full_path = os.path.join(self.config.remote_work_dir, path)
    
    # Use find command with name pattern
    stdin, stdout, stderr = ssh_client.exec_command(
        f"find {full_path} -name '{pattern}' -type f"
    )
    output = stdout.read().decode().strip()
    
    if output:
        return output.split('\n')
    return []

def _remote_stat(self, path: str) -> FileInfo:
    """Remote file stat via SSH."""
    ssh_client = self._get_ssh_client()
    full_path = os.path.join(self.config.remote_work_dir, path)
    
    # Use stat command with specific format
    cmd = f"stat -c '%s %Y %f' {full_path} && test -d {full_path} && echo 'DIR' || echo 'FILE'"
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    output = stdout.read().decode().strip().split('\n')
    
    size, mtime, mode = output[0].split()
    is_dir = output[1] == 'DIR'
    
    return FileInfo(
        size=int(size),
        modified=int(mtime),
        is_dir=is_dir,
        permissions=oct(int(mode, 16))[-3:]
    )

def _remote_glob(self, pattern: str, path: str) -> List[str]:
    """Remote glob pattern matching via SSH."""
    ssh_client = self._get_ssh_client()
    full_path = os.path.join(self.config.remote_work_dir, path)
    
    # Use shell glob expansion
    stdin, stdout, stderr = ssh_client.exec_command(
        f"cd {full_path} && ls -1 {pattern} 2>/dev/null || true"
    )
    output = stdout.read().decode().strip()
    
    if output:
        return output.split('\n')
    return []

def _remote_du(self, path: str) -> DiskUsage:
    """Remote disk usage via SSH."""
    ssh_client = self._get_ssh_client()
    full_path = os.path.join(self.config.remote_work_dir, path)
    
    # Get total size and file count
    cmd = f"du -sb {full_path} && find {full_path} -type f | wc -l"
    stdin, stdout, stderr = ssh_client.exec_command(cmd)
    output = stdout.read().decode().strip().split('\n')
    
    total_bytes = int(output[0].split()[0])
    file_count = int(output[1])
    
    return DiskUsage(total_bytes=total_bytes, file_count=file_count)

def _remote_count_files(self, path: str, pattern: str) -> int:
    """Remote file counting via SSH."""
    ssh_client = self._get_ssh_client()
    full_path = os.path.join(self.config.remote_work_dir, path)
    
    stdin, stdout, stderr = ssh_client.exec_command(
        f"find {full_path} -name '{pattern}' -type f | wc -l"
    )
    return int(stdout.read().decode().strip())
```

#### 1.4 Data Classes
```python
@dataclass
class FileInfo:
    """File information structure."""
    size: int
    modified: float  # Unix timestamp
    is_dir: bool
    permissions: str

@dataclass 
class DiskUsage:
    """Disk usage information."""
    total_bytes: int
    file_count: int
    
    @property
    def total_mb(self) -> float:
        return self.total_bytes / (1024 * 1024)
    
    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024 * 1024 * 1024)
```

#### 1.5 Convenience Functions
```python
# Global convenience functions for easy import
def cluster_ls(path: str, config: ClusterConfig) -> List[str]:
    """List directory contents locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.ls(path)

def cluster_find(pattern: str, path: str, config: ClusterConfig) -> List[str]:
    """Find files matching pattern locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.find(pattern, path)

def cluster_stat(path: str, config: ClusterConfig) -> FileInfo:
    """Get file information locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.stat(path)

def cluster_exists(path: str, config: ClusterConfig) -> bool:
    """Check if file/directory exists locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.exists(path)

def cluster_isdir(path: str, config: ClusterConfig) -> bool:
    """Check if path is directory locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.isdir(path)

def cluster_isfile(path: str, config: ClusterConfig) -> bool:
    """Check if path is file locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.isfile(path)

def cluster_glob(pattern: str, path: str, config: ClusterConfig) -> List[str]:
    """Pattern matching for files locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.glob(pattern, path)

def cluster_du(path: str, config: ClusterConfig) -> DiskUsage:
    """Get directory usage locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.du(path)

def cluster_count_files(path: str, pattern: str, config: ClusterConfig) -> int:
    """Count files matching pattern locally or remotely based on config."""
    fs = ClusterFilesystem(config)
    return fs.count_files(path, pattern)
```

## Part II: Dependency Packaging System

### Core Concept

Instead of serializing function objects, we:
1. **Analyze** the function's complete dependency tree
2. **Package** all local dependencies into a transferable archive
3. **Deploy** the archive to remote cluster with unique identifier
4. **Execute** the function in a reconstructed environment

### Technical Design

#### 2.1 Dependency Analysis
```python
class DependencyAnalyzer:
    def analyze_function(self, func: Callable) -> DependencyGraph:
        """Analyze function for all dependencies."""
        
        # Get function source code
        source = inspect.getsource(func)
        tree = ast.parse(source)
        
        dependencies = DependencyGraph()
        
        # Find imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.add_imports([alias.name for alias in node.names])
            elif isinstance(node, ast.ImportFrom):
                dependencies.add_from_imports(node.module, 
                                            [alias.name for alias in node.names])
        
        # Find file operations and filesystem calls
        file_refs = self._find_file_references(tree)
        dependencies.add_file_references(file_refs)
        
        # Find cluster filesystem calls
        cluster_fs_calls = self._find_cluster_filesystem_calls(tree)
        dependencies.add_cluster_filesystem_calls(cluster_fs_calls)
        
        # Find function calls that might be local
        local_calls = self._find_local_function_calls(tree, func)
        dependencies.add_local_functions(local_calls)
        
        return dependencies
    
    def _find_cluster_filesystem_calls(self, tree: ast.AST) -> List[FilesystemCall]:
        """Find calls to cluster filesystem functions."""
        fs_functions = {
            'cluster_ls', 'cluster_find', 'cluster_stat', 'cluster_exists',
            'cluster_isdir', 'cluster_isfile', 'cluster_glob', 'cluster_du',
            'cluster_count_files'
        }
        
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in fs_functions:
                    calls.append(FilesystemCall(
                        function=node.func.id,
                        args=[ast.unparse(arg) for arg in node.args],
                        lineno=node.lineno
                    ))
        
        return calls
```

#### 2.2 File Packaging with Filesystem Integration
```python
class FilePackager:
    def package_dependencies(self, dependencies: DependencyGraph, 
                           context: ExecutionContext) -> PackageInfo:
        """Create package with all dependencies including filesystem context."""
        
        package_id = self._generate_package_id(dependencies)
        package_path = f"/tmp/clustrix_package_{package_id}.zip"
        
        with zipfile.ZipFile(package_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add function source files
            for source_file in dependencies.source_files:
                self._add_file_to_zip(zf, source_file)
            
            # Add local modules
            for module_file in dependencies.local_modules:
                self._add_module_to_zip(zf, module_file)
            
            # Add data files referenced in function
            for data_file in dependencies.data_files:
                self._add_file_to_zip(zf, data_file)
            
            # Add filesystem utilities module
            self._add_filesystem_utilities(zf)
            
            # Add execution metadata
            self._add_execution_metadata(zf, context, dependencies)
        
        return PackageInfo(
            package_id=package_id,
            package_path=package_path,
            dependencies=dependencies,
            size_bytes=os.path.getsize(package_path)
        )
    
    def _add_filesystem_utilities(self, zipfile: zipfile.ZipFile):
        """Add cluster filesystem utilities to package."""
        # Include the filesystem module in the package
        fs_module_source = inspect.getsource(ClusterFilesystem)
        zipfile.writestr("clustrix_filesystem.py", fs_module_source)
```

#### 2.3 Remote Execution with Filesystem Support
```python
class RemoteExecutor:
    def execute_function(self, remote_env: RemoteEnvironment,
                        func_args: tuple, func_kwargs: dict,
                        config: ClusterConfig) -> Any:
        """Execute function in remote environment with filesystem support."""
        
        execution_script = f'''
# Set up environment
exec(open("{remote_env.setup_script}").read())

# Import cluster filesystem utilities
import sys
sys.path.insert(0, "{remote_env.remote_dir}")
from clustrix_filesystem import ClusterFilesystem, cluster_ls, cluster_find, cluster_stat

# Set up cluster config for filesystem operations
import json
with open("{remote_env.remote_dir}/cluster_config.json", "r") as f:
    config_data = json.load(f)

# Recreate config object
from clustrix.config import ClusterConfig
cluster_config = ClusterConfig(**config_data)

# Make filesystem functions available in global scope
globals()['cluster_ls'] = lambda path: cluster_ls(path, cluster_config)
globals()['cluster_find'] = lambda pattern, path=".": cluster_find(pattern, path, cluster_config)
globals()['cluster_stat'] = lambda path: cluster_stat(path, cluster_config)
globals()['cluster_exists'] = lambda path: cluster_exists(path, cluster_config)
globals()['cluster_isdir'] = lambda path: cluster_isdir(path, cluster_config)
globals()['cluster_isfile'] = lambda path: cluster_isfile(path, cluster_config)
globals()['cluster_glob'] = lambda pattern, path=".": cluster_glob(pattern, path, cluster_config)
globals()['cluster_du'] = lambda path: cluster_du(path, cluster_config)
globals()['cluster_count_files'] = lambda path, pattern="*": cluster_count_files(path, pattern, cluster_config)

# Load and execute function
with open("{remote_env.remote_dir}/metadata.json", "r") as f:
    metadata = json.load(f)

function_source = metadata["function_info"]["source"]
exec(function_source, globals())

function_name = metadata["function_info"]["name"]
func = globals()[function_name]

# Execute with provided arguments
import pickle
args = pickle.loads({pickle.dumps(func_args)!r})
kwargs = pickle.loads({pickle.dumps(func_kwargs)!r})

try:
    result = func(*args, **kwargs)
    
    with open("{remote_env.remote_dir}/result.pkl", "wb") as f:
        pickle.dump(result, f)
        
except Exception as e:
    import traceback
    error_info = {{
        "error": str(e),
        "traceback": traceback.format_exc()
    }}
    
    with open("{remote_env.remote_dir}/error.pkl", "wb") as f:
        pickle.dump(error_info, f)
    
    raise
'''
        
        return execution_script
```

## Implementation Phases

### Phase 1: Filesystem Utilities (Week 1-2)
- [ ] Implement `ClusterFilesystem` class with local operations
- [ ] Add SSH-based remote operations  
- [ ] Create convenience functions (`cluster_ls`, `cluster_find`, etc.)
- [ ] Add comprehensive unit tests
- [ ] Test development-to-production workflow

### Phase 2: Dependency Packaging Core (Week 3-4)
- [ ] Implement `DependencyAnalyzer` with AST analysis
- [ ] Create `FilePackager` for selective file collection
- [ ] Build `RemoteDeployer` for secure file transfer
- [ ] Integrate filesystem utilities into packaging

### Phase 3: Remote Execution (Week 5-6)
- [ ] Build remote environment recreation
- [ ] Implement function execution engine with filesystem support
- [ ] Add error handling and diagnostics
- [ ] Create cleanup mechanisms

### Phase 4: Integration & Testing (Week 7-8)
- [ ] Integrate with existing `ClusterExecutor`
- [ ] Add comprehensive test suite
- [ ] Performance benchmarking
- [ ] Security auditing

## Usage Examples

### Complete Development-to-Production Example
```python
from clustrix import cluster, cluster_ls, cluster_find, cluster_glob, ClusterConfig

# Development configuration
dev_config = ClusterConfig(cluster_type="local", local_work_dir="./project")

# Production configuration  
prod_config = ClusterConfig(
    cluster_type="slurm",
    cluster_host="cluster.edu", 
    remote_work_dir="/scratch/project"
)

def process_file(filename, config):
    """Process individual file with filesystem operations."""
    full_path = f"data/{filename}"
    
    # Check if file exists and get info
    if cluster_exists(full_path, config):
        file_info = cluster_stat(full_path, config)
        print(f"Processing {filename} ({file_info.size} bytes)")
        
        # Simulate processing
        return {"filename": filename, "size": file_info.size, "processed": True}
    else:
        return {"filename": filename, "error": "File not found"}

@cluster(cores=8)  # This will parallelize the loop automatically
def analyze_dataset(config):
    """Analyze dataset - works locally or remotely with parallelization."""
    # Find all data files
    data_files = cluster_glob("*.csv", "data/", config)
    print(f"Found {len(data_files)} data files")
    
    # Process each file - this loop will be parallelized by @cluster
    results = []
    for filename in data_files:
        result = process_file(filename, config)
        results.append(result)
    
    return results

# Development phase - test locally (runs locally but may use parallel processing)
print("=== Development Phase ===")
dev_results = analyze_dataset(dev_config)

# Production phase - run on cluster (automatically distributes loop across cluster nodes)
print("=== Production Phase ===") 
prod_results = analyze_dataset(prod_config)
```

### Advanced Parallel Processing Example
```python
@cluster(cores=16, auto_parallel=True)
def process_large_dataset(config):
    """Process large dataset with automatic parallelization."""
    
    # Get dataset information
    data_usage = cluster_du("data/", config)
    file_count = cluster_count_files("data/", "*.csv", config)
    
    print(f"Dataset: {data_usage.total_gb:.2f} GB, {file_count} files")
    
    # Find files by pattern - Clustrix will automatically parallelize this loop
    csv_files = cluster_find("*.csv", "data/", config)
    json_files = cluster_find("*.json", "data/", config)
    
    results = []
    
    # Process CSV files (parallelized automatically)
    for csv_file in csv_files:
        if cluster_isfile(f"data/{csv_file}", config):
            result = process_csv_file(csv_file, config)
            results.append(result)
    
    # Process JSON files (parallelized automatically) 
    for json_file in json_files:
        if cluster_isfile(f"data/{json_file}", config):
            result = process_json_file(json_file, config)
            results.append(result)
    
    return {
        "total_files_processed": len(results),
        "dataset_size_gb": data_usage.total_gb,
        "results": results
    }

def process_csv_file(filename, config):
    """Process a single CSV file."""
    file_info = cluster_stat(f"data/{filename}", config)
    # ... CSV processing logic ...
    return {"type": "csv", "filename": filename, "size": file_info.size}

def process_json_file(filename, config):
    """Process a single JSON file."""
    file_info = cluster_stat(f"data/{filename}", config)
    # ... JSON processing logic ...
    return {"type": "json", "filename": filename, "size": file_info.size}

# Execute with automatic parallelization
cluster_config = ClusterConfig(cluster_type="slurm", cluster_host="hpc.edu")
results = process_large_dataset(cluster_config)
```

## Risk Mitigation

### Security Considerations
1. **Command Injection Prevention**: Sanitize all file paths and patterns
2. **Path Traversal Protection**: Validate paths stay within work directories
3. **Resource Limits**: Implement timeouts for filesystem operations
4. **Access Control**: Respect cluster filesystem permissions

### Performance Optimization
1. **Caching**: Cache filesystem operation results when appropriate
2. **Batching**: Combine multiple operations into single SSH commands
3. **Connection Pooling**: Reuse SSH connections for multiple operations
4. **Parallel Operations**: Execute independent filesystem operations concurrently

### Error Handling
1. **Graceful Degradation**: Handle filesystem operation failures elegantly
2. **Detailed Diagnostics**: Provide clear error messages for failed operations
3. **Retry Logic**: Implement retries for transient network issues
4. **Fallback Mechanisms**: Provide alternative approaches when operations fail

## Success Metrics

1. **Functionality**: Successfully execute 95% of locally-defined functions with filesystem operations
2. **Performance**: Filesystem operations complete within 5 seconds for typical directories  
3. **Usability**: Seamless transition from local development to cluster production
4. **Reliability**: Less than 1% failure rate for well-formed filesystem operations
5. **Security**: No security incidents from filesystem access operations

## Conclusion

This unified architecture provides both comprehensive function serialization capabilities and essential filesystem utilities for real-world data processing workflows. The config-driven approach ensures consistent interfaces while supporting seamless development-to-production transitions.

The filesystem utilities address a critical gap in cluster computing workflows, while the dependency packaging system solves fundamental limitations in function serialization. Together, they enable Clustrix to support complex, real-world usage patterns with intuitive APIs and automatic parallelization.

---

**Next Steps**: 
1. Review and approve technical design
2. Begin Phase 1 implementation (Filesystem Utilities)
3. Create detailed API specifications
4. Set up development and testing infrastructure