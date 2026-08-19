# Credential Setup for Real-World Testing

This guide explains how to supply credentials to the Clustrix real-world test
suite, both for local development (environment variables) and for GitHub
Actions (repository secrets).

Clustrix has four execution backends: `local`, `ssh`, `slurm` and
`huggingface` (HuggingFace **Jobs**). Only SSH/SLURM and HuggingFace need
credentials at all. `clustrix.credential_manager` reads the `SSH_*` and `HF_*`
variables and nothing else.

Clustrix does not support the PBS, SGE, Kubernetes, AWS, GCP, Azure or Lambda
Cloud backends; each is tracked in its own issue
([#140-#146](https://github.com/ContextLab/clustrix/issues/140)). Cloud
credentials are still described at the end of this page for one reason only:
the standalone `scripts/aws/` cleanup utilities read AWS credentials directly
through boto3.

## Local development

### 1. Environment variables

The test suite's credential manager (`tests/real_world/credential_manager.py`)
reads `TEST_*` variables. Put them in a `.env` file in the project root, which
must be listed in `.gitignore` (the repository ignores `.env.local` and
`.env.validation`, so add a plain `.env` yourself if you use that name):

```bash
# .env file for local development - DO NOT COMMIT TO GIT

# SSH cluster
TEST_SSH_HOST=ssh.example.edu
TEST_SSH_USERNAME=user
TEST_SSH_PASSWORD=your-password
TEST_SSH_PRIVATE_KEY_PATH=/path/to/private-key
TEST_SSH_PORT=22

# SLURM cluster
TEST_SLURM_HOST=slurm.example.edu
TEST_SLURM_USERNAME=user
TEST_SLURM_PASSWORD=your-password

# HuggingFace Jobs
HF_TOKEN=your-hf-token
HF_USERNAME=your-hf-username
```

`HUGGINGFACE_TOKEN` and `HUGGINGFACE_USERNAME` are accepted as alternative
spellings of the last two.

Clustrix's own `credential_manager` reads a different, unprefixed set —
`SSH_HOST`, `SSH_USERNAME`, `SSH_PASSWORD`, `SSH_PRIVATE_KEY_PATH`, `SSH_PORT`,
`HF_TOKEN` and `HF_USERNAME` — from `~/.clustrix/.env` or from the process
environment. The two sets are separate: the `TEST_*` names configure the test
suite, the unprefixed names configure the library.

### 2. Exporting directly

If you would rather not keep a `.env` file:

```bash
export TEST_SSH_HOST="ssh.example.edu"
export TEST_SSH_USERNAME="user"
export TEST_SSH_PASSWORD="your-password"
export TEST_SSH_PRIVATE_KEY_PATH="/path/to/private-key"

export TEST_SLURM_HOST="slurm.example.edu"
export TEST_SLURM_USERNAME="user"
export TEST_SLURM_PASSWORD="your-password"

export HF_TOKEN="your-token"
export HF_USERNAME="your-hf-username"
```

### 3. Check what is visible

```bash
python scripts/run_real_world_tests.py --check-creds
```

This prints one line per service (SSH, SLURM, HuggingFace) saying whether
usable credentials were found, plus whether a 1Password CLI session is
available. When 1Password is reachable, the credential manager prefers it and
falls back to the environment variables only if a lookup fails.

## GitHub Actions

### 1. Repository secrets

Add these under `Settings → Secrets and variables → Actions`:

- `CLUSTRIX_USERNAME` — username for the SSH and SLURM hosts
- `CLUSTRIX_PASSWORD` — password for the SSH and SLURM hosts
- `HF_TOKEN` — HuggingFace token with `job.write` in the target namespace
- `HF_USERNAME` — HuggingFace username

Those four are the only secrets `.github/workflows/real-world-tests.yml`
references. Adding cloud-provider secrets has no effect, because no workflow
reads them.

### 2. What the workflow does

`.github/workflows/real-world-tests.yml` creates a local SSH server to test
against, injects the secrets above as environment variables, runs the
real-world suites, and uploads the resulting artifacts. Jobs are individually
gated on whether the secrets they need are present, so a fork without secrets
skips rather than fails.

### 3. Reproducing the workflow environment locally

```bash
export GITHUB_ACTIONS=true
export CLUSTRIX_USERNAME="user"
export CLUSTRIX_PASSWORD="your-password"
export HF_USERNAME="your-hf-username"
export HF_TOKEN="your-hf-token"

python scripts/run_real_world_tests.py --check-creds
```

With `GITHUB_ACTIONS=true` set, the credential manager reads `CLUSTRIX_*` for
the SSH and SLURM hosts in place of `TEST_SSH_*` and `TEST_SLURM_*`. The
HuggingFace names are the same either way.

## Running tests

### Locally

```bash
# Check credentials
python scripts/run_real_world_tests.py --check-creds

# Run one category at a time
python scripts/run_real_world_tests.py --filesystem
python scripts/run_real_world_tests.py --ssh
python scripts/run_real_world_tests.py --api
python scripts/run_real_world_tests.py --visual

# Everything
python scripts/run_real_world_tests.py --all

# Include the tests marked expensive
python scripts/run_real_world_tests.py --all --expensive
```

### In GitHub Actions

Real-world tests do **not** run on push or pull request. The `Real-World
Tests` workflow deliberately has no `push:` or `pull_request:` trigger,
because these jobs use real credentials and some of them provision billable
resources — a pull request from a fork must never be able to start them. It
runs on a weekly `schedule` (default branch only) or when you start it by
hand:

1. Open the `Actions` tab
2. Select `Real-World Tests`
3. Click `Run workflow`
4. Tick `Run expensive tests` only if you want the jobs that provision
   billable resources
5. Click `Run workflow` again to confirm

## Security practices

- Keep `.env` files out of version control. `.gitignore` lists `.env.local`
  and `.env.validation`; add whatever name you actually use.
- Use scoped, short-lived credentials for testing rather than your everyday
  ones.
- Rotate anything that has been exported into a shell history or a CI log.
- Give test accounts the least privilege that lets the tests pass. A
  HuggingFace token needs `job.write` in one namespace, not organization-wide
  write.
- In GitHub Actions, use repository secrets rather than plain `env:` values in
  the workflow file, and keep secret access scoped to the workflows that need
  it.

A reasonable rotation cadence is 90 days for SSH keys, 30 days for API tokens.
To rotate: create the new credential, update your `.env` and the repository
secrets, run `--check-creds`, then revoke the old one.

## Troubleshooting

### Variables are not being picked up

```bash
# Is it set in this shell?
echo "$TEST_SSH_HOST"

# Is the .env file being loaded?
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print(os.environ.get('TEST_SSH_HOST', 'Not set'))"
```

A `.env` file is read from the current working directory, so run the tests
from the project root.

### SSH permission failures

```bash
ls -la ~/.ssh/
chmod 600 ~/.ssh/id_rsa

ssh -vvv user@ssh.example.edu
```

Clustrix verifies host keys by default. If a host is genuinely new, add it to
`~/.ssh/known_hosts` rather than turning verification off.

### GitHub Actions failures

Check the run's logs under the `Actions` tab. A job that skipped rather than
failed usually means the secret it gates on is missing from the repository.

## AWS credentials for the cleanup scripts

`scripts/aws/cleanup_resources.py` and `scripts/aws/destroy_cluster.py` tear
down leftover AWS resources. They talk to boto3 directly and are unrelated to
any clustrix backend. They read `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` from the environment or from `~/.clustrix/.env`.
