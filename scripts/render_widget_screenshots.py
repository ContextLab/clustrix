#!/usr/bin/env python3
"""Regenerate the widget screenshots in ``docs/source/_static/widget/``.

The pictures are taken from a real Jupyter session: this script starts a
Jupyter server with a throwaway home directory, opens a notebook that
displays ``ModernClustrixWidget`` in headless Chromium, presses the widget's
own buttons, and photographs the cell output. Nothing is drawn or simulated,
and nothing personal can appear, because the server never sees the
developer's home directory, profiles or keys.

    python scripts/render_widget_screenshots.py [OUT_DIR [HOME_DIR]]

``HOME_DIR`` is the home directory the Jupyter server is given; a temporary
one is made when it is left out. Its path is visible in one field (the SSH
key file), so name a neutral one when the pictures are for publication.

Requirements: the ``widget`` extra (Jupyter, ipywidgets), and Node with the
``playwright`` package and its Chromium (``npx playwright install chromium``).

Half of the pictures need a cluster to talk to -- the unknown-host
fingerprint, a test job, the applied state. They are taken when the test
cluster of ``scripts/setup_local_slurm.sh`` is named in the environment::

    export CLUSTRIX_TEST_SSH_HOST=127.0.0.1 CLUSTRIX_TEST_SSH_PORT=2222 \\
           CLUSTRIX_TEST_SSH_USER=clx \\
           CLUSTRIX_TEST_SSH_KEY=/tmp/clustrix-local-cluster/id_ed25519

Without those variables only the pictures that need no connection are taken
(first-run, huggingface, validation, advanced) and the script says so.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "docs" / "source" / "_static" / "widget"

FIRST_RUN = """\
from clustrix.modern_notebook_widget import ModernClustrixWidget

widget = ModernClustrixWidget()
widget.display()
"""

# The form is filled in from Python, which is what typing into it does: each
# assignment is an edit to the draft of the selected profile.
CLUSTER = """\
from clustrix.modern_notebook_widget import ModernClustrixWidget

widget = ModernClustrixWidget()
fields = widget.widgets
fields["profile_dropdown"].value = "SSH remote machine"
fields["host"].value = {host!r}
fields["port"].value = {port}
fields["username"].value = {user!r}
fields["ssh_key_file"].value = {key!r}
fields["home_dir"].value = "~/clustrix-widget-demo"
fields["cpus"].value = 1
fields["ram"].value = "1GB"
fields["time"].value = "00:10:00"
fields["clone_env"].value = False
widget.display()
"""


def write_notebook(path: Path, source: str) -> None:
    cell = {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }
    notebook = {
        "cells": [cell],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            }
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(notebook))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_server(base: str, server: subprocess.Popen) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("The Jupyter server exited before it was ready.")
        try:
            urllib.request.urlopen(base + "/api", timeout=2).read()
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError("The Jupyter server did not answer within 90 seconds.")


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 2:
        home = Path(sys.argv[2]).resolve()
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
    else:
        home = Path(tempfile.mkdtemp(prefix="clustrix-widget-shots-"))
    notebooks = home / "notebooks"
    notebooks.mkdir()
    # Jupyter follows the browser's light/dark preference, which is how the
    # one dark picture is taken without a second server.
    theme = home / ".jupyter/lab/user-settings/@jupyterlab/apputils-extension"
    theme.mkdir(parents=True)
    (theme / "themes.jupyterlab-settings").write_text('{"adaptive-theme": true}')
    write_notebook(notebooks / "first_run.ipynb", FIRST_RUN)

    host = os.environ.get("CLUSTRIX_TEST_SSH_HOST")
    user = os.environ.get("CLUSTRIX_TEST_SSH_USER")
    key = os.environ.get("CLUSTRIX_TEST_SSH_KEY")
    with_cluster = bool(host and user and key)
    if with_cluster:
        # A copy of the key under the throwaway home, so the form shows a
        # path any reader could have, and an absent known_hosts, so the host
        # is unknown to begin with.
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        shutil.copy(str(key), ssh_dir / "id_ed25519")
        (ssh_dir / "id_ed25519").chmod(0o600)
        write_notebook(
            notebooks / "cluster.ipynb",
            CLUSTER.format(
                host=host,
                port=int(os.environ.get("CLUSTRIX_TEST_SSH_PORT", "22")),
                user=user,
                # Written out in full: a leading ~ in this field is not
                # expanded before the key is opened.
                key=str(ssh_dir / "id_ed25519"),
            ),
        )
    else:
        print(
            "No test cluster named (CLUSTRIX_TEST_SSH_HOST, _USER, _KEY): "
            "taking only the pictures that need no connection."
        )

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    environment = dict(
        os.environ, HOME=str(home), CLUSTRIX_CONFIG_DIR=str(home / ".clustrix")
    )
    command = [
        sys.executable,
        "-m",
        "notebook",
        "--no-browser",
        f"--port={port}",
        "--ServerApp.token=",
        "--ServerApp.password=",
        f"--ServerApp.root_dir={notebooks}",
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command.append("--allow-root")
    log = open(home / "jupyter.log", "w")
    server = subprocess.Popen(
        command, env=environment, stdout=log, stderr=subprocess.STDOUT, cwd=notebooks
    )
    try:
        wait_for_server(base, server)
        result = subprocess.run(
            [
                "node",
                str(Path(__file__).with_suffix(".js")),
                base,
                str(out),
                "1" if with_cluster else "0",
            ]
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
    if result.returncode == 0:
        shutil.rmtree(home, ignore_errors=True)
    else:
        print(f"Failed; the server log is {home / 'jupyter.log'}")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
