Notebook Magic Commands
=======================

.. currentmodule:: clustrix.notebook_magic

Clustrix provides seamless integration with Jupyter notebooks through IPython magic commands and interactive widgets.

Magic Commands
--------------

%%clusterfy
~~~~~~~~~~~

The ``%%clusterfy`` magic command creates an interactive widget interface for managing cluster configurations directly in Jupyter notebooks.

**Usage:**

.. code-block:: jupyter

   %%clusterfy
   # Interactive widget appears with full configuration interface

**Features:**

- **Configuration Management**: Create, edit, and delete cluster configurations
- **Pre-built Templates**: Default configurations for major cloud providers
- **Interactive Forms**: GUI elements for all configuration options
- **Save/Load**: Export/import configurations as YAML or JSON files
- **One-click Application**: Apply configurations to current session instantly

**Default Templates Include:**

- Local development environment
- AWS GPU instances (small/large)
- Google Cloud CPU instances
- Azure GPU instances  
- SLURM HPC clusters
- Kubernetes clusters

Widget Interface
----------------

ClusterConfigWidget
~~~~~~~~~~~~~~~~~~~

.. autoclass:: ClusterConfigWidget
   :members:
   :undoc-members:
   :show-inheritance:

   The main widget class that provides the interactive configuration interface.

   **Key Methods:**

   - ``display()``: Show the widget interface
   - ``_on_config_select()``: Handle configuration selection
   - ``_on_apply_config()``: Apply selected configuration
   - ``_on_save_configs()``: Save configurations to file
   - ``_on_load_configs()``: Load configurations from file

ClusterfyMagics
~~~~~~~~~~~~~~~

.. autoclass:: ClusterfyMagics
   :members:
   :undoc-members:
   :show-inheritance:

   IPython magic command class for the ``%%clusterfy`` command.

Configuration Defaults
-----------------------

DEFAULT_CONFIGS
~~~~~~~~~~~~~~~

.. autodata:: DEFAULT_CONFIGS

   Dictionary containing pre-built cluster configuration templates.

   **Available Templates:**

   .. code-block:: python

      {
          "local_dev": {
              "name": "Local Development",
              "cluster_type": "local",
              "default_cores": 4,
              "default_memory": "8GB",
              "description": "Local machine for development and testing"
          },
          "aws_gpu_small": {
              "name": "AWS GPU Small",
              "cluster_type": "ssh",
              "cluster_host": "aws-instance-ip",
              "username": "ubuntu",
              "key_file": "~/.ssh/aws_key.pem",
              "default_cores": 8,
              "default_memory": "60GB",
              "description": "AWS p3.2xlarge instance (1 V100 GPU)"
          },
          # ... more templates
      }

Extension Loading
-----------------

load_ipython_extension
~~~~~~~~~~~~~~~~~~~~~~

.. autofunction:: load_ipython_extension

   Automatically loads the magic command when Clustrix is imported in a Jupyter environment.

   **Parameters:**

   - ``ipython``: The IPython instance to register the magic command with

   **Example:**

   .. code-block:: python

      # Magic command is automatically registered when importing clustrix
      import clustrix
      
      # Use the magic command
      %%clusterfy

Requirements
------------

The notebook magic functionality requires:

- ``IPython`` - For magic command support  
- ``ipywidgets`` - For the interactive widget interface
- ``PyYAML`` - For YAML configuration file support

**Installation:**

.. code-block:: bash

   pip install clustrix[notebook]
   # or
   pip install ipywidgets pyyaml

Usage Examples
--------------

Basic Widget Usage
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   import clustrix
   
   # Widget appears automatically with magic command
   %%clusterfy

Programmatic Access
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.notebook_magic import ClusterConfigWidget
   
   # Create widget programmatically
   widget = ClusterConfigWidget()
   widget.display()

Custom Configuration Templates
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix.notebook_magic import DEFAULT_CONFIGS
   
   # View available templates
   for name, config in DEFAULT_CONFIGS.items():
       print(f"{name}: {config['description']}")
   
   # Customize templates
   custom_config = {
       "name": "My Custom Cluster",
       "cluster_type": "slurm",
       "cluster_host": "my-cluster.edu",
       "username": "myuser",
       "default_cores": 32,
       "default_memory": "128GB",
       "description": "Custom high-memory cluster"
   }

Save and Load Configurations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The widget provides built-in save/load functionality:

1. **Save**: Use the "Save Configs" button to export configurations
2. **Load**: Use the "Load Configs" button to import configurations  
3. **File Formats**: Supports both YAML and JSON formats
4. **File Naming**: Automatically detects format from file extension

Error Handling
--------------

The widget includes comprehensive error handling:

- **Import Errors**: Graceful fallback when IPython/ipywidgets unavailable
- **File Errors**: Clear error messages for save/load operations
- **Validation**: Configuration validation before application
- **Connection Testing**: Basic connectivity checks for remote clusters

Notes
-----

- The magic command is automatically registered when importing clustrix in a Jupyter environment
- Widget state is preserved during the notebook session
- Configurations can be shared between team members via saved files
- The widget works in both Jupyter Lab and Jupyter Notebook environments