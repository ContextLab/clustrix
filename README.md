# Clustrix

[![Tests](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml/badge.svg)](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml)
[![Documentation](https://readthedocs.org/projects/clustrix/badge/?version=latest)](https://clustrix.readthedocs.io/en/latest/?badge=latest)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Clustrix is a Python package that enables seamless distributed computing on clusters. With a simple decorator, you can execute any Python function remotely on cluster resources while automatically handling dependency management, environment setup, and result collection.

## Features

- **Simple Decorator Interface**: Just add `@cluster` to any function
- **Multiple Cluster Support**: SLURM, PBS, SGE, Kubernetes, and SSH
- **Automatic Dependency Management**: Captures and replicates your exact Python environment
- **Loop Parallelization**: Automatically distributes loops across cluster nodes
- **Flexible Configuration**: Easy setup with config files or environment variables
- **Error Handling**: Comprehensive error reporting and job monitoring

## Quick Start

### Installation

```bash
pip install clustrix
```

### Basic Configuration

```python
import clustrix

# Configure your cluster
clustrix.configure(
    cluster_type='slurm',
    cluster_host='your-cluster.example.com',
    username='your-username',
    default_cores=4,
    default_memory='8GB'
)
```

### Using the Decorator

```python
from clustrix import cluster

@cluster(cores=8, memory='16GB', time='02:00:00')
def expensive_computation(data, iterations=1000):
    import numpy as np
    result = 0
    for i in range(iterations):
        result += np.sum(data ** 2)
    return result

# This function will execute on the cluster
data = [1, 2, 3, 4, 5]
result = expensive_computation(data, iterations=10000)
print(f"Result: {result}")
```

### Configuration File

Create a `clustrix.yml` file in your project directory:

```yaml
cluster_type: slurm
cluster_host: cluster.example.com
username: myuser
key_file: ~/.ssh/id_rsa

default_cores: 4
default_memory: 8GB
default_time: "01:00:00"
default_partition: gpu

remote_work_dir: /scratch/myuser/clustrix
conda_env_name: myproject

auto_parallel: true
max_parallel_jobs: 50
cleanup_on_success: true

module_loads:
  - python/3.9
  - cuda/11.2

environment_variables:
  CUDA_VISIBLE_DEVICES: "0,1"
```

## Advanced Usage

### Custom Resource Requirements

```python
@cluster(
    cores=16,
    memory='32GB',
    time='04:00:00',
    partition='gpu',
    environment='tensorflow-env'
)
def train_model(data, epochs=100):
    # Your machine learning code here
    pass
```

### Manual Parallelization Control

```python
@cluster(parallel=False)  # Disable automatic loop parallelization
def sequential_computation(data):
    result = []
    for item in data:
        result.append(process_item(item))
    return result

@cluster(parallel=True)   # Enable automatic loop parallelization
def parallel_computation(data):
    results = []
    for item in data:  # This loop will be automatically distributed
        results.append(expensive_operation(item))
    return results
```

### Different Cluster Types

```python
# SLURM cluster
clustrix.configure(cluster_type='slurm', cluster_host='slurm.example.com')

# PBS cluster  
clustrix.configure(cluster_type='pbs', cluster_host='pbs.example.com')

# Kubernetes cluster
clustrix.configure(cluster_type='kubernetes')

# Simple SSH execution (no scheduler)
clustrix.configure(cluster_type='ssh', cluster_host='server.example.com')
```

## Command Line Interface

```bash
# Configure Clustrix
clustrix config --cluster-type slurm --cluster-host cluster.example.com --cores 8

# Check current configuration
clustrix config

# Load configuration from file
clustrix load my-config.yml

# Check cluster status
clustrix status
```

## How It Works

1. **Function Serialization**: Clustrix captures your function, arguments, and dependencies using advanced serialization
2. **Environment Replication**: Creates an identical Python environment on the cluster with all required packages
3. **Job Submission**: Submits your function as a job to the cluster scheduler
4. **Execution**: Runs your function on cluster resources with specified requirements
5. **Result Collection**: Automatically retrieves results once execution completes
6. **Cleanup**: Optionally cleans up temporary files and environments

## Supported Cluster Types

- **SLURM**: Full support for Slurm Workload Manager
- **PBS/Torque**: Support for PBS Professional and Torque
- **SGE**: Sun Grid Engine support
- **Kubernetes**: Execute jobs as Kubernetes pods
- **SSH**: Direct execution via SSH (no scheduler)

## Dependencies

Clustrix automatically handles dependency management by:

- Capturing your current Python environment with `pip freeze`
- Creating virtual environments on cluster nodes
- Installing exact package versions to match your local environment
- Supporting conda environments for complex scientific software stacks

## Error Handling and Monitoring

```python
from clustrix import ClusterExecutor

# Monitor job status
executor = ClusterExecutor(clustrix.get_config())
job_id = "12345"
status = executor._check_job_status(job_id)

# Cancel jobs if needed
executor.cancel_job(job_id)
```

## Examples

### Machine Learning Training

```python
@cluster(cores=8, memory='32GB', time='12:00:00', partition='gpu')
def train_neural_network(training_data, model_config):
    import tensorflow as tf
    
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(128, activation='relu'),
        tf.keras.layers.Dense(10, activation='softmax')
    ])
    
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy')
    model.fit(training_data, epochs=model_config['epochs'])
    
    return model.get_weights()

# Execute training on cluster
weights = train_neural_network(my_data, {'epochs': 50})
```

### Scientific Computing

```python
@cluster(cores=16, memory='64GB')
def monte_carlo_simulation(n_samples=1000000):
    import numpy as np
    
    # This loop will be automatically parallelized
    results = []
    for i in range(n_samples):
        x, y = np.random.random(2)
        if x*x + y*y <= 1:
            results.append(1)
        else:
            results.append(0)
    
    pi_estimate = 4 * sum(results) / len(results)
    return pi_estimate

pi_value = monte_carlo_simulation(10000000)
```

### Data Processing Pipeline

```python
@cluster(cores=8, memory='16GB')
def process_large_dataset(file_path, chunk_size=10000):
    import pandas as pd
    
    results = []
    for chunk in pd.read_csv(file_path, chunksize=chunk_size):
        # Process each chunk
        processed = chunk.groupby('category').sum()
        results.append(processed)
    
    return pd.concat(results)

# Process data on cluster
processed_data = process_large_dataset('/path/to/large_file.csv')
```

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## License

Clustrix is released under the MIT License. See [LICENSE](LICENSE) for details.

## Support

- Documentation: [https://clustrix.readthedocs.io](https://clustrix.readthedocs.io)
- Issues: [https://github.com/ContextLab/clustrix/issues](https://github.com/ContextLab/clustrix/issues)
- Discussions: [https://github.com/ContextLab/clustrix/discussions](https://github.com/ContextLab/clustrix/discussions)