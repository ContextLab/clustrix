PBS/Torque Cluster Tutorial
===========================

This tutorial demonstrates how to use Clustrix with PBS (Portable Batch System) and Torque clusters, commonly used in academic and research computing environments.

Prerequisites
------------

1. Access to a PBS/Torque cluster
2. SSH key setup (see :doc:`../ssh_setup`)
3. Clustrix installed with: ``pip install clustrix``

Configuration Options
--------------------

**Option 1: Interactive Widget (Recommended for Jupyter)**

For Jupyter notebook users, use the interactive configuration widget:

.. code-block:: python

   import clustrix  # Auto-loads the magic command
   
   # Use the magic command to open the configuration widget
   %%clusterfy
   # Interactive widget appears with PBS/Torque templates and GUI configuration

**Option 2: Programmatic Configuration**

Configure Clustrix programmatically for your PBS cluster:

.. code-block:: python

   from clustrix import configure
   
   configure(
       cluster_type="pbs",
       cluster_host="pbs.university.edu",
       username="your_username",
       key_file="~/.ssh/pbs_key",
       remote_work_dir="/home/your_username/clustrix"
   )

PBS Resource Specification
--------------------------

PBS uses different resource syntax compared to SLURM:

.. code-block:: python

   from clustrix import cluster
   
   @cluster(
       cores=8,               # Number of CPU cores
       memory="16GB",         # Memory requirement
       time="02:00:00",       # Wall time (HH:MM:SS)
       queue="batch",         # PBS queue name
       nodes=1,               # Number of nodes
       ppn=8                  # Processors per node (PBS-specific)
   )
   def pbs_computation():
       """Example computation on PBS cluster."""
       import numpy as np
       
       # Create large matrix
       size = 5000
       matrix_a = np.random.rand(size, size)
       matrix_b = np.random.rand(size, size)
       
       # Matrix multiplication
       result = np.dot(matrix_a, matrix_b)
       
       return {
           'shape': result.shape,
           'mean': float(np.mean(result)),
           'std': float(np.std(result))
       }
   
   # Execute on PBS cluster
   result = pbs_computation()
   print(f"Matrix computation result: {result}")

Advanced PBS Configuration
-------------------------

Environment and Queue Setup
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   configure(
       cluster_type="pbs",
       cluster_host="torque.research.org",
       username="researcher",
       
       # PBS-specific settings
       default_queue="normal",        # Default queue
       default_walltime="04:00:00",  # Default wall time
       
       # Resource defaults
       default_cores=4,
       default_memory="8GB",
       default_nodes=1,
       
       # Environment setup
       environment_variables={
           "PBS_O_WORKDIR": "/home/researcher/work",
           "OMP_NUM_THREADS": "4"
       }
   )

Configuration File for PBS
~~~~~~~~~~~~~~~~~~~~~~~~~

Create ``~/.clustrix/config.yml``:

.. code-block:: yaml

   cluster_type: "pbs"
   cluster_host: "pbs.cluster.edu"
   username: "researcher"
   key_file: "~/.ssh/pbs_key"
   remote_work_dir: "/home/researcher/clustrix"
   
   # PBS-specific settings
   default_queue: "batch"
   default_walltime: "02:00:00"
   
   # Resource defaults
   default_cores: 8
   default_memory: "16GB"
   default_nodes: 1
   
   # Job management
   job_poll_interval: 30  # Check job status every 30 seconds
   cleanup_on_success: true

PBS Job Examples
---------------

Array-style Processing
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=4, memory="8GB", queue="batch")
   def process_file(file_id, operation="mean"):
       """Process a single file."""
       import numpy as np
       import time
       
       # Simulate file processing
       print(f"Processing file {file_id} with operation: {operation}")
       
       # Generate synthetic data (simulating file loading)
       data = np.random.rand(10000, 100) * file_id
       
       if operation == "mean":
           result = np.mean(data)
       elif operation == "std":
           result = np.std(data)
       elif operation == "sum":
           result = np.sum(data)
       else:
           result = np.median(data)
       
       # Simulate processing time
       time.sleep(1)
       
       return {
           'file_id': file_id,
           'operation': operation,
           'result': float(result),
           'data_shape': data.shape
       }
   
   # Process multiple files
   file_ids = range(1, 11)  # Files 1-10
   results = []
   
   for file_id in file_ids:
       result = process_file(file_id, operation="mean")
       results.append(result)
   
   print(f"Processed {len(results)} files")
   for r in results[:3]:  # Show first 3 results
       print(f"File {r['file_id']}: {r['result']:.4f}")

Bioinformatics Pipeline
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=8, memory="32GB", time="06:00:00", queue="bioqueue")
   def analyze_genome_sequence(sequence_id, analysis_params):
       """Analyze a genome sequence."""
       import random
       import string
       
       # Simulate sequence analysis
       print(f"Analyzing sequence {sequence_id}")
       
       # Generate mock sequence
       bases = ['A', 'T', 'G', 'C']
       sequence_length = analysis_params.get('length', 100000)
       sequence = ''.join(random.choices(bases, k=sequence_length))
       
       # Mock analysis results
       gc_content = (sequence.count('G') + sequence.count('C')) / len(sequence)
       
       # Simulate finding patterns
       patterns_found = []
       for i in range(5):
           pattern_length = random.randint(5, 10)
           pattern = ''.join(random.choices(bases, k=pattern_length))
           count = sequence.count(pattern)
           if count > 0:
               patterns_found.append({
                   'pattern': pattern,
                   'count': count,
                   'frequency': count / (len(sequence) - pattern_length + 1)
               })
       
       return {
           'sequence_id': sequence_id,
           'sequence_length': len(sequence),
           'gc_content': gc_content,
           'patterns_found': patterns_found,
           'analysis_params': analysis_params
       }
   
   # Analyze multiple sequences
   sequences = [
       {'id': 'seq_001', 'params': {'length': 50000}},
       {'id': 'seq_002', 'params': {'length': 75000}},
       {'id': 'seq_003', 'params': {'length': 100000}}
   ]
   
   results = []
   for seq in sequences:
       result = analyze_genome_sequence(seq['id'], seq['params'])
       results.append(result)
   
   # Summary statistics
   avg_gc = sum(r['gc_content'] for r in results) / len(results)
   print(f"Average GC content: {avg_gc:.3f}")

PBS Job Management
-----------------

Resource Monitoring
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=4, memory="8GB", time="01:00:00")
   def resource_intensive_task():
       """Task that monitors its resource usage."""
       import psutil
       import time
       import numpy as np
       
       # Get initial resource info
       process = psutil.Process()
       initial_memory = process.memory_info().rss / 1024 / 1024  # MB
       
       print(f"Initial memory usage: {initial_memory:.2f} MB")
       
       # Gradually increase memory usage
       data_chunks = []
       for i in range(10):
           # Create 100MB of data
           chunk = np.random.rand(100, 1250, 1000)  # ~100MB
           data_chunks.append(chunk)
           
           current_memory = process.memory_info().rss / 1024 / 1024
           print(f"Step {i+1}: Memory usage: {current_memory:.2f} MB")
           
           time.sleep(5)  # Wait 5 seconds
       
       # Final computation
       total_sum = sum(np.sum(chunk) for chunk in data_chunks)
       final_memory = process.memory_info().rss / 1024 / 1024
       
       return {
           'initial_memory_mb': initial_memory,
           'final_memory_mb': final_memory,
           'memory_increase_mb': final_memory - initial_memory,
           'computation_result': float(total_sum),
           'chunks_processed': len(data_chunks)
       }
   
   result = resource_intensive_task()
   print(f"Memory increased by: {result['memory_increase_mb']:.2f} MB")

Error Handling and Debugging
---------------------------

Handling PBS-specific Errors
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=2, memory="4GB", queue="debug")
   def debug_function(test_case="success"):
       """Function for testing error handling."""
       
       if test_case == "memory_error":
           # Try to allocate too much memory
           import numpy as np
           huge_array = np.zeros((100000, 100000))  # ~80GB
           return "This shouldn't succeed"
           
       elif test_case == "time_limit":
           # Exceed time limit
           import time
           time.sleep(7200)  # 2 hours
           return "This took too long"
           
       elif test_case == "import_error":
           # Missing package
           import nonexistent_package
           return "This package doesn't exist"
           
       else:
           # Successful execution
           return f"Test case '{test_case}' completed successfully"
   
   # Test different scenarios
   test_cases = ["success", "import_error"]  # Start with safe tests
   
   for case in test_cases:
       try:
           result = debug_function(case)
           print(f"✓ {case}: {result}")
       except Exception as e:
           print(f"✗ {case}: {type(e).__name__}: {e}")

Debugging with Logs
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import logging
   logging.basicConfig(level=logging.DEBUG)
   
   from clustrix import configure, cluster
   
   # Enable detailed logging
   configure(
       cluster_type="pbs",
       cluster_host="pbs.cluster.edu",
       username="your_user"
   )
   
   @cluster(cores=2, memory="4GB")
   def logged_function():
       """Function with detailed logging."""
       import logging
       
       # Create logger for remote execution
       logger = logging.getLogger(__name__)
       logger.info("Starting computation")
       
       try:
           import numpy as np
           data = np.random.rand(1000, 1000)
           result = np.mean(data)
           logger.info(f"Computation successful: {result}")
           return result
       except Exception as e:
           logger.error(f"Computation failed: {e}")
           raise
   
   result = logged_function()

Best Practices for PBS
---------------------

Queue Selection Strategy
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def select_pbs_queue(cores, memory_gb, time_hours):
       """Select appropriate PBS queue based on resources."""
       
       if time_hours <= 1 and cores <= 4:
           return "express"  # Fast turnaround for small jobs
       elif time_hours <= 4 and cores <= 16:
           return "normal"   # Standard queue
       elif time_hours <= 24:
           return "long"     # Long-running jobs
       elif cores > 32:
           return "bigmem"   # High-memory/high-core jobs
       else:
           return "batch"    # Default fallback
   
   # Use dynamic queue selection
   cores = 8
   memory_gb = 32
   time_hours = 6
   
   selected_queue = select_pbs_queue(cores, memory_gb, time_hours)
   
   @cluster(cores=cores, memory=f"{memory_gb}GB", 
           time=f"{time_hours:02d}:00:00", queue=selected_queue)
   def adaptive_computation():
       return "Computation with optimal queue selection"

Efficient Data Handling
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=4, memory="16GB", time="03:00:00")
   def efficient_data_processing(chunk_size=1000):
       """Process data in chunks to manage memory."""
       import numpy as np
       
       total_sum = 0
       chunk_count = 0
       
       # Process data in chunks to avoid memory issues
       for i in range(100):  # 100 chunks
           # Generate chunk
           chunk = np.random.rand(chunk_size, chunk_size)
           
           # Process chunk
           chunk_sum = np.sum(chunk)
           total_sum += chunk_sum
           chunk_count += 1
           
           # Clear memory
           del chunk
           
           if i % 10 == 0:
               print(f"Processed {i+1} chunks")
       
       return {
           'total_sum': float(total_sum),
           'chunks_processed': chunk_count,
           'average_chunk_sum': float(total_sum / chunk_count)
       }
   
   result = efficient_data_processing()
   print(f"Processed {result['chunks_processed']} chunks efficiently")

Complete PBS Example
-------------------

Scientific Computing Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import configure, cluster
   import numpy as np
   
   # Configure PBS cluster
   configure(
       cluster_type="pbs",
       cluster_host="pbs.research.edu",
       username="scientist",
       remote_work_dir="/home/scientist/clustrix",
       
       # PBS-specific settings
       default_queue="normal",
       default_walltime="04:00:00",
       
       # Default resources
       default_cores=8,
       default_memory="16GB",
       
       # Environment
       environment_variables={
           "OMP_NUM_THREADS": "8",
           "TMPDIR": "/tmp"
       }
   )
   
   @cluster(cores=16, memory="32GB", time="06:00:00", queue="compute")
   def monte_carlo_integration(n_samples, dimensions):
       """Monte Carlo integration in high dimensions."""
       import numpy as np
       import time
       
       start_time = time.time()
       
       def integrand(x):
           """Function to integrate: exp(-sum(x^2))"""
           return np.exp(-np.sum(x**2, axis=-1))
       
       # Generate random samples in [-1, 1]^dimensions
       samples = np.random.uniform(-1, 1, (n_samples, dimensions))
       
       # Evaluate integrand
       values = integrand(samples)
       
       # Monte Carlo estimate
       volume = 2**dimensions  # Volume of [-1,1]^d
       integral_estimate = volume * np.mean(values)
       error_estimate = volume * np.std(values) / np.sqrt(n_samples)
       
       end_time = time.time()
       
       return {
           'dimensions': dimensions,
           'n_samples': n_samples,
           'integral_estimate': float(integral_estimate),
           'error_estimate': float(error_estimate),
           'computation_time': end_time - start_time,
           'samples_per_second': n_samples / (end_time - start_time)
       }
   
   # Run integration for different dimensions
   dimensions_list = [2, 4, 6, 8]
   n_samples = 1000000
   
   results = []
   for dim in dimensions_list:
       print(f"Computing {dim}D integral...")
       result = monte_carlo_integration(n_samples, dim)
       results.append(result)
       print(f"  Result: {result['integral_estimate']:.6f} ± {result['error_estimate']:.6f}")
       print(f"  Time: {result['computation_time']:.2f}s")
   
   # Analysis
   print("\nSummary:")
   for r in results:
       efficiency = r['samples_per_second'] / 1000  # K samples/sec
       print(f"{r['dimensions']}D: {r['integral_estimate']:.4f} ({efficiency:.1f}K samples/s)")

This tutorial provides comprehensive coverage of using Clustrix with PBS/Torque clusters, including resource specification, job management, and best practices for scientific computing workloads.