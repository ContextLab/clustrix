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

## Directory layout

```
tests/
├── real_world/
│   ├── __init__.py                  # TempResourceManager
│   ├── conftest.py                  # automatic real_world marking, private-cluster skips
│   ├── credential_manager.py        # credential lookup
│   ├── test_local_sshd_slurm.py     # @cluster against a real sshd and SLURM
│   ├── test_*_real.py               # the same cluster: staging, GPUs, signals, paths, ...
│   └── test_filesystem_real.py      # the filesystem helpers on this machine
├── unit/
└── test_*.py
```

## Test infrastructure

### TempResourceManager

A context manager that deletes what it created:

```python
from tests.real_world import TempResourceManager

with TempResourceManager() as temp_mgr:
    temp_file = temp_mgr.create_temp_file("content", ".txt")
    temp_dir = temp_mgr.create_temp_dir()
    # everything above is removed on exit
```

### Credentials

`tests/real_world/credential_manager.py` resolves credentials from
`~/.clustrix/.env` and the environment:

```python
from tests.real_world.credential_manager import get_credential_manager

manager = get_credential_manager()
ssh_creds = manager.get_ssh_credentials()
```

The lookups are `get_ssh_credentials`, `get_slurm_credentials` and
`get_huggingface_credentials`, plus `get_credential_status` and
`print_credential_status`. There is no cloud-provider lookup, because clustrix
has no cloud backend to hand credentials to. The cluster tests do not use it:
they read the four variables below.

## Environment variables

The tests that run jobs read four variables naming one host that has an sshd
and, for the SLURM half, `sbatch`. `scripts/setup_local_slurm.sh` stands such
a host up on the machine you are sitting at:

```bash
sudo scripts/setup_local_slurm.sh
export CLUSTRIX_TEST_SSH_HOST=127.0.0.1 CLUSTRIX_TEST_SSH_PORT=2222 \
       CLUSTRIX_TEST_SSH_USER=clx \
       CLUSTRIX_TEST_SSH_KEY=/tmp/clustrix-local-cluster/id_ed25519
```

With none of them set those tests skip and say which variables to set. The
[Credential Setup Guide](CREDENTIAL_SETUP.md) covers the credential file and
`HF_TOKEN`.

## Host keys

`tests/conftest.py` gives every test its own empty `$HOME`, so no host is ever
in `known_hosts` when a test starts, and clustrix's default host-key policy is
`"reject"`. A test that connects therefore has to say what it trusts: the
cluster tests configure `ssh_host_key_policy="auto_add"` for the host named by
the variables above, which is a machine the developer chose. A test that
connects with the default policy can only fail with
`HostKeyVerificationError`; the tests that did were removed for that reason.

Outside the test suite the rule is the opposite: add a host's key to
`known_hosts` deliberately (`ssh-keyscan host >> ~/.ssh/known_hosts`, after
checking the fingerprint out-of-band) rather than turning verification off.

## Running tests

```bash
# What CI runs: everything safe without credentials or money
pytest tests/ -m "not real_world" --ignore=tests/real_world

# Real-world tests
pytest tests/real_world/ -m real_world
```

Selecting specific files works as usual:

```bash
pytest tests/real_world/test_filesystem_real.py
pytest tests/real_world/test_local_sshd_slurm.py -m real_world
```

## Cost

Nothing under `tests/real_world/` costs money: the cluster is one you run
yourself. The one paid operation in the project's testing is the HuggingFace
Jobs round trip in the workflow, pinned to the `cpu-basic` flavor.

## What gets tested where

### Filesystem operations

`test_filesystem_real.py` runs `ClusterFilesystem` and the `cluster_*` helpers
over real files and directories on this machine.

### SSH and SLURM

`test_local_sshd_slurm.py` drives `@cluster` at a real sshd and a real SLURM
controller: key authentication, SFTP, virtualenv builds, `sbatch`, `squeue`,
`sacct`, `scancel`. The other `test_*_real.py` files use the same host for
input staging, GPU requests, signals, job names and `~` in paths.

### HuggingFace Jobs

There is no HF Jobs test file under `tests/real_world/`; the round trip lives
in the `hf-jobs-integration` job of `.github/workflows/real-world-tests.yml`,
which submits a `cpu-basic` job to the `contextlab` namespace through the
`@cluster` decorator and asserts on the returned value. It needs a token
rather than a cluster account, which makes it the cheapest end-to-end check
the project has.

## Writing a real-world test

Group tests into a class and clean up after yourself:

```python
class TestComponentReal:
    """Real-world tests for component functionality."""

    def test_basic_functionality_real(self):
        with TempResourceManager() as temp_mgr:
            temp_file = temp_mgr.create_temp_file("content")
            ...
```

Skip when a resource is genuinely unavailable, and say which one:

```python
HOST = os.environ.get("CLUSTRIX_TEST_SSH_HOST")

pytestmark = pytest.mark.skipif(
    not HOST, reason="No test cluster named: set CLUSTRIX_TEST_SSH_HOST"
)
```

Do not wrap the whole body in `try/except Exception: pytest.skip(...)`. That
turns a real failure into a pass, which is exactly what this suite exists to
prevent. Skip on a missing prerequisite you checked for, not on any exception
that happens to come out.

## Continuous integration

`.github/workflows/real-world-tests.yml` runs this suite. It has no `push:` or
`pull_request:` trigger, on purpose: its HuggingFace job uses a real token, so
a pull request from a fork must never be able to start it.

- `local-cluster` (manual only) runs `scripts/setup_local_slurm.sh` in an
  Ubuntu 24.04 container and then `pytest tests/real_world/ -m real_world`
  with the four `CLUSTRIX_TEST_SSH_*` variables. It needs no secret. **It has
  not yet been run on GitHub**: the script and the suite were run in such a
  container elsewhere, and that the hosted runner behaves the same is
  inferred.
- `hf-jobs-integration` (manual, and weekly on the default branch) is gated
  on `HF_TOKEN` being present, resolved in a separate `check-secrets` job —
  the `secrets` context is not available in `if:` conditions, so the presence
  check has to become a job output first.

## Troubleshooting

**Credential errors.** Run `python scripts/run_real_world_tests.py
--check-creds` to see what the suite can find. A credential that works
interactively but not under pytest is usually in a shell profile the test
process never sourced.

**SSH connection failures.** Check the daemon is up and that key permissions
are `600`; `ssh -vvv` will tell you which. A `HostKeyVerificationError` inside
a test means the test connected without naming a host-key policy — see "Host
keys" above.

**File permission errors.** Check the temp directory is writable and that a
previous run's `TempResourceManager` cleanup actually ran.

For more output:

```bash
pytest -v -s
pytest --log-cli-level=DEBUG
pytest tests/real_world/test_filesystem_real.py::TestRealFilesystemOperations::test_create_and_read_file_real -v -s
```
