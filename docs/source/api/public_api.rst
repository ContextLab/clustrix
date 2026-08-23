Public API reference
====================

Every name in ``clustrix.__all__`` is public by declaration: it is what a
user is invited to import. Most are documented on their own pages (see the
table below); this page exists so that none of them is documented nowhere.

+-------------------------------------+-----------------------------------+
| Export group                        | Documented on                     |
+=====================================+===================================+
| ``cluster``, ``configure``,         | :doc:`decorator`,                 |
| ``get_config``, ``ClusterConfig``   | :doc:`config`                     |
+-------------------------------------+-----------------------------------+
| ``LocalExecutor``,                  | :doc:`local_executor`             |
| ``create_local_executor``           |                                   |
+-------------------------------------+-----------------------------------+
| ``cluster_ls`` …                    | :doc:`filesystem`                 |
| ``cluster_count_files``,            |                                   |
| ``ClusterFilesystem``, ``FileInfo``,|                                   |
| ``DiskUsage``                       |                                   |
+-------------------------------------+-----------------------------------+
| ``DependencyAnalyzer`` …            | :doc:`dependency_analysis`        |
| ``analyze_function_loops``          |                                   |
+-------------------------------------+-----------------------------------+
| ``data_package``,                   | :doc:`../data_packages`           |
| ``list_data_packages``,             |                                   |
| ``delete_data_package``,            |                                   |
| ``materialize_packages``,           |                                   |
| ``DataPackage``, ``StagingError``   |                                   |
+-------------------------------------+-----------------------------------+
| ``%%remote``, ``%clustrix``         | :doc:`notebook_magic`             |
+-------------------------------------+-----------------------------------+

The names below had no page before 0.2.0 (#162). Each is documented here;
all docstrings are authoritative for signatures.

SSH key automation -- ``setup_ssh_keys``, ``setup_ssh_keys_with_fallback``,
``add_host_key``
--------------------------------------------------------------------------------

The one-call setup described in :doc:`../ssh_setup`
generates an Ed25519 key, deploys it, and writes the matching
``~/.ssh/config`` entry:

.. code-block:: python

   from clustrix import ClusterConfig, setup_ssh_keys_with_fallback

   config = ClusterConfig(
       cluster_type="slurm",
       cluster_host="cluster.example.edu",
       username="myuser",
   )
   result = setup_ssh_keys_with_fallback(config)  # -> {"success": True, ...}

``setup_ssh_keys(config)`` is the non-fallback core (key generation and
deployment only); ``setup_ssh_keys_with_fallback(config)`` adds password
discovery (Colab secrets, host-named environment variables, then a prompt)
so it works unattended where credentials are available. Both return a dict
with ``success`` and, on failure, ``error``.

``add_host_key(hostname, port=22)`` appends one host's key to your
``~/.ssh/known_hosts`` — the manual equivalent of answering "yes" to
OpenSSH's fingerprint prompt. Prefer connecting once through clustrix's
default ``reject`` policy, which prints exactly this call's ``ssh-keyscan``
equivalent when it refuses; use ``add_host_key`` when you have verified the
fingerprint out of band.

Environment replication -- ``setup_environment``
------------------------------------------------

.. code-block:: python

   # cluster-required: builds a virtualenv on the configured cluster
   from clustrix import setup_environment

   message = setup_environment(
       work_dir="/scratch/myuser/env",
       requirements={"numpy": "2.0.1"},
       config=config,
   )
   print(message)

Builds (or reuses) the remote virtualenv for ``config`` from the
``requirements`` mapping and returns a human-readable report line. It is
the same routine every backend runs inside a job; calling it yourself is
for pre-warming a cluster or checking what *would* be installed. Not needed
for ``huggingface``, which replicates the environment inside its container.

Profiles -- ``ProfileManager``
------------------------------

.. code-block:: python

   from clustrix import ClusterConfig, ProfileManager

   manager = ProfileManager()
   manager.get_profile_names()   # -> ["University SLURM Cluster", ...]
   manager.create_profile(
       "mine",
       ClusterConfig(cluster_type="local", default_cores=2),
   )
   config = manager.load_profile("mine")
   manager.remove_profile("mine")

Named clusters stored under ``~/.clustrix/profiles/``. The notebook widget
and ``%clustrix config <name>`` are front-ends to the same store. A store
written before clustrix recorded provenance loads with a warning naming the
affected profiles, and releasing a stored credential to a host such a
profile named is refused until you run ``clustrix.adopt_profile_store()``
after recognising every entry.

Widget constructors -- ``ModernClustrixWidget``,
``create_modern_cluster_widget``, ``display_modern_widget``, ``show_widget``
-----------------------------------------------------------------------------

Four spellings of the notebook panel shown by ``%%remote``
(:doc:`notebook_magic`), for callers who want the object rather than the
magic:

.. code-block:: python

   from clustrix import (
       ModernClustrixWidget,          # the widget class itself
       create_modern_cluster_widget,  # -> widget instance
       display_modern_widget,         # construct + display, returns widget
       show_widget,                   # alias for display_modern_widget
   )

Importing clustrix never displays it; see ``CLUSTRIX_AUTO_WIDGET`` in
:doc:`config`.

Packaging internals -- ``PackageInfo``, ``ExecutionContext``,
``create_execution_context``, ``package_function_for_execution``,
``PackagedFile``
---------------------------------------------------------------------------

The packaging pipeline (:doc:`file_packaging`) exposes these for tooling
that wants to inspect what would travel with a function before submitting
it:

.. code-block:: python

   from clustrix import ClusterConfig, package_function_for_execution

   def train(matrix):
       return sum(matrix)

   packaged = package_function_for_execution(
       train,
       ClusterConfig(cluster_type="local"),
       ([1, 2, 3],),
   )
   packaged.package_id        # the staged package's id
   packaged.dependencies      # DependencyGraph: what would travel with it
   packaged.size_bytes

Nothing in the execution path requires you to touch these; they exist so
dependency analysis is inspectable rather than opaque.
