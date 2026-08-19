.. _installation:

Installation
============

Clustrix is a pure-Python package. The base install pulls in everything the
verified backends need -- SSH (``paramiko``), serialization (``cloudpickle``,
``dill``), the CLI (``click``) and the Hugging Face Jobs client
(``huggingface_hub``). Everything else is an optional extra.

Requirements
------------

- **Python 3.10 or newer** (``requires-python = ">=3.10"``).
- For remote backends: SSH access to the target machine, and the scheduler's
  own client tools (``sbatch``/``squeue``, ``qsub``, ...) present *on that
  machine*. Nothing scheduler-specific is needed locally.
- For ``cluster_type="huggingface"``: a Hugging Face token with permission to
  write jobs in the namespace you target.

Basic Installation
------------------

.. code-block:: bash

   pip install clustrix

Development Installation
~~~~~~~~~~~~~~~~~~~~~~~~

For the latest source, or to work on Clustrix itself:

.. code-block:: bash

   git clone https://github.com/ContextLab/clustrix.git
   cd clustrix
   pip install -e ".[dev]"

The ``dev`` extra installs the test suite's dependencies (pytest, numpy,
pandas, ipywidgets) plus the quality tools CI enforces: ``black``, ``flake8``
and ``mypy``.

Optional Dependencies
---------------------

Jupyter Notebook Support
~~~~~~~~~~~~~~~~~~~~~~~~

For the interactive configuration widget and the ``%%remote`` magic:

.. code-block:: bash

   pip install "clustrix[widget]"
   # or
   pip install clustrix ipywidgets jupyter ipython

Importing ``clustrix`` registers the magic but deliberately displays nothing.
Run ``%%remote`` in a cell to show the widget.

Kubernetes Support
~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   pip install "clustrix[kubernetes]"

.. warning::

   The Kubernetes backend is implemented but has never been verified against
   a real cluster. See :ref:`supported-cluster-types`.

Cloud Provider Support
~~~~~~~~~~~~~~~~~~~~~~

These extras install each provider's SDK. They are what the **pricing and
cost-estimation** clients use, and those do work -- they query provider
pricing APIs and never submit a job.

.. code-block:: bash

   pip install "clustrix[aws]"     # boto3 + kubernetes
   pip install "clustrix[gcp]"     # google-cloud-* + kubernetes
   pip install "clustrix[azure]"   # azure-* + kubernetes
   pip install "clustrix[cloud]"   # all three

.. warning::

   Installing these does **not** give you a working cloud execution backend.
   No AWS, GCP, Azure or Lambda Cloud job has been shown to run end to end.
   See :ref:`supported-cluster-types`.

Documentation
~~~~~~~~~~~~~

To build this documentation locally:

.. code-block:: bash

   pip install "clustrix[docs]"
   cd docs
   make html

The rendered site lands in ``docs/build/html``.

Everything
~~~~~~~~~~

.. code-block:: bash

   pip install "clustrix[all]"

Verification
------------

This runs entirely on your own machine -- no cluster, no credentials:

.. code-block:: python

   import clustrix
   print(clustrix.__version__)

   from clustrix import cluster, configure

   configure(cluster_type="local")  # run in the calling process

   @cluster(cores=2)
   def test_function():
       return "Clustrix is working!"

   result = test_function()
   print(result)  # Should print: "Clustrix is working!"

Check the CLI at the same time:

.. code-block:: bash

   clustrix config   # prints the current settings

Verify Jupyter Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

If you installed widget support, test the magic command in Jupyter. Importing
``clustrix`` registers the magic but does not display anything -- the widget
appears when you run ``%%remote``, in a cell of its own:

.. code-block:: python

   import clustrix  # registers the magic; displays nothing

.. code-block:: ipython3

   %%remote

Setting ``CLUSTRIX_AUTO_WIDGET=1`` before the import restores the older
display-on-import behaviour.

Next
----

- :doc:`quickstart` -- a working result in five minutes.
- :doc:`introduction` -- what Clustrix is for, and when to reach for
  something else.
