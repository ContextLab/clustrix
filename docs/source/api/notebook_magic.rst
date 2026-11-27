Notebook magics and the widget
==============================

Install the widget's dependencies with ``pip install "clustrix[widget]"``.

Magics
------

.. code-block:: ipython

   %load_ext clustrix

``%load_ext clustrix`` registers the magics below and puts ``cluster``,
``configure`` and ``get_config`` in the notebook's namespace if those names
are free. ``import clustrix`` also registers the magics. Neither displays
anything.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Magic
     - Effect
   * - ``%%remote``
     - Shows the configuration widget, then runs the rest of the cell as
       ordinary code.
   * - ``%clustrix status``
     - Prints the backend ``@cluster`` would use now and its default
       resources; for ``ssh`` and ``slurm`` also the host, user and work
       directory. The backend is the one in effect, not the ``cluster_type``
       field: with no ``cluster_host`` an ``ssh`` or ``slurm`` configuration
       runs the function in this process, and is reported as ``local (no
       cluster host set; cluster_type is 'slurm')``. Returns nothing.
       ``%clustrix`` alone does the same.
   * - ``%clustrix config``
     - Prints the same summary and returns the ``ClusterConfig``.
   * - ``%clustrix config <profile>``
     - Makes a saved profile the live configuration. The profile replaces
       the configuration; it is not merged into it.
   * - ``%clustrix load <file>``
     - Loads a YAML or JSON configuration file.
   * - ``%%clusterfy``
     - Deprecated alias for ``%%remote``. It works and emits a
       ``DeprecationWarning``.

Every form except ``status`` returns the resulting ``ClusterConfig`` (IPython
shows it as the result of the line), or ``None`` when the command was
refused. From a real IPython session, with the returned values left out:

.. code-block:: text

   In [1]: %load_ext clustrix

   In [2]: %clustrix status
   backend      : local (no cluster host set; cluster_type is 'slurm')
   resources    : 4 cores, 8GB, 01:00:00

   In [3]: %clustrix config nope
   ❌ No profile named 'nope'. Available: HuggingFace Jobs (CPU), HuggingFace Jobs (GPU), Local all cores, Local single-core, SLURM cluster, SSH remote machine

   In [4]: c = %clustrix config Local all cores
   ✅ Applied profile 'Local all cores'

   In [5]: print(c.cluster_type, c.default_cores, c.cluster_host)
   local -1 None

   In [6]: %clustrix frobnicate
   ❌ Unknown command 'frobnicate'. Try: status | config | config <profile> | load <file>

``%clustrix load`` with a file that does not exist raises
``FileNotFoundError``.

Setting the environment variable ``CLUSTRIX_AUTO_WIDGET=1`` before importing
Clustrix makes the widget appear on import. By default it does not.

The widget
----------

There is one widget, :class:`clustrix.ModernClustrixWidget`.

.. image:: ../_static/widget/widget-slurm.png
   :alt: The configuration widget in JupyterLab's dark theme, SLURM selected
   :width: 640px

Three things are kept apart
~~~~~~~~~~~~~~~~~~~~~~~~~~~

* **The live configuration**: what ``@cluster`` uses now. The header names
  it ("@cluster currently runs on ...").
* **Saved profiles**: named configurations on disk.
* **The form**: a *draft* of the selected profile. Editing a field changes
  neither the profile nor the live configuration.

**Apply** saves the draft to the profile and makes it the live configuration
in one step. **Revert** discards the draft. A draft survives switching to
another profile and back for as long as the widget exists.

Two indicators
~~~~~~~~~~~~~~

* The pill in the header says only whether the form is what ``@cluster`` is
  using: *applied* or *not applied*.
* The pill beside the **Result** console reports the outcome of the last
  action (for example *connected*, *unknown host*, *export failed*).

Controls
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Control
     - What it does
   * - Saved profile, Rename to, Duplicate, Delete
     - Choose, rename, copy and remove profiles. Six are provided at first:
       Local single-core, Local all cores, SLURM cluster, SSH remote machine,
       HuggingFace Jobs (CPU) and HuggingFace Jobs (GPU).
   * - Where to run
     - One tile per supported backend: Local, SSH, SLURM, Hugging Face. The
       tiles are built from ``clustrix.config.SUPPORTED_CLUSTER_TYPES``.
   * - CPUs per job, Memory per job, Time limit per job
     - ``default_cores``, ``default_memory``, ``default_time``.
   * - Partition
     - ``default_partition`` (SLURM only).
   * - Host, Port, Username
     - ``cluster_host``, ``cluster_port``, ``username`` (SSH and SLURM).
   * - Password
     - Used for this session only. Never written to a profile.
   * - ...or the environment variable that holds the password
     - ``password_env_var``.
   * - SSH key file
     - ``key_file``. Blank means the SSH agent and the default keys.
   * - Set up SSH key
     - Generates a key and installs it on the host, using the password above
       once. "Replace a key that already works" forces a new key.
   * - Directory on the host for job files
     - ``remote_work_dir``.
   * - Namespace, Hardware, Time limit per job, Token
     - HuggingFace Jobs: ``hf_namespace``, ``hf_flavor``,
       ``hf_job_timeout``, and a token used for this session only.
   * - Allow GPU hardware, which is billed per second
     - ``hf_allow_gpu_flavors``.
   * - Test connection
     - Connects and authenticates through the same code a job uses, without
       running a job.
   * - Run test job
     - Runs one small job end to end and shows where it ran.
   * - Trust this host
     - Appears when a connection is refused because the host is unknown; see
       below.

**Advanced settings** adds: Package manager, Python executable, Existing
conda environment (``conda_env_name``), "Install my local packages on the
worker" (``replicate_local_environment``), "Delete job files on the host
after success" (``cleanup_on_success``), Modules to load, Environment
variables (this session only), Shell commands to run before the job, and
the profiles file with **Export** and **Import**.

.. image:: ../_static/widget/widget-advanced.png
   :alt: The widget with the advanced settings shown
   :width: 640px

**Export** writes every saved profile to the named file, without
credentials and without edits that have not been applied. **Import**
replaces every profile with the ones in the file. A bare file name is
resolved inside the Clustrix configuration directory.

A host seen for the first time
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Host keys are verified (:ref:`host-key-verification`). When the host is not
in ``known_hosts``, the widget shows the fingerprint of the key the host
offered and a **Trust this host** button. Pressing it records exactly that
key in ``~/.ssh/known_hosts`` and tries again. Check the fingerprint against
the one your cluster's administrators publish before pressing it.

.. image:: ../_static/widget/widget-unknown-host.png
   :alt: The widget showing an unknown host's fingerprint and the Trust this host button
   :width: 640px

``ssh_host_key_policy`` and ``hf_image`` are never applied from a profile.

.. _security-note:

Security note
~~~~~~~~~~~~~

The widget itself never writes a password or token to a profile or to an
exported file. The notebook file is a different matter. If JupyterLab's
**Settings → Save Widget State Automatically** is on, the state of every
widget, including the text typed into the Password and Token fields, is
stored in the ``.ipynb`` file when the notebook is saved. Anyone who can
read that file, or a repository it is committed to, can read the secret.

Turn that setting off before typing a secret into the widget, or do not type
one: use an SSH key, the environment-variable field, ``HF_TOKEN`` or
``hf auth login``. If a notebook was saved with a secret in it, clear its
outputs and widget state and change the secret.

In code
-------

.. code-block:: python

   from clustrix import ModernClustrixWidget

   widget = ModernClustrixWidget()
   print(sorted(widget.widgets)[:5])
   print(widget.widgets["cluster_type"].value)
   container = widget.get_widget()   # the ipywidgets container; display it yourself

.. code-block:: text

   ['add_profile_btn', 'advanced_section', 'advanced_toggle', 'applied_pill', 'apply_btn']
   local

``display_modern_widget()`` and its alias ``show_widget()`` display a new
widget and return ``None``. ``create_modern_cluster_widget()`` returns the
container without displaying it.

.. autoclass:: clustrix.ModernClustrixWidget
   :members: get_widget, display, set_status
   :no-undoc-members:
   :no-special-members:

.. autofunction:: clustrix.display_modern_widget

.. autofunction:: clustrix.create_modern_cluster_widget

.. autofunction:: clustrix.load_ipython_extension

Removed
-------

``EnhancedClusterConfigWidget``, ``DEFAULT_CONFIGS``,
``detect_config_files``, ``load_config_from_file``, ``validate_ip_address``,
``validate_hostname`` and the modules ``clustrix.notebook_magic_widget`` and
``clustrix.notebook_magic_config`` no longer exist. Use
``ModernClustrixWidget`` for the form and :func:`clustrix.config.load_config`
to read a file.
