Kubernetes Cluster Tutorial
===========================

This tutorial demonstrates how to use Clustrix with Kubernetes clusters for cloud-native distributed computing. Kubernetes provides excellent scalability and resource management for containerized workloads.

Prerequisites
------------

1. Access to a Kubernetes cluster (local, cloud, or on-premises)
2. kubectl configured with cluster access
3. Clustrix installed with Kubernetes support: ``pip install clustrix[kubernetes]``

Configuration Options
--------------------

**Option 1: Interactive Widget (Recommended for Jupyter)**

For Jupyter notebook users, use the interactive configuration widget:

.. code-block:: python

   import clustrix  # Auto-loads the magic command
   
   # Use the magic command to open the configuration widget
   %%clusterfy
   # Interactive widget appears with Kubernetes templates and GUI configuration

**Option 2: Programmatic Configuration**

Configure Clustrix programmatically for your Kubernetes cluster:

.. code-block:: python

   from clustrix import configure
   
   configure(
       cluster_type="kubernetes",
       # Note: Kubernetes uses kubectl config, no host/SSH needed
       namespace="default",  # Optional: specify namespace
       docker_image="python:3.11-slim"  # Optional: custom image
   )

Kubernetes-specific Features
---------------------------

Resource Specification
~~~~~~~~~~~~~~~~~~~~~

Kubernetes uses different resource syntax:

.. code-block:: python

   from clustrix import cluster
   
   @cluster(
       cores=2,              # CPU cores (can be fractional: 0.5, 1.5)
       memory="4Gi",         # Memory in Kubernetes format
       time="01:00:00",      # Job timeout
       namespace="compute",  # Kubernetes namespace
       image="python:3.11"   # Custom Docker image
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
---------------------

Custom Docker Images
~~~~~~~~~~~~~~~~~~~

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
       namespace="ml-compute",
       docker_image="your-registry/clustrix-ml:latest"
   )
   
   @cluster(cores=4, memory="8Gi", image="your-registry/clustrix-ml:latest")
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
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configure resource limits for better cluster utilization:

.. code-block:: python

   @cluster(
       # Resource requests (guaranteed)
       cores=1,              # Guaranteed 1 CPU core
       memory="2Gi",         # Guaranteed 2GB RAM
       
       # Resource limits (maximum)
       cpu_limit=2,          # Can burst up to 2 cores
       memory_limit="4Gi",   # Maximum 4GB RAM
       
       # Additional Kubernetes settings
       restart_policy="Never",
       backoff_limit=3,      # Retry failed jobs 3 times
       active_deadline_seconds=3600  # Kill job after 1 hour
   )
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
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=2, memory="4Gi", namespace="data-processing")
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
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=1, memory="2Gi", namespace="microservices")
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
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Configure for auto-scaling environments
   configure(
       cluster_type="kubernetes",
       namespace="auto-scale",
       
       # Resource settings that work well with auto-scaling
       default_cores=1,        # Start small
       default_memory="2Gi",   # Conservative memory
       
       # Job settings
       active_deadline_seconds=1800,  # 30 minute timeout
       backoff_limit=2,               # Limited retries
       restart_policy="Never"         # Don't restart failed jobs
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
~~~~~~~~~~~~~

.. code-block:: python

   @cluster(cores=2, memory="4Gi", backoff_limit=3)
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
---------------------

Kubernetes Job Monitoring
~~~~~~~~~~~~~~~~~~~~~~~~

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
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from clustrix import configure, cluster
   import numpy as np
   
   # Configure for ML workloads
   configure(
       cluster_type="kubernetes",
       namespace="ml-compute",
       docker_image="python:3.11-slim",
       
       # Default resources for ML tasks
       default_cores=2,
       default_memory="4Gi",
       active_deadline_seconds=3600,  # 1 hour limit
       backoff_limit=1                # Single retry
   )
   
   @cluster(cores=4, memory="8Gi", cpu_limit=6, memory_limit="12Gi")
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