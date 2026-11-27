"""What ``clustrix credentials`` writes to ``.env`` is what it reads back.

Four defects in the credential file's write path, all measured before they
were fixed:

* a password was written unquoted, so ``pa ss #word`` came back ``pa ss``,
  surrounding quotes and whitespace were stripped, and ``${HOME}`` was
  expanded;
* a value that already existed could never be changed -- only commented-out
  and absent names were written;
* a first-run ``clustrix credentials setup`` cancelled itself, because the
  manager's constructor creates the template and the wizard then asked
  whether to update the "existing" file, defaulting to No;
* ``credentials list`` always showed a LOCAL entry, so its "nothing is
  configured" hint could never print.

Real files in the real (temporary) configuration directory, the real parser,
and the real CLI in a separate process.
"""

import os
import subprocess
import sys

import pytest

from clustrix.cli_credentials import (
    _merge_env_content,
    _write_credentials_to_env_file,
    edit_credentials_command,
)
from clustrix.credential_manager import (
    FlexibleCredentialManager,
    format_env_assignment,
    get_credential_manager,
    parse_env_file,
)

#: Every one of these is a password somebody could really have.
NASTY_VALUES = [
    "plain",
    "pa ss #word",
    "#starts-with-a-hash",
    "trailing-hash #",
    " leading and trailing spaces ",
    "\ttabs\tinside\t",
    "'single-quoted'",
    '"double-quoted"',
    "it's",
    'say "hi"',
    "mixed '\" quotes",
    "${HOME}",
    "$HOME and ${USER:-nobody}",
    "$(whoami) `id`",
    "back\\slash",
    "ends-with-backslash\\",
    "double\\\\backslash",
    "backslash-quote\\'",
    "\\n is two characters, not a newline",
    "key=value=more",
    "export NOT=a-directive",
    "semi;colon && pipe | redirect > file",
    "unicode-é-☃-密码",
    "!@#$%^&*()_+-=[]{}|;:,.<>?/~",
    "'",
    '"',
    "\\",
    " ",
]


@pytest.fixture
def env_file():
    """The credential file of a real manager over the isolated config dir."""
    return FlexibleCredentialManager().env_file


@pytest.mark.parametrize("value", NASTY_VALUES)
def test_a_password_round_trips_exactly(env_file, value):
    assert _write_credentials_to_env_file(env_file, {"SSH_PASSWORD": value})

    print(
        "line written:",
        [ln for ln in env_file.read_text().splitlines() if ln[:4] == "SSH_"],
    )
    assert parse_env_file(env_file)["SSH_PASSWORD"] == value


@pytest.mark.parametrize("value", NASTY_VALUES)
def test_a_password_survives_being_written_twice(env_file, value):
    """Read, write back, read: the second trip must not re-escape the first."""
    _write_credentials_to_env_file(env_file, {"SSH_PASSWORD": value})
    _write_credentials_to_env_file(env_file, parse_env_file(env_file))

    assert parse_env_file(env_file)["SSH_PASSWORD"] == value


def test_every_nasty_value_at_once_keeps_its_own_name(env_file):
    """One value's quoting must not swallow the line after it."""
    credentials = {f"NASTY_{i}": value for i, value in enumerate(NASTY_VALUES)}

    assert _write_credentials_to_env_file(env_file, credentials)

    parsed = parse_env_file(env_file)
    assert {k: parsed[k] for k in credentials} == credentials


@pytest.mark.parametrize("value", ["two\nlines", "carriage\rreturn", "nul\x00byte"])
def test_a_value_that_cannot_be_one_line_is_refused_not_rewritten(env_file, value):
    before = env_file.read_text()

    with pytest.raises(ValueError, match="line break or NUL"):
        format_env_assignment("SSH_PASSWORD", value)
    assert _write_credentials_to_env_file(env_file, {"SSH_PASSWORD": value}) is False

    assert env_file.read_text() == before


def test_an_existing_value_is_replaced(env_file):
    """Setup could never change a password it had already saved."""
    _write_credentials_to_env_file(
        env_file, {"SSH_HOST": "old.example.edu", "SSH_PASSWORD": "old-password"}
    )

    _write_credentials_to_env_file(
        env_file, {"SSH_HOST": "new.example.edu", "SSH_PASSWORD": "new-password"}
    )

    parsed = parse_env_file(env_file)
    assert parsed["SSH_HOST"] == "new.example.edu"
    assert parsed["SSH_PASSWORD"] == "new-password"
    text = env_file.read_text()
    assert "old-password" not in text
    assert text.count("SSH_PASSWORD=") == 1


def test_merging_keeps_comments_order_and_unrelated_values():
    existing = (
        "# my notes\n"
        "OTHER=keep me\n"
        "# SSH_HOST=your-cluster.university.edu\n"
        "export SSH_USERNAME=old\n"
        "SSH_USERNAME=older-duplicate\n"
        "# trailing comment\n"
    )

    merged = _merge_env_content(
        existing, {"SSH_USERNAME": "new", "SSH_HOST": "h", "HF_TOKEN": "t"}
    )

    print(merged)
    assert merged == (
        "# my notes\n"
        "OTHER=keep me\n"
        "SSH_HOST='h'\n"
        "SSH_USERNAME='new'\n"
        "# trailing comment\n"
        "HF_TOKEN='t'\n"
    )


def test_a_template_comment_is_left_alone_when_the_name_is_already_set():
    """Otherwise the name would be assigned twice and the stale one could win."""
    existing = "SSH_HOST=mine.example.edu\n# SSH_HOST=your-cluster.university.edu\n"

    merged = _merge_env_content(existing, {"SSH_HOST": "new.example.edu"})

    assert merged == (
        "SSH_HOST='new.example.edu'\n# SSH_HOST=your-cluster.university.edu\n"
    )


def test_the_template_states_the_precedence_the_code_implements(env_file, monkeypatch):
    """The template said ".env file -> environment"; the code does the reverse."""
    _write_credentials_to_env_file(
        env_file, {"SSH_HOST": "file.example.edu", "SSH_USERNAME": "from-file"}
    )
    monkeypatch.setenv("SSH_HOST", "environment.example.edu")

    status = FlexibleCredentialManager().get_credential_status()
    from clustrix.credential_release import describe_credential

    described = describe_credential("ssh")

    assert status["providers"]["ssh"]["available"]
    assert described.host == "environment.example.edu"
    assert described.username == "from-file"
    template = FlexibleCredentialManager()._generate_env_template()
    assert "environment overrides the same name in" in template
    assert ".env file → environment variables" not in template


def run_cli(*arguments, stdin=""):
    result = subprocess.run(
        [sys.executable, "-m", "clustrix.cli", *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=120,
    )
    print(f"$ clustrix {' '.join(arguments)}  -> exit {result.returncode}")
    print(result.stdout, result.stderr, sep="")
    return result


def test_a_first_run_setup_is_not_cancelled_by_its_own_template():
    """Enter, Enter: decline both providers. That is not "Setup cancelled"."""
    result = run_cli("credentials", "setup", stdin="\n\n")

    assert "Setup cancelled" not in result.stdout
    assert "update existing credentials" not in result.stdout
    assert "Configure SSH cluster access" in result.stdout
    assert "No credentials configured" in result.stdout


def test_setup_still_asks_before_touching_credentials_that_exist(env_file):
    _write_credentials_to_env_file(env_file, {"SSH_HOST": "mine.example.edu"})

    result = run_cli("credentials", "setup", stdin="\n")

    assert "Credentials already configured: SSH_HOST" in result.stdout
    assert "Setup cancelled" in result.stdout
    assert parse_env_file(env_file) == {"SSH_HOST": "mine.example.edu"}


def test_list_says_so_when_nothing_is_configured(monkeypatch):
    """A permanent "LOCAL: type" entry meant this hint could never print."""
    for name in ("SSH_HOST", "SSH_USERNAME", "SSH_PASSWORD", "HF_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.delenv("SSH_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("SSH_PORT", raising=False)
    monkeypatch.delenv("HF_USERNAME", raising=False)
    monkeypatch.delenv("HUGGINGFACE_USERNAME", raising=False)

    result = run_cli("credentials", "list")

    assert "LOCAL" not in result.stdout
    assert "No credentials configured" in result.stdout
    assert set(get_credential_manager().get_credential_status()["providers"]) == {
        "ssh",
        "huggingface",
    }


def test_editor_with_arguments_is_run_as_a_command_line(tmp_path, monkeypatch):
    """``EDITOR="code -w"`` was handed to exec as one program name.

    A real executable stands in for the editor: it records its arguments and
    exits, which is all an editor's command line can be checked for.
    """
    record = tmp_path / "invocation.txt"
    editor = tmp_path / "my editor"
    editor.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{record}"\n')
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", f"'{editor}' --wait -n")

    edit_credentials_command()

    assert record.read_text().splitlines() == [
        "--wait",
        "-n",
        str(get_credential_manager().env_file),
    ]
