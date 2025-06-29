Installation
============

Clustrix can be installed using pip or conda, with optional dependencies for specific cluster types.

Basic Installation
------------------

Install Clustrix using pip:

.. code-block:: bash

   pip install clustrix

Development Installation
~~~~~~~~~~~~~~~~~~~~~~~~

For development or to get the latest features:

.. code-block:: bash

   git clone https://github.com/ContextLab/clustrix.git
   cd clustrix
   pip install -e ".[dev]"

Optional Dependencies
~~~~~~~~~~~~~~~~~~~~~

Jupyter Notebook Support
~~~~~~~~~~~~~~~~~~~~~~~~

For Jupyter notebook integration with interactive widgets:

.. code-block:: bash

   pip install clustrix[notebook]
   # or
   pip install clustrix ipywidgets pyyaml

This enables the ``%%clusterfy`` magic command for interactive configuration.

Kubernetes Support
~~~~~~~~~~~~~~~~~~

For Kubernetes cluster support:

.. code-block:: bash

   pip install clustrix[kubernetes]
   # or
   pip install clustrix kubernetes

Documentation and Tutorials
~~~~~~~~~~~~~~~~~~~~~~~~~~~

To build documentation locally:

.. code-block:: bash

   pip install clustrix[docs]
   cd docs
   make html

All Optional Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~

Install everything:

.. code-block:: bash

   pip install clustrix[all]

Requirements
~~~~~~~~~~~~

- Python 3.8 or higher
- SSH access to target clusters (for remote execution)
- Appropriate cluster scheduler tools (SLURM, PBS, SGE) on target systems

Verification
~~~~~~~~~~~~

Verify your installation:

.. code-block:: python

   import clustrix
   print(clustrix.__version__)
   
   # Test local execution
   from clustrix import cluster, configure
   
   configure(cluster_host=None)  # Local execution
   
   @cluster(cores=2)
   def test_function():
       return "Clustrix is working!"
   
   result = test_function()
   print(result)  # Should print: "Clustrix is working!"

Verify Jupyter Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

If you installed notebook support, test the magic command in Jupyter:

.. code-block:: python

   import clustrix
   # Magic command should be auto-registered
   
   # In a Jupyter cell:
   %%clusterfy
   # Interactive widget should appear