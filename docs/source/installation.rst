.. _installation:

Installation
============

.. warning::

   **The version on PyPI is behind this documentation.** When this page was
   written, ``pip install clustrix`` installed **0.1.1**. That release has
   none of the loop division, input staging, host key verification or result
   authentication described here. Until a newer release is published, install
   from the repository::

       pip install "git+https://github.com/ContextLab/clustrix.git@master"

   Check what you have with ``python -c "import clustrix;
   print(clustrix.__version__)"`` and see the
   `changelog <https://github.com/ContextLab/clustrix/blob/master/CHANGELOG.md>`_.

Clustrix is a pure-Python package. The base install pulls in everything the
verified backends need -- SSH (``paramiko``), serialization (``cloudpickle``,
``dill``), the CLI (``click``) and the Hugging Face Jobs client
(``huggingface_hub``). Everything else is an optional extra.

Requirements
------------

- **Python 3.10 to 3.13** locally (``requires-python = ">=3.10"``).
- **The same Python minor version on the remote side**, or conda there so
  that Clustrix can create it. A serialized function does not load under a
  different minor version, and a mismatch is refused when the job is
  submitted.
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

There are none, and there is nothing to install. Clustrix has no Kubernetes
backend and no AWS / GCP / Azure / Lambda Cloud VM backend, and no cost
monitoring or cloud pricing API. Each backend is planned for a future release
and each has a tracking issue -- see :ref:`removed-backends` for the list and
the links.

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

   configure(cluster_type="local")

   @cluster(cores=2)
   def squares(n):
       found = []
       for i in range(n):
           found.append(i * i)
       return found

   print(clustrix.explain(squares))
   print(squares(5))  # [0, 1, 4, 9, 16]

Check the CLI at the same time:

.. code-block:: bash

   clustrix config   # prints the current settings

Verify Jupyter Integration
~~~~~~~~~~~~~~~~~~~~~~~~~~

If you installed widget support, run these in two notebook cells. Loading
the extension displays nothing; ``%%remote`` shows the widget
(:doc:`api/notebook_magic`):

.. code-block:: ipython

   %load_ext clustrix

.. code-block:: ipython

   %%remote

Setting ``CLUSTRIX_AUTO_WIDGET=1`` before the import restores the older
display-on-import behaviour.

Next
----

- :doc:`quickstart` -- a working result in five minutes.
- :doc:`introduction` -- what Clustrix is for, and when to reach for
  something else.
