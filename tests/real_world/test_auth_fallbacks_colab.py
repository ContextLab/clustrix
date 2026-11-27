"""The Colab branch of the password fallback, inside Colab and nowhere else.

``google.colab.userdata`` exists only in a Colab runtime. A stand-in module
would be testing the stand-in, so outside Colab this is skipped and says so:
the branch is untested on every other machine, and that is visible here
rather than hidden behind a passing fake.
"""

import pytest

pytest.importorskip(
    "google.colab",
    reason="google.colab is importable only inside a Google Colab runtime",
)

from clustrix.auth_fallbacks import (  # noqa: E402
    _colab_password,
    detect_environment,
)
from clustrix.credential_release import CredentialTarget  # noqa: E402


def test_a_colab_runtime_is_detected_as_colab():
    assert detect_environment() == "colab"


def test_a_secret_that_was_never_stored_is_no_password():
    target = CredentialTarget(
        hostname="never-stored.clustrix.invalid",
        username="nobody",
        described_as="a test",
    )

    assert _colab_password(target, None) is None
