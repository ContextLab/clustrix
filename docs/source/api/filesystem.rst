Filesystem Utilities
====================

.. currentmodule:: clustrix.filesystem

Every member of this module is documented explicitly below (grouped by
purpose). A blanket ``automodule:: :members:`` is deliberately not used here: this
project's global ``autodoc_default_options`` sets ``members: True``, so an
``automodule`` directive combined with the explicit per-member directives
below would document every class and function twice.

Overview
--------

One set of calls -- ``cluster_ls``, ``cluster_find``, ``cluster_stat``,
``cluster_exists``, ``cluster_isdir``, ``cluster_isfile``, ``cluster_glob``,
``cluster_du``, ``cluster_count_files`` -- answers questions about a
filesystem, and the same call works whether that filesystem is the one under
your feet or one on a cluster. Which it is depends on the
:class:`~clustrix.config.ClusterConfig` you pass, not on how you write the
call. In other words, you write the code once and choose the machine later.

``cluster_stat`` and ``cluster_du`` return :class:`FileInfo` and
:class:`DiskUsage` rather than tuples, so the fields have names.

These utilities are **read-only**. There is no ``cluster_put``, no
``cluster_get``, and no copy or delete. They tell you what is on a filesystem;
moving data onto one is your job, and is tracked as `issue #151
<https://github.com/ContextLab/clustrix/issues/151>`_.

Behind the Scenes
------------------

Every ``cluster_*()`` convenience function (``cluster_ls``, ``cluster_stat``,
...) is a thin wrapper that constructs a fresh :class:`ClusterFilesystem`
from the ``config`` you pass, calls the matching method, and lets it go --
verified directly against ``clustrix/filesystem.py``: each one is
``fs = ClusterFilesystem(config); return fs.<method>(...)``. What that
instance actually does depends on ``config.cluster_type``:

**Local (``cluster_type="local"``).** Every operation is a plain ``os`` /
``glob`` call against ``config.local_work_dir`` (or the current directory if
that's unset) -- no network, no subprocess, nothing to open or close.

**Remote (anything else).** Operations go over SFTP. The connection is
opened *lazily* -- ``ClusterFilesystem.__init__`` does not connect; the
first method call that needs one triggers ``_get_ssh_client()``, which
opens a ``paramiko.SSHClient`` (applying ``config.ssh_host_key_policy`` --
see :doc:`config` -- and the ``ssh_connect_timeout``/``auth_timeout``/
``banner_timeout`` settings so an unreachable host fails fast instead of
hanging), and ``_get_sftp_client()``, which opens an SFTP channel on top of
it. Both are cached on the instance and reused for every subsequent call on
*that instance* -- but because each ``cluster_*()`` call builds its own new
``ClusterFilesystem``, calling several ``cluster_*()`` functions in a row
against a remote config opens (and, via ``__del__``, closes) a **separate**
SSH connection per call, not one shared connection. Instantiate
``ClusterFilesystem`` yourself and reuse it across calls when that matters
for a tight loop:

.. code-block:: python

   # cluster-required: needs a real, reachable remote host
   from clustrix.filesystem import ClusterFilesystem
   from clustrix.config import ClusterConfig

   remote_config = ClusterConfig(
       cluster_type="slurm", cluster_host="cluster.edu", username="researcher"
   )
   fs = ClusterFilesystem(remote_config)
   for name in fs.ls("data/"):        # first call opens the connection
       info = fs.stat(f"data/{name}")  # reused, not reopened

**Paths** are resolved against ``config.local_work_dir`` (local) or
``config.remote_work_dir`` (remote) unless the path you pass is already
absolute -- see ``ClusterFilesystem._get_full_path``.

**"Already on the cluster" detection.** ``ClusterFilesystem.__init__`` also
calls ``_auto_detect_cluster_location()``, which can switch a *non-local*
config over to local operations transparently: if this process's own
hostname matches ``config.cluster_host`` *and* ``config.remote_work_dir`` is
actually visible on this filesystem (a real, checkable fact -- as opposed to
the previous heuristic of comparing hostnames by substring, which judged a
laptop on a VPN to be the SLURM login node it was tunnelling into), it
rewrites ``config.cluster_type`` to ``"local"`` in place and logs that it
did so. This matters for code that runs *on* a shared-filesystem HPC
cluster already: it avoids SSH-ing to itself over the loopback interface
for every filesystem call.

Core Functions
--------------

Directory Operations
~~~~~~~~~~~~~~~~~~~~

.. autofunction:: clustrix.cluster_ls
.. autofunction:: clustrix.cluster_find
.. autofunction:: clustrix.cluster_glob
.. autofunction:: clustrix.cluster_count_files

File Operations
~~~~~~~~~~~~~~~

.. autofunction:: clustrix.cluster_stat
.. autofunction:: clustrix.cluster_exists
.. autofunction:: clustrix.cluster_isdir
.. autofunction:: clustrix.cluster_isfile

Storage Operations
~~~~~~~~~~~~~~~~~~

.. autofunction:: clustrix.cluster_du

Data Classes
------------

.. autoclass:: clustrix.filesystem.FileInfo
   :members:
   :undoc-members:

.. autoclass:: clustrix.filesystem.DiskUsage
   :members:
   :undoc-members:

Core Implementation
-------------------

.. autoclass:: clustrix.filesystem.ClusterFilesystem
   :members:
   :undoc-members:
   :show-inheritance:

Usage Examples
--------------

Basic Operations
~~~~~~~~~~~~~~~~

Against a real remote cluster, ``config`` would carry ``cluster_host`` and
credentials (this specific block needs one to run, so it's marked
accordingly); everything below it runs identically against a local
directory, and is executed for real against this project's own checkout as
part of this page's own test suite:

.. code-block:: python

    # cluster-required: needs a real, reachable remote host
    from clustrix import cluster_ls, cluster_find, cluster_stat
    from clustrix.config import ClusterConfig

    config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="cluster.edu",
        username="researcher",
        remote_work_dir="/scratch/project"
    )

    files = cluster_ls("data/", config)
    csv_files = cluster_find("*.csv", "datasets/", config)
    file_info = cluster_stat("large_dataset.h5", config)
    print(f"Size: {file_info.size:,} bytes")

.. code-block:: python

    from clustrix import cluster_ls, cluster_find, cluster_stat
    from clustrix.config import ClusterConfig

    config = ClusterConfig(cluster_type="local", local_work_dir=".")

    # List directory contents
    files = cluster_ls(".", config)

    # Find Python files recursively
    py_files = cluster_find("*.py", ".", config)

    # Get file information
    with open("example.txt", "w") as f:
        f.write("sample\n")
    file_info = cluster_stat("example.txt", config)
    print(f"Size: {file_info.size:,} bytes")

Data-Driven Workflows
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix import cluster, cluster_glob, cluster_stat

    @cluster(cores=8)
    def process_datasets(config):
        # Find all data files on the cluster
        data_files = cluster_glob("*.csv", "input/", config)
        
        results = []
        # Sequential -- see the auto-parallelization contract in limitations.
        for filename in data_files:
            # Check file size before processing
            file_info = cluster_stat(filename, config)
            if file_info.size > 100_000_000:  # Large files
                result = process_large_file(filename, config)
            else:
                result = process_small_file(filename, config)
            results.append(result)
        
        return results

Local vs Remote Operations
~~~~~~~~~~~~~~~~~~~~~~~~~~

The same function call works against either kind of config -- only the
config object passed to it changes:

.. code-block:: python

    from clustrix import cluster_ls
    from clustrix.config import ClusterConfig

    local_config = ClusterConfig(cluster_type="local", local_work_dir=".")
    local_files = cluster_ls(".", local_config)

.. code-block:: python

    # cluster-required: needs a real, reachable remote host
    from clustrix import cluster_ls
    from clustrix.config import ClusterConfig

    remote_config = ClusterConfig(
        cluster_type="slurm",
        cluster_host="cluster.edu",
        username="researcher"
    )
    remote_files = cluster_ls(".", remote_config)

Pattern Matching
~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix import cluster_find, cluster_glob, cluster_count_files
    from clustrix.config import ClusterConfig

    config = ClusterConfig(cluster_type="local", local_work_dir=".")

    # Find all Python files
    py_files = cluster_find("*.py", "clustrix/", config)

    # Use glob patterns
    # Patterns are plain shell globs -- brace expansion is not supported,
    # so match each extension separately.
    data_files = (
        cluster_glob("*.yml", ".", config)
        + cluster_glob("*.json", ".", config)
    )

    # Count files by type
    total_files = cluster_count_files(".", "*", config)
    python_files = cluster_count_files(".", "*.py", config)

Directory Usage Analysis
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from clustrix import cluster_du
    from clustrix.config import ClusterConfig

    config = ClusterConfig(cluster_type="local", local_work_dir="clustrix")

    # Get directory usage information
    usage = cluster_du(".", config)
    print(f"Total size: {usage.total_gb:.2f} GB")
    print(f"File count: {usage.file_count:,}")
    if usage.file_count > 0:
        print(f"Average file size: {usage.total_mb/usage.file_count:.1f} MB")

Error Handling
--------------

.. code-block:: python

    from clustrix import cluster_stat, cluster_exists
    from clustrix.config import ClusterConfig

    config = ClusterConfig(cluster_type="local", local_work_dir=".")

    try:
        file_info = cluster_stat("nonexistent.txt", config)
    except FileNotFoundError:
        print("File does not exist")

    # Safe existence check
    if cluster_exists("setup.py", config):
        file_info = cluster_stat("setup.py", config)

Best Practices
--------------

1. **Use config-driven execution**: Pass `ClusterConfig` objects to enable local/remote switching
2. **Check file existence**: Use `cluster_exists()` before operations that assume file presence
3. **Handle large directories carefully**: Remote operations on large directories may be slow
4. **Use appropriate patterns**: Leverage `cluster_find()` and `cluster_glob()` for efficient file discovery
5. **Cache results**: Store file listings locally when processing many files

See Also
--------

- :doc:`../tutorials/filesystem_tutorial` - Comprehensive tutorial with examples
- :doc:`config` - Configuration management
- :doc:`decorator` - Using filesystem utilities with the @cluster decorator