#!/usr/bin/env python3
"""A HuggingFace token in ``~/.clustrix/.env`` has to be findable.

``ValidationCredentials.get_huggingface_credentials`` read ``os.environ``
and nothing else. That appeared to work only because some earlier lookup in
the same process called ``load_dotenv``, which exports the whole of
``~/.clustrix/.env`` into the environment for the rest of the process's
life. When that process-wide export was removed (issue #153, correctly -- it
made real AWS and HuggingFace credentials visible to every test that
happened to run afterwards) a token living *only* in the ``.env`` file
became invisible here, and two callers that were correctly configured
stopped finding it:

* ``tests/real_world/test_credential_access.py`` line 71
* ``scripts/debug_huggingface_auth.py`` line 21

The fix is not to re-export. It is to ask the supported lookup, which reads
the environment *and* the file without writing to either.

Real files, no mocks: an actual ``.env`` is written into an actual
temporary config directory (``$HOME`` and ``CLUSTRIX_CONFIG_DIR`` are
redirected by the autouse fixtures in ``tests/conftest.py``) and read back
through the real credential manager.
"""

import os

import pytest

import clustrix.credential_manager as credential_manager_module
from clustrix.config import get_config_dir
from clustrix.secure_credentials import ValidationCredentials

#: Assembled rather than written as a literal, so no token-shaped string
#: appears in the repository.
TOKEN = "-".join(["clustrix", "sentinel", "hf", "token"])


@pytest.fixture(autouse=True)
def no_ambient_huggingface_credentials(monkeypatch):
    """A developer's exported HF_TOKEN must not decide the outcome."""
    for name in (
        "HF_TOKEN",
        "HUGGINGFACE_TOKEN",
        "HF_USERNAME",
        "HUGGINGFACE_USERNAME",
    ):
        monkeypatch.delenv(name, raising=False)
    credential_manager_module._credential_manager = None
    yield
    credential_manager_module._credential_manager = None


def _write_env_file(text):
    config_dir = get_config_dir()
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_file = config_dir / ".env"
    env_file.write_text(text, encoding="utf-8")
    env_file.chmod(0o600)
    credential_manager_module._credential_manager = None
    return env_file


def test_a_token_only_in_the_env_file_is_found():
    """The regression, stated as the property that was lost."""
    _write_env_file(f"HF_TOKEN={TOKEN}\nHF_USERNAME=someuser\n")

    credentials = ValidationCredentials().get_huggingface_credentials()

    assert credentials == {"token": TOKEN, "username": "someuser"}


def test_the_huggingface_aliases_work_from_the_file_too():
    """``HUGGINGFACE_*`` and ``HF_*`` name the same credential.

    They resolve from one table in ``credential_manager``, which is the
    other half of why going through it is the right fix: the two sources
    cannot disagree about which spellings count.
    """
    _write_env_file(f"HUGGINGFACE_TOKEN={TOKEN}\nHUGGINGFACE_USERNAME=someuser\n")

    credentials = ValidationCredentials().get_huggingface_credentials()

    assert credentials == {"token": TOKEN, "username": "someuser"}


def test_the_environment_still_works(monkeypatch):
    """Routing through the manager may not cost the case that did work."""
    monkeypatch.setenv("HUGGINGFACE_TOKEN", TOKEN)
    monkeypatch.setenv("HUGGINGFACE_USERNAME", "someuser")
    credential_manager_module._credential_manager = None

    credentials = ValidationCredentials().get_huggingface_credentials()

    assert credentials == {"token": TOKEN, "username": "someuser"}


def test_a_token_with_no_username_reports_an_empty_one():
    """Callers index ``username``; absent would be an AttributeError later."""
    _write_env_file(f"HF_TOKEN={TOKEN}\n")

    credentials = ValidationCredentials().get_huggingface_credentials()

    assert credentials == {"token": TOKEN, "username": ""}


def test_no_credentials_anywhere_is_still_None():
    """ "Not configured" has to stay distinguishable from "configured"."""
    _write_env_file("SSH_HOST=cluster.example.edu\n")

    assert ValidationCredentials().get_huggingface_credentials() is None


def test_the_lookup_does_not_export_the_file_into_the_environment():
    """The export is the thing that was removed; do not bring it back.

    Reading a credential is a read. A lookup that also copies the file into
    ``os.environ`` changes what every later import in the process sees,
    which is how a real token ended up visible to unrelated tests.
    """
    _write_env_file(f"HF_TOKEN={TOKEN}\nSSH_PASSWORD=whatever\n")

    ValidationCredentials().get_huggingface_credentials()

    assert os.environ.get("HF_TOKEN") is None
    assert os.environ.get("SSH_PASSWORD") is None
