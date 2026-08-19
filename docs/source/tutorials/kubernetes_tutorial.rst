Kubernetes Cluster Tutorial
===========================

This tutorial demonstrates how to use Clustrix with Kubernetes clusters for cloud-native distributed computing. Kubernetes provides excellent scalability and resource management for containerized workloads.

.. warning::

   The Kubernetes backend has not been verified against a real cluster. Treat
   this tutorial as a description of the intended interface, not as a record of
   something that has been run.

   Two limitations are worth knowing before you start. The notebook widget
   has a Kubernetes section covering namespace, image, service account and
   image pull policy; the remaining ``k8s_*`` settings come from a
   configuration file or ``configure()``. And per-job Kubernetes overrides are
   not implemented: the executor reads only the configuration-level ``k8s_*``
   settings and derives pod resource requests and limits from ``cores`` and
   ``memory``, so ``namespace``, ``image``, ``cpu_limit``, ``memory_limit``,
   ``restart_policy``, ``backoff_limit`` and ``active_deadline_seconds`` passed
   to ``@cluster`` are silently ignored.

   Memory is translated for you: ``memory="8GB"`` becomes the Kubernetes
   quantity ``8Gi``, so clustrix's usual spelling is accepted here.

Prerequisites
-------------

1. Access to a Kubernetes cluster (local, cloud, or on-premises) -- or let
   Clustrix create one for you, see `Auto-Provisioning a Cluster`_ below
2. kubectl configured with cluster access (not needed if you use
   auto-provisioning; Clustrix configures kubectl itself)
3. Clustrix installed with Kubernetes support: ``pip install clustrix[kubernetes]``

Auto-Provisioning a Cluster
----------------------------

If you don't already have a Kubernetes cluster, ``clustrix.kubernetes`` can
create one from scratch: locally with `kind <https://kind.sigs.k8s.io/>`_
(Kubernetes-in-Docker), or on a cloud provider. This is the
``KubernetesClusterProvisioner`` API used internally by
``@cluster(auto_provision=True, ...)`` (see below); you can also call it
directly.

.. important::

   The cloud provisioning paths (AWS, GCP, Azure, HuggingFace, Lambda Cloud)
   are **unverified** -- consistent with this tutorial's opening warning and
   with the main README, no cloud job has been shown to provision a cluster
   and run to completion end to end. Only the local ``kind``-based path is
   described as verified below, and only in the narrow sense that it does not
   require cloud credentials and its prerequisites (Docker, ``kind``,
   ``kubectl``) can be checked locally; the provisioner itself has not been
   exercised end to end in this session either. Treat every code sample here
   as a description of the documented interface, not a record of a
   successful run, until you have run it yourself.

Local Provisioning (kind) -- No Cloud Credentials Required
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Requires Docker, `kind (installation instructions)
<https://kind.sigs.k8s.io/docs/user/quick-start/#installation>`_,
and ``kubectl`` on the machine running Clustrix. No cloud account, API key,
or credentials of any kind are needed -- the local provisioner uses a
placeholder ``{"type": "local"}`` credential internally and ignores it.

.. code-block:: python

   # cluster-required: provisions a real kind cluster via Docker
   from clustrix import configure, cluster

   configure(
       cluster_type="kubernetes",
       auto_provision_k8s=True,
       k8s_provider="local",      # selects LocalDockerKubernetesProvisioner
       k8s_node_count=2,
       k8s_cluster_name="my-local-cluster",  # optional; auto-generated if omitted
   )

   @cluster(platform="kubernetes", auto_provision=True, cores=1, memory="512Mi")
   def analyze(x):
       return x * 2

   analyze(21)  # provisions (or reuses) the kind cluster, then runs the job

.. warning::

   The ``provider=`` keyword on ``@cluster(...)`` (used for the hostful cloud
   VM backends -- Lambda Cloud, AWS, Azure, GCP) is **not** the same setting
   as the Kubernetes provider. There is no ``k8s_provider=`` (or ``region=``)
   parameter on ``@cluster`` itself; ``config.k8s_provider`` defaults to
   ``"aws"`` and must be set explicitly via ``configure()`` (or a
   ``ClusterConfig``) as shown above. Passing ``provider="local"`` directly
   to ``@cluster(...)`` has no effect on which Kubernetes provisioner runs.

Instead of the decorator, you can provision (and later tear down) a cluster
directly:

.. code-block:: python

   # cluster-required: provisions a real kind cluster via Docker
   from clustrix.kubernetes.cluster_provisioner import (
       provision_kubernetes_cluster,
       destroy_kubernetes_cluster,
   )

   cluster_info = provision_kubernetes_cluster(
       provider="local",
       cluster_name="my-local-cluster",
       region="local",   # ignored by the local provisioner, but required by the function signature
       node_count=2,
   )
   print(cluster_info["cluster_id"])

   # ... later ...
   destroy_kubernetes_cluster(cluster_info["cluster_id"], provider="local")

Cloud Provisioning -- Unverified
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The same ``provision_kubernetes_cluster()`` / ``@cluster(auto_provision=True)``
interface supports five cloud providers by creating a from-scratch cluster
(EKS, GKE, AKS, a HuggingFace Space, or a Lambda Cloud Kubernetes deployment).
**None of these have been run end to end**; only DigitalOcean and Linode are
excluded because no provisioner exists for them at all -- the five below at
least have provisioner code, but it has not been validated against a live
account.

.. code-block:: python

   # cluster-required: unverified cloud path, needs real provider credentials
   from clustrix import configure, cluster

   configure(
       cluster_type="kubernetes",
       auto_provision_k8s=True,
       k8s_provider="aws",            # aws, gcp, azure, huggingface, lambda
       k8s_region="us-west-2",
       k8s_node_count=3,
       k8s_node_type="t3.large",      # provider-specific; see defaults below
       k8s_version="1.28",
   )

   @cluster(platform="kubernetes", auto_provision=True, cores=2, memory="4Gi")
   def train(x):
       return x

Credentials are read from environment variables via
``clustrix.credential_manager``, one set per provider:

.. list-table::
   :header-rows: 1

   * - ``k8s_provider``
     - Environment variables
     - Default ``node_type``
   * - ``aws``
     - ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, ``AWS_REGION``
     - ``t3.medium``
   * - ``gcp``
     - ``GCP_PROJECT_ID``, ``GCP_SERVICE_ACCOUNT_JSON``
     - ``e2-standard-4``
   * - ``azure``
     - ``AZURE_SUBSCRIPTION_ID``, ``AZURE_TENANT_ID``, ``AZURE_CLIENT_ID``, ``AZURE_CLIENT_SECRET``
     - ``Standard_D2s_v3``
   * - ``huggingface``
     - ``HF_TOKEN``, ``HF_USERNAME``
     - (Space-based; no VM instance type)
   * - ``lambda``
     - ``LAMBDA_CLOUD_API_KEY``
     - (Lambda Cloud instance types)

If credentials for the selected provider aren't found,
``KubernetesClusterProvisioner`` raises ``ValueError`` rather than falling
back to another provider or to local execution.

Configuration Options
---------------------

**Option 1: Interactive Widget (Recommended for Jupyter)**

For Jupyter notebook users, use the interactive configuration widget:

Importing ``clustrix`` registers the magic but does not display anything. Run
``%%remote`` in a cell of its own to open the widget:

.. code-block:: ipython3

   %%remote

Selecting ``kubernetes`` shows a Kubernetes section with namespace, image,
service account and image pull policy. The remaining ``k8s_*`` settings
below have to come from a configuration file or ``configure()``.

**Option 2: Programmatic Configuration**

Configure Clustrix programmatically for your Kubernetes cluster:

.. code-block:: python

   from clustrix import configure
   
   configure(
       cluster_type="kubernetes",
       # Note: Kubernetes uses kubectl config, no host/SSH needed
       k8s_namespace="default",       # Optional: specify namespace
       k8s_image="python:3.11-slim",  # Optional: custom image
   )

Kubernetes-specific Features
----------------------------

Resource Specification
~~~~~~~~~~~~~~~~~~~~~~

Kubernetes uses different resource syntax:

.. code-block:: python

   from clustrix import cluster
   
   @cluster(
       cores=2,              # CPU cores (can be fractional: 0.5, 1.5)
       memory="4Gi",         # Memory in Kubernetes format
       time="01:00:00",      # Job timeout
       k8s_namespace="compute",  # Kubernetes namespace
       k8s_image="python:3.11",  # Custom Docker image
   )
   def k8s_computation():
       """Example computation on Kubernetes."""
       import numpy as np
       import time
       
       print("Starting Kubernetes job...")
       
       # CPU-intensive computation
       size = 3000
       matrix_a = np.random.rand(size, size)
       matrix_b = np.random.rand(size, size)
       
       start_time = time.time()
       result = np.dot(matrix_a, matrix_b)
       end_time = time.time()
       
       return {
           'computation_time': end_time - start_time,
           'matrix_size': size,
           'result_trace': float(np.trace(result)),
           'result_frobenius_norm': float(np.linalg.norm(result, 'fro'))
       }
   
   # Execute on Kubernetes
   result = k8s_computation()
   print(f"Computation completed in {result['computation_time']:.2f} seconds")

Advanced Configuration
----------------------

Custom Docker Images
~~~~~~~~~~~~~~~~~~~~

For complex dependencies, use custom images:

.. code-block:: python

   # First, create a Dockerfile for your requirements
   """
   # Dockerfile
   FROM python:3.11-slim
   
   RUN pip install numpy pandas scikit-learn matplotlib
   RUN pip install torch torchvision  # For ML workloads
   
   WORKDIR /app
   CMD ["python"]
   """
   
   # Then configure Clustrix to use your image
   configure(
       cluster_type="kubernetes",
       k8s_namespace="ml-compute",
       k8s_image="your-registry/clustrix-ml:latest"
   )
   
   @cluster(cores=4, memory="8Gi")
   def ml_computation():
       """Machine learning computation with custom image."""
       import torch
       import numpy as np
       
       # Check GPU availability
       device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
       print(f"Using device: {device}")
       
       # Create neural network
       model = torch.nn.Sequential(
           torch.nn.Linear(100, 50),
           torch.nn.ReLU(),
           torch.nn.Linear(50, 1)
       ).to(device)
       
       # Generate synthetic data
       X = torch.randn(1000, 100).to(device)
       y = torch.randn(1000, 1).to(device)
       
       # Simple training loop
       optimizer = torch.optim.Adam(model.parameters())
       loss_fn = torch.nn.MSELoss()
       
       losses = []
       for epoch in range(100):
           optimizer.zero_grad()
           predictions = model(X)
           loss = loss_fn(predictions, y)
           loss.backward()
           optimizer.step()
           losses.append(loss.item())
       
       return {
           'device': str(device),
           'final_loss': losses[-1],
           'training_losses': losses[::10],  # Every 10th loss
           'model_parameters': sum(p.numel() for p in model.parameters())
       }

Resource Limits and Requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configure resource limits for better cluster utilization:

.. code-block:: python

   # The executor sets pod resource requests and limits to the same values,
   # derived from cores and memory. There is no separate limit argument.
   @cluster(cores=1, memory="2Gi")
   def resource_managed_task():
       """Task with detailed resource management."""
       import psutil
       import time
       
       # Monitor resource usage
       process = psutil.Process()
       
       results = {
           'cpu_count': psutil.cpu_count(),
           'memory_total_gb': psutil.virtual_memory().total / (1024**3),
           'measurements': []
       }
       
       # Simulate varying workload
       for i in range(10):
           # CPU-intensive phase
           start_time = time.time()
           sum(x**2 for x in range(100000))
           end_time = time.time()
           
           # Measure current usage
           cpu_percent = process.cpu_percent()
           memory_mb = process.memory_info().rss / (1024**2)
           
           results['measurements'].append({
               'step': i,
               'cpu_percent': cpu_percent,
               'memory_mb': memory_mb,
               'duration_ms': (end_time - start_time) * 1000
           })
           
           time.sleep(1)
       
       return results

Kubernetes-Native Examples
--------------------------

Distributed Data Processing
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=2, memory="4Gi")
   def process_data_partition(partition_id, total_partitions, data_size=10000):
       """Process a partition of a large dataset."""
       import numpy as np
       import json
       
       print(f"Processing partition {partition_id}/{total_partitions}")
       
       # Simulate loading partition data
       np.random.seed(partition_id)  # Ensure reproducible partitions
       partition_size = data_size // total_partitions
       
       # Generate partition data
       data = np.random.rand(partition_size, 50)
       labels = np.random.randint(0, 5, partition_size)
       
       # Process partition
       results = {
           'partition_id': partition_id,
           'partition_size': partition_size,
           'feature_means': np.mean(data, axis=0).tolist(),
           'feature_stds': np.std(data, axis=0).tolist(),
           'label_distribution': {
               str(label): int(count) 
               for label, count in zip(*np.unique(labels, return_counts=True))
           }
       }
       
       return results
   
   # Process data in parallel across multiple Kubernetes jobs
   total_partitions = 8
   partition_results = []
   
   for partition_id in range(total_partitions):
       result = process_data_partition(partition_id, total_partitions)
       partition_results.append(result)
   
   # Aggregate results
   total_samples = sum(r['partition_size'] for r in partition_results)
   print(f"Processed {total_samples} samples across {total_partitions} partitions")
   
   # Compute global statistics
   all_feature_means = np.array([r['feature_means'] for r in partition_results])
   global_feature_means = np.mean(all_feature_means, axis=0)
   print(f"Global feature means: {global_feature_means[:5]}")  # Show first 5

Microservices-Style Computing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=1, memory="2Gi")
   def image_processing_service(image_id, operations):
       """Microservice for image processing."""
       import numpy as np
       import json
       
       print(f"Processing image {image_id} with operations: {operations}")
       
       # Simulate image (random pixels)
       height, width = 512, 512
       image = np.random.randint(0, 256, (height, width, 3), dtype=np.uint8)
       
       results = {
           'image_id': image_id,
           'original_shape': image.shape,
           'operations_performed': []
       }
       
       # Apply operations
       for operation in operations:
           if operation == 'grayscale':
               # Convert to grayscale
               gray = np.dot(image[...,:3], [0.2989, 0.5870, 0.1140])
               image = np.stack([gray, gray, gray], axis=-1).astype(np.uint8)
               results['operations_performed'].append('grayscale')
               
           elif operation == 'blur':
               # Simple blur (average with neighbors)
               from scipy import ndimage
               for channel in range(3):
                   image[:,:,channel] = ndimage.uniform_filter(
                       image[:,:,channel].astype(float), size=3
                   ).astype(np.uint8)
               results['operations_performed'].append('blur')
               
           elif operation == 'edge_detect':
               # Simple edge detection
               edges = np.abs(np.diff(image.astype(float), axis=0)).sum(axis=-1)
               edges = np.pad(edges, ((0,1), (0,0)), mode='constant')
               results['edge_strength'] = float(np.mean(edges))
               results['operations_performed'].append('edge_detect')
       
       # Compute final statistics
       results['final_mean_intensity'] = float(np.mean(image))
       results['final_std_intensity'] = float(np.std(image))
       
       return results
   
   # Process multiple images with different operations
   image_tasks = [
       {'id': 'img_001', 'ops': ['grayscale', 'blur']},
       {'id': 'img_002', 'ops': ['edge_detect']},
       {'id': 'img_003', 'ops': ['grayscale', 'edge_detect']},
       {'id': 'img_004', 'ops': ['blur', 'edge_detect']}
   ]
   
   results = []
   for task in image_tasks:
       result = image_processing_service(task['id'], task['ops'])
       results.append(result)
   
   # Summary
   for r in results:
       print(f"Image {r['image_id']}: {', '.join(r['operations_performed'])}")

Cloud-Native Best Practices
---------------------------

Auto-scaling Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Configure for auto-scaling environments
   configure(
       cluster_type="kubernetes",
       k8s_namespace="auto-scale",
       
       # Resource settings that work well with auto-scaling
       default_cores=1,        # Start small
       default_memory="2Gi",   # Conservative memory
       
       # Job settings
       k8s_backoff_limit=2,    # Limited retries
   )
   
   @cluster(cores=0.5, memory="1Gi")  # Fractional cores for efficiency
   def lightweight_task(task_id):
       """Lightweight task suitable for auto-scaling."""
       import time
       import random
       
       # Variable processing time
       processing_time = random.uniform(10, 60)  # 10-60 seconds
       
       print(f"Task {task_id} starting (estimated {processing_time:.1f}s)")
       
       # Simulate work
       start_time = time.time()
       time.sleep(processing_time)
       end_time = time.time()
       
       return {
           'task_id': task_id,
           'estimated_time': processing_time,
           'actual_time': end_time - start_time,
           'efficiency': processing_time / (end_time - start_time)
       }

Fault Tolerance
~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=2, memory="4Gi")
   def fault_tolerant_computation(data_chunk_id, retry_count=0):
       """Computation with built-in fault tolerance."""
       import random
       import time
       import numpy as np
       
       print(f"Processing chunk {data_chunk_id} (attempt {retry_count + 1})")
       
       # Simulate random failures (20% chance)
       if random.random() < 0.2 and retry_count < 2:
           raise RuntimeError(f"Simulated failure in chunk {data_chunk_id}")
       
       # Simulate computation
       chunk_size = 1000
       data = np.random.rand(chunk_size, 100)
       
       # Add checkpointing for long computations
       checkpoint_interval = 200
       results = []
       
       for i in range(0, chunk_size, checkpoint_interval):
           end_idx = min(i + checkpoint_interval, chunk_size)
           batch = data[i:end_idx]
           
           # Process batch
           batch_result = np.mean(batch, axis=0)
           results.append(batch_result)
           
           print(f"Checkpoint: processed {end_idx}/{chunk_size} samples")
           time.sleep(0.1)  # Small delay
       
       # Combine results
       final_result = np.mean(results, axis=0)
       
       return {
           'chunk_id': data_chunk_id,
           'chunk_size': chunk_size,
           'checkpoints': len(results),
           'result_mean': float(np.mean(final_result)),
           'result_std': float(np.std(final_result)),
           'retry_count': retry_count
       }
   
   # Process multiple chunks with fault tolerance
   chunk_ids = range(10)
   successful_results = []
   
   for chunk_id in chunk_ids:
       try:
           result = fault_tolerant_computation(chunk_id)
           successful_results.append(result)
           print(f"✓ Chunk {chunk_id} completed successfully")
       except Exception as e:
           print(f"✗ Chunk {chunk_id} failed after retries: {e}")
   
   print(f"Successfully processed {len(successful_results)}/{len(chunk_ids)} chunks")

Monitoring and Logging
----------------------

Kubernetes Job Monitoring
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=2, memory="4Gi")
   def monitored_computation():
       """Computation with comprehensive monitoring."""
       import time
       import psutil
       import logging
       import json
       
       # Set up logging
       logging.basicConfig(level=logging.INFO)
       logger = logging.getLogger(__name__)
       
       # Monitoring data
       monitor_data = {
           'start_time': time.time(),
           'resource_snapshots': [],
           'milestones': []
       }
       
       def log_resources(milestone):
           """Log current resource usage."""
           snapshot = {
               'timestamp': time.time(),
               'milestone': milestone,
               'cpu_percent': psutil.cpu_percent(interval=1),
               'memory_mb': psutil.virtual_memory().used / (1024**2),
               'memory_percent': psutil.virtual_memory().percent
           }
           monitor_data['resource_snapshots'].append(snapshot)
           logger.info(f"Milestone '{milestone}': CPU {snapshot['cpu_percent']:.1f}%, "
                      f"Memory {snapshot['memory_mb']:.1f}MB")
       
       try:
           log_resources("computation_start")
           
           # Phase 1: Data preparation
           import numpy as np
           data = np.random.rand(5000, 1000)
           monitor_data['milestones'].append("data_prepared")
           log_resources("data_preparation_complete")
           
           # Phase 2: Computation
           result = np.linalg.svd(data, compute_uv=False)
           monitor_data['milestones'].append("computation_complete")
           log_resources("computation_complete")
           
           # Phase 3: Analysis
           analysis = {
               'singular_values_count': len(result),
               'max_singular_value': float(np.max(result)),
               'min_singular_value': float(np.min(result)),
               'condition_number': float(np.max(result) / np.min(result))
           }
           monitor_data['milestones'].append("analysis_complete")
           log_resources("analysis_complete")
           
           monitor_data['end_time'] = time.time()
           monitor_data['total_duration'] = monitor_data['end_time'] - monitor_data['start_time']
           
           return {
               'analysis_results': analysis,
               'monitoring_data': monitor_data,
               'success': True
           }
           
       except Exception as e:
           logger.error(f"Computation failed: {e}")
           monitor_data['error'] = str(e)
           monitor_data['end_time'] = time.time()
           raise

Complete Kubernetes Example
---------------------------

Distributed Machine Learning
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import configure, cluster
   import numpy as np
   
   # Configure for ML workloads
   configure(
       cluster_type="kubernetes",
       k8s_namespace="ml-compute",
       k8s_image="python:3.11-slim",
       
       # Default resources for ML tasks
       default_cores=2,
       default_memory="4Gi",
       k8s_backoff_limit=1            # Single retry
   )
   
   @cluster(cores=4, memory="8Gi")
   def distributed_training_worker(worker_id, total_workers, epochs=100):
       """Distributed training worker for machine learning."""
       import numpy as np
       from sklearn.datasets import make_classification
       from sklearn.ensemble import RandomForestClassifier
       from sklearn.model_selection import train_test_split
       from sklearn.metrics import accuracy_score, classification_report
       import time
       import json
       
       print(f"Worker {worker_id}/{total_workers} starting training...")
       
       # Generate worker-specific dataset
       np.random.seed(worker_id)  # Ensure different data per worker
       
       X, y = make_classification(
           n_samples=10000,
           n_features=50,
           n_informative=30,
           n_redundant=10,
           n_classes=5,
           random_state=worker_id
       )
       
       # Split data
       X_train, X_test, y_train, y_test = train_test_split(
           X, y, test_size=0.2, random_state=worker_id
       )
       
       print(f"Worker {worker_id}: Dataset prepared ({len(X_train)} training samples)")
       
       # Train model
       start_time = time.time()
       
       model = RandomForestClassifier(
           n_estimators=epochs,
           max_depth=10,
           random_state=worker_id,
           n_jobs=-1  # Use all available cores
       )
       
       model.fit(X_train, y_train)
       training_time = time.time() - start_time
       
       # Evaluate model
       y_pred = model.predict(X_test)
       accuracy = accuracy_score(y_test, y_pred)
       
       # Feature importance
       feature_importance = model.feature_importances_
       top_features = np.argsort(feature_importance)[-10:]  # Top 10 features
       
       results = {
           'worker_id': worker_id,
           'total_workers': total_workers,
           'training_samples': len(X_train),
           'test_samples': len(X_test),
           'training_time_seconds': training_time,
           'accuracy': float(accuracy),
           'top_feature_indices': top_features.tolist(),
           'top_feature_importance': feature_importance[top_features].tolist(),
           'model_parameters': {
               'n_estimators': epochs,
               'max_depth': 10
           }
       }
       
       print(f"Worker {worker_id} completed: accuracy = {accuracy:.4f}")
       return results
   
   # Run distributed training
   total_workers = 6
   print(f"Starting distributed training with {total_workers} workers...")
   
   worker_results = []
   for worker_id in range(total_workers):
       result = distributed_training_worker(worker_id, total_workers, epochs=150)
       worker_results.append(result)
   
   # Aggregate results
   accuracies = [r['accuracy'] for r in worker_results]
   training_times = [r['training_time_seconds'] for r in worker_results]
   
   print("\nDistributed Training Results:")
   print(f"Average accuracy: {np.mean(accuracies):.4f} ± {np.std(accuracies):.4f}")
   print(f"Average training time: {np.mean(training_times):.2f}s ± {np.std(training_times):.2f}s")
   print(f"Total training samples: {sum(r['training_samples'] for r in worker_results)}")
   
   # Find best performing worker
   best_worker = max(worker_results, key=lambda x: x['accuracy'])
   print(f"Best worker: {best_worker['worker_id']} (accuracy: {best_worker['accuracy']:.4f})")

This tutorial demonstrates the cloud-native capabilities of Clustrix with Kubernetes, showcasing containerized distributed computing, auto-scaling, fault tolerance, and comprehensive monitoring for modern cloud environments.