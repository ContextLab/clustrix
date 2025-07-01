Dependency Analysis
===================

.. automodule:: clustrix.dependency_analysis
   :members:
   :undoc-members:
   :show-inheritance:

Overview
--------

The dependency analysis module provides automatic detection and analysis of function dependencies for the packaging system. This enables seamless remote execution of locally-defined functions with their complete dependency context.

Key Features
------------

- **AST-Based Analysis**: Uses Python's Abstract Syntax Tree for accurate dependency detection
- **Import Detection**: Identifies all import statements and their usage patterns
- **Local Function Detection**: Finds calls to user-defined functions in the same scope
- **Filesystem Call Detection**: Identifies cluster filesystem operations for proper setup
- **File Reference Analysis**: Detects file operations and data dependencies
- **Loop Analysis**: Analyzes loops for automatic parallelization opportunities

Core Components
---------------

Dependency Analysis
~~~~~~~~~~~~~~~~~~~

.. autoclass:: clustrix.dependency_analysis.DependencyAnalyzer
   :members:
   :undoc-members:
   :show-inheritance:

.. autoclass:: clustrix.dependency_analysis.DependencyGraph
   :members:
   :undoc-members:
   :show-inheritance:

Loop Analysis
~~~~~~~~~~~~~

.. autoclass:: clustrix.dependency_analysis.LoopAnalyzer
   :members:
   :undoc-members:
   :show-inheritance:

Data Structures
---------------

.. autoclass:: clustrix.dependency_analysis.ImportInfo
   :members:
   :undoc-members:

.. autoclass:: clustrix.dependency_analysis.LocalFunctionCall
   :members:
   :undoc-members:

.. autoclass:: clustrix.dependency_analysis.FilesystemCall
   :members:
   :undoc-members:

.. autoclass:: clustrix.dependency_analysis.FileReference
   :members:
   :undoc-members:

Convenience Functions
---------------------

.. autofunction:: clustrix.dependency_analysis.analyze_function_dependencies
.. autofunction:: clustrix.dependency_analysis.analyze_function_loops

Usage Examples
--------------

Basic Dependency Analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix.dependency_analysis import analyze_function_dependencies

    def data_processing_function():
        import pandas as pd
        from clustrix import cluster_find, cluster_stat
        
        # Find CSV files
        csv_files = cluster_find("*.csv", "data/")
        
        results = []
        for filename in csv_files:
            file_info = cluster_stat(filename)
            if file_info.size > 1000000:  # Large files
                df = pd.read_csv(filename, chunksize=10000)
                processed = process_large_file(df)
            else:
                df = pd.read_csv(filename)
                processed = process_small_file(df)
            results.append(processed)
        
        return results

    # Analyze the function's dependencies
    deps = analyze_function_dependencies(data_processing_function)
    
    # Inspect detected imports
    for imp in deps.imports:
        print(f"Import: {imp.module} ({'from import' if imp.is_from_import else 'direct'})")
    
    # Check filesystem operations
    if deps.requires_cluster_filesystem:
        for fs_call in deps.filesystem_calls:
            print(f"Filesystem call: {fs_call.function}({', '.join(fs_call.args)})")
    
    # Check local function dependencies
    for local_call in deps.local_function_calls:
        print(f"Local function: {local_call.function_name}")

Advanced Analysis
~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix.dependency_analysis import DependencyAnalyzer, LoopAnalyzer

    def complex_analysis_function():
        import numpy as np
        import scipy.stats as stats
        from pathlib import Path
        from clustrix import cluster_ls, cluster_glob
        
        # Multiple loops for potential parallelization
        data_files = cluster_glob("*.dat", "experiments/")
        results = {}
        
        for experiment_dir in ["exp1", "exp2", "exp3"]:
            experiment_files = cluster_ls(experiment_dir)
            
            for data_file in experiment_files:
                if data_file.endswith(".dat"):
                    # Process data file
                    data = np.loadtxt(data_file)
                    result = stats.describe(data)
                    results[data_file] = result
        
        return results

    # Detailed analysis
    analyzer = DependencyAnalyzer()
    deps = analyzer.analyze_function(complex_analysis_function)
    
    # Check import types
    import_types = {}
    for imp in deps.imports:
        import_types[imp.module] = "from" if imp.is_from_import else "direct"
    print("Import types:", import_types)
    
    # Analyze loops for parallelization
    loop_analyzer = LoopAnalyzer()
    loops = loop_analyzer.analyze_loops(deps.ast_tree)
    
    for i, loop in enumerate(loops):
        print(f"Loop {i+1}: {loop['type']} loop")
        print(f"  Target: {loop['target']}")
        print(f"  Parallelizable: {loop['is_parallelizable']}")

Filesystem Integration Analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def filesystem_heavy_function():
        from clustrix import (
            cluster_ls, cluster_find, cluster_stat, 
            cluster_exists, cluster_du
        )
        
        # Multiple filesystem operations
        all_files = cluster_ls("data/")
        large_files = []
        
        for filename in all_files:
            if cluster_exists(f"data/{filename}"):
                file_info = cluster_stat(f"data/{filename}")
                if file_info.size > 100_000:
                    large_files.append(filename)
        
        # Directory analysis
        usage = cluster_du("data/")
        
        # Pattern-based search
        config_files = cluster_find("*.json", "config/")
        
        return {
            "large_files": large_files,
            "disk_usage": usage.total_mb,
            "config_files": config_files
        }

    deps = analyze_function_dependencies(filesystem_heavy_function)
    
    # Filesystem operations detected
    print(f"Requires cluster filesystem: {deps.requires_cluster_filesystem}")
    print(f"Filesystem calls: {len(deps.filesystem_calls)}")
    
    for call in deps.filesystem_calls:
        print(f"  {call.function} on line {call.lineno}")

Local Function Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def helper_function(data):
        """Helper function for data processing."""
        return [x * 2 for x in data]

    def another_helper(data):
        """Another helper function."""
        return sum(data) / len(data)

    def main_function():
        """Main function that uses local helpers."""
        from clustrix import cluster_find
        
        data_files = cluster_find("*.txt", "input/")
        results = []
        
        for filename in data_files:
            with open(filename, 'r') as f:
                numbers = [int(line.strip()) for line in f]
            
            # Use local helper functions
            doubled = helper_function(numbers)
            average = another_helper(doubled)
            results.append(average)
        
        return results

    # Add helpers to function's global scope
    main_function.__globals__['helper_function'] = helper_function
    main_function.__globals__['another_helper'] = another_helper

    deps = analyze_function_dependencies(main_function)
    
    # Local dependencies detected
    for local_call in deps.local_function_calls:
        print(f"Local function: {local_call.function_name}")
        print(f"  Defined in scope: {local_call.defined_in_scope}")
        print(f"  Call on line: {local_call.lineno}")

File Reference Detection
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def file_operations_function():
        import json
        from clustrix import cluster_stat
        
        # Direct file operations
        with open("config.json", "r") as f:
            config = json.load(f)
        
        # String literals that look like paths
        log_file = "logs/application.log"
        data_dir = "/scratch/datasets/"
        
        # Cluster filesystem operations
        if cluster_exists("results/output.txt"):
            result_info = cluster_stat("results/output.txt")
            return result_info.size
        
        return 0

    deps = analyze_function_dependencies(file_operations_function)
    
    # File references detected
    for file_ref in deps.file_references:
        print(f"File reference: {file_ref.path}")
        print(f"  Operation: {file_ref.operation}")
        print(f"  Line: {file_ref.lineno}")
        print(f"  Accessible: {file_ref.accessible}")

Error Handling
--------------

.. code-block:: python

    def problematic_function():
        # This will fail analysis
        return len([1, 2, 3])

    try:
        deps = analyze_function_dependencies(len)  # Built-in function
    except ValueError as e:
        print(f"Analysis failed: {e}")

    # Function with no dependencies
    def simple_function():
        return 42

    deps = analyze_function_dependencies(simple_function)
    assert len(deps.imports) == 0
    assert len(deps.local_function_calls) == 0

Best Practices
--------------

1. **Function Scope**: Ensure local helper functions are in the global scope of the main function
2. **Import Patterns**: Use standard import patterns for better detection
3. **File Paths**: Use relative paths for better portability
4. **Filesystem Operations**: Prefer cluster filesystem functions for remote compatibility
5. **Error Handling**: Be prepared for analysis failures with complex code patterns

Integration with Packaging
---------------------------

The dependency analysis is automatically used by the file packaging system:

.. code-block:: python

    from clustrix.file_packaging import package_function_for_execution
    from clustrix.config import ClusterConfig

    def analyzed_function():
        import pandas as pd
        from clustrix import cluster_find
        
        csv_files = cluster_find("*.csv", "data/")
        return len(csv_files)

    config = ClusterConfig(cluster_type="slurm", cluster_host="cluster.edu")
    
    # Dependency analysis happens automatically during packaging
    package_info = package_function_for_execution(
        func=analyzed_function,
        cluster_config=config,
        func_args=(),
        func_kwargs={}
    )
    
    # Access the dependency analysis results
    print(f"Package ID: {package_info.package_id}")
    print(f"Dependencies detected: {package_info.metadata['has_dependencies']}")

Limitations
-----------

- **Dynamic Imports**: Cannot detect imports created at runtime
- **Method Calls**: Currently detects function calls but not method calls on cluster filesystem objects
- **Complex Control Flow**: May miss dependencies in complex conditional or dynamic code
- **Eval/Exec**: Cannot analyze dynamically executed code

See Also
--------

- :doc:`file_packaging` - File packaging system that uses dependency analysis
- :doc:`filesystem` - Cluster filesystem utilities
- :doc:`decorator` - The @cluster decorator that triggers the packaging system
- :doc:`../tutorials/filesystem_tutorial` - Complete examples of filesystem operations