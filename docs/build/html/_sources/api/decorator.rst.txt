Decorator API
=============

The ``@cluster`` decorator is the main interface for Clustrix, allowing you to easily execute functions on remote clusters or locally with parallelization.

.. automodule:: clustrix.decorator
   :members:
   :undoc-members:
   :show-inheritance:

Examples
--------

Basic Usage
~~~~~~~~~~~

.. code-block:: python

   from clustrix import cluster
   
   @cluster(cores=4, memory='8GB')
   def my_function(x, y):
       return x + y
   
   result = my_function(10, 20)

Resource Specification
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(
       cores=16,
       memory='32GB', 
       time='04:00:00',
       partition='gpu'
   )
   def gpu_computation():
       # Your GPU code here
       pass

Parallel Loop Execution
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=8, parallel=True)
   def parallel_processing(data):
       results = []
       for item in data:  # This loop will be parallelized
           results.append(expensive_operation(item))
       return results

Local Parallelization
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Configure for local execution
   import clustrix
   clustrix.configure(cluster_host=None)
   
   @cluster(cores=8, parallel=True)
   def local_parallel_function(data):
       # Executes locally using multiprocessing
       return [process_item(item) for item in data]