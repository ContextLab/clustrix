"""Run a child process on a real pseudo-terminal.

For tests of anything that behaves differently when a person is at a
terminal: ``getpass``, ``input``, ``click.prompt``, ``sys.stdin.isatty()``,
and every OpenSSH tool that asks a question. The child is the session leader
of a real pty, so the terminal is its *controlling* terminal -- ``/dev/tty``
opens, ``^C`` raises ``SIGINT`` and ``^D`` is end-of-file -- which a pipe
with ``isatty`` patched to say yes is not.
"""

import os
import pty
import select
import signal
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_on_a_terminal(argv, dialogue, env, timeout=60, cwd=REPO_ROOT):
    """Run ``argv`` with a real pty as its controlling terminal.

    ``dialogue`` is a list of ``(expected, keys)`` pairs: each time
    ``expected`` appears on the terminal after the previous answer, ``keys``
    are typed. Returns everything the terminal displayed, as text, and the
    exit status. A child still running at ``timeout`` is killed and the test
    fails with what the terminal was showing.
    """
    pid, master = pty.fork()
    if pid == 0:  # pragma: no cover - the child
        try:
            os.chdir(cwd)
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)

    pending = [(expected.encode(), keys.encode()) for expected, keys in dialogue]
    displayed = b""
    answered_up_to = 0
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.5)
            if not readable:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:  # EIO: the child closed its side
                break
            if not chunk:
                break
            displayed += chunk
            while pending and pending[0][0] in displayed[answered_up_to:]:
                expected, keys = pending.pop(0)
                answered_up_to = displayed.index(expected, answered_up_to) + len(
                    expected
                )
                os.write(master, keys)
        else:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            pytest.fail(f"the child never finished; terminal showed {displayed!r}")
    finally:
        os.close(master)
    _, status = os.waitpid(pid, 0)
    text = displayed.decode(errors="replace")
    print("terminal showed:", text)
    return text, os.waitstatus_to_exitcode(status)
