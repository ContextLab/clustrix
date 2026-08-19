# Clustrix

[![Tests](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/ContextLab/clustrix/actions/workflows/tests.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Linting: flake8](https://img.shields.io/badge/linting-flake8-blue.svg)](https://github.com/PyCQA/flake8)
[![Type Checking: mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![PyPI version](https://img.shields.io/pypi/v/clustrix.svg)](https://pypi.org/project/clustrix/)
[![Downloads](https://static.pepy.tech/badge/clustrix)](https://pepy.tech/project/clustrix)
[![Documentation](https://readthedocs.org/projects/clustrix/badge/?version=latest)](https://clustrix.readthedocs.io/en/latest/?badge=latest)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Clustrix is a Python package that enables seamless distributed computing on clusters. With a simple decorator, you can execute any Python function remotely on cluster resources while automatically handling dependency management, environment setup, and result collection.

## Features

- **Simple Decorator Interface**: Just add `@cluster` to any function
- **Automated SSH Key Setup**: Create and deploy SSH keys to enable secure passwordless authentication with one click or API call
- **Interactive Jupyter Widget**: `%%remote` magic command with GUI configuration manager
- **Multiple Cluster Backends**: SLURM, SSH and HuggingFace Jobs are verified working; PBS, SGE and Kubernetes are implemented but untested (see [Supported Cluster Types](#supported-cluster-types))
- **Unified Filesystem Utilities**: Work with files seamlessly across local and remote clusters
- **Automatic Dependency Management**: Captures and replicates your exact Python environment
- **Loop Parallelization**: distributes a loop across nodes when its body has no
  dependencies between iterations. The analysis is conservative and declines
  most real loops — see [Limitations](https://clustrix.readthedocs.io/en/latest/limitations.html)
- **Flexible Configuration**: config files, `configure()`, or the interactive
  widget. Note there is no general "override any field from the environment"
  mechanism — only `CLUSTRIX_CONFIG_DIR` and the password variable named by
  `password_env_var`
- **Error Handling**: Comprehensive error reporting and job monitoring

Read [Supported Cluster Types](#supported-cluster-types) before relying on a
backend. Not everything in this package works, and the sections below say which
parts do.

## Quick Start

### Installation

> **⚠️ PyPI is behind this README.** `pip install clustrix` installs **0.1.1**;
> this document describes **0.2.0**. 0.1.1 predates the fixes for two real
> defects: `@cluster` could return a fabricated string instead of your result,
> and remote results were unpickled without authentication (a remote-to-local
> code execution path). Until 0.2.0 is published, install from the repository.

```bash
pip install "git+https://github.com/ContextLab/clustrix.git@master"
```

Check what you actually have:

```bash
python -c "import clustrix; print(clustrix.__version__)"
```

### Basic Configuration

```python
# cluster-required: needs a configured cluster to execute
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
# cluster-required: needs a configured cluster to execute
from clustrix import cluster

@cluster(cores=8, memory='16GB', time='02:00:00')
def expensive_computation(data, iterations=1000):
    import numpy as np
    array = np.asarray(data)
    result = 0
    for i in range(iterations):
        result += np.sum(array ** 2)
    return result

# This function will execute on the cluster
data = [1, 2, 3, 4, 5]
result = expensive_computation(data, iterations=10000)
print(f"Result: {result}")
```

### Jupyter Notebook Integration

Clustrix ships an IPython magic that opens a configuration widget in the
notebook:

```jupyter
%%remote
```

Importing `clustrix` registers the magic but does **not** display the widget --
a library should not inject UI as a side effect of being imported. Run
`%%remote` in a cell when you want the widget, or call
`clustrix.notebook_magic.display_config_widget()`. Setting
`CLUSTRIX_AUTO_WIDGET=1` restores the old display-on-import behaviour.

`%%clusterfy` still works as a deprecated alias and emits a `DeprecationWarning`.

#### Interactive Configuration Widget

The widget edits the same settings as `clustrix.configure()` and applies them to
the current session.

![Clustrix widget in JupyterLab, light theme](docs/evidence/widget/02-after-light.jpg)

Its colours resolve through JupyterLab's own theme variables, so it follows the
notebook theme instead of carrying a second hand-maintained dark stylesheet:

![The same widget in JupyterLab's dark theme](docs/evidence/widget/03-after-dark.jpg)

"Show advanced" reveals package manager, Python executable, environment
variables, module loads and pre-execution commands:

![Widget with the Advanced panel expanded](docs/evidence/widget/04-advanced-light.jpg)

##### What the widget covers

The cluster type dropdown offers `local`, `ssh`, `slurm`, `pbs`, `sge`,
`kubernetes` and `huggingface`.

- `ssh`, `slurm`, `pbs`, `sge` show the connection section: host, port,
  username, SSH key file, password, remote work directory, an environment
  variable to read the password from, and an "Auto setup SSH keys" button.
- `huggingface` shows namespace, flavor, token, and an "Allow paid GPU flavors"
  checkbox. GPU flavors bill by the second, so that box has to be ticked before
  one is accepted.
- `kubernetes` shows a Kubernetes section: namespace, image, service account and
  image pull policy. The remaining `k8s_*` settings (node count, region,
  provider, auto-provisioning) are config-file or `clustrix.configure()` only.

There are no AWS, GCP, Azure or Lambda Cloud entries: those backends are
unverified (see [Cloud Providers](#cloud-providers)).

##### Using the widget

1. **Select a profile**: choose one from the dropdown or add a new one with `+`
2. **Edit settings**: cluster type, resources, connection details
3. **Advanced options**: click "Show advanced" for environment setup
4. **Test**: "Test connection" and "Test job submission" report into the Output
   panel at the bottom of the widget
5. **Apply**: "Apply" calls `configure()` with these settings, so subsequent
   `@cluster` functions use them
6. **Save for later**: "Save" writes the profile to the named configuration file

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

`remote_work_dir` defaults to `~/.clustrix/jobs`. It must be on a filesystem
the compute node can see: on SLURM, PBS and SGE each node has its own `/tmp`,
so an environment built on the login node is simply absent at run time and the
job dies with exit 127 before writing any diagnostics. A home directory or a
shared scratch path (as above) both work; `/tmp` does not.

Clustrix reads `config.yml`, `config.yaml` or `config.json` from `~/.clustrix`,
then `clustrix.yml`/`.yaml`/`.json` from the current directory, and stops at the
first one it finds. Setting `CLUSTRIX_CONFIG_DIR` moves the first of those
locations somewhere else, which matters in containers and CI images where
`$HOME` is not writable or not persistent, on machines shared by several
projects, and in tests -- without it, the widget's "Save" button writes into
your real `~/.clustrix` during a test run.

Two timeouts are worth knowing about:

- `ssh_connect_timeout` (default 30 seconds) bounds how long paramiko waits to
  establish a connection. The OS default is minutes, which turns an unreachable
  host into a hang rather than an error.
- `venv_setup_timeout` (default 300 seconds) bounds remote virtualenv creation.

## SSH Key Automation

Clustrix provides automated SSH key setup: it generates a key, deploys it to
the cluster and writes the `~/.ssh/config` entry in one call, instead of doing
those three steps by hand.

### Quick Setup Methods

#### Method 1: Jupyter Widget (Recommended)
Open the widget with the `%%remote` magic:

```jupyter
%%remote
```

1. Choose a remote cluster type (`ssh`, `slurm`, `pbs` or `sge`) so the
   connection section appears
2. Enter your cluster hostname and username
3. Enter your password
4. Click "Auto setup SSH keys"

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
# cluster-required: needs a configured cluster to execute
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
# cluster-required: needs a configured cluster to execute
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
    # Runs sequentially on the cluster node -- auto-parallelization needs a
    # literal range() and a function that accepts the chunk keywords.
    for filename in data_files:
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

Clustrix includes cost estimation for cloud providers. This is independent of
the (broken) cloud execution backends: it queries pricing and reports local
resource usage, and never submits a job.

```python
from clustrix import get_cost_monitor

monitor = get_cost_monitor('gcp')

cost_estimate = monitor.estimate_cost('n2-standard-4', hours_used=2.0)
print(f"Estimated cost: ${cost_estimate.estimated_cost:.2f}")

pricing = monitor.get_pricing_info()          # {instance_type: hourly_usd}

usage = monitor.get_resource_usage()          # CPU/memory/GPU on this machine
recommendations = monitor.get_cost_optimization_recommendations(
    usage, cost_estimate
)
```

`get_cost_optimization_recommendations()` takes the usage and the estimate as
positional arguments; calling it with none raises `TypeError`.

Providers with a cost monitor: **AWS**, **Google Cloud**, **Azure**, **Lambda
Cloud**. Where a live pricing API is unavailable the monitor falls back to a
hardcoded table and says so on stderr.

### Custom Resource Requirements

```python
# cluster-required: needs a configured cluster to execute
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
# cluster-required: needs a configured cluster to execute
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
# cluster-required: needs a configured cluster to execute
# SLURM cluster
clustrix.configure(cluster_type='slurm', cluster_host='slurm.example.com')

# Simple SSH execution (no scheduler)
clustrix.configure(cluster_type='ssh', cluster_host='server.example.com')

# HuggingFace Jobs (no host: work is submitted over an HTTP API)
clustrix.configure(cluster_type='huggingface', hf_namespace='my-org')

# PBS and SGE clusters (implemented, not verified against real hardware)
clustrix.configure(cluster_type='pbs', cluster_host='pbs.example.com')
clustrix.configure(cluster_type='sge', cluster_host='sge.example.com')

# Kubernetes (implemented, not verified against a real cluster)
clustrix.configure(cluster_type='kubernetes')
```

### HuggingFace Jobs

`cluster_type="huggingface"` runs each function inside a HuggingFace Job. It
needs no cluster reservation, no VPN and no institutional SSH credentials,
which is why the integration tests use it.

```python
# cluster-required: needs a configured cluster to execute
import clustrix
from clustrix import cluster

clustrix.configure(
    cluster_type='huggingface',
    hf_token='hf_...',        # optional: HF_TOKEN or `hf auth login` also work
    hf_namespace='my-org',    # usually an org, not a personal account
)

@cluster(cores=2)
def where_am_i():
    import platform
    return platform.node(), platform.python_version()

print(where_am_i())
```

How a job is carried out: the function, its arguments and its keyword arguments
are serialized with dill and passed to the container in an environment
variable; the container decodes them, calls the function, and prints the
serialized result between two marker lines; this side reads the job's logs and
decodes what is between them. There is no shared filesystem, so nothing is
uploaded and nothing is left behind.

Configuration:

| Option | Default | Notes |
|-|-|-|
| `hf_namespace` | `hf_username` | The account the job is billed to. Personal accounts are often not on a plan that can run jobs, so this is usually an org. |
| `hf_flavor` | `cpu-basic` | Hardware tier. |
| `hf_image` | `python:<your minor version>-slim` | See below. |
| `hf_job_timeout` | `30m` | Passed to the HF API. |
| `hf_allow_gpu_flavors` | `False` | Must be `True` before any non-`cpu-` flavor is accepted. |

Three things regularly catch people out:

- **The image tracks your local Python.** dill payloads carry CPython bytecode,
  which is not portable across minor versions, so the container image defaults
  to the calling interpreter's version. Overriding `hf_image` with a mismatched
  Python is the single most likely way to get an "unknown opcode" failure.
- **GPU flavors bill by the second.** Any flavor whose name does not start with
  `cpu-` is treated as a GPU tier and rejected unless
  `hf_allow_gpu_flavors=True`. The check is a prefix test rather than a list of
  GPU names, so new HF hardware fails closed rather than being waved through.
- **Payloads are capped at 256 KB** once base64-encoded. Pass large data through
  a Hub dataset and load it inside the function rather than closing over it.

Deserializing a dill payload executes arbitrary code, so results are not
trusted on sight. Each job is given a fresh random key as a job secret; the
container prints an HMAC-SHA256 of the bytes it emitted, and clustrix refuses
to unpickle anything whose tag does not verify. The SSH and scheduler paths
verify their results the same way.

### Cloud Providers

The `provider=` argument to `@cluster` (`'aws'`, `'gcp'`, `'azure'`,
`'lambda'`, `'huggingface'`) routes to the AWS EC2, Google Compute Engine,
Azure VM and Lambda Cloud backends. **None of them has been shown to run a job
end to end.** Until recently the path could not have run at all: the serializer
writes the function under a `"function"` key while the remote bootstrap read
`"func"`, so every cloud job died with a `KeyError` on its first line. That was
fixed (issue #119), but nothing has since demonstrated a completed cloud job,
and `scripts/collect_execution_evidence.py` does not cover these backends.

Treat the cloud tutorials in the documentation as a description of the intended
interface rather than a record of something that has been run. The notebook
widget does not offer these as cluster types.

The pricing and cost-estimation clients for those providers (see
[Cost Monitoring](#cost-monitoring)) are separate code and do work; they query
provider pricing APIs and do not submit jobs.

`cluster_type='huggingface'` (HuggingFace Jobs, above) is a different thing
from `provider='huggingface'` (the HuggingFace Spaces provider, which never
satisfied the dispatch interface). Use the former.

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

# Set up SSH keys for a cluster
clustrix ssh-setup --host cluster.example.com --user myuser

# Manage stored credentials
clustrix credentials --help
```

## How It Works

1. **Function Serialization**: Clustrix captures your function, arguments, and dependencies using advanced serialization
2. **Environment Replication**: Creates an identical Python environment on the cluster with all required packages
3. **Job Submission**: Submits your function as a job to the cluster scheduler
4. **Execution**: Runs your function on cluster resources with specified requirements
5. **Result Collection**: Automatically retrieves results once execution completes
6. **Cleanup**: Optionally cleans up temporary files and environments

### Important Notes

**⚠️ REPL/Interactive Python Limitation**: Functions defined interactively in the Python REPL (command line `python` interpreter) lose the *source-based* features — automatic loop parallelization and complexity analysis — because those parse the function's source with `ast` and `inspect.getsource()` cannot recover it.

Serialization itself does **not** need the source. `clustrix.utils.serialize_function` / `deserialize_function` work from the code object and round-trip such a function correctly, so it still runs remotely and returns the right answer. This affects:
- Interactive Python sessions (`python` command)
- Some notebook environments that don't preserve function source

**✅ Recommended Approach**: Define functions in:
- Python files (`.py` scripts)
- Jupyter notebooks 
- IPython environments
- Any environment where `inspect.getsource()` can access the function source code

```pycon
# In the interactive REPL this still runs and returns the right answer, but no
# loop parallelization is applied, because that
# need the source.
>>> @cluster(cores=2)
... def my_function(x):
...     return x * 2
>>> my_function(5)  # -> 10, executed remotely, analysed features skipped
10
```

In a `.py` file or a notebook you get everything, including the source-based
features:

```python
# cluster-required: needs a configured cluster to execute
from clustrix import cluster

@cluster(cores=2)
def my_function(x):
    return x * 2

result = my_function(5)
```

## Supported Cluster Types

| `cluster_type` | Status |
|-|-|
| `slurm` | Verified. A real job ran on `discovery.dartmouth.edu` and returned its result. |
| `ssh` | Verified. Direct execution over SSH with no scheduler; a real job ran on an 8-GPU host. |
| `huggingface` | Verified. HuggingFace Jobs; a real job ran in a container. |
| `local` | Runs in local processes. Used for development and the fast tests. |
| `pbs` | Implemented, **not verified**. All four of SLURM/PBS/SGE/SSH now share one environment-setup path, so PBS builds the same two-venv environment SLURM does -- but no PBS job has been run against a real scheduler. |
| `sge` | Implemented, **not verified**. Same caveat as PBS. |
| AWS / GCP / Azure / Lambda VM backends | **Unverified.** No cloud job has been shown to run end to end. See [Cloud Providers](#cloud-providers). |

The three "Verified" rows are the backends exercised by
`scripts/collect_execution_evidence.py`, which submits a genuine job to each
reachable target, waits for it, and prints what came back. Nothing in it is
mocked, and a target it cannot reach is reported as skipped rather than as
passing:

```bash
python scripts/collect_execution_evidence.py            # all reachable targets
python scripts/collect_execution_evidence.py slurm gpu  # a subset
```

Credentials come from `~/.clustrix-dev-credentials` or from the environment
(`CLUSTRIX_SLURM_PASSWORD`, `CLUSTRIX_GPU_PASSWORD`, `HF_TOKEN`). The transcript
of the last run is in [docs/evidence/execution-evidence.txt](docs/evidence/execution-evidence.txt).

## Repository Structure

The Clustrix repository follows standard Python project organization:

```
clustrix/
├── clustrix/              # Main package source code
│   ├── __init__.py       # Package initialization and public API
│   ├── decorator.py      # @cluster decorator implementation
│   ├── config.py         # Configuration management
│   ├── executor_*.py     # Execution engines (core, connections, schedulers, etc.)
│   ├── filesystem.py     # Cross-cluster filesystem utilities
│   ├── utils.py          # Core utilities and job management
│   ├── cli.py            # Command line interface
│   ├── kubernetes/       # Kubernetes providers (AWS, GCP, Azure, etc.)
│   └── pricing_clients/  # Cost monitoring integrations
├── tests/                # Test suite organized by category
│   ├── unit/            # Fast unit tests (run in CI)
│   ├── integration/     # Provisions REAL billable AWS resources;
│   │                    # refuses to run without CLUSTRIX_ALLOW_BILLABLE=1
│   ├── real_world/      # Tests requiring actual cluster access
│   ├── comprehensive/   # Performance and edge case tests
│   └── infrastructure/  # Test infrastructure setup
├── docs/                # Documentation and tutorials
│   ├── source/          # Sphinx documentation source
│   │   └── notebooks/   # Tutorial notebooks
│   └── *.md             # Various documentation files
├── scripts/             # Utility scripts for development
│   ├── check_quality.py               # Code quality validation
│   ├── pre_push_check.py              # Pre-push validation script
│   ├── run_real_world_tests.py        # Real world test runner
│   └── collect_execution_evidence.py  # Real job on every reachable backend
└── .github/             # CI/CD configuration
    ├── workflows/       # GitHub Actions workflows
    └── ISSUE_TEMPLATE/  # Issue templates
```

### Development Workflow

- **Testing**: Use `pytest tests/unit/` for development testing.
  `tests/integration/` provisions real, billable AWS resources and is
  refused unless `CLUSTRIX_ALLOW_BILLABLE=1` is set deliberately (#109).
- **Quality Checks**: Run `python scripts/check_quality.py` before committing
- **Real World Tests**: Use `python scripts/run_real_world_tests.py` when credentials available
- **Documentation**: Build with `cd docs && make html`

## Dependencies

Clustrix automatically handles dependency management by:

- Capturing your current Python environment by reading installed package
  metadata directly (`importlib.metadata`), not by shelling out to `pip freeze`
  -- the freeze output renders conda-built packages as unusable local paths,
  which silently dropped a third of the environment
- Creating virtual environments on cluster nodes
- Installing exact package versions to match your local environment
- Supporting conda environments for complex scientific software stacks

## Error Handling and Monitoring

```python
# cluster-required: needs a real submitted job to monitor
import clustrix
from clustrix import ClusterExecutor

executor = ClusterExecutor(clustrix.get_config())

# job_id is what submit_job() returned for a job you actually submitted.
status = executor.get_job_status(job_id)

# Cancel it if needed.
executor.cancel_job(job_id)
```

Results are HMAC-verified before they are deserialized, so a job whose result
cannot be authenticated raises rather than returning a value -- see
[the execution model](https://clustrix.readthedocs.io/en/latest/execution_model.html).

## Examples

### Machine Learning Training

```python
# cluster-required: needs a configured cluster to execute
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
# cluster-required: needs a configured cluster to execute
@cluster(cores=16, memory='64GB')
def monte_carlo_simulation(n_samples=1000000):
    import numpy as np
    
    # NOTE: this loop is NOT auto-parallelized. range(n_samples) is not a
    # literal range, and this function does not accept the chunk keywords,
    # so clustrix runs it whole on one node. That is still useful -- the
    # node has 16 cores and 64GB -- but the parallelism is yours to write.
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
# cluster-required: needs a configured cluster to execute
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

The goal is that anything claimed to work has been run for real, against real
infrastructure, and that the transcript is reproducible by someone else in one
command. `scripts/collect_execution_evidence.py` is that command, and
`docs/evidence/execution-evidence.txt` is its output.

The existing test suite does not meet that goal yet. It is being worked
towards, and the README should not be read as saying it has been reached:

- 42 of 215 test modules (20%) still use `unittest.mock`. (Count: files
  named `test_*.py` under `tests/`, via
  `find tests -name "test_*.py" | wc -l` and
  `grep -lE "unittest\.mock|Mock\(|MagicMock\(|@patch" $(find tests -name "test_*.py") | wc -l`.)
  Migrating them is in progress; the claim that this project uses zero
  mocks was not true.
- The main CI workflow runs `tests/unit/` plus a local-only slice of the
  integration tests. The SSH, scheduler and cloud tests need credentials CI
  does not have.
- No trustworthy coverage figure has been measured. Several conflicting numbers
  exist in old artifacts; none of them is reproducible, which is why this README
  no longer carries a coverage badge.

### Running Tests

```bash
# Fast unit tests (what CI runs)
pytest tests/unit/ -q

# Everything except tests needing real cluster access
pytest tests/ -m "not real_world"

# A real job on every reachable backend, nothing mocked
python scripts/collect_execution_evidence.py

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
