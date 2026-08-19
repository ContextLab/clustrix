# Real-World Testing

## What this is

Clustrix's real-world tests exercise the code against actual external
resources — a real SSH daemon, a real scheduler, a real HuggingFace Jobs
namespace, real files on disk — rather than against stand-ins.

## Why

Mocks answer the question "does my code call the API the way I think it
does?" They cannot answer "does the API behave the way I think it does?" A
suite that has only ever run against mocks is evidence about the mocks. So the
project's rule is: a capability is not working until it has been exercised
against the real thing. Mocks are allowed afterwards, as a cost-control
measure in CI, using the same call syntax that the real run verified. A mock
is never a fallback — when the real resource is unavailable the test skips or
fails, it does not quietly substitute a fake and go green.

## Test categories

### Unit tests

Fast, no external dependencies, in `tests/test_*.py` and `tests/unit/`. These
are what CI runs on every push.

### Real-world tests

Under `tests/real_world/`. Every item collected from that directory is given
the `real_world` marker automatically by its `conftest.py`, whether or not the
file applies the decorator. That is deliberate: six files once lacked the
decorator, so `-m "not real_world"` collected 26 tests capable of real SSH and
cloud calls. Location in the directory is now sufficient.

### Hybrid tests

`tests/test_filesystem_hybrid.py` uses real files for the primary assertions
and mocks only the error conditions that are hard to provoke on demand.

## Directory layout

```
tests/
├── real_world/
│   ├── __init__.py                  # test_manager, TempResourceManager, credentials
│   ├── conftest.py                  # markers, options, automatic real_world marking
│   ├── credential_manager.py        # credential lookup
│   ├── cluster_job_validator.py     # job monitoring and validation
│   ├── cluster_validation/          # cluster job test runner and helpers
│   ├── test_filesystem_real.py      # real filesystem tests
│   ├── test_ssh_real.py             # real SSH tests
│   ├── test_visual_verification.py  # widget visual tests
│   └── screenshots/                 # visual verification outputs
├── integration/                     # provisions billable resources; opt-in
├── unit/
└── test_*.py
```

## Test infrastructure

### RealWorldTestManager

Tracks API call count and estimated spend so a run cannot quietly cost money:

```python
from tests.real_world import test_manager

if test_manager.can_make_api_call(estimated_cost=0.01):
    result = api_call()
    test_manager.record_api_call(cost=0.01)
```

`can_make_api_call` returns `False` once the session has made `daily_limit`
calls (100 by default) or once the next call would push `current_cost` past
`cost_limit_usd` ($5.00 by default). Both are set from the command line — see
below.

### TempResourceManager

A context manager that deletes what it created:

```python
from tests.real_world import TempResourceManager

with TempResourceManager() as temp_mgr:
    temp_file = temp_mgr.create_temp_file("content", ".txt")
    temp_dir = temp_mgr.create_temp_dir()
    # everything above is removed on exit
```

### TestCredentials

A thin wrapper over `credential_manager.py`:

```python
from tests.real_world import credentials

ssh_creds = credentials.get_ssh_credentials()
if ssh_creds:
    ...
```

The available lookups are `get_ssh_credentials`, `get_slurm_credentials`,
`get_huggingface_credentials`, `get_gpu_cluster_credentials`,
`get_slurm_cluster_credentials`, plus `get_credential_status` and
`print_credential_status`. There is no cloud-provider lookup, because clustrix
has no cloud backend to hand credentials to.

## Environment variables

See the [Credential Setup Guide](CREDENTIAL_SETUP.md) for the full list. The
short version, for local runs:

```bash
export TEST_SSH_HOST="localhost"
export TEST_SSH_USERNAME="$USER"
export TEST_SSH_PRIVATE_KEY_PATH="$HOME/.ssh/id_rsa"

export TEST_SLURM_HOST="slurm.example.edu"
export TEST_SLURM_USERNAME="user"

export HF_TOKEN="your-hf-token"
export HF_USERNAME="your-hf-username"
```

## Prerequisite: host keys must be in `known_hosts`

**Every real-world test that opens an SSH connection fails unless the target
host's key is already in your `known_hosts`.** This is a hard prerequisite,
not a recommendation.

SSH connections go through
`clustrix.ssh_security.configure_host_key_policy()`, whose default policy is
`"reject"`. An unknown host key raises `HostKeyVerificationError` rather than
being trusted on sight.

Add each host you intend to test against, once, deliberately:

```bash
# Replace cluster.example.edu with the host under test.
ssh-keyscan cluster.example.edu >> ~/.ssh/known_hosts

# Non-standard SSH port:
ssh-keyscan -p 2222 cluster.example.edu >> ~/.ssh/known_hosts

# Local sshd used by the localhost-only SSH tests:
ssh-keyscan localhost 127.0.0.1 >> ~/.ssh/known_hosts
```

Check a host is present before running the suite:

```bash
ssh-keygen -F cluster.example.edu
```

Hosts come from `CLUSTRIX_TEST_SSH_HOST`, `CLUSTRIX_TEST_SSH_HOST_2`,
`CLUSTRIX_TEST_SLURM_HOST` and `CLUSTRIX_TEST_SLURM_HOST_2` (see
`tests/real_world/credential_manager.py`), so scan whichever of those you have
set. In GitHub Actions, the `real-world-tests` workflow does this in its
"Populate known_hosts for host key verification" step.

`ssh-keyscan` trusts the network at the moment you run it. On a host you have
never reached before, compare the fingerprint it prints against one you got
out-of-band from the cluster's administrators before appending it.

Turning verification off is possible, but it is not a fix for a failing test —
it is a decision to stop verifying host keys at all:

```python
config = ClusterConfig(..., ssh_host_key_policy="auto_add")
```

## Running tests

```bash
# What CI runs: everything safe without credentials or money
pytest tests/ -m "not real_world" --ignore=tests/real_world --ignore=tests/integration

# Real-world tests
pytest tests/real_world/ -m real_world
```

The options below are registered by `tests/real_world/conftest.py`, so they
are available when that directory is part of the run:

```bash
# Include tests marked expensive
pytest tests/real_world/ --run-expensive

# Include visual tests, which produce artifacts for a human to look at
pytest tests/real_world/ --run-visual

# Raise or lower the cost ceiling for the session
pytest tests/real_world/ --api-cost-limit=10.0 --api-call-limit=200
```

Markers registered for this suite are `real_world`, `expensive`, `visual` and
`ssh_required`. Selecting specific files works as usual:

```bash
pytest tests/real_world/test_filesystem_real.py
TEST_SSH_HOST=cluster.example.edu pytest tests/real_world/test_ssh_real.py
pytest tests/test_filesystem_hybrid.py
pytest tests/real_world/test_visual_verification.py --run-visual
```

`tests/integration/` is separate and provisions real, billable AWS resources.
It refuses to run unless `CLUSTRIX_ALLOW_BILLABLE=1` is set.

## Cost management

Defaults are 100 API calls and $5.00 per session, both adjustable with the
options above. The suite prefers operations that cost nothing:

1. **HuggingFace Jobs** — CPU flavors only unless a paid GPU flavor is
   explicitly allowed; GPU flavors bill by the second.
2. **AWS STS GetCallerIdentity** — free, and used only to check the
   credentials the `scripts/aws/` cleanup tooling needs. It is not an
   execution backend.
3. **Public APIs** — GitHub, PyPI, HuggingFace, all free at these volumes.

Clustrix has no cloud pricing clients and no cloud VM backend; nothing in the
suite queries a provider's price list.

To see where a session stands:

```python
from tests.real_world import test_manager

print(f"API calls this session: {test_manager.api_calls_today}")
print(f"Current cost: ${test_manager.current_cost:.2f}")
print(f"Cost limit: ${test_manager.cost_limit_usd:.2f}")
```

## What gets tested where

### Filesystem operations

Real tests create actual files and directories, exercise permissions, and
handle large files. The hybrid file uses real files for the main path and
mocks SSH/SFTP only to produce failures that are otherwise hard to arrange.

### SSH operations

Real tests connect to an actual SSH server — localhost by default — and cover
key-based authentication, SFTP transfers, and connection timeouts.

### HuggingFace Jobs

There is no HF Jobs test file under `tests/real_world/`; the round trip lives
in the `hf-jobs-integration` job of `.github/workflows/real-world-tests.yml`,
which submits a `cpu-basic` job to the `contextlab` namespace through the
`@cluster` decorator and asserts on the returned value. It needs a token
rather than a cluster account, which makes it the cheapest end-to-end check
the project has.

### Visual verification

Tests generate widget HTML and write it under
`tests/real_world/screenshots/`, along with `index.html` and
`screenshot_instructions.json`. A person opens those files and looks at them;
there is no automated image comparison.

## Writing a real-world test

Group tests into a class, mark what needs marking, and clean up after
yourself:

```python
class TestComponentReal:
    """Real-world tests for component functionality."""

    def test_basic_functionality_real(self):
        with TempResourceManager() as temp_mgr:
            temp_file = temp_mgr.create_temp_file("content")
            ...

    @pytest.mark.expensive
    def test_expensive_operation_real(self):
        ...

    @pytest.mark.visual
    def test_visual_verification(self):
        ...
```

Skip when a resource is genuinely unavailable, and say which one:

```python
def test_against_remote():
    creds = credentials.get_ssh_credentials()
    if not creds:
        pytest.skip("No SSH credentials available")
    ...
```

Do not wrap the whole body in `try/except Exception: pytest.skip(...)`. That
turns a real failure into a pass, which is exactly what this suite exists to
prevent. Skip on a missing prerequisite you checked for, not on any exception
that happens to come out.

A starting point for a new file:

```python
"""
Real-world tests for [component].

These tests use actual [external resource] to verify functionality.
"""

import pytest
from tests.real_world import test_manager, TempResourceManager


class TestComponentReal:
    def test_basic_functionality_real(self):
        if not test_manager.can_make_api_call(0.01):
            pytest.skip("API limit reached")

        with TempResourceManager() as temp_mgr:
            ...

        test_manager.record_api_call(0.01)
```

## Continuous integration

`.github/workflows/real-world-tests.yml` runs this suite. It has no `push:` or
`pull_request:` trigger, on purpose: these jobs use real credentials and some
provision billable resources, so a pull request from a fork must never be able
to start them. It runs on a weekly schedule against the default branch, or
manually through `workflow_dispatch`.

Each job is gated on whether the secrets it needs are present, resolved in a
separate `check-secrets` job — the `secrets` context is not available in `if:`
conditions, so the presence check has to become a job output first.

## Troubleshooting

**Credential errors.** Run `python scripts/run_real_world_tests.py
--check-creds` to see what the suite can find. A credential that works
interactively but not under pytest is usually in a shell profile the test
process never sourced.

**SSH connection failures.** Check the daemon is up, that key permissions are
`600`, and that the host key is in `known_hosts` — see the prerequisite
section above. `ssh -vvv` will tell you which of the three it is.

**Cost or call limits reached.** Raise them with `--api-cost-limit` and
`--api-call-limit`, or work out why a test is making more calls than it needs.

**File permission errors.** Check the temp directory is writable and that a
previous run's `TempResourceManager` cleanup actually ran.

For more output:

```bash
pytest -v -s
pytest --log-cli-level=DEBUG
pytest tests/real_world/test_filesystem_real.py::TestRealFilesystemOperations::test_create_and_read_file_real -v -s
```
