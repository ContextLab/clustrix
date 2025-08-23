# Clustrix

[![Tests](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml/badge.svg)](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/badge/coverage-64%25-yellow.svg)](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Linting: flake8](https://img.shields.io/badge/linting-flake8-blue.svg)](https://github.com/PyCQA/flake8)
[![Type Checking: mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![PyPI version](https://img.shields.io/pypi/v/clustrix.svg)](https://pypi.org/project/clustrix/)
[![Downloads](https://static.pepy.tech/badge/clustrix)](https://pepy.tech/project/clustrix)
[![Documentation](https://readthedocs.org/projects/clustrix/badge/?version=latest)](https://clustrix.readthedocs.io/en/latest/?badge=latest)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Clustrix is a Python package that enables seamless distributed computing on clusters. With a simple decorator, you can execute any Python function remotely on cluster resources while automatically handling dependency management, environment setup, and result collection.

## Features

- **Simple Decorator Interface**: Just add `@cluster` to any function
- **Automated SSH Key Setup**: Create and deplot SSH keys to enable secure passwordless authentication with one click or API call
- **Interactive Jupyter Widget**: `%%clusterfy` magic command with GUI configuration manager
- **Multiple Cluster Support**: SLURM, PBS, SGE, Kubernetes, SSH, and major cloud providers
- **Cloud Provider Integration**: Native support for AWS (EC2/EKS), Google Cloud (GCE/GKE), Azure (VM/AKS), Lambda Cloud, and HuggingFace Spaces
- **Unified Filesystem Utilities**: Work with files seamlessly across local and remote clusters
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

## SSH Key Automation

Clustrix provides automated SSH key setup to eliminate the manual process of generating and deploying SSH keys to clusters. This feature transforms a 15-30 minute manual setup into a **15-second automated process**.

### Quick Setup Methods

#### Method 1: Jupyter Widget (Recommended)
The easiest way is using the interactive widget that appears when you import Clustrix:

```python
import clustrix
# Look for the "SSH Key Setup" section in the widget interface
```

1. Enter your cluster hostname and username
2. Enter your password  
3. Click "Setup SSH Keys"
4. ✅ Done! Secure access in <15 seconds

#### Method 2: CLI Command
```bash
# Basic setup
clustrix ssh-setup --host cluster.university.edu --user your_username

# With custom alias for easy access
clustrix ssh-setup --host cluster.university.edu --user your_username --alias my_hpc

# Now you can connect with: ssh my_hpc
```

#### Method 3: Python API
```python
from clustrix import setup_ssh_keys_with_fallback
from clustrix.config import ClusterConfig

config = ClusterConfig(
    cluster_type="slurm",
    cluster_host="cluster.university.edu", 
    username="your_username"
)

result = setup_ssh_keys_with_fallback(config)
if result["success"]:
    print("✅ SSH keys setup successfully!")
```

### Key Features

- **🔒 Secure**: Ed25519 keys with proper permissions (600/644)
- **🧹 Smart Cleanup**: Automatically removes conflicting old keys
- **🔄 Key Rotation**: Force refresh to generate new keys
- **🌐 Cross-platform**: Works on Windows, macOS, Linux  
- **🏢 Enterprise Ready**: Handles Kerberos clusters gracefully
- **💡 Smart Fallbacks**: Environment-specific password retrieval

### Password Fallback System

No need to enter passwords manually! Clustrix automatically retrieves passwords from:

- **Google Colab**: Colab secrets (stored securely)
- **Environment Variables**: `CLUSTRIX_PASSWORD_*` or `CLUSTER_PASSWORD`
- **Interactive Prompts**: GUI popups in notebooks, terminal prompts in CLI

### Enterprise Cluster Support

For university/enterprise clusters using Kerberos authentication:
```bash
# Clustrix deploys keys successfully, then use Kerberos for auth
kinit your_netid@UNIVERSITY.EDU
ssh your_netid@cluster.university.edu
```

**📖 For complete details, try the interactive [SSH Key Automation Tutorial](docs/ssh_key_automation_tutorial.ipynb)** [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/ContextLab/clustrix/blob/master/docs/ssh_key_automation_tutorial.ipynb)

## Advanced Usage

### Unified Filesystem Utilities

Clustrix provides unified filesystem operations that work seamlessly across local and remote clusters:

```python
from clustrix import cluster_ls, cluster_find, cluster_stat, cluster_exists, cluster_glob
from clustrix.config import ClusterConfig

# Configure for local or remote operations
config = ClusterConfig(
    cluster_type="slurm",  # or "local" for local operations
    cluster_host="cluster.edu",
    username="researcher",
    remote_work_dir="/scratch/project"
)

# List directory contents (works locally and remotely)
files = cluster_ls("data/", config)

# Find files by pattern
csv_files = cluster_find("*.csv", "datasets/", config)

# Check file existence
if cluster_exists("results/output.json", config):
    print("Results already computed!")

# Get file information
file_info = cluster_stat("large_dataset.h5", config)
print(f"Dataset size: {file_info.size / 1e9:.1f} GB")

# Use with @cluster decorator for data-driven workflows
@cluster(cores=8)
def process_datasets(config):
    # Find all data files on the cluster
    data_files = cluster_glob("*.csv", "input/", config)
    
    results = []
    for filename in data_files:  # Loop gets parallelized automatically
        # Check file size before processing
        file_info = cluster_stat(filename, config)
        if file_info.size > 100_000_000:  # Large files
            result = process_large_file(filename, config)
        else:
            result = process_small_file(filename, config)
        results.append(result)
    
    return results
```

**Available filesystem operations:**

- `cluster_ls()` - List directory contents
- `cluster_find()` - Find files by pattern (recursive)
- `cluster_stat()` - Get file information (size, modified time, permissions)
- `cluster_exists()` - Check if file/directory exists
- `cluster_isdir()` / `cluster_isfile()` - Check file type
- `cluster_glob()` - Pattern matching for files
- `cluster_du()` - Directory usage information
- `cluster_count_files()` - Count files matching pattern

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

### Important Notes

**⚠️ REPL/Interactive Python Limitation**: Functions defined interactively in the Python REPL (command line `python` interpreter) cannot be serialized for remote execution because their source code is not available. This affects:
- Interactive Python sessions (`python` command)
- Some notebook environments that don't preserve function source

**✅ Recommended Approach**: Define functions in:
- Python files (`.py` scripts)
- Jupyter notebooks 
- IPython environments
- Any environment where `inspect.getsource()` can access the function source code

```python
# ❌ This won't work in interactive Python REPL
>>> @cluster(cores=2)
... def my_function(x):
...     return x * 2
>>> my_function(5)  # Error: source code not available

# ✅ This works in .py files and notebooks
@cluster(cores=2)
def my_function(x):
    return x * 2

result = my_function(5)  # Works correctly
```

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

## Testing Philosophy

Clustrix follows a strict **NO MOCKS** testing policy. All tests use real infrastructure to ensure reliability:

- **✅ Real Infrastructure**: Tests run on actual clusters, containers, and cloud services
- **✅ Real Computations**: Genuine data processing validates functionality
- **✅ Real Failures**: Actual error conditions test recovery mechanisms
- **❌ No Mock Objects**: Zero use of `@patch`, `Mock()`, or simulations

### Running Tests

```bash
# Quick unit tests (local execution)
pytest tests/ -m "not real_world"

# Comprehensive real-world tests (requires infrastructure)
python tests/run_real_world_tests.py

# Setup local test infrastructure (Docker-based)
python tests/infrastructure/setup_test_infrastructure.py setup

# Run specific test categories
pytest tests/comprehensive/test_edge_cases_real.py
pytest tests/comprehensive/test_performance_benchmarks_real.py
pytest tests/comprehensive/test_failure_recovery_real.py
```

### Test Infrastructure

Clustrix provides Docker-based local test infrastructure for cost-free testing:

- **Kubernetes**: Kind (Kubernetes in Docker) cluster
- **SSH Server**: OpenSSH test server on port 2222
- **SLURM Mock**: Simulated SLURM scheduler
- **MinIO**: S3-compatible object storage
- **PostgreSQL**: Database for state management
- **Redis**: Cache and message queue

### Test Categories

1. **Unit Tests**: Core functionality with local execution
2. **Integration Tests**: Multi-component interactions with real services
3. **Edge Cases**: Boundary conditions and unusual scenarios
4. **Performance**: Latency, throughput, and scalability benchmarks
5. **Failure Recovery**: Error handling and resilience testing

## Code Quality

Clustrix maintains high code quality standards:

- **Testing**: Real-world tests following NO MOCKS principle
- **Code Style**: Enforced with Black formatter
- **Linting**: Checked with flake8
- **Type Checking**: Validated with mypy
- **CI/CD**: GitHub Actions for automated testing

To check code quality locally:

```bash
# Run comprehensive quality check (auto-retries until clean)
python scripts/pre_push_check.py

# Run individual checks
pytest --cov=clustrix --cov-report=term
black clustrix/
flake8 clustrix/
mypy clustrix/
```

### Pre-commit Hooks

Pre-commit hooks automatically run quality checks before each commit:

```bash
# Install pre-commit hooks
pre-commit install

# Manual run
pre-commit run --all-files
```

## Additional Documentation

For more detailed information on specific topics, see the organized documentation in the `docs/` directory:

### Cloud Provider Setup
- **[AWS Setup Guide](docs/aws/AWS_PERMISSIONS_SETUP_GUIDE.md)** - Complete AWS permissions configuration
- **[AWS Console Quick Steps](docs/aws/AWS_CONSOLE_QUICK_STEPS.md)** - Fast AWS setup guide
- **[AWS EKS Policy Setup](docs/aws/ADD_CUSTOM_EKS_POLICY.md)** - EKS-specific policy configuration
- **[AWS EKS Troubleshooting](docs/aws/AWS_EKS_TROUBLESHOOTING.md)** - Common AWS access issues

### GPU Computing
- **[GPU Parallelization Design](docs/gpu/GPU_PARALLELIZATION_DESIGN.md)** - Comprehensive GPU parallelization guide
- **[GPU Detection Fix](docs/gpu/GPU_DETECTION_FIX.md)** - GPU detection troubleshooting

### Technical Design
- **[Function Dependency Design](docs/design/function_dependency_design.md)** - Function dependency resolution architecture
- **[Complexity Threshold Analysis](docs/design/COMPLEXITY_THRESHOLD_ANALYSIS.md)** - Function complexity analysis and optimization

### Complete Documentation
- **[Full Documentation](https://clustrix.readthedocs.io)** - Complete API reference and tutorials
- **[SSH Setup Tutorial](docs/ssh_key_automation_tutorial.ipynb)** - Interactive SSH key automation guide

## Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

## License

Clustrix is released under the MIT License. See [LICENSE](LICENSE) for details.

## Support

- Documentation: [https://clustrix.readthedocs.io](https://clustrix.readthedocs.io)
- Issues: [https://github.com/ContextLab/clustrix/issues](https://github.com/ContextLab/clustrix/issues)
