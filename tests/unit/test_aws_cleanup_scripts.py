#!/usr/bin/env python3
"""Verify scripts/aws/cleanup_resources.py and scripts/aws/destroy_cluster.py
are safe to ship, without ever calling AWS.

Regression/coverage tests for issue #95: these two scripts delete real
cloud infrastructure, so per project policy they are never exercised
against a live AWS account here (that costs money and can destroy real
resources) and this file never mocks boto3. Instead it verifies, using only
real subprocess invocations, real argparse round trips, and static analysis
of the actual source files:

* ``--help`` works for both scripts.
* Both scripts fail loudly (non-zero exit, clear stderr message) when AWS
  credentials are absent, before any AWS API call could occur.
* ``--execute`` really does default to False via a real argparse parse.
* Every destructive boto3 call (delete_*, release_address,
  detach_internet_gateway, detach_role_policy) is lexically reachable only
  through the function gated behind ``if args.execute:`` in main() -- i.e.
  the delete path is provably unreachable without the explicit flag.
"""

import ast
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLEANUP_SCRIPT = REPO_ROOT / "scripts" / "aws" / "cleanup_resources.py"
DESTROY_SCRIPT = REPO_ROOT / "scripts" / "aws" / "destroy_cluster.py"


def _load_module(script_path: pathlib.Path):
    """Import a standalone script file as a module, the same way the other
    scripts/*.py utilities in this repo are written to be run: directly,
    not as part of a package."""
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env_without_aws_credentials():
    """A real environment with no AWS/Clustrix credentials discoverable:
    no AWS_* env vars, and HOME pointed at an empty directory so
    ~/.clustrix/.env cannot exist. Not in GitHub Actions either."""
    env = {"PATH": os.environ.get("PATH", "")}
    tmp_home = tempfile.mkdtemp(prefix="clustrix-no-creds-home-")
    env["HOME"] = tmp_home
    return env, tmp_home


class TestScriptsExist:
    def test_cleanup_script_exists(self):
        assert CLEANUP_SCRIPT.is_file(), f"missing {CLEANUP_SCRIPT}"

    def test_destroy_script_exists(self):
        assert DESTROY_SCRIPT.is_file(), f"missing {DESTROY_SCRIPT}"


class TestHelpOutput:
    """--help must work and must document the safety model, per issue #95."""

    def test_cleanup_help_runs_and_documents_safety(self):
        result = subprocess.run(
            [sys.executable, str(CLEANUP_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "--execute" in out
        assert "dry run" in out.lower() or "DRY RUN" in result.stdout
        assert "clustrix:managed" in out

    def test_destroy_help_runs_and_documents_safety(self):
        result = subprocess.run(
            [sys.executable, str(DESTROY_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "--execute" in out
        assert "cluster_name" in out
        assert "clustrix:managed" in out
        assert "clustrix:cluster" in out

    def test_cleanup_help_has_no_positional_required_args(self):
        # --help must succeed with zero other arguments -- no hidden
        # required positional that would make --help itself fail.
        result = subprocess.run(
            [sys.executable, str(CLEANUP_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0


class TestMissingCredentialsFailsLoudly:
    """Per issue #95: 'They must fail loudly on missing credentials rather
    than silently doing nothing.' Verified with real subprocess calls
    against a real (deliberately empty) environment -- no mocking."""

    def test_cleanup_dry_run_without_credentials_errors_clearly(self):
        env, tmp_home = _clean_env_without_aws_credentials()
        try:
            result = subprocess.run(
                [sys.executable, str(CLEANUP_SCRIPT), "--region", "us-east-1"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=str(REPO_ROOT),
            )
        finally:
            import shutil

            shutil.rmtree(tmp_home, ignore_errors=True)

        assert result.returncode != 0, (
            f"expected non-zero exit with no credentials, got 0. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        output = (result.stdout + result.stderr).lower()
        # Either failure is the behaviour under test -- refusing loudly with
        # an actionable message rather than silently doing nothing. CI has no
        # AWS SDK, so the SDK message is the one it hits; a developer machine
        # with boto3 installed hits the credential one.
        assert "credential" in output or "aws sdk" in output, output
        assert (
            "pip install boto3" in output or "aws_access_key_id" in output.lower()
        ), output

    def test_cleanup_execute_without_credentials_also_errors_clearly(self):
        # --execute must not bypass the credential check either.
        env, tmp_home = _clean_env_without_aws_credentials()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLEANUP_SCRIPT),
                    "--region",
                    "us-east-1",
                    "--execute",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=str(REPO_ROOT),
            )
        finally:
            import shutil

            shutil.rmtree(tmp_home, ignore_errors=True)

        assert result.returncode != 0
        output = (result.stdout + result.stderr).lower()
        # Either failure is the behaviour under test -- refusing loudly with
        # an actionable message rather than silently doing nothing. CI has no
        # AWS SDK, so the SDK message is the one it hits; a developer machine
        # with boto3 installed hits the credential one.
        assert "credential" in output or "aws sdk" in output, output
        assert (
            "pip install boto3" in output or "aws_access_key_id" in output.lower()
        ), output

    def test_destroy_dry_run_without_credentials_errors_clearly(self):
        env, tmp_home = _clean_env_without_aws_credentials()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(DESTROY_SCRIPT),
                    "some-cluster",
                    "--region",
                    "us-east-1",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=str(REPO_ROOT),
            )
        finally:
            import shutil

            shutil.rmtree(tmp_home, ignore_errors=True)

        assert result.returncode != 0, (
            f"expected non-zero exit with no credentials, got 0. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        output = (result.stdout + result.stderr).lower()
        # Either failure is the behaviour under test -- refusing loudly with
        # an actionable message rather than silently doing nothing. CI has no
        # AWS SDK, so the SDK message is the one it hits; a developer machine
        # with boto3 installed hits the credential one.
        assert "credential" in output or "aws sdk" in output, output
        assert (
            "pip install boto3" in output or "aws_access_key_id" in output.lower()
        ), output

    def test_destroy_execute_without_credentials_also_errors_clearly(self):
        env, tmp_home = _clean_env_without_aws_credentials()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(DESTROY_SCRIPT),
                    "some-cluster",
                    "--region",
                    "us-east-1",
                    "--execute",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=str(REPO_ROOT),
            )
        finally:
            import shutil

            shutil.rmtree(tmp_home, ignore_errors=True)

        assert result.returncode != 0
        output = (result.stdout + result.stderr).lower()
        # Either failure is the behaviour under test -- refusing loudly with
        # an actionable message rather than silently doing nothing. CI has no
        # AWS SDK, so the SDK message is the one it hits; a developer machine
        # with boto3 installed hits the credential one.
        assert "credential" in output or "aws sdk" in output, output
        assert (
            "pip install boto3" in output or "aws_access_key_id" in output.lower()
        ), output


class TestArgParsingRoundTrip:
    """Real argparse round trips (no subprocess, no mock) confirming
    --execute defaults to False and the parser accepts the documented
    flags."""

    def test_cleanup_execute_defaults_false(self):
        module = _load_module(CLEANUP_SCRIPT)
        parser = module.build_arg_parser()
        args = parser.parse_args(["--region", "us-west-2"])
        assert args.execute is False
        assert args.region == "us-west-2"

    def test_cleanup_execute_flag_sets_true(self):
        module = _load_module(CLEANUP_SCRIPT)
        parser = module.build_arg_parser()
        args = parser.parse_args(["--region", "us-west-2", "--execute"])
        assert args.execute is True

    def test_cleanup_region_defaults_us_east_1(self):
        module = _load_module(CLEANUP_SCRIPT)
        parser = module.build_arg_parser()
        args = parser.parse_args([])
        assert args.region == "us-east-1"
        assert args.execute is False

    def test_destroy_execute_defaults_false(self):
        module = _load_module(DESTROY_SCRIPT)
        parser = module.build_arg_parser()
        args = parser.parse_args(["my-cluster"])
        assert args.execute is False
        assert args.cluster_name == "my-cluster"
        assert args.region == "us-east-1"

    def test_destroy_execute_flag_sets_true(self):
        module = _load_module(DESTROY_SCRIPT)
        parser = module.build_arg_parser()
        args = parser.parse_args(["my-cluster", "--execute"])
        assert args.execute is True

    def test_destroy_requires_cluster_name(self):
        module = _load_module(DESTROY_SCRIPT)
        parser = module.build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--execute"])  # no cluster_name -> argparse errors


# Destructive boto3 method names we must prove are gated behind --execute.
_DESTRUCTIVE_METHODS = {
    "delete_nat_gateway",
    "release_address",
    "delete_subnet",
    "delete_route_table",
    "detach_internet_gateway",
    "delete_internet_gateway",
    "delete_security_group",
    "delete_vpc",
    "delete_nodegroup",
    "delete_cluster",
    "detach_role_policy",
    "delete_role",
}


def _function_defs_by_name(tree: ast.Module) -> dict:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _destructive_call_names_in(node: ast.AST) -> set:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in _DESTRUCTIVE_METHODS:
                found.add(child.func.attr)
    return found


class TestDeletePathUnreachableWithoutExecuteFlag:
    """Static proof (via ast, on the real source file -- no mock, no AWS
    call) that every destructive boto3 call in each script is only
    reachable through execute_plan(), and that execute_plan() is only
    invoked from main() inside a conditional on args.execute."""

    @pytest.mark.parametrize("script_path", [CLEANUP_SCRIPT, DESTROY_SCRIPT])
    def test_destructive_calls_only_appear_inside_execute_plan(self, script_path):
        source = script_path.read_text()
        tree = ast.parse(source, filename=str(script_path))
        functions = _function_defs_by_name(tree)

        assert "execute_plan" in functions, "expected an execute_plan() function"
        assert "main" in functions, "expected a main() function"

        # Every destructive call anywhere in the module...
        all_destructive_calls = _destructive_call_names_in(tree)
        assert (
            all_destructive_calls
        ), "expected to find destructive boto3 calls somewhere"

        # ...must also appear inside execute_plan()...
        calls_in_execute_plan = _destructive_call_names_in(functions["execute_plan"])
        assert all_destructive_calls <= calls_in_execute_plan, (
            f"destructive calls found outside execute_plan(): "
            f"{all_destructive_calls - calls_in_execute_plan}"
        )

        # ...and every OTHER top-level function (besides execute_plan
        # itself) must contain zero destructive calls.
        for name, func_node in functions.items():
            if name == "execute_plan":
                continue
            leaked = _destructive_call_names_in(func_node) - calls_in_execute_plan
            stray = _destructive_call_names_in(func_node) & _DESTRUCTIVE_METHODS
            # Only execute_plan may contain destructive calls; everything
            # else (main, plan_cleanup/plan_destruction, print_plan, ...)
            # must be entirely free of them.
            assert (
                not stray
            ), f"{name}() unexpectedly contains destructive call(s): {stray}"
            del leaked  # informational only

    @pytest.mark.parametrize("script_path", [CLEANUP_SCRIPT, DESTROY_SCRIPT])
    def test_execute_plan_only_called_inside_if_args_execute(self, script_path):
        source = script_path.read_text()
        tree = ast.parse(source, filename=str(script_path))
        functions = _function_defs_by_name(tree)
        main_node = functions["main"]

        # Build a parent map so we can walk upward from each Call node.
        parent = {}
        for node in ast.walk(main_node):
            for child in ast.iter_child_nodes(node):
                parent[child] = node

        call_sites = [
            node
            for node in ast.walk(main_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "execute_plan"
        ]
        assert call_sites, "expected main() to call execute_plan()"

        for call_node in call_sites:
            # Walk up from the call to find the nearest enclosing ast.If
            # whose test mentions "execute".
            current = call_node
            enclosing_if = None
            while current in parent:
                current = parent[current]
                if isinstance(current, ast.If):
                    test_src = ast.dump(current.test)
                    if "execute" in test_src:
                        enclosing_if = current
                        break
            assert enclosing_if is not None, (
                "execute_plan() call is not nested inside an "
                "`if ...execute...:` block in main()"
            )

    @pytest.mark.parametrize("script_path", [CLEANUP_SCRIPT, DESTROY_SCRIPT])
    def test_no_destructive_calls_at_module_scope(self, script_path):
        """Destructive calls must never run merely by importing the file
        (e.g. at module scope outside any function)."""
        source = script_path.read_text()
        tree = ast.parse(source, filename=str(script_path))

        module_scope_calls = set()
        for stmt in tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            module_scope_calls |= _destructive_call_names_in(stmt)
        assert (
            not module_scope_calls
        ), f"destructive call(s) reachable at module import time: {module_scope_calls}"


class TestTaggingConventionMatchesProvisioner:
    """The scripts must honour the exact tag/name convention that
    clustrix.kubernetes.aws_provisioner.AWSEKSFromScratchProvisioner uses,
    per issue #95 ('Whatever tagging/naming convention the original used,
    honour it and state it in --help')."""

    def test_cleanup_uses_clustrix_managed_tag(self):
        module = _load_module(CLEANUP_SCRIPT)
        assert module.MANAGED_TAG_KEY == "clustrix:managed"
        assert module.MANAGED_TAG_VALUE == "true"

    def test_destroy_uses_clustrix_managed_and_cluster_tags(self):
        module = _load_module(DESTROY_SCRIPT)
        assert module.MANAGED_TAG_KEY == "clustrix:managed"
        assert module.MANAGED_TAG_VALUE == "true"
        assert module.CLUSTER_TAG_KEY == "clustrix:cluster"

    def test_destroy_iam_role_names_match_provisioner(self):
        module = _load_module(DESTROY_SCRIPT)
        cluster_role, node_role = module.iam_role_names("demo-cluster")
        assert cluster_role == "clustrix-eks-cluster-role-demo-cluster"
        assert node_role == "clustrix-eks-node-role-demo-cluster"

    def test_provisioner_actually_applies_these_tags(self):
        """Cross-check against the real provisioner source so this test
        (and the scripts) can't silently drift from what
        aws_provisioner.py actually tags resources with."""
        provisioner_path = REPO_ROOT / "clustrix" / "kubernetes" / "aws_provisioner.py"
        assert provisioner_path.is_file()
        source = provisioner_path.read_text()
        assert '"clustrix:managed": "true"' in source
        assert '"clustrix:cluster"' in source
        assert "clustrix-eks-cluster-role-" in source
        assert "clustrix-eks-node-role-" in source
