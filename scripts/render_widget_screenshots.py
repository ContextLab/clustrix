"""Render the configuration widget to standalone HTML with fake values.

Usage::

    python scripts/render_widget_screenshots.py OUT.html [light|dark] [advanced|hf]

Then screenshot the page in a browser. The originals were captured by hand in
a live notebook, which is why they carried the author's real cluster hostname,
username, SSH key path and home directory into the published documentation.
Regenerating them from this script keeps that from happening again.

The originals were captured by hand in a live notebook, so they carried the
author's real cluster hostname, username, SSH key path and home directory
straight into the published documentation. This regenerates them from
placeholders instead.
"""

import os
import pathlib
import sys
import tempfile

# Point the widget at a throwaway config dir BEFORE importing clustrix, so it
# cannot pick up (or display the name of) the developer's real profile store.
_tmp = tempfile.mkdtemp(prefix="clustrix-shots-")
os.environ["CLUSTRIX_CONFIG_DIR"] = _tmp

from ipywidgets.embed import embed_minimal_html  # noqa: E402
from clustrix.modern_notebook_widget import ModernClustrixWidget  # noqa: E402

SANITIZED = {
    "cluster_type": "slurm",
    "host": "hpc.example.edu",
    "username": "researcher",
    "cpus": 8,
    "ram": "32GB",
    "time": "02:00:00",
    "home_dir": "~/.clustrix/jobs",
    "ssh_key_file": "~/.ssh/id_ed25519",
    "password_env_var": "MY_CLUSTER_PASSWORD",
}

theme = sys.argv[2] if len(sys.argv) > 2 else "light"
mode = sys.argv[3] if len(sys.argv) > 3 else ""
advanced = mode == "advanced"
hf = mode == "hf"

widget = ModernClustrixWidget()
for key, value in SANITIZED.items():
    if key in widget.widgets:
        try:
            widget.widgets[key].value = value
        except Exception:
            pass
if hf:
    widget.widgets["cluster_type"].value = "huggingface"
    for k, v in (
        ("hf_namespace", "your-org"),
        ("hf_flavor", "cpu-basic"),
        ("cpus", 1),
        ("ram", "16GB"),
        ("time", "01:00:00"),
    ):
        if k in widget.widgets:
            try:
                widget.widgets[k].value = v
            except Exception:
                pass
widget._update_ui_for_cluster_type()
if "pre_exec_commands" in widget.widgets:
    widget.widgets["pre_exec_commands"].value = (
        "source /opt/conda/etc/profile.d/conda.sh"
    )
if advanced and "advanced_section" in widget.widgets:
    widget.widgets["advanced_section"].layout.display = "block"
widget.set_status("ok", "job completed" if hf else "connected")

out = pathlib.Path(sys.argv[1])
embed_minimal_html(str(out), views=[widget.get_widget()], title="Clustrix widget")

# embed_minimal_html captures the widget tree but not the stylesheet, which the
# widget publishes separately via display(HTML(...)). Pull it straight off the
# instance and inline it, plus the handful of JupyterLab theme tokens the sheet
# resolves against, so the standalone page looks like it does in a notebook.
from unittest import mock  # noqa: E402

captured = {}
with mock.patch(
    "clustrix.modern_notebook_widget.display",
    side_effect=lambda obj: captured.setdefault("css", getattr(obj, "data", "")),
):
    widget._inject_css_styles()
css = captured.get("css", "")

DARK = theme == "dark"
tokens = f"""
<style>
:root, .clustrix-widget, .clustrix-widget * {{
  --jp-ui-font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  --jp-ui-font-size1: 13px;
  --jp-ui-font-color1: {'rgba(255,255,255,.87)' if DARK else 'rgba(0,0,0,.87)'};
  --jp-ui-font-color2: {'rgba(255,255,255,.54)' if DARK else 'rgba(0,0,0,.54)'};
  --jp-ui-font-color3: {'rgba(255,255,255,.38)' if DARK else 'rgba(0,0,0,.38)'};
  --jp-layout-color1: {'#111416' if DARK else '#ffffff'};
  --jp-layout-color2: {'#212529' if DARK else '#f5f5f5'};
  --jp-layout-color3: {'#2b3035' if DARK else '#eeeeee'};
  --jp-border-color1: {'#3a4048' if DARK else '#e0e0e0'};
  --jp-border-color2: {'#2b3035' if DARK else '#eeeeee'};
  --jp-brand-color1: #1976d2;
  --jp-success-color1: #2e7d32;
}}
body {{
  background: {'#111416' if DARK else '#ffffff'};
  margin: 0;
  padding: 24px;
  font-family: var(--jp-ui-font-family);
}}
</style>
"""

html = out.read_text()
html = html.replace("</head>", tokens + css + "</head>", 1)
out.write_text(html)
print(f"wrote {out} ({out.stat().st_size} bytes, theme={theme}, advanced={advanced})")
print(f"css inlined: {len(css)} chars")
