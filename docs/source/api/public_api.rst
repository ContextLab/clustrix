Public API
==========

``clustrix.__all__`` is the public API. Every name in it is listed here with
the page that documents it. The list on this page was compared with
``sorted(clustrix.__all__)`` when it was written.

.. list-table::
   :header-rows: 1
   :widths: 46 54

   * - Names
     - Documented in
   * - ``cluster``, ``explain``
     - :doc:`decorator`, :doc:`../parallel_loops`
   * - ``configure``, ``get_config``, ``ClusterConfig``
     - :doc:`config`, :doc:`../configuration`
   * - ``data_package``, ``DataPackage``, ``materialize_packages``,
       ``list_data_packages``, ``delete_data_package``, ``StagingError``
     - :doc:`../data_packages`
   * - ``cluster_ls``, ``cluster_find``, ``cluster_glob``, ``cluster_stat``,
       ``cluster_exists``, ``cluster_isdir``, ``cluster_isfile``,
       ``cluster_du``, ``cluster_count_files``, ``ClusterFilesystem``,
       ``FileInfo``, ``DiskUsage``
     - :doc:`filesystem`
   * - ``ModernClustrixWidget``, ``create_modern_cluster_widget``,
       ``display_modern_widget``, ``show_widget``,
       ``load_ipython_extension``
     - :doc:`notebook_magic`
   * - ``setup_ssh_keys``, ``setup_ssh_keys_with_fallback``,
       ``add_host_key``, ``find_ssh_keys``, ``list_ssh_keys``
     - :doc:`../ssh_setup` and below
   * - ``ClusterExecutor``, ``setup_environment``, ``ProfileManager``,
       ``adopt_profile_store``
     - Below
   * - ``DependencyAnalyzer``, ``DependencyGraph``, ``ImportInfo``,
       ``LocalFunctionCall``, ``FilesystemCall``, ``FileReference``,
       ``analyze_function_dependencies``, ``FilePackager``, ``PackageInfo``,
       ``PackagedFile``, ``ExecutionContext``, ``create_execution_context``,
       ``package_function_for_execution``
     - :ref:`Below <not-on-the-execution-path>`; not used when a job runs

There is no separate module to import for loop division or input staging.
``clustrix.parallelize``, ``clustrix.loop_workers`` and
``clustrix.auto_stage`` are used by ``@cluster``; the user-facing parts are
``clustrix.explain``, the ``cores`` and ``parallel`` parameters, and the
``data`` / ``no_data`` keywords and ``auto_stage`` settings.

SSH helpers
-----------

.. py:function:: clustrix.setup_ssh_keys(config, password, cluster_alias=None, key_type="ed25519", force_refresh=False, auto_refresh_days=None)
   :no-index:

   Generate a key, install it on ``config.cluster_host`` using ``password``
   once, test it, and optionally write a ``~/.ssh/config`` entry. Returns a
   dict with ``success``, ``key_path``, ``key_already_existed``,
   ``key_deployed``, ``connection_tested``, ``error`` and ``details``.
   ``auto_refresh_days`` is accepted and not implemented. See
   :doc:`../ssh_setup`.

.. autofunction:: clustrix.setup_ssh_keys_with_fallback

.. autofunction:: clustrix.find_ssh_keys

.. autofunction:: clustrix.list_ssh_keys

.. autofunction:: clustrix.add_host_key

``add_host_key`` trusts whatever key the host presents at that moment. To
record the key a refused connection was actually offered, after you have
compared its fingerprint, use
:func:`clustrix.ssh_security.trust_offered_host_key`; see
:ref:`host-key-verification`.

.. autofunction:: clustrix.ssh_security.trust_offered_host_key

.. autoexception:: clustrix.ssh_security.HostKeyVerificationError
   :no-members:

``ClusterExecutor``
-------------------

The object ``@cluster`` uses to connect, submit, poll and fetch. Use it
directly to test a connection or to manage a job yourself.

.. code-block:: python

   # cluster-required: needs an SSH host
   from clustrix import ClusterExecutor, get_config

   executor = ClusterExecutor(get_config())
   try:
       executor.connect()
       print("connected")
   finally:
       executor.disconnect()

While waiting for a result the executor checks after one second and backs
off from there; ``job_poll_interval`` is the longest it waits between checks,
not the interval. ``job_wait_timeout`` bounds the whole wait.

``setup_environment``
---------------------

.. autofunction:: clustrix.setup_environment

Every remote job calls this itself. Call it directly only to build an
environment ahead of time.

Profiles
--------

``ProfileManager`` is the store of named configurations behind the notebook
widget and ``%clustrix config <profile>``. It lives in
``<config dir>/profiles/profiles.yml``; the constructor takes a different
directory.

.. code-block:: python

   import tempfile

   from clustrix import ClusterConfig, ProfileManager

   manager = ProfileManager(tempfile.mkdtemp())
   manager.create_profile("mine", ClusterConfig(cluster_type="local", default_cores=2))
   print("mine" in manager.get_profile_names())
   print(manager.load_profile("mine").default_cores)
   manager.remove_profile("mine")
   print("mine" in manager.get_profile_names())

.. code-block:: text

   True
   2
   False

.. autofunction:: clustrix.adopt_profile_store

.. _not-on-the-execution-path:

Exported, but not used when a job runs
--------------------------------------

``clustrix.dependency_analysis`` (``DependencyAnalyzer``,
``DependencyGraph``, ``ImportInfo``, ``LocalFunctionCall``,
``FilesystemCall``, ``FileReference``, ``analyze_function_dependencies``)
and ``clustrix.file_packaging`` (``FilePackager``, ``PackageInfo``,
``PackagedFile``, ``ExecutionContext``, ``create_execution_context``,
``package_function_for_execution``) are importable from ``clustrix``.
**Nothing that** ``@cluster`` **does calls them.** A job's code, its
dependencies and its input files are found by other means (cloudpickle, the
payload's imports, :doc:`../input_files`). They are kept for existing callers
and have no guide in this documentation.
