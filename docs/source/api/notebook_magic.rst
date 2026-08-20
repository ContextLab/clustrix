Notebook Magic Commands
=======================

.. currentmodule:: clustrix.notebook_magic

Clustrix integrates with Jupyter notebooks through an IPython magic command and
an interactive widget.

Magic Commands
--------------

%%remote
~~~~~~~~

``%%remote`` displays the configuration widget. Any code in the rest of the
cell is executed afterwards.

**Usage:**

.. code-block:: ipython3

   %%remote

Importing ``clustrix`` registers the magic but does **not** display the widget.
A library should not inject UI as a side effect of being imported, and an
import that displayed the widget would also put a second copy of it next to
any explicit ``%%remote`` or ``display()`` call. Set
``CLUSTRIX_AUTO_WIDGET=1`` if you want display-on-import anyway.

%%clusterfy (deprecated)
~~~~~~~~~~~~~~~~~~~~~~~~

``%%clusterfy`` is an alias for ``%%remote``. It works, and it emits a
``DeprecationWarning``.

Widget Interface
----------------

``%%remote`` shows :class:`clustrix.modern_notebook_widget.ModernClustrixWidget`.

.. autoclass:: clustrix.modern_notebook_widget.ModernClustrixWidget
   :members: display, get_widget, set_status
   :show-inheritance:

**Sections:**

- **Profile**: the active profile, and the configuration file that Save and
  Load use. New profiles are added with ``+`` and removed with ``-``.
- **Resources**: cluster type, CPUs, memory, walltime.
- **Connection** (``ssh``, ``slurm`` only): host, port,
  username, SSH key file, password, remote work directory, an environment
  variable to read the password from, and an "Auto setup SSH keys" button.
- **HuggingFace Jobs** (``huggingface`` only): namespace, flavor, token, and an
  "Allow paid GPU flavors" checkbox. GPU flavors bill by the second, so that
  box has to be ticked before one is accepted.
- **Actions**: "Test connection", "Test job submission", "Apply".
- **Advanced**: package manager, Python executable, whether to replicate the
  local environment, environment variables, module loads, pre-execution
  commands.
- **Output**: where the test buttons and errors report.

The cluster type dropdown offers ``local``, ``ssh``, ``slurm`` and
``huggingface`` -- the contents of
:data:`clustrix.config.SUPPORTED_CLUSTER_TYPES`, and nothing else. There are
no PBS, SGE, Kubernetes, AWS, GCP, Azure or Lambda Cloud entries, and no
``k8s_*`` settings, because Clustrix does not support those backends. Each is
planned for a future release under its own tracking issue -- see
:ref:`removed-backends`.

"Apply" calls :func:`clustrix.configure` with the widget's values, so
subsequent ``@cluster`` functions use them.

Where the widget saves
~~~~~~~~~~~~~~~~~~~~~~

Save and Load use the filename in the Profile row, resolved against the
clustrix configuration directory -- ``~/.clustrix`` unless
``CLUSTRIX_CONFIG_DIR`` says otherwise. Set that variable in tests and in CI,
or the Save button writes into your real configuration.

ClusterfyMagics
~~~~~~~~~~~~~~~

.. autoclass:: ClusterfyMagics
   :members:
   :undoc-members:
   :show-inheritance:

A second widget class
~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: EnhancedClusterConfigWidget
   :members:
   :undoc-members:
   :show-inheritance:

   A separate widget implementation, kept importable, along with
   :data:`DEFAULT_CONFIGS`. ``%%remote`` displays
   :class:`~clustrix.modern_notebook_widget.ModernClustrixWidget` instead.
   Any template this one offers that names a cluster type outside
   :data:`clustrix.config.SUPPORTED_CLUSTER_TYPES` cannot be dispatched by the
   executor; see :ref:`removed-backends`.

.. Documented from the module that defines it, not from the one that
   re-exports it: autodoc only picks up the ``#:`` comment at the definition
   site, so pointing at ``clustrix.notebook_magic`` made it fall back to
   ``dict.__doc__`` -- whose own ``**kwargs`` and indented body are not valid
   RST and produced four build warnings.

.. autodata:: clustrix.notebook_magic_config.DEFAULT_CONFIGS
   :no-value:

   Configuration templates for the widget above, keyed by display name
   (``'Local Single-core'``, ``'University SLURM Cluster'``, ...). Entries hold
   plain :class:`~clustrix.config.ClusterConfig` field names; there is no
   ``name`` or ``description`` key.

Extension Loading
-----------------

.. autofunction:: load_ipython_extension

   Registers the magic commands with an IPython instance. Called automatically
   when clustrix is imported in a Jupyter environment.

   **Parameters:**

   - ``ipython``: The IPython instance to register the magic command with

Requirements
------------

The notebook magic functionality requires:

- ``IPython`` - For magic command support
- ``ipywidgets`` - For the interactive widget interface
- ``PyYAML`` - For YAML configuration file support

**Installation:**

.. code-block:: bash

   pip install clustrix[widget]
   # or
   pip install ipywidgets pyyaml

Usage Examples
--------------

Displaying the widget
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: ipython3

   %%remote

Programmatic Access
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.modern_notebook_widget import ModernClustrixWidget

   widget = ModernClustrixWidget()
   widget.display()

Or, equivalently:

.. code-block:: python

   from clustrix.notebook_magic import display_config_widget

   display_config_widget()

Save and Load Configurations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Save**: writes the current profile to the named configuration file
2. **Load**: reads profiles back from it
3. **File Formats**: YAML and JSON, detected from the file extension

.. autofunction:: clustrix.notebook_magic_config.load_config_from_file

   Who chose the path decides what a failure means, and the two answers are
   deliberately different:

   * A file you **name** is a file you chose, so a path that does not exist,
     one you cannot read, or one that does not parse **raises** --
     :class:`FileNotFoundError`, :class:`PermissionError`,
     ``yaml.YAMLError`` or :class:`json.JSONDecodeError`, the same errors
     :func:`clustrix.config.load_config` raises for the same file.
   * A file clustrix **discovered** by searching the standard locations
     (``discovered=True``, which is what the widget's own scan passes) is
     best effort: an unreadable one is skipped so that it cannot take the
     widget's other profiles down with it, and the reason is written to the
     ``clustrix.notebook_magic_config`` logger at ``WARNING``.

   .. note::

      **Behaviour change.** The named case used to return an empty mapping
      for *every* failure.
      That made a typo, a permissions problem and malformed YAML all report
      identically to a file that genuinely holds no configurations, and the
      widget offered the result as a valid, blank profile. If you were
      relying on the old behaviour, pass ``discovered=True`` -- but read the
      log, because an empty result now no longer means the file was empty.

Notes
-----

- The magic command is registered when importing clustrix in a Jupyter
  environment; the widget is not displayed until you ask for it
- Widget state is preserved during the notebook session
- Configurations can be shared between team members via saved files
- The widget works in both JupyterLab and Jupyter Notebook. Its colours resolve
  through JupyterLab's ``--jp-*`` theme variables, so it follows the notebook
  theme rather than carrying a separate dark stylesheet
- The widget does not validate that a cluster type is one that works. Consult
  the supported cluster types table before relying on a backend
