Tutorials
=========

Seven notebooks, meant to be read in order. Each was executed to produce the
output it shows: the first two on one machine, tutorials 3 to 5 against a
single-node test cluster (a real OpenSSH server and a real SLURM scheduler),
so their timings show the shape of a result and are not a benchmark. Tutorial
7 submits to a paid service and was not executed; it says so at the top.

They assume you know Python and have an account on a cluster, and nothing
about Clustrix.

.. toctree::
   :maxdepth: 1

   01_first_steps
   02_parallel_loops
   03_ssh_cluster
   04_slurm
   05_data
   06_notebook_widget
   07_huggingface_jobs

1. :doc:`01_first_steps` -- install, decorate a function, find out where it
   ran, ``configure()`` and ``clustrix.explain()``.
2. :doc:`02_parallel_loops` -- loops divided among worker processes: measured
   examples, the loops that are not divided and how to rewrite them, the
   run-time checks, and what no check can see.
3. :doc:`03_ssh_cluster` -- a remote machine over SSH: host keys, the remote
   environment, exceptions, a loop as several jobs, cleaning up.
4. :doc:`04_slurm` -- SLURM: partition, memory and time, one job per chunk,
   ``squeue`` and ``sacct``, time limits, failures and cancelling.
5. :doc:`05_data` -- input files: what is staged automatically, what is not,
   and what to do about output files.
6. :doc:`06_notebook_widget` -- the notebook configuration widget and the
   ``%clustrix`` magic, with screenshots.
7. :doc:`07_huggingface_jobs` -- Hugging Face Jobs: token, namespace,
   hardware, billing.

Two shorter pages sit beside them:

.. toctree::
   :maxdepth: 1

   usage_patterns
   filesystem_tutorial

- :doc:`usage_patterns` -- how to lay out code that uses ``@cluster``.
- :doc:`filesystem_tutorial` -- the read-only ``cluster_*`` helpers for
  looking at files on a cluster.

To run a tutorial yourself, download the ``.ipynb`` from
``docs/source/tutorials/`` in the repository and replace the host, user and
key in its ``configure()`` call with your own.
