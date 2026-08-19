Filesystem Utilities Tutorial
=============================

This tutorial demonstrates how to use Clustrix's unified filesystem utilities for seamless file operations across local and remote clusters.

Overview
--------

Clustrix provides a set of filesystem utilities that work identically whether you're operating on local files or files on remote clusters. This enables data-driven cluster computing workflows where your code can discover, analyze, and process files without worrying about whether they're local or remote.

Key Benefits
~~~~~~~~~~~~

- **Unified API**: Same function calls work locally and remotely
- **Automatic SSH Management**: No need to manage SSH connections manually
- **Path Normalization**: Consistent behavior across different operating systems
- **Data-Driven Workflows**: Enable processing based on actual file contents and metadata
- **Seamless Integration**: Works perfectly with the ``@cluster`` decorator

What Actually Happens Behind the Scenes
-----------------------------------------

Every function below (``cluster_ls``, ``cluster_stat``, ...) builds a fresh
``ClusterFilesystem`` from the ``config`` you pass, runs one operation on
it, and lets it go. What that operation does depends entirely on
``config.cluster_type``:

- **``cluster_type="local"``**: a plain ``os``/``glob`` call against
  ``config.local_work_dir`` (or the current directory). No network
  involved, nothing to connect or disconnect.
- **Anything else (SLURM, PBS, SGE, SSH, Kubernetes)**: an operation over
  SFTP. The SSH connection is opened lazily, on the *first* call that needs
  one -- not when you construct the config -- and it applies
  ``config.ssh_host_key_policy`` (``"reject"`` by default; see
  :doc:`../api/config`) and the connect/auth/banner timeouts so an
  unreachable host fails within ``config.ssh_connect_timeout`` seconds
  rather than hanging.

**Because each convenience function builds its own new** ``ClusterFilesystem``,
calling several of them in a row -- ``cluster_ls(...)`` then
``cluster_stat(...)`` then ``cluster_exists(...)`` -- opens and closes a
*separate* SSH connection per call, not one connection reused across all
three. For a tight loop against a remote cluster, construct
``clustrix.filesystem.ClusterFilesystem`` once and call its methods
directly to reuse a single connection:

.. code-block:: python

    from clustrix.filesystem import ClusterFilesystem
    from clustrix.config import ClusterConfig

    # Shown here against a local config, but the same construct-once,
    # call-many-times shape is what avoids repeated SSH connections
    # against a remote one.
    config = ClusterConfig(cluster_type="local", local_work_dir=".")

    fs = ClusterFilesystem(config)  # no connection to open for "local"
    names = fs.ls(".")
    for name in names[:5]:
        info = fs.stat(name)  # same fs instance, same (here: no-op) connection

**Paths** passed to any of these functions are resolved against
``config.local_work_dir`` (local) or ``config.remote_work_dir`` (remote)
unless already absolute.

**Auto-detection when you're already on the cluster.** If this process's
own hostname matches ``config.cluster_host`` *and* ``config.remote_work_dir``
is actually visible on the local filesystem (e.g. your code is running on
the login node itself, or on a compute node sharing NFS/Lustre with it),
``ClusterFilesystem`` switches to local operations automatically and logs
that it did so -- so filesystem calls made from code that is *already*
running on the cluster don't SSH back to the machine they're running on.

Getting Started
---------------

Basic Setup
~~~~~~~~~~~

Every example below through "Directory Analysis" uses the same ``config``,
defined once here and reused across the rest of this page (each is a real,
executed call -- against local files, so no cluster is needed to follow
along):

.. code-block:: python

    from clustrix import (
        cluster_ls, cluster_find, cluster_stat, cluster_exists,
        cluster_isdir, cluster_isfile, cluster_glob, cluster_du,
        cluster_count_files,
    )
    from clustrix.config import ClusterConfig

    # Local configuration -- every call below runs directly against this
    # checkout's own files, no cluster required.
    config = ClusterConfig(
        cluster_type="local",
        local_work_dir="."
    )

    # Remote cluster configuration -- shown for comparison; connecting to
    # it needs a real, reachable host (see the cluster-required examples
    # in :doc:`../api/filesystem`).
    remote_config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="cluster.example.edu",
        username="researcher",
        remote_work_dir="/scratch/project"
    )

Available Operations
~~~~~~~~~~~~~~~~~~~~

Clustrix provides nine core filesystem operations:

1. **cluster_ls()** - List directory contents
2. **cluster_find()** - Find files by pattern (recursive)

.. important::

   ``cluster_find()`` and ``cluster_glob()`` return paths **relative to the
   directory they searched**, not to the working directory. Passing a result
   straight to ``cluster_stat()`` therefore raises ``FileNotFoundError``
   unless you searched ``"."``. Join the search directory back on first::

       import os

       search_dir = "data/"
       for name in cluster_find("*.csv", search_dir, config):
           info = cluster_stat(os.path.join(search_dir, name), config)

3. **cluster_stat()** - Get file information (size, modified time, permissions)
4. **cluster_exists()** - Check if file/directory exists
5. **cluster_isdir()** - Check if path is a directory
6. **cluster_isfile()** - Check if path is a file
7. **cluster_glob()** - Pattern matching for files
8. **cluster_du()** - Directory usage information
9. **cluster_count_files()** - Count files matching pattern

Basic Operations
----------------

Directory Listing
~~~~~~~~~~~~~~~~~

.. code-block:: python

    # List files in current directory
    files = cluster_ls(".", config)
    print(f"Found {len(files)} items:")
    for file in files[:5]:  # Show first 5
        print(f"  - {file}")

    # List files in a specific directory
    data_files = cluster_ls("datasets/", config)

File Discovery
~~~~~~~~~~~~~~

.. code-block:: python

    # Find all Python files recursively
    py_files = cluster_find("*.py", ".", config)
    
    # Find CSV files in a specific directory
    csv_files = cluster_find("*.csv", "data/", config)
    
    # Find files with multiple extensions
    # Patterns are plain shell globs -- brace expansion is not supported,
    # so match each extension separately.
    data_files = []
    for extension in ("csv", "json", "h5"):
        data_files += cluster_find(f"*.{extension}", "datasets/", config)

File Information
~~~~~~~~~~~~~~~~

.. code-block:: python

    # Get detailed file information
    with open("example_dataset.txt", "w") as f:
        f.write("sample data\n" * 1000)

    file_info = cluster_stat("example_dataset.txt", config)
    print(f"File: {file_info.size:,} bytes")
    print(f"Modified: {file_info.modified_datetime}")
    print(f"Is directory: {file_info.is_dir}")
    print(f"Permissions: {file_info.permissions}")

File Existence Checking
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Check if results already exist
    if cluster_exists("results/output.json", config):
        print("Results already computed!")
    else:
        print("Need to run computation")

    # Check file types
    if cluster_isdir("datasets", config):
        print("datasets is a directory")
    
    if cluster_isfile("README.md", config):
        print("README.md is a file")

Pattern Matching
~~~~~~~~~~~~~~~~

.. code-block:: python

    # Use glob patterns for flexible file matching
    log_files = cluster_glob("*.log", "logs/", config)
    backup_files = (
        cluster_glob("backup_*.tar", "backups/", config)
        + cluster_glob("backup_*.zip", "backups/", config)
    )
    
    # Find all image files
    images = []
    for extension in ("png", "jpg", "jpeg", "gif"):
        images += cluster_glob(f"*.{extension}", "images/", config)

Directory Analysis
~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Get directory usage information
    usage = cluster_du("clustrix/", config)
    print(f"Total size: {usage.total_gb:.2f} GB")
    print(f"File count: {usage.file_count:,}")
    if usage.file_count > 0:
        print(f"Average file size: {usage.total_mb/usage.file_count:.1f} MB")
    
    # Count specific file types
    total_files = cluster_count_files(".", "*", config)
    python_files = cluster_count_files(".", "*.py", config)
    print(f"Python files: {python_files}/{total_files}")

Data-Driven Workflows
---------------------

The real power of filesystem utilities comes when combined with the ``@cluster`` decorator for data-driven processing:

Automatic Dataset Processing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix import cluster

    @cluster(cores=8, memory="16GB")
    def process_datasets(config):
        """Process all CSV files in the input directory."""
        import pandas as pd
        
        # Find all CSV files
        csv_files = cluster_find("*.csv", "input/", config)
        print(f"Found {len(csv_files)} CSV files to process")
        
        results = []
        for filename in csv_files:  # This loop gets parallelized automatically!
            # Get file info to make processing decisions
            file_info = cluster_stat(filename, config)
            
            if file_info.size > 100_000_000:  # > 100MB
                print(f"Processing large file: {filename}")
                # Use chunked processing for large files
                result = process_large_csv(filename, config)
            else:
                # Process smaller files normally
                result = process_small_csv(filename, config)
            
            results.append(result)
        
        return results

Conditional Processing
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    @cluster(cores=4)
    def smart_processing(config):
        """Only process files if results don't already exist."""
        
        # Check if results already exist
        if cluster_exists("results/final_output.json", config):
            print("Results already computed, loading...")
            return load_existing_results(config)
        
        # Find input files
        input_files = cluster_glob("*.dat", "input/", config)
        
        # Process only if we have data
        if len(input_files) == 0:
            raise ValueError("No input files found!")
        
        return process_files(input_files, config)

Adaptive Resource Allocation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def determine_resources(config):
        """Determine resource requirements based on data size."""
        
        # Count total files
        total_files = cluster_count_files("input/", "*.csv", config)
        
        # Get total data size
        usage = cluster_du("input/", config)
        
        # Adaptive resource allocation
        if usage.total_gb > 100:
            return {"cores": 16, "memory": "64GB", "time": "08:00:00"}
        elif usage.total_gb > 10:
            return {"cores": 8, "memory": "32GB", "time": "04:00:00"}
        else:
            return {"cores": 4, "memory": "16GB", "time": "02:00:00"}

    # Use adaptive resources
    resources = determine_resources(config)
    
    @cluster(**resources)
    def process_data(config):
        # Processing logic here
        pass

Monitoring and Validation
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    @cluster(cores=8)
    def monitored_processing(config):
        """Processing with built-in monitoring."""
        import time

        # Initial state
        initial_usage = cluster_du(".", config)
        print(f"Starting with {initial_usage.total_gb:.1f} GB")
        
        # Find and validate input files
        input_files = cluster_find("*.raw", "input/", config)
        
        # Anything older than a day is left over from a previous run
        cutoff_time = time.time() - 24 * 60 * 60

        valid_files = []
        for filename in input_files:
            file_info = cluster_stat(filename, config)
            
            # Validate file size and age
            if file_info.size > 1000 and file_info.modified > cutoff_time:
                valid_files.append(filename)
        
        print(f"Validated {len(valid_files)} out of {len(input_files)} files")
        
        # Process valid files
        results = process_files(valid_files, config)
        
        # Check final state
        final_usage = cluster_du(".", config)
        added_data = final_usage.total_gb - initial_usage.total_gb
        print(f"Generated {added_data:.1f} GB of new data")
        
        return results

Advanced Patterns
-----------------

Working with Large Datasets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    @cluster(cores=16, memory="64GB")
    def process_large_dataset(config):
        """Handle large datasets efficiently."""
        
        # Find all data files
        data_files = cluster_glob("*.h5", "datasets/", config)
        
        # Group files by size for efficient processing
        small_files = []
        large_files = []
        
        for filename in data_files:
            file_info = cluster_stat(filename, config)
            if file_info.size > 1_000_000_000:  # > 1GB
                large_files.append(filename)
            else:
                small_files.append(filename)
        
        # Process small files in batches
        small_results = process_file_batch(small_files, config)
        
        # Process large files individually
        large_results = []
        for filename in large_files:
            result = process_single_large_file(filename, config)
            large_results.append(result)
        
        return small_results + large_results

Multi-Directory Processing
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    @cluster(cores=12)
    def process_multiple_directories(config):
        """Process files from multiple directories."""
        
        # Find all subdirectories with data
        all_dirs = cluster_ls("data/", config)
        data_dirs = [d for d in all_dirs if cluster_isdir(f"data/{d}", config)]
        
        results = {}
        for dir_name in data_dirs:
            dir_path = f"data/{dir_name}"
            
            # Check if this directory has CSV files
            csv_files = cluster_find("*.csv", dir_path, config)
            if len(csv_files) > 0:
                print(f"Processing {len(csv_files)} files in {dir_name}")
                results[dir_name] = process_directory(dir_path, config)
        
        return results

File Synchronization Patterns
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def sync_processing_state(local_config, remote_config):
        """Synchronize processing state between local and remote."""
        
        # Check what files we have locally
        local_files = set(cluster_find("*.processed", "output/", local_config))
        
        # Check what files exist remotely
        remote_files = set(cluster_find("*.processed", "output/", remote_config))
        
        # Find files that need to be processed
        local_raw = set(cluster_find("*.raw", "input/", local_config))
        remote_raw = set(cluster_find("*.raw", "input/", remote_config))
        
        # Determine what needs processing
        need_processing = (local_raw | remote_raw) - (local_files | remote_files)
        
        return list(need_processing)

Best Practices
--------------

Configuration Management
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Use environment-specific configs
    def get_config():
        import os
        
        if os.getenv("CLUSTRIX_ENV") == "production":
            return ClusterConfig(
                cluster_type="slurm",
                cluster_host="prod-cluster.edu",
                username="prod_user",
                remote_work_dir="/scratch/production"
            )
        else:
            return ClusterConfig(
                cluster_type="local",
                local_work_dir="./dev_data"
            )

Error Handling
~~~~~~~~~~~~~~

.. code-block:: python

    def safe_file_operations(config):
        """Demonstrate proper error handling."""
        
        try:
            # Check if directory exists before listing
            if not cluster_exists("data/", config):
                print("Data directory doesn't exist")
                return []
            
            # Safe file listing
            files = cluster_ls("data/", config)
            
            # Validate files before processing
            valid_files = []
            for filename in files:
                try:
                    file_info = cluster_stat(f"data/{filename}", config)
                    if file_info.size > 0:  # Non-empty files only
                        valid_files.append(filename)
                except FileNotFoundError:
                    print(f"File disappeared: {filename}")
                    continue
            
            return valid_files
            
        except Exception as e:
            print(f"Error in file operations: {e}")
            return []

Performance Optimization
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def optimized_file_discovery(config):
        """Optimize file discovery for large directories."""
        
        # Use count to check if directory has files before listing
        file_count = cluster_count_files("large_directory/", "*", config)
        
        if file_count == 0:
            return []
        
        if file_count > 10000:
            # For very large directories, use pattern-specific searches
            csv_files = cluster_find("*.csv", "large_directory/", config)
            json_files = cluster_find("*.json", "large_directory/", config)
            return csv_files + json_files
        else:
            # For smaller directories, list all and filter
            all_files = cluster_ls("large_directory/", config)
            return [f for f in all_files if f.endswith(('.csv', '.json'))]

Integration Examples
--------------------

With Pandas
~~~~~~~~~~~

.. code-block:: python

    @cluster(cores=8)
    def pandas_integration(config):
        """Integrate filesystem utilities with Pandas."""
        import pandas as pd
        
        # Find all CSV files
        csv_files = cluster_find("*.csv", "data/", config)
        
        dataframes = []
        for filename in csv_files:
            # Check file size to determine read strategy
            file_info = cluster_stat(filename, config)
            
            if file_info.size > 500_000_000:  # > 500MB
                # Use chunked reading for large files
                chunks = pd.read_csv(filename, chunksize=10000)
                df = pd.concat([chunk.sample(frac=0.1) for chunk in chunks])
            else:
                df = pd.read_csv(filename)
            
            dataframes.append(df)
        
        return pd.concat(dataframes, ignore_index=True)

With NumPy/HDF5
~~~~~~~~~~~~~~~

.. code-block:: python

    @cluster(cores=4, memory="32GB")
    def numpy_hdf5_integration(config):
        """Work with NumPy arrays and HDF5 files."""
        import numpy as np
        import h5py
        
        # Find all HDF5 files
        h5_files = cluster_find("*.h5", "arrays/", config)
        
        total_arrays = 0
        total_size = 0
        
        for filename in h5_files:
            file_info = cluster_stat(filename, config)
            total_size += file_info.size
            
            # Count arrays in each file (this would work locally)
            if config.cluster_type == "local":
                with h5py.File(filename, 'r') as f:
                    total_arrays += len(f.keys())
        
        print(f"Found {total_arrays} arrays in {len(h5_files)} files")
        print(f"Total size: {total_size / 1e9:.1f} GB")
        
        return {"files": len(h5_files), "arrays": total_arrays, "size_gb": total_size / 1e9}

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

**SSH Connection Problems**

.. code-block:: python

    # Test SSH connectivity
    try:
        files = cluster_ls(".", remote_config)
        print("SSH connection working")
    except Exception as e:
        print(f"SSH connection failed: {e}")
        # Check your SSH keys, hostname, username

**Path Issues**

.. code-block:: python

    # Always use relative paths or properly configured absolute paths
    # Good:
    files = cluster_ls("data/", config)
    
    # Be careful with absolute paths:
    files = cluster_ls("/scratch/user/data/", config)  # Must exist on cluster

**Performance Issues**

.. code-block:: python

    # For large directories, avoid listing all files
    # Instead of:
    all_files = cluster_ls("huge_directory/", config)  # Slow!
    
    # Use:
    csv_files = cluster_find("*.csv", "huge_directory/", config)  # Faster!

Next Steps
----------

- Explore the :doc:`../api/filesystem` API reference for detailed function documentation
- Check out the complete tutorial at ``examples/filesystem_tutorial.py``
- Learn about :doc:`../api/config` for advanced configuration options
- See :doc:`../api/decorator` for more ``@cluster`` decorator patterns