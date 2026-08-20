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

   from clustrix import cluster, configure

   configure(cluster_type="local")  # no cluster needed to try this

   @cluster(cores=8, memory="16GB", time="02:00:00")
   def expensive_computation(iterations=1000):
       import math

       return sum(math.sqrt(i) for i in range(iterations))

   print(expensive_computation(iterations=10_000))

Change ``cluster_type="local"`` to a SLURM login node and those same lines
submit a batch job. That substitutability is the point of the library.

Start here
----------

- :doc:`introduction` -- what Clustrix is, what it is not, and how it compares
  to hand-written sbatch scripts, Dask, Ray, joblib and plain SSH.
- :doc:`installation` -- install it, with the optional extras.
- :doc:`quickstart` -- a real result in five minutes, beginning with a backend
  that needs no cluster at all.
- :ref:`supported-cluster-types` -- the four backends Clustrix supports, and
  the evidence that each one runs a real job.
- :ref:`removed-backends` -- if you are looking for PBS, SGE, Kubernetes or a
  cloud VM provider, start here.

What it does
------------

- **One decorator.** ``@cluster`` on a function is the whole interface.
- **Function packaging.** Your function is serialized by value with dill and
  cloudpickle, so closures, nested functions and project-local modules travel
  with it. Source code is not required.
- **Four backends**: SLURM, SSH, HuggingFace Jobs and local execution. Each has
  been run end to end -- see :ref:`supported-cluster-types`.
- **Environment replication.** The remote environment is rebuilt from your
  local ``pip freeze``.
- **Read-only filesystem utilities.** ``cluster_ls``, ``cluster_glob``,
  ``cluster_stat`` and their siblings work against a local path or a remote one
  through the same call. They inspect; they do not transfer.
- **Shared-storage detection.** A worker that already shares your filesystem
  is detected, so the payload is not copied across a network that does not
  need it.
- **Loop parallelization.** A loop whose body carries no dependency between
  iterations can be distributed across nodes. The analysis is deliberately
  conservative and declines most real loops -- see :doc:`limitations`.
- **A Jupyter widget.** ``%%remote`` opens a configuration panel in the
  notebook.
- **Errors that reach you.** A remote traceback is re-raised in your own
  process rather than left in a log file on the cluster.

Two things that sound like features and are not. ``@cluster(cores=N)`` does not
split an ordinary local function across N workers: it runs once in your own
process, and Clustrix warns that the number was discarded whenever you asked
for more than one core. Loop parallelization is on by default, so nothing has
to be switched on: ``cores`` sizes a local pool only when the loop analysis
finds a supported loop *and* the function accepts the matching chunk argument.
Clustrix also does not move your data -- see :doc:`introduction`.

Jupyter Notebook Integration
----------------------------

Clustrix registers an IPython magic that opens a configuration widget:

.. code-block:: ipython3

   %%remote

Importing ``clustrix`` registers the magic but does **not** display the widget.
A library should not inject UI as a side effect of being imported, so the
widget appears on demand: run ``%%remote`` in a cell, or call
``clustrix.notebook_magic.display_config_widget()``. Setting
``CLUSTRIX_AUTO_WIDGET=1`` makes it display on import instead.

``%%clusterfy`` is an alias for ``%%remote``. It works, and it emits a
``DeprecationWarning``.

Interactive Configuration Widget
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The widget edits the same settings as ``clustrix.configure()`` and applies them
to the current session.

.. image:: _static/widget/02-after-light.jpg
   :alt: Clustrix widget in JupyterLab, light theme
   :width: 700px

Its colours resolve through JupyterLab's own theme variables, so it follows the
notebook theme rather than carrying a second hand-maintained dark stylesheet:

.. image:: _static/widget/03-after-dark.jpg
   :alt: The same widget in JupyterLab's dark theme
   :width: 700px

"Show advanced" reveals the package manager, Python executable, environment
variables, module loads and pre-execution commands:

.. image:: _static/widget/04-advanced-light.jpg
   :alt: Widget with the Advanced panel expanded
   :width: 700px

**What the widget covers**

The cluster type dropdown offers ``local``, ``ssh``, ``slurm`` and
``huggingface`` -- the same four values as
:data:`clustrix.config.SUPPORTED_CLUSTER_TYPES`.

- ``ssh`` and ``slurm`` show the connection section: host, port, username, SSH
  key file, password, remote work directory, an environment variable to read
  the password from, and an "Auto setup SSH keys" button.
- ``huggingface`` shows namespace, flavor, token and an "Allow paid GPU
  flavors" checkbox. GPU flavors bill by the second, so that box has to be
  ticked before one is accepted.
- ``local`` needs no connection settings at all.

There are no PBS, SGE, Kubernetes, AWS, GCP, Azure or Lambda Cloud entries,
because Clustrix does not support those backends; see :ref:`removed-backends`.

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

   execution_model
   configuration
   data_packages
   ssh_setup
   limitations
   troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Tutorials

   tutorials/usage_patterns
   tutorials/filesystem_tutorial
   tutorials/slurm_tutorial

.. toctree::
   :maxdepth: 2
   :caption: Interactive Notebooks

   notebooks/local_parallel_comparison
   notebooks/filesystem_tutorial
   notebooks/cluster_config_example
   notebooks/complete_api_demo
   notebooks/slurm_tutorial
   notebooks/ssh_tutorial
   notebooks/basic_usage

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/decorator
   api/filesystem
   api/dependency_analysis
   api/file_packaging
   api/config
   api/notebook_magic
   api/local_executor

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
| ``slurm``          | Verified          | A real job ran on ``hpc.example.edu``            |
|                    |                   | and returned its result.                         |
+--------------------+-------------------+--------------------------------------------------+
| ``ssh``            | Verified          | Direct execution, no scheduler. A real job ran   |
|                    |                   | on an 8-GPU host.                                |
+--------------------+-------------------+--------------------------------------------------+
| ``huggingface``    | Verified          | HuggingFace Jobs. A real job ran in a container. |
+--------------------+-------------------+--------------------------------------------------+
| ``local``          | Works             | Runs in the calling process. Used for            |
|                    |                   | development and the fast tests.                  |
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
