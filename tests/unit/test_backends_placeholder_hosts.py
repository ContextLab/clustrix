"""A provider that cannot determine a host must say so (#119).

No mocks. Every failure below is a real one: an unauthenticated provider has a
real ``None`` client, and the Lambda case makes a real HTTP request to a port
nothing is listening on. Previously each of these returned a config carrying
``cluster_host = "placeholder.<provider>.com"``, which clustrix then tried to
SSH into -- so the error the user saw was a DNS failure for a domain they had
never heard of, arbitrarily far from the thing that actually went wrong.

Unverified here: the success paths, which need real cloud credentials and a
real running instance.
"""

import pytest

from clustrix.cloud_providers.azure import AzureProvider
from clustrix.cloud_providers.gcp import GCPProvider
from clustrix.cloud_providers.lambda_cloud import LambdaCloudProvider


def _assert_no_placeholder(excinfo, identifier):
    message = str(excinfo.value)
    assert "placeholder" not in message.lower()
    assert identifier in message


def test_azure_reports_it_cannot_determine_the_host():
    provider = AzureProvider()

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_cluster_config("my-vm", cluster_type="vm")

    _assert_no_placeholder(excinfo, "my-vm")
    assert "connection details" in str(excinfo.value)


def test_gcp_reports_it_cannot_determine_the_host():
    provider = GCPProvider()

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_cluster_config("my-instance", cluster_type="compute")

    _assert_no_placeholder(excinfo, "my-instance")
    assert "connection details" in str(excinfo.value)


def test_lambda_reports_it_cannot_reach_the_api():
    provider = LambdaCloudProvider()
    provider.authenticated = True
    # Port 1 on loopback refuses connections immediately: a real request, a
    # real failure, no network dependency and no credentials.
    provider.base_url = "http://127.0.0.1:1"

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_cluster_config("i-12345")

    _assert_no_placeholder(excinfo, "i-12345")


def test_no_provider_ships_a_placeholder_hostname():
    """The literal placeholder hosts are gone from the shipped code."""
    from pathlib import Path

    import clustrix.cloud_providers as pkg

    offenders = []
    for path in Path(pkg.__file__).parent.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "placeholder." in line and not line.lstrip().startswith("#"):
                offenders.append(f"{path.name}:{number}")

    assert offenders == []
