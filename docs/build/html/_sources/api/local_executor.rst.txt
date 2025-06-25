Local Executor API
==================

The Local Executor enables multi-core parallel execution on the local machine using multiprocessing or threading.

.. automodule:: clustrix.local_executor
   :members:
   :undoc-members:
   :show-inheritance:

Usage Examples
--------------

Basic Local Execution
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.local_executor import LocalExecutor
   
   def compute_square(x):
       return x ** 2
   
   with LocalExecutor(max_workers=4) as executor:
       # Execute single function
       result = executor.execute_single(compute_square, (5,), {})
       print(result)  # 25
       
       # Execute multiple work chunks
       work_chunks = [
           {'args': (i,), 'kwargs': {}} 
           for i in range(10)
       ]
       results = executor.execute_parallel(compute_square, work_chunks)
       print(results)  # [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]

Auto-Detection of Executor Type
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.local_executor import create_local_executor
   
   # I/O-bound function - will use threads
   def download_file(url):
       import requests
       return requests.get(url).content
   
   # CPU-bound function - will use processes  
   def compute_heavy(n):
       return sum(i**2 for i in range(n))
   
   # Auto-detect appropriate executor
   io_executor = create_local_executor(func=download_file)
   cpu_executor = create_local_executor(func=compute_heavy)

Loop Parallelization
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.local_executor import LocalExecutor
   
   def process_data(data_chunk):
       return [x * 2 for x in data_chunk]
   
   data = list(range(100))
   
   with LocalExecutor(max_workers=4) as executor:
       results = executor.execute_loop_parallel(
           func=process_data,
           loop_var='data_chunk',
           iterable=data,
           chunk_size=25
       )
       print(len(results))  # 200

Choosing Threads vs Processes
-----------------------------

The executor automatically chooses between threads and processes based on:

**Use Threads When:**
- Function performs I/O operations (file, network, database)
- Objects cannot be pickled (closures, lambdas)
- Shared memory access is needed

**Use Processes When:**
- Function is CPU-intensive
- True parallelism is required
- Objects can be pickled safely

You can override the auto-detection:

.. code-block:: python

   # Force thread usage
   thread_executor = LocalExecutor(use_threads=True)
   
   # Force process usage  
   process_executor = LocalExecutor(use_threads=False)

Performance Considerations
-------------------------

**Process Pool Overhead:**
- Higher memory usage (separate Python interpreters)
- Serialization overhead for arguments and results
- Slower startup time

**Thread Pool Overhead:**
- Shared memory (lower memory usage)
- GIL limitations for CPU-bound tasks
- Faster startup time

**Optimal Worker Count:**
- CPU-bound: ``os.cpu_count()``
- I/O-bound: ``os.cpu_count() * 2-4``
- Custom: Based on your specific workload