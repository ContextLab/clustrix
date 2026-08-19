.. _installation:

Installation
============

.. warning::

   **The version on PyPI is behind this documentation.** ``pip install
   clustrix`` currently installs **0.1.1**; these pages document **0.2.0**.

   That gap is not cosmetic. 0.1.1 predates fixes for two defects that
   matter:

   - ``@cluster`` could return a fabricated string instead of your result
     when a function's source could not be read.
   - Results fetched from a remote host were unpickled without
     authentication, which is a remote-to-local code execution path.

   Until 0.2.0 is published, install from the repository::

       pip install "git+https://github.com/ContextLab/clustrix.git@master"

   Everything below describes 0.2.0. If you installed from PyPI, check what
   you actually have with ``python -c "import clustrix;
   print(clustrix.__version__)"``.

Clustrix is a pure-Python package. The base install pulls in everything the
verified backends need -- SSH (``paramiko``), serialization (``cloudpickle``,
``dill``), the CLI (``click``) and the Hugging Face Jobs client
(``huggingface_hub``). Everything else is an optional extra.

Requirements
------------

- **Python 3.10 or newer** (``requires-python = ">=3.10"``).
- For remote backends: SSH access to the target machine, and the scheduler's
  own client tools (``sbatch``/``squeue``) present *on that machine*. Nothing
  scheduler-specific is needed locally.
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

Kubernetes and cloud provider extras
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

There are none, and there is nothing to install. The Kubernetes backend and
the AWS / GCP / Azure / Lambda Cloud VM backends were removed in v0.2.0
because none of them had ever been shown to run a job end to end. The cost
monitoring and cloud pricing API went with them. They are planned for a future
release and each has a tracking issue -- see :ref:`removed-backends` for the
list and the links.

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
