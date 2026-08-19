# AWS Resource Management Scripts

Utilities for cleaning up AWS resources left behind by Clustrix's EKS
provisioner (`clustrix.kubernetes.aws_provisioner.AWSEKSFromScratchProvisioner`),
used during development and real-world testing of AWS/EKS functionality.

Restored from `cleanup_test_resources.py` and `destroy_cluster.py`, which
were deleted from the repository root by commit `b9c836f` ("Issue #72:
Delete obsolete development scripts") on the mistaken claim that they had
been migrated to `scripts/aws/` -- that directory never existed until now.
See GitHub issue #95.

## When to use these

* **`cleanup_resources.py`** -- after a test run leaves NAT gateways, a VPC,
  or related networking resources behind (e.g. a crashed test, an
  interrupted `destroy_cluster.py` run, or leftover resources from
  `AWSEKSFromScratchProvisioner` that weren't cleaned up automatically).
* **`destroy_cluster.py`** -- to tear down one specific EKS cluster by name,
  including its node groups, VPC, and IAM roles.

Both are safe to run speculatively: they default to a **dry run** and only
report what they would delete.

## Safety model

* **Dry run by default.** Neither script deletes or releases anything
  unless you pass `--execute`.
* **Print before delete.** Every resource under consideration is printed
  with its AWS region and resource id, in both dry-run and `--execute`
  mode, before any delete call is made.
* **Positive identification only.** Both scripts only act on resources
  tagged (or, for IAM roles, named) exactly as
  `clustrix.kubernetes.aws_provisioner` creates them:
  * VPCs/EKS clusters: tag `clustrix:managed=true` (`destroy_cluster.py`
    additionally requires `clustrix:cluster=<cluster_name>`).
  * IAM roles: exact names `clustrix-eks-cluster-role-<cluster_name>` and
    `clustrix-eks-node-role-<cluster_name>`.

  Anything without these tags/names -- including the account's default VPC
  and resources created by hand or by another tool -- is left untouched, no
  matter how it's named. Run `--help` on either script for the full
  explanation.
* **Fail loudly on missing credentials.** Both scripts load AWS credentials
  through `clustrix.credential_manager.FlexibleCredentialManager`
  (environment variables or `~/.clustrix/.env`). If no credentials are
  found, the script exits immediately with an error instead of silently
  falling back to boto3's default credential chain.

## Usage

```bash
# See exactly what would be deleted, without deleting anything
python scripts/aws/cleanup_resources.py --region us-east-1
python scripts/aws/destroy_cluster.py my-test-cluster --region us-east-1

# Actually delete
python scripts/aws/cleanup_resources.py --region us-east-1 --execute
python scripts/aws/destroy_cluster.py my-test-cluster --region us-east-1 --execute
```

Run `python scripts/aws/cleanup_resources.py --help` or
`python scripts/aws/destroy_cluster.py --help` for full flag documentation.

## Verifying without touching a real AWS account

These scripts are deliberately never exercised against a live AWS account in
CI or in this repo's test suite -- that would cost money and risks deleting
real resources. `tests/unit/test_aws_cleanup_scripts.py` instead verifies,
without mocking boto3 and without any network access:

* `--help` output for both scripts.
* That running either script with AWS credentials absent exits non-zero
  with a clear error message, before any AWS API call is attempted.
* Real argparse round trips confirming `--execute` defaults to `False`.
* Static analysis (via the `ast` module, reading the actual script source)
  proving that every destructive boto3 call is only reachable through the
  function invoked inside `if args.execute:`, and nowhere else in the file.

Before relying on these scripts against a real account, manually validate
them against a disposable test cluster/VPC and confirm the resources they
report match what actually gets deleted.
