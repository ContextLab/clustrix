File Packaging System
=====================

.. currentmodule:: clustrix.file_packaging

Every member of this module is documented explicitly below (grouped by
purpose). A blanket ``automodule:: :members:`` is deliberately not used here: this
project's global ``autodoc_default_options`` sets ``members: True``, so an
``automodule`` directive combined with the explicit per-member directives
below would document every class and function twice.

Overview
--------

:class:`FilePackager` reads a function's source with ``ast``, works out which
local modules and data files it refers to, and writes a ZIP archive containing
them together with a metadata record.

.. warning::

   **Nothing in the execution path calls this module.** A ``@cluster`` job is
   shipped by :func:`clustrix.utils.serialize_function`, which pickles the
   function by value with dill and cloudpickle; see :doc:`../execution_model`.
   ``file_packaging`` is importable from the top-level ``clustrix`` namespace
   and it works when you call it, and no decorator, executor or scheduler
   reaches it. You can confirm this yourself::

       grep -rn "FilePackager\|package_function" clustrix/decorator.py \
           clustrix/executor_core.py clustrix/executor_connections.py \
           clustrix/utils.py

   That search returns nothing. Treat this page as a reference for a component
   you may call directly, not as a description of what happens when you submit
   a job. To send data to a worker, use :func:`clustrix.data_package`, which
   takes the files you name rather than the files this module's analysis
   guesses at.

What it does when you call it
-----------------------------

1. **Dependency analysis** -- :func:`clustrix.dependency_analysis.analyze_function_dependencies`
   walks the function's AST.
2. **File collection** -- local modules and referenced data files are gathered.
3. **Package creation** -- a ZIP archive is written with the collected files
   and a metadata record.

Step 4 in the obvious sequence -- putting that archive on a cluster -- has no
implementation here. Transport lives in
``clustrix/executor_connections.py`` and is used for the pickled payload only.

Core Components
---------------

File Packager
~~~~~~~~~~~~~

.. autoclass:: clustrix.file_packaging.FilePackager
   :members:
   :undoc-members:
   :show-inheritance:

Data Structures
~~~~~~~~~~~~~~~

.. autoclass:: clustrix.file_packaging.PackageInfo
   :members:
   :undoc-members:

.. autoclass:: clustrix.file_packaging.ExecutionContext
   :members:
   :undoc-members:

Convenience Functions
~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: clustrix.file_packaging.package_function_for_execution
.. autofunction:: clustrix.file_packaging.create_execution_context

Usage Examples
--------------

Basic Function Packaging
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix.file_packaging import package_function_for_execution
    from clustrix.config import ClusterConfig

    def simple_analysis():
        """A simple function to be executed remotely."""
        import math
        
        result = math.sqrt(42)
        return result

    # Configure target cluster
    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="cluster.example.edu",
        username="researcher",
        remote_work_dir="/scratch/project"
    )

    # Package the function
    package_info = package_function_for_execution(
        func=simple_analysis,
        cluster_config=config,
        func_args=(),
        func_kwargs={}
    )

    print(f"Package created: {package_info.package_path}")
    print(f"Package ID: {package_info.package_id}")
    print(f"Size: {package_info.size_bytes:,} bytes")

Function with Local Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def helper_function(data):
        """Helper function for data processing."""
        return [x * 2 for x in data if x > 0]

    def process_data(filename):
        """Process a single data file."""
        with open(filename, 'r') as f:
            numbers = [int(line.strip()) for line in f]
        return helper_function(numbers)

    def main_analysis():
        """Main function that uses local dependencies."""
        from clustrix import cluster_find
        
        # Find all data files
        data_files = cluster_find("*.txt", "input/")
        
        results = []
        for filename in data_files:
            result = process_data(filename)
            results.append(sum(result))
        
        return results

    # Add local functions to global scope
    main_analysis.__globals__['helper_function'] = helper_function
    main_analysis.__globals__['process_data'] = process_data

    # Package with dependencies
    package_info = package_function_for_execution(
        func=main_analysis,
        cluster_config=config,
        func_args=(),
        func_kwargs={}
    )

    # Check dependency detection
    print(f"Local functions detected: {package_info.metadata['local_function_count']}")

Filesystem-Intensive Function
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def filesystem_analysis():
        """Function that uses cluster filesystem extensively."""
        from clustrix import (
            cluster_ls, cluster_find, cluster_stat,
            cluster_exists, cluster_du, cluster_count_files
        )
        
        # Directory analysis
        all_files = cluster_ls("data/")
        total_files = cluster_count_files("data/", "*")
        usage = cluster_du("data/")
        
        # Find specific file types
        csv_files = cluster_find("*.csv", "data/")
        json_files = cluster_find("*.json", "data/")
        
        # Analyze file sizes
        large_files = []
        for filename in all_files:
            full_path = f"data/{filename}"
            if cluster_exists(full_path):
                file_info = cluster_stat(full_path)
                if file_info.size > 1_000_000:  # > 1MB
                    large_files.append({
                        'name': filename,
                        'size': file_info.size,
                        'size_mb': file_info.size / 1_000_000
                    })
        
        return {
            'total_files': total_files,
            'total_size_gb': usage.total_gb,
            'csv_files': len(csv_files),
            'json_files': len(json_files),
            'large_files': large_files
        }

    # Package filesystem-intensive function
    package_info = package_function_for_execution(
        func=filesystem_analysis,
        cluster_config=config,
        func_args=(),
        func_kwargs={}
    )

    # Check filesystem integration
    print(f"Requires cluster filesystem: {package_info.metadata['has_filesystem_ops']}")

Function with External Packages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def scientific_analysis():
        """Function that requires external packages."""
        import numpy as np
        import pandas as pd
        import scipy.stats as stats
        from clustrix import cluster_find
        
        # Find CSV data files
        data_files = cluster_find("*.csv", "experiments/")
        
        results = []
        for filename in data_files:
            # Load data with pandas
            df = pd.read_csv(filename)
            
            # Perform statistical analysis
            numerical_cols = df.select_dtypes(include=[np.number]).columns
            
            for col in numerical_cols:
                data = df[col].dropna()
                if len(data) > 10:
                    # Statistical tests
                    normality_p = stats.shapiro(data)[1]
                    mean_val = np.mean(data)
                    std_val = np.std(data)
                    
                    results.append({
                        'file': filename,
                        'column': col,
                        'mean': mean_val,
                        'std': std_val,
                        'normal': normality_p > 0.05
                    })
        
        return results

    # Package with external dependencies
    package_info = package_function_for_execution(
        func=scientific_analysis,
        cluster_config=config,
        func_args=(),
        func_kwargs={}
    )

    # External packages will be automatically installed on the remote cluster

Advanced Packaging with Custom Context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix.file_packaging import FilePackager, create_execution_context

    def custom_analysis(data_dir, output_format="json"):
        """Function with custom arguments."""
        import json
        from clustrix import cluster_find, cluster_stat
        
        files = cluster_find("*.dat", data_dir)
        results = {}
        
        for filename in files:
            file_info = cluster_stat(filename)
            results[filename] = {
                'size': file_info.size,
                'modified': file_info.modified
            }
        
        if output_format == "json":
            return json.dumps(results, indent=2)
        else:
            return results

    # Create custom execution context
    context = create_execution_context(
        cluster_config=config,
        func_args=("experiments/",),
        func_kwargs={"output_format": "dict"}
    )

    # Use FilePackager directly for more control
    packager = FilePackager()
    package_info = packager.package_function(custom_analysis, context)

    print(f"Custom package created: {package_info.package_path}")

Package Inspection
~~~~~~~~~~~~~~~~~~

.. code-block:: python

    import zipfile
    import json

    def inspect_package(package_path):
        """Inspect the contents of a package."""
        with zipfile.ZipFile(package_path, 'r') as zf:
            print("Package contents:")
            for filename in zf.namelist():
                info = zf.getinfo(filename)
                print(f"  {filename} ({info.file_size:,} bytes)")
            
            # Read metadata
            metadata_content = zf.read("metadata.json").decode()
            metadata = json.loads(metadata_content)
            
            print("\nFunction metadata:")
            print(f"  Name: {metadata['function_info']['name']}")
            print(f"  External dependencies: {metadata['dependencies']['external_dependencies']}")
            print(f"  Filesystem operations: {metadata['dependencies']['requires_cluster_filesystem']}")
            
            # Show detected imports
            if metadata['dependencies']['imports']:
                print("\nDetected imports:")
                for imp in metadata['dependencies']['imports']:
                    print(f"  {imp['module']} ({'from' if imp['is_from_import'] else 'direct'})")
            
            # Show filesystem calls
            if metadata['dependencies']['filesystem_calls']:
                print("\nFilesystem operations:")
                for call in metadata['dependencies']['filesystem_calls']:
                    print(f"  {call['function']}() on line {call['lineno']}")

    # Inspect a created package
    inspect_package(package_info.package_path)

Package Deployment and Execution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix.executor import ClusterExecutor

    def complete_workflow():
        """Complete workflow from packaging to execution."""
        
        def analysis_function():
            from clustrix import cluster_ls, cluster_count_files
            
            files = cluster_ls(".")
            file_count = cluster_count_files(".", "*.py")
            
            return {
                'total_files': len(files),
                'python_files': file_count
            }
        
        # 1. Package the function
        package_info = package_function_for_execution(
            func=analysis_function,
            cluster_config=config,
            func_args=(),
            func_kwargs={}
        )
        print(f"Function packaged: {package_info.package_id}")
        
        # 2. Execute via ClusterExecutor (this would normally be done by @cluster decorator)
        executor = ClusterExecutor(config)
        
        # The executor automatically handles package deployment and execution
        # This is normally done internally by the @cluster decorator
        
        return package_info

Error Handling
--------------

.. code-block:: python

    def handle_packaging_errors():
        """Examples of error handling in packaging."""
        
        # Built-in functions cannot be packaged
        try:
            package_info = package_function_for_execution(
                func=len,  # Built-in function
                cluster_config=config,
                func_args=(),
                func_kwargs={}
            )
        except ValueError as e:
            print(f"Cannot package built-in function: {e}")
        
        # Functions with missing dependencies
        def function_with_missing_deps():
            from clustrix import cluster_ls
            
            files = cluster_ls(".")
            # This would fail at runtime, but packaging succeeds
            return undefined_function(files)
        
        # Packaging succeeds, but runtime execution would fail
        package_info = package_function_for_execution(
            func=function_with_missing_deps,
            cluster_config=config,
            func_args=(),
            func_kwargs={}
        )
        print("Package created despite missing dependencies")

Configuration and Options
-------------------------

Where packages are written
~~~~~~~~~~~~~~~~~~~~~~~~~~

Packages are written to a fresh ``tempfile.mkdtemp(prefix="clustrix_packages_")``
directory. There is no environment variable that redirects this: no
``CLUSTRIX_PACKAGE_DIR``, ``CLUSTRIX_REMOTE_PYTHON_PATH`` or
``CLUSTRIX_DEBUG_PACKAGING`` is read anywhere in the codebase. Use
``package_info.package_path`` to find the archive that was just built, and
``logging.basicConfig(level=logging.DEBUG)`` to see what the packager is doing.

Package Cleanup
~~~~~~~~~~~~~~~

.. code-block:: python

    import os
    import glob

    def cleanup_packages():
        """Clean up old packages."""
        import tempfile

        package_pattern = os.path.join(
            tempfile.gettempdir(), "clustrix_packages_*", "clustrix_package_*.zip"
        )
        old_packages = glob.glob(package_pattern)
        
        for package_path in old_packages:
            # Check if package is older than 1 hour
            import time
            if time.time() - os.path.getmtime(package_path) > 3600:
                os.remove(package_path)
                print(f"Removed old package: {package_path}")

Performance Considerations
--------------------------

1. **Package Size**: Large packages take longer to transfer to remote clusters
2. **Dependency Analysis**: Complex functions take longer to analyze
3. **Caching**: Identical functions produce identical package IDs for caching
4. **Network Transfer**: Consider package size for remote clusters with slow networks
5. **Storage**: Packages are stored locally and remotely; clean up periodically

Best Practices
--------------

1. **Function Design**: Keep functions focused and minimize external dependencies
2. **Import Organization**: Use standard import patterns for better detection
3. **Local Functions**: Ensure helper functions are in the main function's global scope
4. **File Paths**: Use relative paths for better portability
5. **Error Handling**: Design functions to handle missing files gracefully
6. **Testing**: Test functions locally before remote packaging
7. **Documentation**: Document function dependencies and requirements

Integration with @cluster Decorator
-----------------------------------

The packaging system is automatically used by the @cluster decorator:

.. code-block:: python

    from clustrix import cluster, configure

    # cluster_host is a configuration setting, not a decorator argument;
    # set it with clustrix.configure(cluster_host="cluster.example.edu"). Explicit
    # and self-contained here so this example runs locally regardless of
    # whatever configuration was active before it.
    configure(cluster_type="local", cluster_host=None)

    @cluster(cores=8)
    def automated_packaging():
        """This function will be automatically packaged and executed remotely."""
        from clustrix import cluster_find, cluster_stat
        
        data_files = cluster_find("*.csv", "data/")
        
        total_size = 0
        # Sequential -- see the auto-parallelization contract in limitations.
        for filename in data_files:
            file_info = cluster_stat(filename)
            total_size += file_info.size
        
        return total_size

    # The decorator handles packaging, deployment, and execution automatically
    result = automated_packaging()
    print(f"Total data size: {result:,} bytes")

Troubleshooting
---------------

Common Issues
~~~~~~~~~~~~~

1. **Import Errors**: Ensure all required packages are available on the remote cluster
2. **Path Issues**: Use relative paths or cluster filesystem functions
3. **Missing Dependencies**: Add local functions to the main function's global scope
4. **Large Packages**: Consider breaking large functions into smaller components
5. **Permission Issues**: Ensure write access to package storage directories

Debug Mode
~~~~~~~~~~

.. code-block:: python

    import logging
    from clustrix.file_packaging import package_function_for_execution
    from clustrix.config import ClusterConfig

    # Enable debug logging
    logging.basicConfig(level=logging.DEBUG)

    def your_function():
        return 42

    config = ClusterConfig(cluster_type="slurm", cluster_host="cluster.example.edu")

    # Package function with detailed logging
    package_info = package_function_for_execution(
        func=your_function,
        cluster_config=config,
        func_args=(),
        func_kwargs={}
    )

See Also
--------

- :doc:`dependency_analysis` - Dependency detection and analysis
- :doc:`filesystem` - Cluster filesystem utilities used by packaged functions
- :doc:`decorator` - The @cluster decorator that uses the packaging system
- :doc:`config` - Configuration management for clusters
- :doc:`../tutorials/filesystem_tutorial` - Complete examples using the packaging system