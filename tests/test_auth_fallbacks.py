"""The password fallback, asked the way a person meets it.

``clustrix.auth_fallbacks`` decides where a password comes from by looking at
what kind of process it is in -- a terminal, a script with piped input, a
Jupyter kernel -- and then prompts accordingly. Every test of it used to
patch ``detect_environment`` to announce the answer, patch ``getpass`` or
``input`` to return a string, and assert that string came back.

Nothing here is patched. Each environment is a real one:

* **a terminal** is a real pseudo-terminal the child process is the session
  leader of, so ``getpass`` really turns echo off, ``^C`` really raises
  ``SIGINT`` and ``^D`` really is end-of-file;
* **a script** is a real child process whose stdin is a real pipe;
* **a notebook** is a real child process that has really imported
  ``ipykernel``, which is the only thing ``detect_environment`` looks at.

The SSH half of ``setup_auth_with_fallback`` runs against the real server in
``tests/ssh_server.py`` with the real ``setup_ssh_keys``: a real key is
generated, really deployed with the password the fallback found, and really
used to log in.

Not reachable from here, and so not tested here: Google Colab's
``userdata`` store (``tests/real_world/test_auth_fallbacks_colab.py`` runs
inside Colab and nowhere else) and the tkinter dialog, which needs a display
and a person.
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from clustrix.auth_fallbacks import (
    requires_password_fallback,
    setup_auth_with_fallback,
)
from clustrix.config import ClusterConfig
from clustrix.ssh_utils import setup_ssh_keys, setup_ssh_keys_with_fallback
from tests.ssh_server import LocalSSHServer
from tests.terminal import run_on_a_terminal

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="pseudo-terminals and POSIX signals are used throughout"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "cluster.example.com"
USER = "researcher"
PROMPT = f"Password for {USER}@{HOST}: "

#: Run in a child process, so that the environment it reports is its own and
#: not pytest's. The result goes to a file: on a terminal, stdout is the same
#: device the prompt is written to.
PROBE = """
import json, sys
out, preload, with_config = sys.argv[1:4]
if preload:
    __import__(preload)
from clustrix.auth_fallbacks import detect_environment, get_cluster_password
from clustrix.config import ClusterConfig
from clustrix.credential_release import CredentialTarget

environment = detect_environment()
target = CredentialTarget(hostname={host!r}, username={user!r}, described_as="a test")
config = (
    ClusterConfig(cluster_host={host!r}, username={user!r}) if with_config else None
)
password = get_cluster_password(target, config=config)
with open(out, "w") as handle:
    json.dump({{"environment": environment, "password": password}}, handle)
""".format(host=HOST, user=USER)


def _probe_argv(result_path, preload="", with_config=False):
    return [
        sys.executable,
        "-c",
        PROBE,
        str(result_path),
        preload,
        "1" if with_config else "",
    ]


def _child_env(**extra):
    env = {
        name: value
        for name, value in os.environ.items()
        if "PASSWORD" not in name.upper()
    }
    env.update(extra)
    return env


def _read_result(result_path):
    assert Path(result_path).exists(), "the probe exited before reporting"
    return json.loads(Path(result_path).read_text())


def _run_as_a_script(argv, stdin_text, env):
    """Run ``argv`` with a real pipe for stdin, holding ``stdin_text``."""
    completed = subprocess.run(
        argv,
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=120,
    )
    print("stdout:", completed.stdout)
    print("stderr:", completed.stderr)
    return completed


class TestOnATerminal:
    """stdin is a tty: the environment is "cli" and the prompt is getpass."""

    def test_the_typed_password_is_returned_and_never_echoed(self, tmp_path):
        result_path = tmp_path / "result.json"

        displayed, status = run_on_a_terminal(
            _probe_argv(result_path), [(PROMPT, "s3cret-typed\n")], _child_env()
        )

        assert status == 0
        assert _read_result(result_path) == {
            "environment": "cli",
            "password": "s3cret-typed",
        }
        assert PROMPT in displayed
        assert "s3cret-typed" not in displayed, "getpass left echo on"

    def test_control_c_at_the_prompt_is_no_password_not_a_traceback(self, tmp_path):
        result_path = tmp_path / "result.json"

        displayed, status = run_on_a_terminal(
            _probe_argv(result_path), [(PROMPT, "\x03")], _child_env()
        )

        assert status == 0, displayed
        assert _read_result(result_path)["password"] is None
        assert "Traceback" not in displayed

    def test_control_d_at_the_prompt_is_no_password_not_a_traceback(self, tmp_path):
        result_path = tmp_path / "result.json"

        displayed, status = run_on_a_terminal(
            _probe_argv(result_path), [(PROMPT, "\x04")], _child_env()
        )

        assert status == 0, displayed
        assert _read_result(result_path)["password"] is None
        assert "Traceback" not in displayed


class TestAsAScript:
    """stdin is a pipe: the environment is "script" and the prompt is input()."""

    def test_a_line_on_stdin_is_the_password(self, tmp_path):
        result_path = tmp_path / "result.json"

        completed = _run_as_a_script(
            _probe_argv(result_path), "s3cret-piped\n", _child_env()
        )

        assert completed.returncode == 0
        assert _read_result(result_path) == {
            "environment": "script",
            "password": "s3cret-piped",
        }
        assert PROMPT in completed.stdout

    def test_closed_stdin_is_no_password_not_a_traceback(self, tmp_path):
        result_path = tmp_path / "result.json"

        completed = _run_as_a_script(_probe_argv(result_path), "", _child_env())

        assert completed.returncode == 0
        assert _read_result(result_path)["password"] is None
        assert "Traceback" not in completed.stderr

    def test_an_interrupt_at_the_prompt_is_no_password_not_a_traceback(self, tmp_path):
        result_path = tmp_path / "result.json"
        child = subprocess.Popen(
            _probe_argv(result_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_env(),
            cwd=REPO_ROOT,
        )
        try:
            shown = b""
            while PROMPT.encode() not in shown:
                byte = child.stdout.read(1)
                assert byte, f"the prompt never appeared: {shown!r}"
                shown += byte
            child.send_signal(signal.SIGINT)
            _, stderr = child.communicate(timeout=60)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
        print("stderr:", stderr)

        assert child.returncode == 0
        assert _read_result(result_path)["password"] is None
        assert b"Traceback" not in stderr


class TestInANotebookKernel:
    """``ipykernel`` has been imported: the environment is "notebook"."""

    def test_with_no_display_and_no_frontend_there_is_no_password(self, tmp_path):
        """Neither notebook prompt can answer here, and neither one hangs.

        With tkinter missing the dialog falls through to the ipywidgets
        prompt, which builds its widgets and returns without waiting for
        anyone to press Submit. So the honest outcome in a kernel that has
        no display is ``None``, promptly -- not a blocked process.
        """
        pytest.importorskip("ipykernel")
        result_path = tmp_path / "result.json"

        completed = _run_as_a_script(
            _probe_argv(result_path, preload="ipykernel"),
            "this-must-not-be-read-as-a-password\n",
            _child_env(),
        )

        assert completed.returncode == 0
        assert _read_result(result_path) == {
            "environment": "notebook",
            "password": None,
        }

    def test_importing_clustrix_does_not_make_a_script_look_like_a_notebook(
        self, tmp_path
    ):
        """The control for the test above: the preload is what changed it."""
        result_path = tmp_path / "result.json"

        _run_as_a_script(_probe_argv(result_path), "", _child_env())

        assert _read_result(result_path)["environment"] == "script"


class TestThePasswordInTheEnvironment:
    """The environment is asked before any person is."""

    def test_a_variable_naming_the_host_is_used_without_prompting(self, tmp_path):
        result_path = tmp_path / "result.json"

        completed = _run_as_a_script(
            _probe_argv(result_path),
            "typed-at-the-prompt\n",
            _child_env(CLUSTRIX_PASSWORD_CLUSTER_EXAMPLE_COM="from-the-environment"),
        )

        assert _read_result(result_path)["password"] == "from-the-environment"
        assert PROMPT not in completed.stdout

    @pytest.mark.parametrize(
        "variable",
        ["CLUSTER_PASSWORD_CLUSTER_EXAMPLE_COM", "CLUSTER_EXAMPLE_COM_PASSWORD"],
    )
    def test_the_other_host_named_spellings_work_too(self, tmp_path, variable):
        result_path = tmp_path / "result.json"

        _run_as_a_script(
            _probe_argv(result_path),
            "",
            _child_env(**{variable: "from-the-environment"}),
        )

        assert _read_result(result_path)["password"] == "from-the-environment"

    def test_a_variable_naming_another_host_is_not_used(self, tmp_path):
        result_path = tmp_path / "result.json"

        _run_as_a_script(
            _probe_argv(result_path),
            "",
            _child_env(CLUSTRIX_PASSWORD_OTHER_EXAMPLE_COM="someone-elses"),
        )

        assert _read_result(result_path)["password"] is None

    @pytest.mark.parametrize(
        "variable", ["CLUSTRIX_DEFAULT_PASSWORD", "CLUSTER_PASSWORD"]
    )
    def test_a_hostless_variable_is_refused_when_nobody_chose_the_host(
        self, tmp_path, variable
    ):
        """It names no host, so it goes only to a host the user chose.

        With nothing recording who chose ``cluster.example.com`` the variable
        is withheld and the person is asked instead -- and told why, with the
        name of the variable that *would* work.
        """
        result_path = tmp_path / "result.json"

        completed = _run_as_a_script(
            _probe_argv(result_path),
            "typed-at-the-prompt\n",
            _child_env(**{variable: "the-default-password"}),
        )

        assert _read_result(result_path)["password"] == "typed-at-the-prompt"
        assert f"${variable} was not offered" in completed.stderr
        assert "CLUSTRIX_PASSWORD_CLUSTER_EXAMPLE_COM" in completed.stderr
        assert "the-default-password" not in completed.stderr

    def test_a_hostless_variable_is_used_for_a_host_the_user_configured(self, tmp_path):
        result_path = tmp_path / "result.json"

        completed = _run_as_a_script(
            _probe_argv(result_path, with_config=True),
            "typed-at-the-prompt\n",
            _child_env(CLUSTRIX_DEFAULT_PASSWORD="the-default-password"),
        )

        assert _read_result(result_path)["password"] == "the-default-password"
        assert PROMPT not in completed.stdout


class TestRequiresPasswordFallback:
    """``requires_password_fallback`` reads the dict ``setup_ssh_keys`` returns."""

    @pytest.mark.parametrize(
        "key_setup_result, expected",
        [
            ({"success": True, "connection_tested": True, "error": None}, False),
            ({"success": True, "connection_tested": True}, False),
            ({"success": True, "connection_tested": True, "error": "disk full"}, False),
            ({"success": False, "connection_tested": True}, True),
            ({"connection_tested": True}, True),
            ({"success": True, "connection_tested": False}, True),
            ({"success": True}, True),
            (
                {
                    "success": True,
                    "connection_tested": True,
                    "error": "Permission denied (publickey)",
                },
                True,
            ),
            (
                {"success": True, "connection_tested": True, "error": "Invalid KEY"},
                True,
            ),
            (
                {
                    "success": True,
                    "connection_tested": True,
                    "error": "Authentication failed",
                },
                True,
            ),
            (
                {"success": True, "connection_tested": True, "error": "GSSAPI error"},
                True,
            ),
            (
                {
                    "success": True,
                    "connection_tested": True,
                    "error": "Kerberos ticket expired",
                },
                True,
            ),
        ],
    )
    def test_each_outcome_of_a_key_setup(self, key_setup_result, expected):
        assert requires_password_fallback(key_setup_result) is expected

    def test_a_real_successful_key_setup_needs_no_fallback(self, ssh_server):
        """The dict the real function returns, not one written for the test."""
        result = setup_ssh_keys(_config(ssh_server), password=SSH_PASSWORD)

        print(result)
        assert result["success"]
        assert requires_password_fallback(result) is False

    def test_a_real_failed_key_setup_needs_the_fallback(self, ssh_server):
        result = setup_ssh_keys(_config(ssh_server), password="not-the-password")

        print(result)
        assert not result["success"]
        assert requires_password_fallback(result) is True


SSH_PASSWORD = "clustrix-test-password"


@pytest.fixture
def ssh_server(tmp_path):
    root = tmp_path / "remote"
    root.mkdir()
    with LocalSSHServer(root=root, password=SSH_PASSWORD) as server:
        server.root_path = root
        yield server


def _config(server):
    return ClusterConfig(
        cluster_host=server.host,
        cluster_port=server.port,
        username="testuser",
        ssh_host_key_policy="auto_add",
    )


def _host_variable(server):
    return "CLUSTRIX_PASSWORD_" + server.host.upper().replace(".", "_")


class TestSetupAuthWithFallback:
    """The real ``setup_ssh_keys``, given whatever password the fallback finds."""

    def test_a_working_password_is_used_as_given(self, ssh_server, monkeypatch):
        monkeypatch.setenv(_host_variable(ssh_server), "would-fail-if-it-were-used")

        result = setup_auth_with_fallback(
            _config(ssh_server), setup_ssh_keys, password=SSH_PASSWORD
        )

        print(result)
        assert result["success"] and result["connection_tested"]
        assert result["key_deployed"]
        assert "used_password_fallback" not in result["details"]
        assert ("testuser", "publickey") in ssh_server.authentications
        deployed = (ssh_server.root_path / ".ssh" / "authorized_keys").read_text()
        assert Path(f"{result['key_path']}.pub").read_text().split()[1] in deployed

    def test_with_no_password_given_the_environment_supplies_one(
        self, ssh_server, monkeypatch
    ):
        monkeypatch.setenv(_host_variable(ssh_server), SSH_PASSWORD)

        result = setup_ssh_keys_with_fallback(_config(ssh_server))

        print(result)
        assert result["success"] and result["connection_tested"]
        assert result["details"]["used_password_fallback"] is True
        assert ("testuser", "password") in ssh_server.authentications
        assert ("testuser", "publickey") in ssh_server.authentications

    def test_a_wrong_password_is_retried_with_the_one_from_the_environment(
        self, ssh_server, monkeypatch
    ):
        monkeypatch.setenv(_host_variable(ssh_server), SSH_PASSWORD)

        result = setup_auth_with_fallback(
            _config(ssh_server), setup_ssh_keys, password="not-the-password"
        )

        print(result)
        assert result["success"] and result["connection_tested"]
        assert result["details"]["used_password_fallback"] is True

    def test_a_wrong_fallback_password_is_reported_as_a_failure(
        self, ssh_server, monkeypatch
    ):
        monkeypatch.setenv(_host_variable(ssh_server), "also-not-the-password")

        result = setup_ssh_keys_with_fallback(_config(ssh_server))

        print(result)
        assert not result["success"]
        assert "Failed to deploy public key" in result["error"]
        assert result["details"]["used_password_fallback"] is True
        assert not (ssh_server.root_path / ".ssh" / "authorized_keys").exists()

    def test_a_config_naming_no_host_has_nobody_to_ask_a_password_for(self):
        result = setup_ssh_keys_with_fallback(ClusterConfig(username="someone"))

        print(result)
        assert result["success"] is False
        assert "no credential target exists" in result["error"]
        assert result["details"] == {
            "fallback_attempted": True,
            "fallback_available": False,
        }

    def test_with_no_password_anywhere_it_says_so_and_connects_to_nothing(
        self, tmp_path
    ):
        """No argument, no variable, and stdin at end-of-file.

        Run as a child process because the last resort is a prompt, and the
        prompt has to meet a real closed stdin.
        """
        script = (
            "import json\n"
            "from clustrix.config import ClusterConfig\n"
            "from clustrix.ssh_utils import setup_ssh_keys_with_fallback\n"
            f"config = ClusterConfig(cluster_host={HOST!r}, username={USER!r})\n"
            "print('RESULT ' + json.dumps(setup_ssh_keys_with_fallback(config)))\n"
        )

        completed = _run_as_a_script([sys.executable, "-c", script], "", _child_env())

        assert completed.returncode == 0
        line = [x for x in completed.stdout.splitlines() if "RESULT " in x][-1]
        assert json.loads(line.split("RESULT ", 1)[1]) == {
            "success": False,
            "error": "SSH key setup failed and no password fallback available",
            "details": {"fallback_attempted": True, "fallback_available": False},
        }
        assert not list((Path(os.environ["HOME"]) / ".ssh").glob("id_*"))
