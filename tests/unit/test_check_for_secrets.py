"""The committed-credential check must catch real ones and ignore stand-ins.

The check this replaces grepped the source for the words "password", "secret",
"key" and "token". It flagged 1138 lines -- including
`kwargs: Function keyword arguments`, because "keyword" contains "key" -- so it
could never pass on a codebase that handles credentials, which is exactly the
codebase worth checking. It also could not have caught a real secret, because a
leaked token does not contain the word "token".

A checker that cries wolf gets switched off, so both halves are tested: what it
must catch, and what it must stay quiet about.
"""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

from check_for_secrets import scan, scan_key_blocks, scan_line  # noqa: E402

REAL = [
    # Deliberately not AWS's documented AKIAIOSFODNN7EXAMPLE, and with no
    # "example" in it -- that word is suppressed, as another test asserts.
    ("AWS access key", 'aws_access_key_id = "AKIA5J7QWTRYUIOPASDF"'),
    ("GitHub token", 'token = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"'),
    ("HuggingFace token", 'hf_token = "hf_QwErTyUiOpAsDfGhJkLzXcVbNm1234567890"'),
    ("OpenAI key", 'key = "sk-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"'),
    ("assigned password", 'password = "Tr0ub4dor&3xK9"'),
]

STAND_INS = [
    "kwargs: Function keyword arguments",
    'password = "<redacted>"',
    'password = "your-password-here"',
    'token = "$HF_TOKEN"',
    'api_key = "{api_key}"',
    'password = "xxxxxxxxxx"',
    'password = "wrong_password"',
    'api_key = "invalid_key"',
    'key = "mock_key_value"',
    "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
    '"aws_secret_key": "secret_access_key"',
    'password = ""',
]


class TestItCatchesRealCredentials:
    @pytest.mark.parametrize("label,line", REAL, ids=[r[0] for r in REAL])
    def test_a_usable_credential_is_reported(self, label, line):
        assert scan_line(line), f"{label} was not detected in {line!r}"


class TestItIgnoresStandIns:
    @pytest.mark.parametrize("line", STAND_INS)
    def test_a_placeholder_is_not_a_finding(self, line):
        assert scan_line(line) == [], f"false positive on {line!r}"

    def test_the_pattern_that_broke_the_old_check(self):
        """The single line that made the previous check unpassable."""
        assert scan_line("            kwargs: Function keyword arguments") == []


class TestPrivateKeys:
    def test_a_key_with_a_real_body_is_reported(self):
        block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEAwJ8k3nBvQ2p1LrTzXyVbNmQwErTyUiOpAsDfGhJkLzXcVbNm\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        assert scan_key_blocks(block) == [1]

    def test_a_header_wrapped_round_a_stand_in_is_not(self):
        """Test fixtures write the header around MOCK_KEY_CONTENT."""
        block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MOCK_KEY_CONTENT\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        assert scan_key_blocks(block) == []


class TestScanningTheRepository:
    def test_the_repository_is_clean(self):
        """This is the check CI runs; it must pass on the tree as committed."""
        root = pathlib.Path(__file__).resolve().parents[2]
        assert scan([root]) == []

    def test_it_reports_a_planted_credential(self, tmp_path):
        """A test that only ever passes proves nothing."""
        planted = tmp_path / "leak.py"
        planted.write_text('SESSION = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"\n')

        assert scan([tmp_path])

    def test_the_script_exits_non_zero_on_a_finding(self, tmp_path):
        (tmp_path / "leak.py").write_text(
            'GITHUB = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"\n'
        )
        script = pathlib.Path(__file__).resolve().parents[2] / "scripts"
        result = subprocess.run(
            [sys.executable, str(script / "check_for_secrets.py"), str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "GitHub token" in result.stdout
