.. _input-files:

Input files
===========

A function sent to a cluster takes its code with it. The files it opens are a
separate matter: ``open("data/scores.csv")`` works on your machine and raises
``FileNotFoundError`` on a host that has no such file. On the ``ssh`` and
``slurm`` backends Clustrix finds the local files a call names and makes them
available to the job before it runs. This is on by default
(``ClusterConfig.auto_stage``). The implementation is
``clustrix/auto_stage.py``.

This page covers that automatic mechanism. Declaring data explicitly with
:func:`clustrix.data_package` is a separate mechanism, described in
:doc:`data_packages`; :ref:`the last section <auto-stage-or-package>` says
when to use which.

.. contents:: On this page
   :local:
   :depth: 1

What it does
------------

.. code-block:: python

   # cluster-required: needs an SSH host; data/scores.csv exists only locally
   import csv

   from clustrix import cluster, configure

   configure(
       cluster_type="ssh",
       cluster_host="gpu-box.example.edu",
       username="researcher",
       remote_work_dir="/home/researcher/clustrix",
   )


   @cluster(cores=1, memory="1GB")
   def mean_score():
       with open("data/scores.csv") as handle:
           rows = list(csv.DictReader(handle))
       return sum(float(r["score"]) for r in rows) / len(rows)


   print(mean_score())

Run against a real ``sshd`` with logging at ``INFO`` (host and paths replaced
by placeholders), the first call logged an upload and the second did not
upload again:

.. code-block:: text

   INFO clustrix.auto_stage: Input /home/researcher/project/data/scores.csv (32 B): uploaded as /home/researcher/clustrix/data/5e2f56d95eac994f5c60/scores.csv
   2.6666666666666665
   INFO clustrix.auto_stage: Input /home/researcher/project/data/scores.csv (32 B): already uploaded as /home/researcher/clustrix/data/5e2f56d95eac994f5c60/scores.csv
   2.6666666666666665

Where paths are looked for
--------------------------

Arguments
   Every ``pathlib.Path`` and every string that is written like a path (it
   contains a separator, starts with ``~`` or has a file extension), at any
   depth inside lists, tuples, sets and dicts. A bare word such as
   ``"train"`` is treated as a path only when the parameter it was passed to
   has a name like ``path``, ``file``, ``dir``, ``data`` or ``checkpoint``.
   URLs (``s3://...``, ``https://...``) are ignored.

The function's code
   String constants in the decorated function, in the project functions it
   calls, and the values of the globals they read.

``@cluster(data=[...])``
   Paths you list. Use this for anything the two sources above cannot see.

A candidate counts only if it exists on your machine. A directory is staged
with everything beneath it; symbolic links inside it are not followed.

What happens to a file that was found
-------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - Situation
     - Result
   * - The same absolute path exists on the cluster with the same size
     - Used in place. Nothing is copied. This is the shared-filesystem case.
   * - The same absolute path exists on the cluster with a different size
     - ``StagingError``. Clustrix does not guess which copy is right.
   * - A relative path (``"data/scores.csv"``)
     - Uploaded once over the existing SSH connection into
       ``<remote_work_dir>/data/<content id>/`` and linked into each job's
       working directory at the same relative path. The code runs unchanged.
   * - An absolute path passed as an argument
     - Uploaded, and the argument is rewritten to the cluster's copy. In a
       real run the function received
       ``/home/researcher/clustrix/data/1b014ec4ca4e73ab535d/notes.txt``
       in place of the local path.
   * - An absolute path written in the code
     - Uploaded, with a warning: the code cannot be rewritten, so the path
       will not exist on the cluster. Pass it as an argument instead.

Files are identified by content (by name, size and modification time above
256 MB), so an unchanged file is uploaded once however many calls name it.

.. _auto-stage-limits:

Limits
------

These are deliberate.

A path found in code must be inside the project
   The project is the git top level, or the working directory when there is
   no repository. The check is made after resolving symbolic links. A path
   outside it that appears only in code is skipped. A path you pass as an
   argument or list in ``data=[...]`` may be anywhere, because you named it.

Credential files are refused
   A path that looks like a credential (for example a ``.env`` file or a
   private key) is never uploaded, however it was named:

   .. code-block:: text

      WARNING clustrix.auto_stage: Not staging: /home/researcher/project/data/.env looks like a credential and is never uploaded

Large inputs stop the call
   When one call names more than ``auto_stage_max_bytes`` (default
   1,000,000,000) the call raises before anything is copied:

   .. code-block:: text

      StagingError: This call names 32 B of local input, more than clustrix copies to a cluster automatically (auto_stage_max_bytes = 10 B):
              32 B  /home/researcher/project/data/scores.csv  (argument 'path')
      Put large data on the cluster yourself and pass its path there, raise auto_stage_max_bytes, or exclude a path with no_data=[...].

   (Produced by setting the limit to 10 bytes.)

Nothing the cluster reports can add a file
   There is no channel by which a job asks for a file.

.. _auto-stage-unseen:

What it cannot see
~~~~~~~~~~~~~~~~~~

A path that is computed when the code runs names nothing beforehand:
``os.path.join(root, f"{subject}.csv")``, a glob, a path read from a
configuration file. In a real run this function failed on the cluster with
``FileNotFoundError: [Errno 2] No such file or directory:
'data/subjects/s01.csv'``:

.. code-block:: python

   # cluster-required: needs an SSH host
   import os

   from clustrix import cluster


   @cluster(cores=1)
   def load(subject):
       return open(os.path.join("data", "subjects", f"{subject}.csv")).read()

and this one succeeded, because the directory is listed:

.. code-block:: python

   # cluster-required: needs an SSH host
   import os

   from clustrix import cluster


   @cluster(cores=1, data=["data/subjects"])
   def load(subject):
       return open(os.path.join("data", "subjects", f"{subject}.csv")).read()

Where it does not apply
~~~~~~~~~~~~~~~~~~~~~~~

* **HuggingFace Jobs.** Use a :doc:`data package <data_packages>`.
* **Asynchronous submission** (``async_submit=True``).
* **The local backend**, where the files are already there.

Outputs are not brought back
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Only the function's return value comes back. A file the job writes stays in
the job's working directory on the cluster, which is removed after a
successful job unless ``cleanup_on_success=False``. Return the data, or write
to a path on the cluster that you choose and collect it yourself.

Nothing is deleted
~~~~~~~~~~~~~~~~~~

The uploaded copies under ``<remote_work_dir>/data/`` are never removed
automatically, and ``cleanup_on_success`` does not touch them. Delete that
directory on the cluster when you no longer need it.

Controlling it
--------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Setting
     - Effect
   * - ``@cluster(data=["path", ...])``
     - Add paths to what is staged for calls of this function.
   * - ``@cluster(no_data=["path", ...])``
     - Keep paths out. In a real run, a function that opened
       ``data/scores.csv`` with ``no_data=["data/scores.csv"]`` found no such
       file on the cluster.
   * - ``configure(auto_stage=False)``
     - Turn the mechanism off.
   * - ``configure(auto_stage_max_bytes=N)``
     - Change the size at which a call stops.

.. _auto-stage-or-package:

Automatic staging or a data package
-----------------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - Automatic staging
     - :func:`clustrix.data_package`
   * - How data is chosen
     - Found from the call
     - You build a package and pass it
   * - Backends
     - ``ssh``, ``slurm``
     - All four; see the caveat below
   * - How the function reads it
     - The original path
     - ``pkg.path("name")``
   * - Where the bytes go
     - The cluster's filesystem, over SSH
     - Inside the payload when small; a private HuggingFace dataset
       repository when large
   * - Removed automatically
     - Never
     - Never

Use automatic staging for files on ``ssh`` and ``slurm``. Use a data package
on HuggingFace Jobs, or when you want an explicit handle that you can list
and delete.

.. warning::

   A data package too large to travel inline is uploaded to a private
   HuggingFace dataset repository. A worker on an ``ssh`` or ``slurm``
   cluster has no Clustrix installation to fetch it with, and dereferencing
   the package there raises ``StagingError`` ("... is stored in a HuggingFace
   repo, and this machine has no clustrix installation to fetch it with ...
   Build the package with force_local=True so that its contents travel with
   the job."). On those backends, let large files be staged automatically,
   or put them on the cluster's filesystem yourself. That error was read from
   the source; the path that raises it was not executed in this documentation
   build, which has no HuggingFace token.
