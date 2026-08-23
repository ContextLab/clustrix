"""Reading a credential must not change what the rest of the process sees.

``DotEnvCredentialSource`` called ``load_dotenv``, which copies every key in
``~/.clustrix/.env`` into ``os.environ`` for the remaining life of the
process. Two separate consequences were observed:

* every credential in the file -- including ones clustrix has no mapping
  for, such as ``AWS_SECRET_ACCESS_KEY`` -- became visible to every
  subsequent import, and to any subprocess, in a process that had merely
  asked "is SSH configured?";
* a test that read the environment passed in CI, where no ``.env`` exists,
  and failed on any developer machine that had one. CI cannot see that
  asymmetry, which makes it worse than a plain failure.

The second half of this module covers the "not configured" answer: the SSH
port default used to be baked into the lookup, so an unconfigured machine
produced ``{"port": "22"}`` and ``ensure_credential("ssh")`` was never
``None``.

Placeholders use the ``<redacted>`` spelling that
``tests/unit/test_check_for_secrets.py`` already treats as a stand-in.
"""

import os

import pytest

from clustrix.credential_manager import (
    DotEnvCredentialSource,
    EnvironmentCredentialSource,
    FlexibleCredentialManager,
    parse_env_file,
    resolve_provider_credentials,
)

#: Names written into the throwaway ``.env`` below. Two clustrix maps to a
#: credential field, one it has never heard of -- the third is the one that
#: proves the export was indiscriminate rather than scoped.
ENV_FILE_NAMES = ("SSH_HOST", "SSH_PASSWORD", "AWS_SECRET_ACCESS_KEY")


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A real ``.env`` on disk, with those names absent from the environment."""
    for name in ENV_FILE_NAMES:
        monkeypatch.delenv(name, raising=False)

    path = tmp_path / ".env"
    path.write_text(
        "SSH_HOST=cluster.example.edu\n"
        "SSH_PASSWORD=<redacted>\n"
        "AWS_SECRET_ACCESS_KEY=<redacted>\n"
        "# a comment\n"
        "\n",
        encoding="utf-8",
    )
    return path


class TestTheEnvFileStaysInTheFile:
    def test_reading_a_credential_leaves_os_environ_alone(self, env_file):
        creds = DotEnvCredentialSource(env_file).get_credentials("ssh")

        assert creds["host"] == "cluster.example.edu"
        assert creds["password"] == "<redacted>"
        for name in ENV_FILE_NAMES:
            assert name not in os.environ, f"{name} was exported process-wide"

    def test_an_unmapped_key_is_not_exported_either(self, env_file):
        """The AWS key is the one clustrix never asked for.

        A scoped export would at least have been arguable. ``load_dotenv``
        copies the whole file, so asking about SSH published the AWS
        credential too.
        """
        DotEnvCredentialSource(env_file).get_credentials("ssh")

        assert os.environ.get("AWS_SECRET_ACCESS_KEY") is None

    def test_listing_providers_leaves_os_environ_alone(self, env_file):
        """``list_available_providers`` reads every provider in turn."""
        DotEnvCredentialSource(env_file).list_available_providers()

        for name in ENV_FILE_NAMES:
            assert name not in os.environ

    def test_the_whole_manager_leaves_os_environ_alone(self, tmp_path, env_file):
        """Construction plus a status sweep, which touches every source."""
        config_dir = tmp_path / "clustrix"
        config_dir.mkdir(mode=0o700)
        (config_dir / ".env").write_text(
            env_file.read_text(encoding="utf-8"), encoding="utf-8"
        )

        manager = FlexibleCredentialManager(config_dir=config_dir)
        manager.get_credential_status()

        for name in ENV_FILE_NAMES:
            assert name not in os.environ

    def test_the_ambient_environment_still_wins(self, env_file, monkeypatch):
        """``load_dotenv`` did not override an already-set variable.

        That precedence is preserved -- the file is layered underneath the
        environment -- so a shell export still beats the file, as before.
        """
        monkeypatch.setenv("SSH_HOST", "shell.example.edu")

        creds = DotEnvCredentialSource(env_file).get_credentials("ssh")

        assert creds["host"] == "shell.example.edu"

    def test_parse_env_file_returns_what_the_file_says(self, env_file):
        values = parse_env_file(env_file)

        assert values["SSH_HOST"] == "cluster.example.edu"
        assert values["AWS_SECRET_ACCESS_KEY"] == "<redacted>"
        assert "# a comment" not in values

    def test_parse_env_file_survives_a_missing_file(self, tmp_path):
        assert parse_env_file(tmp_path / "absent.env") == {}


class TestNotConfiguredMeansNone:
    def test_an_empty_environment_has_no_ssh_credentials(self, monkeypatch):
        """The bug: a default port made the answer permanently truthy."""
        for name in (
            "SSH_HOST",
            "SSH_USERNAME",
            "SSH_PASSWORD",
            "SSH_PRIVATE_KEY_PATH",
            "SSH_PORT",
        ):
            monkeypatch.delenv(name, raising=False)

        assert EnvironmentCredentialSource().get_credentials("ssh") is None

    def test_an_empty_env_file_has_no_ssh_credentials(self, tmp_path, monkeypatch):
        for name in (
            "SSH_HOST",
            "SSH_USERNAME",
            "SSH_PASSWORD",
            "SSH_PRIVATE_KEY_PATH",
            "SSH_PORT",
        ):
            monkeypatch.delenv(name, raising=False)
        path = tmp_path / ".env"
        path.write_text("# nothing configured\n", encoding="utf-8")

        assert DotEnvCredentialSource(path).get_credentials("ssh") is None

    def test_ensure_credential_reports_ssh_as_missing(self, tmp_path, monkeypatch):
        """The caller-facing consequence, through the real manager."""
        for name in (
            "SSH_HOST",
            "SSH_USERNAME",
            "SSH_PASSWORD",
            "SSH_PRIVATE_KEY_PATH",
            "SSH_PORT",
        ):
            monkeypatch.delenv(name, raising=False)
        config_dir = tmp_path / "clustrix"

        manager = FlexibleCredentialManager(config_dir=config_dir)

        # ``_configured_fields`` is the non-secret half of the old
        # ``ensure_credential``: which source answered, and which field
        # names it holds. "Nothing is configured" never needed the secret,
        # and the store no longer hands one out to anybody but the gate.
        assert manager._configured_fields("ssh") == (None, [])
        assert manager.get_missing_providers(["ssh"]) == ["ssh"]

    def test_a_configured_host_still_gets_the_default_port(self, monkeypatch):
        monkeypatch.setenv("SSH_HOST", "cluster.example.edu")
        for name in (
            "SSH_USERNAME",
            "SSH_PASSWORD",
            "SSH_PRIVATE_KEY_PATH",
            "SSH_PORT",
        ):
            monkeypatch.delenv(name, raising=False)

        creds = EnvironmentCredentialSource().get_credentials("ssh")

        assert creds == {"host": "cluster.example.edu", "port": "22"}


class TestBothSourcesAgree:
    """The two sources used to resolve different names for the same field."""

    @pytest.mark.parametrize(
        "names,expected",
        [
            ({"HF_TOKEN": "<redacted>"}, "<redacted>"),
            ({"HUGGINGFACE_TOKEN": "<redacted>"}, "<redacted>"),
        ],
    )
    def test_the_huggingface_aliases_resolve_from_a_file_too(
        self, tmp_path, monkeypatch, names, expected
    ):
        """``HUGGINGFACE_TOKEN`` worked from the shell and not from ``.env``.

        A credential that resolves one way and not the other is
        indistinguishable, to the user, from a credential that is wrong.
        """
        for name in (
            "HF_TOKEN",
            "HUGGINGFACE_TOKEN",
            "HF_USERNAME",
            "HUGGINGFACE_USERNAME",
        ):
            monkeypatch.delenv(name, raising=False)
        path = tmp_path / ".env"
        path.write_text(
            "".join(f"{k}={v}\n" for k, v in names.items()), encoding="utf-8"
        )

        creds = DotEnvCredentialSource(path).get_credentials("huggingface")

        assert creds == {"token": expected}

    def test_an_unknown_provider_is_none_everywhere(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("SSH_HOST=cluster.example.edu\n", encoding="utf-8")

        assert DotEnvCredentialSource(path).get_credentials("aws") is None
        assert EnvironmentCredentialSource().get_credentials("aws") is None
        assert resolve_provider_credentials({}, "aws") is None

    def test_local_needs_no_credentials(self):
        assert resolve_provider_credentials({}, "local") == {"type": "local"}
