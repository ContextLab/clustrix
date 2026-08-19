"""The cloud-provider job interface is explicit and fails at submit time (#119).

No mocks and no credentials. These assert error behaviour, which is the part
that was wrong: a provider with no ``create_instance`` was accepted, the job
was reported as submitted, and the ``NotImplementedError`` then surfaced deep
inside a background thread where nothing could act on it.

What these tests CANNOT verify is that the 'lambda' path actually provisions a
machine -- that needs real Lambda Cloud credentials and real money.
"""

import pytest

from clustrix.config import ClusterConfig
from clustrix.executor_cloud import REQUIRED_PROVIDER_METHODS, CloudJobManager
from clustrix.utils import serialize_function


def add(a, b):
    return a + b


FUNC_DATA = None


def _func_data():
    global FUNC_DATA
    if FUNC_DATA is None:
        FUNC_DATA = serialize_function(add, (1, 2), {})
    return FUNC_DATA


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp", "huggingface"])
def test_providers_without_instance_creation_are_refused_at_submit(provider):
    manager = CloudJobManager(ClusterConfig())

    with pytest.raises(NotImplementedError) as excinfo:
        manager.submit_cloud_job(_func_data(), {"cores": 1}, provider)

    message = str(excinfo.value)
    assert f"'{provider}'" in message
    assert "create_instance" in message
    # And nothing was left behind claiming to be a running job.
    assert manager.active_jobs == {}


def test_lambda_without_credentials_fails_on_authentication_not_interface():
    """Lambda implements the interface; the missing piece is credentials."""
    manager = CloudJobManager(ClusterConfig(lambda_api_key=None))

    with pytest.raises(RuntimeError, match="not authenticated"):
        manager.submit_cloud_job(_func_data(), {"cores": 1}, "lambda")

    assert manager.active_jobs == {}


def test_lambda_provider_implements_the_declared_interface():
    from clustrix.cloud_providers.lambda_cloud import LambdaCloudProvider

    provider = LambdaCloudProvider()
    missing = [
        name
        for name in REQUIRED_PROVIDER_METHODS
        if not callable(getattr(provider, name, None))
    ]
    assert missing == []


def test_unknown_provider_is_rejected():
    manager = CloudJobManager(ClusterConfig())
    with pytest.raises(ValueError, match="Unsupported cloud provider"):
        manager.submit_cloud_job(_func_data(), {"cores": 1}, "nimbus")
