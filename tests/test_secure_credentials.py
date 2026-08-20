"""Tests for the legacy ``clustrix.secure_credentials`` shim.

1Password support was removed in issue #97, leaving this module as a
deprecation surface. These tests pin the two things that matter about it:
retrieval honestly reports "nothing here", and storage refuses loudly rather
than returning ``False`` -- a silent write failure is indistinguishable from a
credential that was saved and then lost.

No mocks: the manager is constructed for real and the environment-variable
reads are exercised against a real (monkeypatched) ``os.environ``.
"""

import pytest

from clustrix.secure_credentials import (
    REPLACEMENT_GUIDANCE,
    SecureCredentialManager,
    ValidationCredentials,
)


class TestSecureCredentialManager:
    def test_1password_is_reported_unavailable(self):
        assert SecureCredentialManager().is_op_available() is False

    def test_get_credential_returns_none(self):
        assert SecureCredentialManager().get_credential("anything") is None

    def test_get_structured_credential_returns_none(self):
        assert SecureCredentialManager().get_structured_credential("anything") is None

    def test_store_credential_raises_instead_of_returning_false(self):
        """A no-op write must not look like a successful-but-false result."""
        manager = SecureCredentialManager()
        with pytest.raises(NotImplementedError) as excinfo:
            manager.store_credential("clustrix-ssh-slurm", {"password": "x"})

        message = str(excinfo.value)
        # The error has to name both the item that was lost and the supported
        # alternative, or the caller has nowhere to go.
        assert "clustrix-ssh-slurm" in message
        assert REPLACEMENT_GUIDANCE in message
        assert "~/.clustrix/.env" in message

    def test_store_credential_never_returns(self):
        """Guard against the raise being demoted back to a return value."""
        with pytest.raises(NotImplementedError):
            SecureCredentialManager().store_credential("item", {}, "API_CREDENTIAL")


class TestValidationCredentials:
    def test_huggingface_credentials_from_huggingface_token(self, monkeypatch):
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_real_looking_token")
        monkeypatch.setenv("HUGGINGFACE_USERNAME", "someuser")
        monkeypatch.delenv("HF_TOKEN", raising=False)

        creds = ValidationCredentials().get_huggingface_credentials()

        assert creds == {"token": "hf_real_looking_token", "username": "someuser"}

    def test_huggingface_credentials_fall_back_to_hf_token(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_USERNAME", raising=False)
        monkeypatch.setenv("HF_TOKEN", "hf_alternate")

        creds = ValidationCredentials().get_huggingface_credentials()

        assert creds == {"token": "hf_alternate", "username": ""}

    def test_huggingface_credentials_absent(self, monkeypatch):
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)

        assert ValidationCredentials().get_huggingface_credentials() is None

    def test_ssh_credentials_are_not_offered_here(self):
        """The method is gone, not returning ``None``.

        Rewritten rather than deleted: the old assertion locked in a method
        that could only ever return ``None``, which reads to the next caller
        as "SSH is supported here and you have none configured". SSH
        credentials come from ``clustrix.credential_manager``; this class
        only ever answered for HuggingFace.
        """
        assert not hasattr(ValidationCredentials(), "get_ssh_credentials")
