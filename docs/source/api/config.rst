Configuration API
=================

The settings themselves are described in :doc:`../configuration`. This page
is the reference for the functions and the class.

.. code-block:: python

   import os
   import tempfile

   from clustrix import ClusterConfig, configure, get_config
   from clustrix.config import load_config, save_config

   configure(cluster_type="local", default_cores=2, default_memory="4GB")
   print(get_config().default_cores, get_config().default_memory)

   path = os.path.join(tempfile.mkdtemp(), "saved.yml")
   save_config(path)

   configure(default_cores=8)
   load_config(path)
   print(get_config().default_cores)

   try:
       configure(default_cores=0)
   except ValueError as error:
       print(error)

.. code-block:: text

   2 4GB
   2
   default_cores=0 is not valid: use a positive number of cores, or -1 for every core available.

.. autofunction:: clustrix.configure
   :no-index:

.. autofunction:: clustrix.get_config

.. autofunction:: clustrix.config.load_config

.. autofunction:: clustrix.config.save_config

.. autofunction:: clustrix.config.get_config_dir

.. autofunction:: clustrix.config.default_config_file

.. autodata:: clustrix.config.SUPPORTED_CLUSTER_TYPES
   :no-value:

``clustrix.config.SUPPORTED_CLUSTER_TYPES`` is
``("local", "ssh", "slurm", "huggingface")``.

.. autoclass:: clustrix.ClusterConfig
   :members: save_to_file, load_from_file
   :no-undoc-members:
   :no-special-members:

.. autoexception:: clustrix.config.ConfigFileError
