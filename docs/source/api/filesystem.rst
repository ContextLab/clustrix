Filesystem Utilities
====================

.. currentmodule:: clustrix.filesystem

Every member of this module is documented explicitly below (grouped by
purpose), following the same pattern used in :doc:`cost_monitoring`. A
blanket ``automodule:: :members:`` is deliberately not used here: this
project's global ``autodoc_default_options`` sets ``members: True``, so an
``automodule`` directive combined with the explicit per-member directives
below would document every class and function twice.

Overview
--------

The filesystem utilities module provides a unified interface for filesystem operations that work seamlessly across local and remote clusters. All operations use the same API regardless of whether you're working locally or on a remote cluster.

Key Features
------------

- **Unified API**: Same function calls work locally and remotely
- **Automatic SSH Management**: Transparent connection handling for remote operations
- **Path Normalization**: Consistent path handling across platforms
- **Data Structures**: Structured returns via `FileInfo` and `DiskUsage` classes
- **Config-Driven**: Uses `ClusterConfig` to determine local vs remote execution

Core Functions
--------------

Directory Operations
~~~~~~~~~~~~~~~~~~~~

.. autofunction:: clustrix.cluster_ls
.. autofunction:: clustrix.cluster_find
.. autofunction:: clustrix.cluster_glob
.. autofunction:: clustrix.cluster_count_files

File Operations
~~~~~~~~~~~~~~~

.. autofunction:: clustrix.cluster_stat
.. autofunction:: clustrix.cluster_exists
.. autofunction:: clustrix.cluster_isdir
.. autofunction:: clustrix.cluster_isfile

Storage Operations
~~~~~~~~~~~~~~~~~~

.. autofunction:: clustrix.cluster_du

Data Classes
------------

.. autoclass:: clustrix.filesystem.FileInfo
   :members:
   :undoc-members:

.. autoclass:: clustrix.filesystem.DiskUsage
   :members:
   :undoc-members:

Core Implementation
-------------------

.. autoclass:: clustrix.filesystem.ClusterFilesystem
   :members:
   :undoc-members:
   :show-inheritance:

Usage Examples
--------------

Basic Operations
~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix import cluster_ls, cluster_find, cluster_stat
    from clustrix.config import ClusterConfig

    # Configure for remote cluster
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="cluster.edu",
        username="researcher",
        remote_work_dir="/scratch/project"
    )

    # List directory contents
    files = cluster_ls("data/", config)
    
    # Find CSV files recursively
    csv_files = cluster_find("*.csv", "datasets/", config)
    
    # Get file information
    file_info = cluster_stat("large_dataset.h5", config)
    print(f"Size: {file_info.size:,} bytes")

Data-Driven Workflows
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix import cluster, cluster_glob, cluster_stat

    @cluster(cores=8)
    def process_datasets(config):
        # Find all data files on the cluster
        data_files = cluster_glob("*.csv", "input/", config)
        
        results = []
        for filename in data_files:  # Loop gets parallelized automatically
            # Check file size before processing
            file_info = cluster_stat(filename, config)
            if file_info.size > 100_000_000:  # Large files
                result = process_large_file(filename, config)
            else:
                result = process_small_file(filename, config)
            results.append(result)
        
        return results

Local vs Remote Operations
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Local configuration
    local_config = ClusterConfig(cluster_type="local", local_work_dir="./data")
    
    # Remote configuration  
    remote_config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="cluster.edu",
        username="researcher"
    )
    
    # Same function calls work for both
    local_files = cluster_ls(".", local_config)
    remote_files = cluster_ls(".", remote_config)

Pattern Matching
~~~~~~~~~~~~~~~~

.. code-block:: python

    # Find all Python files
    py_files = cluster_find("*.py", "src/", config)
    
    # Use glob patterns
    # Patterns are plain shell globs -- brace expansion is not supported,
    # so match each extension separately.
    data_files = (
        cluster_glob("data_*.csv", "input/", config)
        + cluster_glob("data_*.json", "input/", config)
    )
    
    # Count files by type
    total_files = cluster_count_files(".", "*", config)
    python_files = cluster_count_files(".", "*.py", config)

Directory Usage Analysis
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # Get directory usage information
    usage = cluster_du("/scratch/project", config)
    print(f"Total size: {usage.total_gb:.2f} GB")
    print(f"File count: {usage.file_count:,}")
    print(f"Average file size: {usage.total_mb/usage.file_count:.1f} MB")

Error Handling
--------------

.. code-block:: python

    try:
        file_info = cluster_stat("nonexistent.txt", config)
    except FileNotFoundError:
        print("File does not exist")
    
    # Safe existence check
    if cluster_exists("results/output.json", config):
        file_info = cluster_stat("results/output.json", config)

Best Practices
--------------

1. **Use config-driven execution**: Pass `ClusterConfig` objects to enable local/remote switching
2. **Check file existence**: Use `cluster_exists()` before operations that assume file presence
3. **Handle large directories carefully**: Remote operations on large directories may be slow
4. **Use appropriate patterns**: Leverage `cluster_find()` and `cluster_glob()` for efficient file discovery
5. **Cache results**: Store file listings locally when processing many files

See Also
--------

- :doc:`../tutorials/filesystem_tutorial` - Comprehensive tutorial with examples
- :doc:`config` - Configuration management
- :doc:`decorator` - Using filesystem utilities with the @cluster decorator