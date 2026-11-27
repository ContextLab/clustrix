Clustrix Documentation
======================

**Run an ordinary Python function somewhere else.**

Add ``@cluster`` to a function, call it normally, and Clustrix serializes it
with its arguments, ships it to the compute resource you configured, runs it
there, and hands you back the return value. No job script, no ``scp``, no
polling loop, no result-unpickling glue.

.. image:: https://img.shields.io/pypi/v/clustrix.svg
   :target: https://pypi.org/project/clustrix/
   :alt: PyPI version

.. image:: https://img.shields.io/pypi/pyversions/clustrix.svg
   :target: https://pypi.org/project/clustrix/
   :alt: Python versions

.. image:: https://img.shields.io/github/license/ContextLab/clustrix.svg
   :target: https://github.com/ContextLab/clustrix/blob/master/LICENSE
   :alt: License

.. code-block:: python

   import clustrix
   from clustrix import cluster, configure

   configure(cluster_type="local")  # no cluster needed to try this


   @cluster(cores=4)
   def roots(numbers):
       import math

       found = []
       for number in numbers:
           found.append(math.sqrt(number))
       return sum(found)


   print(clustrix.explain(roots))
   print(roots(range(10_000)))

Configure a SLURM login node or an SSH host in place of ``"local"`` and the
same function runs there.

Start here
----------

- :doc:`introduction` -- what Clustrix is and is not.
- :doc:`installation`
- :doc:`quickstart`
- :ref:`supported-cluster-types` -- the four backends, and the ones that are
  not supported.

What it does
------------

- **Runs a function elsewhere.** The function is serialized by value, so
  functions defined in a script, a notebook or the REPL work, and modules
  from your own project travel with it.
- **Divides a loop automatically.** A top-level ``for`` loop whose iterations
  are independent is run in parallel: among ``cores`` worker processes
  locally, as one job per piece on ``ssh`` and ``slurm``. The function
  returns what it would have returned. :func:`clustrix.explain` prints the
  decision. See :doc:`parallel_loops`, including what it cannot check.
- **Stages input files automatically** on ``ssh`` and ``slurm``. A local file
  the call names is uploaded once and made available at the path the code
  uses. See :doc:`input_files`, including its limits. Files a job writes are
  not brought back.
- **Installs the dependencies.** The packages a function imports are detected
  and installed in a cached environment on the remote host. Local and remote
  Python must be the same minor version.
- **Four backends**: ``slurm``, ``ssh``, ``huggingface`` (HuggingFace Jobs)
  and ``local``.
- **Verifies what it connects to and what it receives.** Unknown SSH hosts
  are refused by default, and a result is authenticated before it is
  deserialized.
- **A notebook widget and a command line.** See :doc:`api/notebook_magic`
  and :doc:`cli`.
- **Read-only filesystem helpers.** ``cluster_ls``, ``cluster_glob``,
  ``cluster_stat`` and others answer the same question about a local or a
  remote path.

What it does not do
-------------------

- It does not divide a loop that is inside a helper function, nested under
  ``if``/``with``/``try``, or whose iterations depend on each other.
- It cannot see side effects outside the values a loop was given (module
  state, files, databases, GPU random state).
- It does not use SLURM job arrays. ``@cluster(gpus=N)`` requests GPUs, and
  the request has been run against a real scheduler, but no computation has
  been run on a real GPU in this release. ``auto_gpu_parallel`` does nothing.
- It does not find a path that is computed at run time, does not stage files
  for HuggingFace Jobs or asynchronous calls, and does not delete staged
  data.
- It does not support PBS, SGE, Kubernetes or cloud VM providers.

:doc:`limitations` has the full list.

Table of Contents
-----------------

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   introduction
   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   parallel_loops
   input_files
   data_packages
   configuration
   cli
   ssh_setup
   execution_model
   limitations
   troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Tutorials

   tutorials/index

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/decorator
   api/config
   api/notebook_magic
   api/filesystem
   api/public_api

.. _supported-cluster-types:

Supported Cluster Types
-----------------------

**Execution backends**

Clustrix supports exactly four ``cluster_type`` values -- the contents of
:data:`clustrix.config.SUPPORTED_CLUSTER_TYPES`, which is also what the CLI and
the notebook widget offer. There are no others.

+--------------------+-------------------+--------------------------------------------------+
| ``cluster_type``   | Status            | Notes                                            |
+====================+===================+==================================================+
| ``slurm``          | Verified          | Verified end to end against a real scheduler.    |
|                    |                   |                                                  |
+--------------------+-------------------+--------------------------------------------------+
| ``ssh``            | Verified          | Direct execution, no scheduler. A real job ran   |
|                    |                   | on an 8-GPU host.                                |
+--------------------+-------------------+--------------------------------------------------+
| ``huggingface``    | Verified          | HuggingFace Jobs. A real job ran in a container. |
+--------------------+-------------------+--------------------------------------------------+
| ``local``          | Works             | Runs on the calling machine. A divided loop uses |
|                    |                   | worker processes.                                |
+--------------------+-------------------+--------------------------------------------------+

``cluster_type='huggingface'`` means HuggingFace *Jobs*. There is no
HuggingFace *Spaces* provider; see :ref:`removed-backends`.

.. _removed-backends-pointer:

**Backends Clustrix does not support**

PBS, SGE, Kubernetes, AWS, GCP, Azure and Lambda Cloud are absent, and so are
the cost-monitoring and cloud pricing APIs that served them. Clustrix does not
claim a backend it has not run a real job on, and none of these has one.
Setting ``cluster_type`` to any of those names raises a ``ValueError`` naming
the backend and its tracking issue. Each is planned for a future release;
:ref:`removed-backends` has the details and the links.

**Evidence**

The three "Verified" rows are the backends exercised by
``scripts/collect_execution_evidence.py``, which submits a genuine job to each
reachable target, waits for it, and prints what came back. Nothing in it is
mocked, and a target it cannot reach is reported as skipped rather than as
passing:

.. code-block:: bash

   python scripts/collect_execution_evidence.py            # all reachable targets
   python scripts/collect_execution_evidence.py slurm gpu  # a subset

Credentials come from ``~/.clustrix-dev-credentials`` or from the environment
(``CLUSTRIX_SLURM_PASSWORD``, ``CLUSTRIX_GPU_PASSWORD``, ``HF_TOKEN``).

Links
-----

* **GitHub Repository**: https://github.com/ContextLab/clustrix
* **PyPI Package**: https://pypi.org/project/clustrix/
* **Issue Tracker**: https://github.com/ContextLab/clustrix/issues
* **Discussions**: https://github.com/ContextLab/clustrix/discussions

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
