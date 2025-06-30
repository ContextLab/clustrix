# Clustrix

[![Tests](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml/badge.svg)](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/badge/coverage-70%25-green.svg)](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Linting: flake8](https://img.shields.io/badge/linting-flake8-blue.svg)](https://github.com/PyCQA/flake8)
[![Type Checking: mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![PyPI version](https://img.shields.io/pypi/v/clustrix.svg)](https://pypi.org/project/clustrix/)
[![Downloads](https://img.shields.io/pypi/dm/clustrix.svg)](https://pypi.org/project/clustrix/)
[![Documentation](https://readthedocs.org/projects/clustrix/badge/?version=latest)](https://clustrix.readthedocs.io/en/latest/?badge=latest)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Clustrix is a Python package that enables seamless distributed computing on clusters. With a simple decorator, you can execute any Python function remotely on cluster resources while automatically handling dependency management, environment setup, and result collection.

## Features

- **Simple Decorator Interface**: Just add `@cluster` to any function
- **Interactive Jupyter Widget**: `%%clusterfy` magic command with GUI configuration manager
- **Multiple Cluster Support**: SLURM, PBS, SGE, Kubernetes, SSH, and major cloud providers
- **Cloud Provider Integration**: Native support for AWS (EC2/EKS), Google Cloud (GCE/GKE), Azure (VM/AKS), Lambda Cloud, and HuggingFace Spaces
- **Automatic Dependency Management**: Captures and replicates your exact Python environment
- **Native Cost Monitoring**: Built-in cost tracking for all major cloud providers
- **Kubernetes Support**: Deploy to EKS, GKE, AKS, or any Kubernetes cluster
- **Loop Parallelization**: Automatically distributes loops across cluster nodes
- **Flexible Configuration**: Easy setup with config files, environment variables, or interactive widget
- **Dynamic Instance Selection**: Auto-populated dropdowns for cloud instance types and regions
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

### Jupyter Notebook Integration

Clustrix provides seamless integration with Jupyter notebooks through an interactive widget:

```python
import clustrix  # Auto-loads the magic command

# Use the %%clusterfy magic command to open the configuration widget
```

```jupyter
%%clusterfy
# Interactive widget appears with:
# - Dropdown to select configurations
# - Forms to create/edit cluster setups  
# - One-click configuration application
# - Save/load configurations to files
```

#### Interactive Configuration Widget

The Clustrix widget provides a comprehensive GUI for managing cluster configurations directly in Jupyter notebooks. Here's what you'll see when you use the `%%clusterfy` magic command:

##### Default View
When the widget first loads, it displays the "Local Single-core" configuration for quick testing:

![Default widget view showing Local Single-core configuration](https://github.com/user-attachments/assets/44baefd7-9dd7-452d-bc43-ef53136d13a4)

##### Configuration Dropdown
The dropdown menu includes pre-built templates for various cluster types and cloud providers:

![Configuration dropdown showing available templates](https://github.com/user-attachments/assets/744e2eef-e03e-46bd-9bb1-e0ed2c11cf45)

The widget includes pre-built templates for:
- **Local Development**: 
  - Local Single-core: Run jobs on one CPU core
  - Local Multi-core: Utilize all available CPU cores
- **HPC Clusters**:
  - SLURM: University and research cluster support
  - PBS/SGE: Traditional HPC schedulers
- **Cloud Providers**:
  - **AWS**: EC2 instances and EKS Kubernetes clusters
  - **Google Cloud**: Compute Engine VMs and GKE clusters
  - **Azure**: Virtual Machines and AKS Kubernetes clusters
  - **Lambda Cloud**: GPU-optimized instances for ML/AI
  - **HuggingFace Spaces**: Deploy to HF Spaces infrastructure
- **Kubernetes**: Native container orchestration support

##### Cluster Configuration Examples

###### SLURM Cluster Configuration
For traditional HPC clusters, the widget provides all essential fields:

![SLURM configuration with basic settings](https://github.com/user-attachments/assets/994a803d-485e-4d14-bf93-2f0223066510)

The advanced settings accordion reveals additional options:

![SLURM advanced configuration options](https://github.com/user-attachments/assets/0930eee0-ee52-4d53-9165-81e907c7d962)

Advanced options include:
- Module loads (e.g., `python/3.9`, `cuda/11.2`)
- Environment variables
- Pre-execution commands
- Custom SSH key paths
- Cost monitoring toggles

###### Cloud Provider Configuration

**Google Cloud Platform:**
When selecting a cloud provider, only relevant fields are displayed:

![GCP VM configuration interface](https://github.com/user-attachments/assets/53acb782-65cd-4b65-93a0-98286144f223)

**Lambda Cloud GPU Instances:**
The widget dynamically populates instance type dropdowns based on the selected provider:

![Lambda Cloud with GPU instance dropdown](https://github.com/user-attachments/assets/4bd0e176-d5f3-430e-86af-ddbfff827d77)

##### Key Widget Features

1. **Dynamic Field Visibility**: Only shows fields relevant to the selected cluster type
2. **Provider-Specific Options**: 
   - AWS: Region selection, instance types, EKS cluster options
   - Azure: Resource groups, VM sizes, AKS configuration
   - GCP: Projects, zones, machine types, GKE options
   - Lambda Cloud: GPU instance selection with live pricing
3. **Input Validation**: Real-time validation for hostnames, IP addresses, and configuration values
4. **Tooltips**: Hover over any field label to see detailed help text
5. **Configuration Management**:
   - Save configurations to YAML/JSON files
   - Load existing configurations
   - Test configurations before applying
   - Add/delete custom configurations

##### Using the Widget

1. **Select a Configuration**: Choose from the dropdown or create a new one
2. **Edit Settings**: Modify cluster connection details and resource requirements
3. **Advanced Options**: Expand the accordion for environment setup and additional settings
4. **Apply Configuration**: Click "Apply Configuration" to use these settings for subsequent `@cluster` decorated functions
5. **Save for Later**: Use "Save Configuration" to persist settings to a file

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

### Cost Monitoring

Clustrix includes built-in cost monitoring for cloud providers:

```python
from clustrix import cost_tracking_decorator, get_cost_monitor

# Automatic cost tracking with decorator
@cost_tracking_decorator('aws', 'p3.2xlarge')  
@cluster(cores=8, memory='60GB')
def expensive_training():
    # Your training code here
    pass

# Manual cost monitoring
monitor = get_cost_monitor('gcp')
cost_estimate = monitor.estimate_cost('n2-standard-4', hours_used=2.0)
print(f"Estimated cost: ${cost_estimate.estimated_cost:.2f}")

# Get pricing information
pricing = monitor.get_pricing_info()
recommendations = monitor.get_cost_optimization_recommendations()
```

Supported cloud providers: **AWS**, **Google Cloud**, **Azure**, **Lambda Cloud**

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

### Cloud Provider Integration

Clustrix provides native integration with major cloud providers for both VM and Kubernetes deployments:

#### AWS Integration

```python
# Configure AWS credentials and region
clustrix.configure(
    cluster_type='aws',
    access_key_id='YOUR_ACCESS_KEY',
    secret_access_key='YOUR_SECRET_KEY',
    region='us-west-2'
)

# Run on EC2 instance
@cluster(provider='aws', instance_type='p3.2xlarge', cores=8, memory='61GB')
def train_on_aws():
    # GPU-accelerated training on AWS
    pass

# Run on EKS Kubernetes cluster
@cluster(provider='aws', cluster_type='kubernetes', cluster_name='my-eks-cluster')
def distributed_training():
    # Runs on Amazon EKS
    pass
```

#### Google Cloud Integration

```python
# Configure GCP with service account
clustrix.configure(
    cluster_type='gcp',
    project_id='your-project-id',
    service_account_key='path/to/service-account-key.json',
    region='us-central1'
)

# Run on Compute Engine
@cluster(provider='gcp', machine_type='n1-highmem-8', cores=8, memory='52GB')
def analyze_data():
    # High-memory computation on GCP
    pass

# Run on GKE cluster
@cluster(provider='gcp', cluster_type='kubernetes', cluster_name='my-gke-cluster')
def kubernetes_job():
    # Runs on Google Kubernetes Engine
    pass
```

#### Azure Integration

```python
# Configure Azure with service principal
clustrix.configure(
    cluster_type='azure',
    subscription_id='YOUR_SUBSCRIPTION_ID',
    client_id='YOUR_CLIENT_ID',
    client_secret='YOUR_CLIENT_SECRET',
    tenant_id='YOUR_TENANT_ID',
    region='eastus'
)

# Run on Azure VM
@cluster(provider='azure', vm_size='Standard_NC6', cores=6, memory='56GB')
def gpu_workload():
    # GPU computation on Azure
    pass

# Run on AKS cluster
@cluster(provider='azure', cluster_type='kubernetes', cluster_name='my-aks-cluster')
def container_workload():
    # Runs on Azure Kubernetes Service
    pass
```

#### Lambda Cloud Integration

```python
# Configure Lambda Cloud for GPU workloads
clustrix.configure(
    cluster_type='lambda_cloud',
    api_key='YOUR_LAMBDA_API_KEY'
)

# Run on Lambda GPU instance
@cluster(provider='lambda_cloud', instance_type='gpu_1x_a100', cores=30, memory='200GB')
def train_large_model():
    # A100 GPU training on Lambda Cloud
    pass
```

#### HuggingFace Spaces Integration

```python
# Configure HuggingFace Spaces
clustrix.configure(
    cluster_type='huggingface_spaces',
    token='YOUR_HF_TOKEN'
)

# Deploy to HuggingFace Spaces
@cluster(provider='huggingface_spaces', space_hardware='gpu-t4-medium')
def inference_endpoint():
    # Runs on HuggingFace infrastructure
    pass
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

## Code Quality

Clustrix maintains high code quality standards:

- **Testing**: Comprehensive test suite with pytest
- **Code Style**: Enforced with Black formatter
- **Linting**: Checked with flake8
- **Type Checking**: Validated with mypy

To check code quality locally:

```bash
# Run all quality checks
python scripts/check_quality.py

# Run individual checks
pytest --cov=clustrix --cov-report=term
black --check clustrix/
flake8 clustrix/
mypy clustrix/
```

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## License

Clustrix is released under the MIT License. See [LICENSE](LICENSE) for details.

## Support

- Documentation: [https://clustrix.readthedocs.io](https://clustrix.readthedocs.io)
- Issues: [https://github.com/ContextLab/clustrix/issues](https://github.com/ContextLab/clustrix/issues)
