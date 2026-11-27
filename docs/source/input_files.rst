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
   calls, and the values of the globals they read. Only your own code is
   read this way: a function from an installed package (anything under a
   ``site-packages`` or ``dist-packages`` directory) is never searched, and
   neither is one whose file is outside the project. If the working
   directory is your home directory, a directory containing it, or ``/``,
   there is no project and nothing is looked for in code at all; see
   :ref:`auto-stage-limits`.

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
   * - The same absolute path exists on the cluster and holds the same data
     - Used in place. Nothing is copied. This is the shared-filesystem case.
       "The same data" is checked, not assumed from the path: a file must
       have the same size and the same SHA-256, computed on the cluster
       with ``sha256sum`` (or ``shasum``); a directory must hold the same
       files, each with the same size and SHA-256. Files left out here
       (credentials, ``no_data``, symbolic links) may be there too.
       Above 256 MB in all nothing is hashed on either side, so that a large
       dataset on a shared filesystem is not read twice before the first
       call: names and sizes are compared, and the logged reason says the
       contents were not.
   * - The same absolute path exists on the cluster and holds something else
     - ``StagingError``, naming what differs. Clustrix does not guess which
       copy is right. ``no_data=[path]`` uses the cluster's copy as it is.
   * - The same absolute path exists there but cannot be compared
     - Uploaded, as if it were not there. This is what happens when the
       cluster has no SHA-256 command, or a file name in the directory has
       a newline in it.
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

With logging at ``INFO`` each input is reported with what was done and why:

.. code-block:: text

   INFO clustrix.auto_stage: Input /shared/project/data/table.csv (8 B): in place (the cluster has it at the same path: same size and sha256)
   INFO clustrix.auto_stage: Input /home/researcher/project/data/in.csv (8 B): uploaded as /home/clx/clustrix/data/2d75b0345428fe7c99e2/in.csv (not readable at that path on the cluster)

Comparing in place hashes the whole file on both sides, however large. It
is done once per input per Python session: a second call does not hash
again unless the local file's size or modification time changed. A copy
changed *on the cluster* between two calls of one session is therefore not
noticed.

The copies are private and read-only
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``<remote_work_dir>/data`` is mode ``0700``, each copy inside it ``0500``
and each file ``0400``, whatever the cluster's umask: a file that only you
can read here is not readable by other accounts there. A store created by
an earlier version with open permissions is closed the next time a call
stages something.

Because a copy is read-only, **a job cannot write to a path that was
staged**. That matters when an existing *output* file is among the
arguments -- last run's ``results/out.csv`` -- because an existing path
looks the same whether the function reads it or is about to overwrite it.
The job then fails, and the error says why:

.. code-block:: text

   PermissionError: [Errno 13] Permission denied (data/out.txt exists on this machine, so it was staged for the job as an input, and staged inputs are read-only. If the job writes it, keep it out with @cluster(no_data=['data/out.txt'])): 'data/out.txt'

Names clustrix uses itself
~~~~~~~~~~~~~~~~~~~~~~~~~~

A relative input is linked into the job's working directory under its own
name, and clustrix writes its own files there: ``job.sh``, ``job.out``,
``job.err``, ``job.exit``, ``function_data.pkl``, ``result.pkl``,
``error.pkl``, ``slurm-<id>.out`` and a few more
(``clustrix.utils.RESERVED_JOB_DIRECTORY_NAMES``). An input whose first
path component is one of those is refused before anything is submitted:

.. code-block:: text

   StagingError: An input's name collides with a file clustrix itself writes in every job directory: 'result.pkl' (argument 'path') would be linked into the job directory as 'result.pkl'. Rename the input, pass it by an absolute path, or keep it out with no_data=[...].

.. _auto-stage-limits:

Limits
------

These are deliberate.

A path found in code must be inside the project
   The project is the git top level, or the working directory when there is
   no repository. The check is made after resolving symbolic links. A path
   outside it that appears only in code is skipped. A path you pass as an
   argument or list in ``data=[...]`` may be anywhere, because you named it.

The home directory is never a project
   Neither is a directory that contains it, or ``/``. Under a home directory
   are ``~/.ssh``, ``~/.kaggle`` and every package installed with ``pip
   install --user``, so "inside the project" would describe a token file
   named by a constant in somebody else's library. When a git checkout is
   that wide (dotfiles kept in ``~``) the project is narrowed to the working
   directory. When the working directory itself is that wide, **nothing
   found in code is staged**; with logging at ``INFO`` the call says so.
   Pass the inputs as arguments or list them in ``data=[...]``, or run from
   the project's own directory.

Credential files are refused
   A path that looks like a credential is never uploaded, however it was
   named, and whether it was named itself or is inside a directory that
   was:

   .. code-block:: text

      WARNING clustrix.auto_stage: Not staging: /home/researcher/project/data/.env looks like a credential and is never uploaded

   The path is judged twice: as written, and with every symbolic link in it
   resolved. ``keys/aws-prod`` where ``keys`` is a link to ``~/.ssh`` is
   refused, and the message names the real path.

   What counts is a list of names, not an understanding of the file:
   private keys and certificates (``*.pem``, ``*.key``, ``id_rsa*``,
   ``id_ed25519*``, ``id_ecdsa*``, ...), ``.env`` and ``*.env``,
   ``.netrc``, ``.pgpass``, ``.my.cnf``, ``.vault-token``, ``.npmrc``,
   ``.pypirc``, ``kaggle.json``, ``application_default_credentials.json``,
   a HuggingFace ``token`` file, and anything inside ``.ssh``, ``.aws``,
   ``.azure``, ``.gnupg``, ``.kube``, ``.docker``, ``.kaggle`` or
   ``.config/gcloud``. The full list is
   ``clustrix.staging.SENSITIVE_PATTERNS``. One check reads the file: a
   ``.json`` file of at most 64 kB that contains ``"type":
   "service_account"`` is a Google Cloud key whatever it is called.
   **A secret in a file with an ordinary name is not recognised** --
   ``notes.txt`` with a password in it is staged like any other file.

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
