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
A library should not inject UI as a side effect of being imported, and the old
behaviour also produced a second copy of the widget next to any explicit
``%%remote`` or ``display()`` call. Set ``CLUSTRIX_AUTO_WIDGET=1`` to restore
display-on-import.

%%clusterfy (deprecated)
~~~~~~~~~~~~~~~~~~~~~~~~

``%%clusterfy`` is a deprecated alias for ``%%remote``. It still works and
emits a ``DeprecationWarning``.

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
- **Connection** (``ssh``, ``slurm``, ``pbs``, ``sge`` only): host, port,
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

The cluster type dropdown offers ``local``, ``ssh``, ``slurm``, ``pbs``,
``sge``, ``kubernetes`` and ``huggingface``. Selecting ``kubernetes`` shows no
dedicated fields: the ``k8s_*`` settings can only be set from a configuration
file or ``clustrix.configure()``. There are no AWS, GCP, Azure or Lambda Cloud
entries, because those execution backends are unverified.

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

Legacy widget
~~~~~~~~~~~~~

.. autoclass:: EnhancedClusterConfigWidget
   :members:
   :undoc-members:
   :show-inheritance:

   The previous widget implementation, along with :data:`DEFAULT_CONFIGS`. It
   is no longer what ``%%remote`` displays and is kept only for compatibility.
   Several of its templates name cluster types (``aws``, ``azure``, ``gcp``,
   ``lambda_cloud``, ``huggingface_spaces``) that the executor cannot dispatch.

.. autodata:: DEFAULT_CONFIGS

   Legacy configuration templates, keyed by display name
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
