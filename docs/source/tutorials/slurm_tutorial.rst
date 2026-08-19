SLURM Cluster Tutorial
======================

This tutorial demonstrates how to use Clustrix with SLURM (Simple Linux Utility for Resource Management) clusters, one of the most common cluster schedulers in high-performance computing.

Prerequisites
-------------

1. Access to a SLURM cluster
2. SSH key setup (see :doc:`../ssh_setup`)
3. Clustrix installed with: ``pip install clustrix``

.. note::

   SLURM is verified end to end against a real cluster (SSH connect, job
   submission, environment build, result retrieval). PBS and SGE are **not
   supported**, and are planned for a future release; see
   :ref:`removed-backends`.

What Happens When You Call a ``@cluster``-Decorated Function
--------------------------------------------------------------

Calling a SLURM-decorated function is not a remote procedure call; it is a
full job submission and poll cycle. In order:

1. **Serialize.** The function, its arguments and keyword arguments are
   pickled with ``dill`` (falling back to ``cloudpickle``). Serialization
   does not need the function's source -- byte-compiled code objects travel
   fine, including closures. Any module the function reaches into that lives
   in your own project (not something ``pip`` installed) is walked and
   embedded by value, because the worker will not have it on its Python
   path. A package that *is* installed locally but cannot be reinstalled on
   the cluster -- an editable install, a git checkout -- makes clustrix
   **refuse to submit**, naming the offending package, rather than shipping a
   job that fails on import an hour into the queue.
2. **Connect.** Clustrix opens an SSH connection to ``cluster_host``. The
   remote host's SSH key is checked against your local ``known_hosts``
   files. An unrecognized key is rejected by default -- see
   :doc:`../ssh_setup` for exactly what that looks like and how to fix it,
   because it is the first thing a new cluster hits.
3. **Stage the job directory.** A directory
   ``{remote_work_dir}/job_{timestamp}_{8 hex chars}`` is created on the
   cluster with mode ``0700``, and a random 256-bit result-signing key is
   written inside it (``.clustrix_result_key``, mode ``0600``). Only someone
   who can already read that directory can read the key.
4. **Upload.** The pickled function/args/kwargs go up as
   ``function_data.pkl`` over SFTP.
5. **Build the environment.** By default (``use_two_venv=True``) clustrix
   builds two virtualenvs on the cluster: one to run the submitting Python
   version and unpickle the payload, one matching the packages your local
   environment reports (``replicate_local_environment``, minus
   ``excluded_packages``, plus ``cluster_packages``). GPU detection runs in
   the first venv and, if a GPU is found, GPU-enabled packages are installed
   into the second. This step has a timeout (``venv_setup_timeout``, default
   300s) and falls back to a single shared venv if it fails or times out.
6. **Generate and upload the job script.** ``clustrix/utils.py::create_job_script``
   writes a bash script (see below) to ``job.sh`` in the job directory.
7. **Submit.** Clustrix runs ``sbatch job.sh`` and parses the job ID from
   the last whitespace-separated token of the output.
8. **Poll.** The submitting process polls ``squeue``/``sacct`` every
   ``job_poll_interval`` seconds (default 30) until the job leaves the
   queue.
9. **Verify and retrieve the result.** ``result.pkl`` and its
   ``result.pkl.hmac`` signature are downloaded. The signature is recomputed
   locally with the key from step 3 and compared; a missing key, a missing
   signature, or a mismatch is refused outright -- the file is never handed
   to the deserializer unverified, because unpickling runs arbitrary code.
   Only then is ``result.pkl`` loaded with ``dill`` and returned to you.
   Errors raised inside your function come back the same way, through a
   signed ``error.pkl``, and re-raise with the original exception type.
10. **Clean up.** If the job succeeded and ``cleanup_on_success=True``
    (the default), the remote job directory is removed. A failed job's
    directory is left in place for you to inspect.

What the Generated Job Script Looks Like
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For ``@cluster(cores=4, memory="8GB", time="01:00:00", partition="compute")``,
``job.sh`` looks like this (module loads, environment variables and
``pre_execution_commands`` from your config are inserted between the
``#SBATCH`` block and the execution block; the memory string is normalized
to what SLURM's ``--mem`` accepts, so ``"8GB"`` becomes ``--mem=8G`` and a
fractional value like ``"1.5GB"`` is rounded up to ``--mem=2G``):

.. code-block:: bash

   #!/bin/bash
   #SBATCH --job-name=clustrix
   #SBATCH --output=/scratch/you/clustrix/job_.../slurm-%j.out
   #SBATCH --error=/scratch/you/clustrix/job_.../slurm-%j.err
   #SBATCH --cpus-per-task=4
   #SBATCH --mem=8G
   #SBATCH --time=01:00:00
   #SBATCH --partition=compute
   module load python/3.11          # from module_loads, if set
   export OMP_NUM_THREADS=8         # from environment_variables, if set
   export CLUSTRIX_RESULT_KEY=$(cat .../.clustrix_result_key 2>/dev/null || true)
   cd /scratch/you/clustrix/job_...
   source venv/bin/activate         # or the two-venv activation sequence
   python -c "
   # unpickle function_data.pkl with dill, run the function,
   # write result.pkl + result.pkl.hmac (or error.pkl + error.pkl.hmac
   # on an exception), then remove CLUSTRIX_RESULT_KEY from the
   # environment before any of that runs -- your function and everything
   # it imports execute in this same interpreter, so nothing downstream
   # can read the signing key.
   "

There is no pass-through for arbitrary ``sbatch`` directives beyond
``cores``, ``memory``, ``time``, ``partition`` and ``queue`` -- an
unrecognized keyword argument to ``@cluster`` is accepted but never written
into the script. If you need ``--nodes``, ``--ntasks-per-node``,
``--account`` or similar, put the equivalent in
``pre_execution_commands`` or your cluster's own scheduler defaults.

Configuration File Precedence
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Settings resolve in this order, highest priority first: keyword arguments
to ``@cluster(...)`` at call time, then ``configure(...)``/
``~/.clustrix/config.yml``, then the ``ClusterConfig`` dataclass defaults.
``default_cores``, ``default_memory``, ``default_time`` and
``default_partition`` fill in anything the decorator omits -- a decorator
with no arguments at all still needs *some* resolved cores/memory/time, and
these are where they come from.

When Things Fail
~~~~~~~~~~~~~~~~~

- **Unknown host key**: refused before any of the above happens; see
  :doc:`../ssh_setup`.
- **Editable/unreproducible local package used by the function**: refused
  at step 1, before any SSH connection is made, naming the package.
- **``ModuleNotFoundError`` on the worker**: a package your function reaches
  by *reference* (e.g. ``import mypkg; mypkg.helpers.clean(x)``) that
  clustrix's dependency walk did not detect. Vendor the code into your
  project or list it explicitly.
- **Job sits in the queue past a reasonable time**: check with
  ``squeue -u $USER`` / ``sinfo -p <partition>`` directly -- clustrix is
  only polling, not scheduling.
- **Job runs but the result never comes back**: check
  ``{remote_work_dir}/job_.../slurm-<jobid>.out`` and ``.err`` on the
  cluster (left behind unless ``cleanup_on_success`` removed them), and
  look for ``result.pkl.hmac``/``error.pkl.hmac`` next to the payload -- a
  missing signature file usually means the job died before reaching the
  signing step, and the ``.err`` file has the traceback.
- **Result refused locally with a signature error**: this means
  ``result.pkl`` didn't match the key clustrix generated for that job --
  treat it as "cannot trust this file," not as a bug to route around.

Configuration Options
---------------------

**Option 1: Interactive Widget (Recommended for Jupyter)**

For Jupyter notebook users, use the interactive configuration widget:

Importing ``clustrix`` registers the magic but does not display anything. Run
``%%remote`` in a cell of its own to open the widget:

.. code-block:: ipython3

   %%remote

Select ``slurm`` as the cluster type to reveal the connection fields, and use
"Save" to keep the configuration for reuse.

**Option 2: Programmatic Configuration**

Configure Clustrix programmatically for your SLURM cluster:

.. code-block:: python

   from clustrix import configure
   
   configure(
       cluster_type="slurm",
       cluster_host="slurm.university.edu",
       username="your_username",
       key_file="~/.ssh/slurm_key",  # Optional if using SSH agent
       remote_work_dir="/scratch/your_username/clustrix"
   )

Simple Job Execution
--------------------

Execute a basic function on the SLURM cluster:

.. code-block:: python

   # cluster-required: submits a real job to a live SLURM cluster
   from clustrix import cluster

   @cluster(cores=4, memory="8GB", time="01:00:00")
   def compute_pi(n_samples):
       """Monte Carlo estimation of pi."""
       import random
       inside_circle = 0
       
       for _ in range(n_samples):
           x, y = random.random(), random.random()
           if x*x + y*y <= 1:
               inside_circle += 1
       
       return 4 * inside_circle / n_samples
   
   # This will submit a job to SLURM and wait for results
   pi_estimate = compute_pi(1000000)
   print(f"Pi estimate: {pi_estimate}")

Resource Specification
----------------------

SLURM-specific resource options:

.. code-block:: python

   from clustrix import cluster

   @cluster(
       cores=16,              # Number of CPU cores
       memory="32GB",         # Memory requirement
       time="04:00:00",       # Wall time (HH:MM:SS)
       partition="compute",   # SLURM partition
   )
   def intensive_computation():
       import numpy as np
       # Simulate intensive computation
       matrix = np.random.rand(10000, 10000)
       eigenvalues = np.linalg.eigvals(matrix)
       return len(eigenvalues)

``cores``, ``memory``, ``time``, ``partition`` and ``queue`` are the resource
arguments the decorator understands. There is no pass-through for arbitrary
``sbatch`` directives such as ``--nodes``, ``--ntasks-per-node`` or
``--account``: unrecognised keyword arguments are collected but never written
into the job script. Put anything else in ``pre_execution_commands`` or in the
cluster's own defaults.

Advanced Configuration
----------------------

Environment and Module Setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configure environment modules and variables:

.. code-block:: python

   configure(
       cluster_type="slurm",
       cluster_host="slurm.hpc.edu",
       username="researcher",
       
       # Load required modules
       module_loads=[
           "python/3.11",
           "gcc/11.2",
           "openmpi/4.1"
       ],
       
       # Set environment variables
       environment_variables={
           "OMP_NUM_THREADS": "16",
           "PYTHONPATH": "/home/researcher/libs:$PYTHONPATH"
       },
       
       # Default resources
       default_cores=8,
       default_memory="16GB",
       default_time="02:00:00",
       default_partition="standard"
   )

Configuration File
~~~~~~~~~~~~~~~~~~

Create ``~/.clustrix/config.yml``:

.. code-block:: yaml

   cluster_type: "slurm"
   cluster_host: "slurm.university.edu"
   username: "researcher"
   key_file: "~/.ssh/slurm_key"
   remote_work_dir: "/scratch/researcher/clustrix"
   
   # SLURM-specific settings
   default_partition: "compute"
   
   # Resource defaults
   default_cores: 8
   default_memory: "16GB"
   default_time: "02:00:00"
   
   # Environment setup
   module_loads:
     - "python/3.11"
     - "gcc/11.2"
     - "intel-mpi/2021"
   
   environment_variables:
     OMP_NUM_THREADS: "8"
     MKL_NUM_THREADS: "8"

Parallel Processing Examples
----------------------------

Array Jobs with Loop Parallelization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Process multiple datasets in parallel:

.. code-block:: python

   # cluster-required: submits real jobs to a live SLURM cluster
   @cluster(cores=4, memory="8GB", parallel=True)
   def process_dataset(dataset_id, analysis_type="standard"):
       """Process a single dataset."""
       import numpy as np
       import time
       
       # Simulate data loading
       print(f"Processing dataset {dataset_id} with {analysis_type}")
       data = np.random.rand(1000, 100)
       
       # Simulate analysis
       if analysis_type == "intensive":
           time.sleep(2)  # Simulate longer computation
           result = np.mean(data**3)
       else:
           result = np.mean(data**2)
       
       return {
           "dataset_id": dataset_id,
           "result": result,
           "analysis_type": analysis_type
       }
   
   # Process multiple datasets
   dataset_ids = range(10)
   results = []
   
   for dataset_id in dataset_ids:
       result = process_dataset(dataset_id, analysis_type="intensive")
       results.append(result)
   
   print(f"Processed {len(results)} datasets")

Machine Learning Workflow
~~~~~~~~~~~~~~~~~~~~~~~~~

Distributed hyperparameter tuning:

.. code-block:: python

   # cluster-required: submits real jobs to a live SLURM cluster
   @cluster(cores=8, memory="16GB", time="03:00:00")
   def train_model(params):
       """Train ML model with given hyperparameters."""
       from sklearn.ensemble import RandomForestRegressor
       from sklearn.datasets import make_regression
       from sklearn.model_selection import cross_val_score
       import numpy as np
       
       # Generate synthetic dataset
       X, y = make_regression(
           n_samples=10000, 
           n_features=20, 
           noise=0.1, 
           random_state=42
       )
       
       # Create model with parameters
       model = RandomForestRegressor(
           n_estimators=params['n_estimators'],
           max_depth=params['max_depth'],
           min_samples_split=params['min_samples_split'],
           random_state=42
       )
       
       # Cross-validation
       scores = cross_val_score(model, X, y, cv=5, scoring='r2')
       
       return {
           'params': params,
           'mean_score': np.mean(scores),
           'std_score': np.std(scores)
       }
   
   # Hyperparameter grid
   param_grid = [
       {'n_estimators': 100, 'max_depth': 10, 'min_samples_split': 2},
       {'n_estimators': 200, 'max_depth': 15, 'min_samples_split': 5},
       {'n_estimators': 300, 'max_depth': 20, 'min_samples_split': 10},
       {'n_estimators': 150, 'max_depth': 12, 'min_samples_split': 3},
   ]
   
   # Train models in parallel
   results = []
   for params in param_grid:
       result = train_model(params)
       results.append(result)
   
   # Find best parameters
   best_result = max(results, key=lambda x: x['mean_score'])
   print(f"Best parameters: {best_result['params']}")
   print(f"Best score: {best_result['mean_score']:.4f}")

Job Management
--------------

Job Status Monitoring
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # cluster-required: connects to and submits a job on a live SLURM cluster
   from clustrix.executor import ClusterExecutor
   from clustrix.config import get_config
   
   # Get current configuration
   config = get_config()
   executor = ClusterExecutor(config)
   
   # Submit job and get job ID
   @cluster(cores=4, memory="8GB")
   def long_running_task():
       import time
       time.sleep(300)  # 5 minutes
       return "Task completed"
   
   result = long_running_task()

To get a handle back instead of blocking, set ``async_submit=True`` on the
decorator or in the configuration.

Error Handling
~~~~~~~~~~~~~~

.. code-block:: python

   # cluster-required: submits real jobs to a live SLURM cluster
   @cluster(cores=2, memory="4GB")
   def error_prone_function(divide_by_zero=False):
       """Function that may raise errors."""
       import numpy as np
       
       if divide_by_zero:
           return 1 / 0  # This will raise ZeroDivisionError
       
       # Normal computation
       data = np.random.rand(1000)
       return np.mean(data)
   
   try:
       # This will work
       result = error_prone_function(divide_by_zero=False)
       print(f"Success: {result}")
       
       # This will raise an error
       result = error_prone_function(divide_by_zero=True)
   except ZeroDivisionError as e:
       print(f"Caught error from remote execution: {e}")

Best Practices
--------------

Resource Estimation
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import cluster

   # Estimate resources based on problem size
   def estimate_resources(data_size_gb):
       """Estimate resources needed for computation."""
       
       # Rule of thumb: 2GB RAM per GB of data
       memory_gb = max(4, int(data_size_gb * 2))
       
       # More cores for larger datasets (up to 16)
       cores = min(16, max(2, int(data_size_gb / 2)))
       
       # Time based on data size (minimum 30 minutes)
       hours = max(0.5, data_size_gb * 0.1)
       time_str = f"{int(hours):02d}:{int((hours % 1) * 60):02d}:00"
       
       return {
           'cores': cores,
           'memory': f"{memory_gb}GB",
           'time': time_str
       }
   
   # Use estimated resources
   data_size = 10  # GB
   resources = estimate_resources(data_size)
   
   @cluster(**resources)
   def process_large_dataset():
       # Process your large dataset
       pass

Debugging and Troubleshooting
-----------------------------

Enable Debug Logging
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import logging
   logging.basicConfig(level=logging.DEBUG)
   
   from clustrix import configure, cluster
   
   # This will show detailed SSH and job submission logs
   configure(cluster_type="slurm", cluster_host="your-cluster")

Common Issues
~~~~~~~~~~~~~

**Job Fails with "Permission Denied"**

Check SSH setup and file permissions:

.. code-block:: bash

   # Test SSH connection
   ssh your-cluster "squeue --version"
   
   # Check permissions
   ssh your-cluster "ls -la ~/.ssh/"

**Jobs Stuck in Queue**

Check SLURM queue status:

.. code-block:: bash

   # Check your jobs
   squeue -u $USER
   
   # Check partition availability  
   sinfo -p your_partition

**Out of Memory Errors**

Increase memory allocation:

.. code-block:: python

   @cluster(cores=4, memory="32GB")  # Increase from default
   def memory_intensive_task():
       import numpy as np
       # Large arrays need more memory
       big_array = np.random.rand(50000, 50000)
       return np.sum(big_array)

Complete Example
----------------

Here's a complete scientific computing example:

.. code-block:: python

   # cluster-required: submits real jobs to a live SLURM cluster
   from clustrix import configure, cluster
   import numpy as np

   # Configure SLURM cluster
   configure(
       cluster_type="slurm",
       cluster_host="slurm.university.edu", 
       username="researcher",
       remote_work_dir="/scratch/researcher/clustrix",
       
       # Environment setup
       module_loads=["python/3.11", "intel-mkl/2021"],
       environment_variables={"MKL_NUM_THREADS": "8"},
       
       # Default resources
       default_cores=8,
       default_memory="16GB",
       default_time="02:00:00"
   )
   
   @cluster(cores=16, memory="32GB", time="04:00:00")
   def monte_carlo_simulation(n_trials, n_steps):
       """Monte Carlo simulation of random walk."""
       import numpy as np
       
       results = []
       for trial in range(n_trials):
           # Random walk
           steps = np.random.choice([-1, 1], size=n_steps)
           positions = np.cumsum(steps)
           
           # Calculate statistics
           max_displacement = np.max(np.abs(positions))
           final_position = positions[-1]
           
           results.append({
               'trial': trial,
               'max_displacement': max_displacement,
               'final_position': final_position
           })
       
       return results
   
   # Run simulation
   print("Starting Monte Carlo simulation on SLURM cluster...")
   results = monte_carlo_simulation(n_trials=1000, n_steps=10000)
   
   # Analyze results
   final_positions = [r['final_position'] for r in results]
   max_displacements = [r['max_displacement'] for r in results]
   
   print(f"Mean final position: {np.mean(final_positions):.2f}")
   print(f"Mean max displacement: {np.mean(max_displacements):.2f}")
   print(f"Std final position: {np.std(final_positions):.2f}")

This tutorial covers the essential aspects of using Clustrix with SLURM clusters. For more advanced topics, see the API documentation and other tutorials.