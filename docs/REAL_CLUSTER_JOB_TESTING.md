# Real Cluster Job Testing Guide

This guide covers the real cluster job tests. They submit jobs to live cluster
systems through the `@cluster` decorator and check the whole path end to end:
submission, scheduling, execution, result retrieval.

## What the suite covers

- Real job submission against SLURM and against a plain SSH host
- End-to-end validation through the `@cluster` decorator
- Job status and resource-usage monitoring
- Checks that results match what the function should have returned
- A JSON report of every run

Clustrix does not support the PBS, SGE or Kubernetes backends, so there are no
job-submission tests for them. Their return is tracked in issues
[#140](https://github.com/ContextLab/clustrix/issues/140),
[#141](https://github.com/ContextLab/clustrix/issues/141) and
[#142](https://github.com/ContextLab/clustrix/issues/142).

## Test structure

### Cluster-specific test files

- `tests/real_world/test_slurm_job_submission_real.py` — SLURM job submission
- `tests/real_world/test_ssh_job_execution_real.py` — SSH-based job execution

### Supporting infrastructure

- `tests/real_world/cluster_job_validator.py` — job monitoring and validation
- `tests/real_world/cluster_validation/run_cluster_job_tests.py` — the test
  runner. Invoke it as
  `python -m tests.real_world.cluster_validation.run_cluster_job_tests`; its
  `sys.path` setup only resolves correctly when it runs as a module from the
  repository root.
- `tests/real_world/credential_manager.py` — credential lookup

## Test categories

### Basic tests (`@pytest.mark.real_world`)

Simple function execution, environment variable access, file I/O, error
handling, resource allocation, job monitoring.

### Expensive tests (`@pytest.mark.expensive`)

Memory-intensive computation, long-running jobs, parallel processing, large
data. These take real cluster time, so they are opt-in.

## Running the tests

### Prerequisites

1. Install dependencies:
   ```bash
   pip install -e ".[test]"
   ```

2. Set up credentials — see the [Credential Setup Guide](CREDENTIAL_SETUP.md).

3. Confirm the clusters answer:
   ```bash
   python -m tests.real_world.cluster_validation.run_cluster_job_tests --check-only
   ```

### Through the runner

```bash
# Basic tests on every reachable cluster
python -m tests.real_world.cluster_validation.run_cluster_job_tests --cluster all --tests basic

# Everything, expensive tests included
python -m tests.real_world.cluster_validation.run_cluster_job_tests --cluster all --tests all

# A longer per-test timeout (default is 300 seconds)
python -m tests.real_world.cluster_validation.run_cluster_job_tests --cluster all --tests basic --timeout 600

# One cluster type at a time
python -m tests.real_world.cluster_validation.run_cluster_job_tests --cluster slurm
python -m tests.real_world.cluster_validation.run_cluster_job_tests --cluster ssh
```

`--cluster` accepts `slurm`, `ssh` or `all`; `--tests` accepts `basic`,
`expensive` or `all`. `--validate` turns on the extra job validation described
below, and `--output` names the JSON report file.

### Through pytest

```bash
# Every SLURM test
pytest tests/real_world/test_slurm_job_submission_real.py -v -m "real_world"

# Basic tests only
pytest tests/real_world/test_slurm_job_submission_real.py -v -m "real_world and not expensive"

# Expensive tests only
pytest tests/real_world/test_slurm_job_submission_real.py -v -m "expensive"
```

## Test examples

These are drawn from `tests/real_world/test_slurm_job_submission_real.py`. The
backend comes from the `slurm_config` fixture, which calls
`configure(cluster_type="slurm", ...)`. `cluster_type` is not a `@cluster`
keyword, and passing it there is ignored with a warning.

### Simple function

```python
@pytest.mark.real_world
def test_simple_function_slurm_submission(self, slurm_config):
    """Submit a simple function to SLURM."""

    @cluster(cores=1, memory="1GB", time="00:05:00")
    def add_numbers(x: int, y: int) -> int:
        return x + y

    result = add_numbers(10, 32)

    assert result == 42
    assert isinstance(result, int)
```

### Environment access

```python
@pytest.mark.real_world
def test_function_with_environment_info_slurm(self, slurm_config):
    """Read the SLURM environment from inside the job."""

    @cluster(cores=1, memory="1GB", time="00:05:00")
    def get_job_environment() -> Dict[str, str]:
        import os

        return {
            "SLURM_JOB_ID": os.getenv("SLURM_JOB_ID", "not_set"),
            "SLURM_JOB_NAME": os.getenv("SLURM_JOB_NAME", "not_set"),
            "SLURM_CPUS_PER_TASK": os.getenv("SLURM_CPUS_PER_TASK", "not_set"),
            "HOSTNAME": os.getenv("HOSTNAME", "not_set"),
            "USER": os.getenv("USER", "not_set"),
        }

    result = get_job_environment()

    assert isinstance(result, dict)
    assert result["SLURM_JOB_ID"] != "not_set"
    assert result["USER"] != "not_set"
```

### Loop parallelization

```python
@pytest.mark.real_world
def test_parallel_loop_slurm(self, slurm_config):
    """Run a loop-carrying function with parallel=True."""

    @cluster(cores=4, memory="4GB", time="00:10:00", parallel=True)
    def compute_squares(numbers: List[int]) -> List[int]:
        import time

        results = []
        for num in numbers:
            time.sleep(0.1)
            results.append(num * num)

        return results

    test_numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    result = compute_squares(test_numbers)

    assert isinstance(result, list)
    assert result == [num * num for num in test_numbers]
```

The assertion here is on the answer, not on the loop having been split up.
AST-based loop parallelization only fires for a loop over a literal `range()`
whose body calls something that accepts the chunk keywords, so this particular
loop runs sequentially inside one job. The test still earns its place: it shows
`parallel=True` does not change the result.

## The validation framework

`ClusterJobValidator` wraps job monitoring:

```python
from tests.real_world.cluster_job_validator import create_validator

validator = create_validator(
    "slurm",
    cluster_host="cluster.example.edu",
    username="user",
)

# Was the job accepted by the scheduler, and with the resources asked for?
result = validator.validate_job_submission(job_id, "my_function", "args", {})

# Poll until the job leaves the queue, or until the timeout
execution_result = validator.monitor_job_execution(job_id, timeout_seconds=300)

# Compare the job's output against what was expected
output_result = validator.validate_job_output(job_id, expected_output=42)
```

`create_validator` passes its keyword arguments straight into `ClusterConfig`,
so anything that class accepts works here.

What it checks: that the job was submitted, that allocated resources match
requested ones, how the job status changes over time, whether the output
matches expectations, what went wrong when it did not, and per-job metrics.

## Cluster-specific coverage

### SLURM

SLURM environment variables (`SLURM_JOB_ID`, `SLURM_CPUS_PER_TASK` and so on),
resource allocation for cores, memory and time limits, partition selection,
loop parallelization, and SLURM accounting metrics.

### SSH

Remote environment access, system command execution, file operations, network
reachability from the remote host, resource monitoring, and inspection of the
remote Python environment.

## Reports

```bash
python -m tests.real_world.cluster_validation.run_cluster_job_tests --output my_test_results.json
```

Each report carries session information (ID, timestamp, duration), pass/fail/skip
counts, which clusters were reachable, per-test detail, error messages and
tracebacks, performance metrics, and a description of the environment the run
happened in.

The shape is:

```json
{
  "session_info": {
    "session_id": "abc12345",
    "start_time": 1625097600.0,
    "duration": 450.2,
    "environment": {
      "python_version": "3.9.7",
      "platform": "linux"
    }
  },
  "results": {
    "total_tests": 45,
    "total_passed": 42,
    "total_failed": 2,
    "total_skipped": 1,
    "success_rate": 93.3,
    "cluster_results": {
      "slurm": {
        "tests_passed": 12,
        "tests_failed": 0,
        "tests_skipped": 0,
        "duration": 180.5
      }
    }
  },
  "credential_status": {
    "slurm": true,
    "ssh": true
  }
}
```

## Troubleshooting

### Jobs will not submit

```bash
# Can the runner see the clusters at all?
python -m tests.real_world.cluster_validation.run_cluster_job_tests --check-only

# Are the credentials where the suite expects them?
python scripts/run_real_world_tests.py --check-creds

# Is the queue accepting work?
squeue -u "$USER"
```

### Tests time out

Raise the per-test budget, or cut the suite down:

```bash
python -m tests.real_world.cluster_validation.run_cluster_job_tests --timeout 600
python -m tests.real_world.cluster_validation.run_cluster_job_tests --tests basic
```

A busy cluster is the usual cause. `sinfo` and `squeue` will say so.

### Permission errors

```bash
ls -la ~/.ssh/
chmod 600 ~/.ssh/id_rsa

ssh -vvv user@cluster.example.edu

# Does the account exist on the SLURM side?
sacctmgr show user "$USER"
```

### Getting more output

```bash
pytest tests/real_world/test_slurm_job_submission_real.py -v -s
```

Clustrix logs through the standard `logging` module, so raising the level on
the `clustrix` logger shows the submission and polling steps.

## Practices worth keeping

Start from the simplest test that could fail, and add the complicated ones
after that one passes. Name tests for what they establish. Cover the failure
paths as well as the success ones, and assert on allocated resources rather
than assuming the scheduler honoured the request.

Ask for the resources the test needs and no more; other people are queueing
behind you. Set timeouts that are generous enough not to be flaky but short
enough to fail fast. Delete the files a test writes on the remote host.

Keep credentials in environment variables or repository secrets, scoped to the
least privilege that lets the tests pass, and rotate them on a schedule.

Group related tests into classes, mark them with `@pytest.mark.real_world` and
`@pytest.mark.expensive` as appropriate, and skip rather than fail when a
cluster is unreachable.

## Continuous integration

`.github/workflows/real-world-tests.yml` runs these jobs. It has no `push:` or
`pull_request:` trigger on purpose: the jobs use real credentials, so a pull
request from a fork must not be able to start them. Use `workflow_dispatch`,
and gate on secret presence:

```yaml
name: Real Cluster Job Tests

on:
  workflow_dispatch:
    inputs:
      cluster_type:
        description: 'Cluster type to test'
        required: true
        default: 'all'
        type: choice
        options:
        - all
        - slurm
        - ssh

jobs:
  cluster-tests:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'

    - name: Install dependencies
      run: |
        pip install -e ".[test]"

    - name: Run cluster job tests
      env:
        CLUSTRIX_USERNAME: ${{ secrets.CLUSTRIX_USERNAME }}
        CLUSTRIX_PASSWORD: ${{ secrets.CLUSTRIX_PASSWORD }}
        HF_TOKEN: ${{ secrets.HF_TOKEN }}
      run: |
        python -m tests.real_world.cluster_validation.run_cluster_job_tests --cluster ${{ inputs.cluster_type }}

    - name: Upload test results
      uses: actions/upload-artifact@v4
      if: always()
      with:
        name: cluster-test-results
        path: test_results/
```

## Extending the suite

To add tests for a cluster type, create
`test_<cluster_type>_job_submission_real.py`, cover the environment and
resource behaviour that is specific to it, teach `credential_manager.py` how to
find its credentials, add the name to the runner's `--cluster` choices, and say
so here. A backend clustrix does not implement cannot be tested this way; the
backend has to land first.

For a new test case within an existing file: decide what behaviour you are
pinning down, write the function with the right decorators, assert on the
result rather than on the absence of an exception, and cover the failure path
too.

## Getting help

Read the troubleshooting section, confirm the clusters are actually up, run the
credential check, read the run's JSON report, and include that report when you
open an issue.
