#!/usr/bin/env python3
"""A stored password is only ever offered to the host it was stored for.

``FlexibleCredentialAuthMethod`` used to decide whether a credential
belonged to a connection with

    hostname == cred_host
    or hostname.split(".")[0] == cred_host.split(".")[0]
    or cred_host in hostname
    or hostname in cred_host

Every clause after the first is a way of saying yes to a host the user
never configured, and the third is the worst of them: a ``.env`` holding
only ``SSH_PASSWORD`` produces ``cred_host == ""``, and the empty string is
a substring of every hostname that exists. Asking to authenticate to
``totally-unrelated.attacker.example`` returned the real cluster password.

Nothing here is mocked. A real ``.env`` file is written into a real
temporary config directory and read back through the real
``FlexibleCredentialManager``, because the defect lived in the seam between
what that manager returns and what the auth method does with it.
"""

import os

import pytest

import clustrix.credential_manager as credential_manager_module
from clustrix.auth_methods import FlexibleCredentialAuthMethod, hostname_matches
from clustrix.config import ClusterConfig, get_config_dir

#: Distinctive, so that a leak is unambiguous wherever it turns up. Built at
#: import time rather than written as a credential-shaped literal.
STORED_PASSWORD = "-".join(["clustrix", "sentinel", "stored", "cluster", "password"])

CONFIGURED_HOST = "hpc.example.edu"
CONFIGURED_USER = "researcher"


@pytest.fixture
def stored_credential(monkeypatch):
    """Write a real ``~/.clustrix/.env`` and return a fresh auth method.

    ``$HOME`` and ``CLUSTRIX_CONFIG_DIR`` already point at a throwaway tree
    (the autouse fixtures in ``tests/conftest.py``). The ambient ``SSH_*``
    variables are cleared as well: the environment source is consulted
    ahead of the file, so a developer who exports ``SSH_HOST`` in their
    shell would otherwise be testing their own machine's configuration.
    """

    def write(**entries):
        for name in list(os.environ):
            if name.startswith("SSH_"):
                monkeypatch.delenv(name, raising=False)

        config_dir = get_config_dir()
        config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        env_file = config_dir / ".env"
        env_file.write_text(
            "".join(f"{k}={v}\n" for k, v in entries.items()), encoding="utf-8"
        )
        env_file.chmod(0o600)

        credential_manager_module._credential_manager = None
        return FlexibleCredentialAuthMethod(ClusterConfig(cluster_type="ssh"))

    return write


class TestTheCredentialReachesItsOwnHost:
    def test_the_configured_host_still_gets_the_password(self, stored_credential):
        """The fix may not be a fix by way of never working."""
        method = stored_credential(
            SSH_HOST=CONFIGURED_HOST,
            SSH_USERNAME=CONFIGURED_USER,
            SSH_PASSWORD=STORED_PASSWORD,
        )

        result = method.attempt_auth(
            {"hostname": CONFIGURED_HOST, "username": CONFIGURED_USER}
        )

        assert result.success
        assert result.password == STORED_PASSWORD

    def test_case_and_a_trailing_dot_are_not_a_different_host(self, stored_credential):
        """DNS is case-insensitive and a trailing dot only means "absolute"."""
        method = stored_credential(
            SSH_HOST=CONFIGURED_HOST,
            SSH_USERNAME=CONFIGURED_USER,
            SSH_PASSWORD=STORED_PASSWORD,
        )

        result = method.attempt_auth(
            {"hostname": "HPC.Example.EDU.", "username": CONFIGURED_USER}
        )

        assert result.success
        assert result.password == STORED_PASSWORD

    def test_a_stored_key_path_still_reaches_its_host(self, stored_credential):
        """The key branch is matched by the same rule as the password one."""
        method = stored_credential(
            SSH_HOST=CONFIGURED_HOST,
            SSH_USERNAME=CONFIGURED_USER,
            SSH_PRIVATE_KEY_PATH="/keys/id_ed25519",
        )

        result = method.attempt_auth(
            {"hostname": CONFIGURED_HOST, "username": CONFIGURED_USER}
        )

        assert result.success
        assert result.key_path == "/keys/id_ed25519"


class TestTheCredentialReachesNobodyElse:
    def test_a_credential_with_no_host_is_offered_to_no_host(self, stored_credential):
        """The reported defect, end to end.

        A ``.env`` holding only ``SSH_PASSWORD`` yields ``host == ""``, and
        ``"" in anything`` is true, so the cluster password was handed to
        whatever host was asked for.
        """
        method = stored_credential(SSH_PASSWORD=STORED_PASSWORD)

        result = method.attempt_auth(
            {"hostname": "totally-unrelated.attacker.example", "username": ""}
        )

        assert not result.success
        assert result.password is None

    @pytest.mark.parametrize(
        "target",
        [
            # A name anybody can create under a domain they control, which
            # the substring rule accepted.
            "hpc.example.edu.attacker.test",
            # The first-label rule accepted this one.
            "hpc.evil.test",
            # ``hostname in cred_host``: the substring rule, backwards.
            "example.edu",
            "hpc",
            # A child of the configured host is still a different host.
            "node1.hpc.example.edu",
            # Nothing whatsoever in common.
            "totally-unrelated.attacker.example",
        ],
    )
    def test_a_host_that_is_not_the_configured_host_gets_nothing(
        self, stored_credential, target
    ):
        method = stored_credential(
            SSH_HOST=CONFIGURED_HOST,
            SSH_USERNAME=CONFIGURED_USER,
            SSH_PASSWORD=STORED_PASSWORD,
        )

        result = method.attempt_auth({"hostname": target, "username": CONFIGURED_USER})

        assert not result.success, f"{target} was offered the stored password"
        assert result.password is None

    def test_a_credential_with_no_username_matches_no_connection(
        self, stored_credential
    ):
        """The same "absent satisfies the test" defect, on the other half.

        ``username == cred_username`` was true when both were empty, so a
        credential that named nobody matched a connection that named
        nobody.
        """
        method = stored_credential(
            SSH_HOST=CONFIGURED_HOST, SSH_PASSWORD=STORED_PASSWORD
        )

        result = method.attempt_auth({"hostname": CONFIGURED_HOST, "username": ""})

        assert not result.success
        assert result.password is None

    def test_a_different_user_on_the_right_host_gets_nothing(self, stored_credential):
        method = stored_credential(
            SSH_HOST=CONFIGURED_HOST,
            SSH_USERNAME=CONFIGURED_USER,
            SSH_PASSWORD=STORED_PASSWORD,
        )

        result = method.attempt_auth(
            {"hostname": CONFIGURED_HOST, "username": "someone-else"}
        )

        assert not result.success
        assert result.password is None

    def test_the_refusal_says_how_to_configure_it(self, stored_credential):
        """A safe failure has to be an actionable one."""
        method = stored_credential(SSH_PASSWORD=STORED_PASSWORD)

        result = method.attempt_auth(
            {"hostname": CONFIGURED_HOST, "username": CONFIGURED_USER}
        )

        assert not result.success
        assert "SSH_HOST" in result.guidance
        assert "SSH_USERNAME" in result.guidance
        assert CONFIGURED_HOST in result.guidance


class TestTheMatchingRuleItself:
    """The rule, tested directly, so a failure names the rule not the seam."""

    @pytest.mark.parametrize(
        "target, stored",
        [
            ("hpc.example.edu", "hpc.example.edu"),
            ("HPC.EXAMPLE.EDU", "hpc.example.edu"),
            ("hpc.example.edu.", "hpc.example.edu"),
            ("  hpc.example.edu  ", "hpc.example.edu."),
        ],
    )
    def test_the_same_host_spelled_differently_matches(self, target, stored):
        assert hostname_matches(target, stored)

    @pytest.mark.parametrize(
        "target, stored",
        [
            # Nothing configured on either side may ever match.
            ("hpc.example.edu", ""),
            ("", "hpc.example.edu"),
            ("", ""),
            ("hpc.example.edu", None),
            (None, "hpc.example.edu"),
            ("hpc.example.edu", "   "),
            # Neither containment direction, and not the first label.
            ("hpc.example.edu.attacker.test", "hpc.example.edu"),
            ("hpc.example.edu", "hpc.example.edu.attacker.test"),
            ("hpc.evil.test", "hpc.example.edu"),
            ("node1.hpc.example.edu", "hpc.example.edu"),
        ],
    )
    def test_anything_else_does_not_match(self, target, stored):
        assert not hostname_matches(target, stored)
