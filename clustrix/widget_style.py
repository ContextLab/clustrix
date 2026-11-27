"""Stylesheet for the notebook configuration widget.

Kept apart from ``modern_notebook_widget`` so that module is about what the
widget *does* and this one is about how it looks.

Three rules hold the sheet together:

* **Every colour resolves through a ``--jp-*`` theme token** that JupyterLab
  and Notebook 7 define, with a literal fallback for hosts that do not (VS
  Code, Colab). The widget therefore follows the notebook's light or dark
  theme without a second, hand-maintained palette.
* **Nothing is fetched.** No web fonts, no icon fonts: a cluster login node is
  often air-gapped, and a configuration form has no business phoning out.
* **Layout is CSS, not nested boxes.** Rows are wrapping flex containers, so
  the form reflows in a narrow side panel instead of clipping its fields.

``!important`` appears only where ipywidgets' own stylesheet sets a fixed
width, height or margin on the same element.
"""

STYLESHEET = """
<style>
.clustrix-widget {
    /* Used only where the host defines no --jp-* tokens (VS Code, Colab). */
    --cx-fb-fg: #1f1f1f;
    --cx-fb-bg: #ffffff;
    --cx-fb-sunken: #f3f3f3;
    --cx-fb-border: #a8a8a8;
    --cx-fb-rule: #dedede;
    --cx-fb-accent: #1967d2;

    --cx-fg:        var(--jp-ui-font-color1, var(--cx-fb-fg));
    --cx-bg:        var(--jp-layout-color1, var(--cx-fb-bg));
    --cx-bg-sunken: var(--jp-layout-color2, var(--cx-fb-sunken));
    --cx-border:    var(--jp-border-color1, var(--cx-fb-border));
    --cx-rule:      var(--jp-border-color2, var(--cx-fb-rule));
    --cx-accent:    var(--jp-brand-color1, var(--cx-fb-accent));
    --cx-on-accent: var(--jp-ui-inverse-font-color1, #fff);
    --cx-ok:        var(--jp-success-color1, #2e7d32);
    --cx-err:       var(--jp-error-color1, #d32f2f);
    --cx-warn:      var(--jp-warn-color1, #ef6c00);
    /* Secondary text is mixed from the two theme colours rather than taken
       from --jp-ui-font-color2, which is below 4.5:1 on a sunken panel. */
    --cx-fg-muted:  color-mix(in srgb, var(--cx-fg) 70%, var(--cx-bg));
    --cx-font:      var(--jp-ui-font-family, system-ui, -apple-system,
                    "Segoe UI", Helvetica, Arial, sans-serif);
    --cx-mono:      var(--jp-code-font-family, ui-monospace, SFMono-Regular,
                    Menlo, Consolas, monospace);
    --cx-control-h: 32px;
    --cx-radius:    6px;
    --cx-tint:      color-mix(in srgb, var(--cx-accent) 10%, var(--cx-bg));

    box-sizing: border-box;
    width: 100%;
    max-width: 760px;
    font-family: var(--cx-font);
    font-size: 13px;
    color: var(--cx-fg);
    background: var(--cx-bg);
    border: 1px solid var(--cx-rule);
    border-radius: 10px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, .06), 0 4px 16px rgba(0, 0, 0, .05);
    overflow: hidden;
    container-type: inline-size;
}
@media (prefers-color-scheme: dark) {
    .clustrix-widget {
        --cx-fb-fg: #e6e6e6;
        --cx-fb-bg: #1e1e1e;
        --cx-fb-sunken: #2a2a2a;
        --cx-fb-border: #6b6b6b;
        --cx-fb-rule: #3a3a3a;
        --cx-fb-accent: #6ea8fe;
    }
}
.clustrix-widget *,
.clustrix-widget *::before,
.clustrix-widget *::after { box-sizing: border-box; }

/* ipywidgets gives every widget a 2px margin and every box a min-width;
   both make a row wider than its card. */
.clustrix-widget .jupyter-widgets { margin: 0; min-width: 0; }
.clustrix-widget .widget-html-content { font-family: var(--cx-font); line-height: 1.4; }

/* Header ------------------------------------------------------------- */
.clustrix-widget .cx-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 6px 12px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--cx-rule);
}
.clustrix-widget .cx-brand { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; }
.clustrix-widget .cx-title {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 15px;
    font-weight: 650;
    letter-spacing: -.01em;
}
/* The mark: three nodes, drawn in CSS so nothing is fetched. */
.clustrix-widget .cx-title::before {
    content: "";
    width: 16px;
    height: 16px;
    border-radius: 4px;
    background:
        radial-gradient(circle at 4px 4px, var(--cx-on-accent) 1.6px, transparent 2px),
        radial-gradient(circle at 12px 4px, var(--cx-on-accent) 1.6px, transparent 2px),
        radial-gradient(circle at 8px 12px, var(--cx-on-accent) 1.6px, transparent 2px),
        var(--cx-accent);
}
.clustrix-widget .cx-in-use { font-size: 12px; color: var(--cx-fg-muted); }
.clustrix-widget .cx-in-use b { font-weight: 600; color: var(--cx-fg); }

.clustrix-widget .cx-pill {
    --cx-pill: var(--cx-fg-muted);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
    /* The words stay in the text colour; only the dot and the wash carry
       the state, so the label is readable in either theme. */
    color: var(--cx-fg);
    background: color-mix(in srgb, var(--cx-pill) 16%, var(--cx-bg));
}
.clustrix-widget .cx-pill::before {
    content: "";
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--cx-pill);
}
.clustrix-widget .cx-pill-busy  { --cx-pill: var(--cx-warn); }
.clustrix-widget .cx-pill-ok    { --cx-pill: var(--cx-ok); }
.clustrix-widget .cx-pill-error { --cx-pill: var(--cx-err); }

/* Sections ----------------------------------------------------------- */
.clustrix-widget .cx-body { padding: 4px 20px 20px; }
.clustrix-widget .cx-section { padding-top: 16px; }
.clustrix-widget .cx-section + .cx-section {
    margin-top: 16px;
    border-top: 1px solid var(--cx-rule);
}
.clustrix-widget .cx-heading {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--cx-fg);
}
.clustrix-widget .cx-hint { font-size: 12px; color: var(--cx-fg-muted); }

/* Rows and fields ---------------------------------------------------- */
.clustrix-widget .cx-row {
    display: flex;
    flex-flow: row wrap;
    align-items: flex-end;
    gap: 12px;
    overflow: visible;
}
.clustrix-widget .cx-row + .cx-row,
.clustrix-widget .cx-row + .cx-hint,
.clustrix-widget .cx-hint + .cx-row { margin-top: 12px; }

/* A field with one line of explanation under it, and a row of two such
   fields that are read as a pair: tops aligned, so that explanations of
   different lengths do not push one field below the other. */
.clustrix-widget .cx-stack {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
    min-width: 0;
    overflow: visible;
}
.clustrix-widget .cx-row.cx-pair { align-items: flex-start; }
.clustrix-widget .cx-hint b { font-weight: 600; color: var(--cx-fg); }

/* A field is one ipywidgets control whose own description is its label.
   ipywidgets lays the two out side by side with a fixed-width, truncating
   label; stacking them lets a label be as long as it needs to be and keeps
   the label wired to its input for screen readers. */
.clustrix-widget .cx-field {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    width: auto;
    height: auto;
    overflow: visible;
}
.clustrix-widget .cx-field > .widget-label {
    width: auto !important;
    max-width: 100%;
    min-width: 0;
    height: auto;
    margin: 0 0 4px;
    padding: 0;
    font-size: 12px;
    line-height: 1.3;
    text-align: left;
    color: var(--cx-fg-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.clustrix-widget .cx-field input[type="text"],
.clustrix-widget .cx-field input[type="number"],
.clustrix-widget .cx-field input[type="password"],
.clustrix-widget .cx-field select,
.clustrix-widget .cx-field textarea {
    flex: 0 0 auto;
    width: 100%;
    min-width: 0;
    height: var(--cx-control-h);
    margin: 0;
    padding: 0 10px;
    font-family: var(--cx-font);
    font-size: 13px;
    line-height: normal;
    transition: border-color .12s, box-shadow .12s;
    color: var(--cx-fg);
    background-color: var(--cx-bg);
    border: 1px solid var(--cx-border);
    border-radius: var(--cx-radius);
    box-shadow: none;
}
/* The arrow is drawn from the theme's text colour. ipywidgets' own is a
   fixed dark image, which disappears on a dark background. */
.clustrix-widget .cx-field select {
    padding-right: 28px;
    appearance: none;
    -webkit-appearance: none;
    background-image:
        linear-gradient(45deg, transparent 50%, var(--cx-fg-muted) 50%),
        linear-gradient(135deg, var(--cx-fg-muted) 50%, transparent 50%);
    background-position: calc(100% - 15px) 50%, calc(100% - 10px) 50%;
    background-size: 5px 5px;
    background-repeat: no-repeat;
}
/* A field the last action could not accept. */
.clustrix-widget .cx-field.cx-invalid input,
.clustrix-widget .cx-field.cx-invalid select,
.clustrix-widget .cx-field.cx-invalid textarea {
    border-color: var(--cx-err);
    box-shadow: 0 0 0 1px var(--cx-err);
}
.clustrix-widget .cx-field.cx-invalid > .widget-label { color: var(--cx-err); }
.clustrix-widget .cx-field textarea {
    height: auto;
    padding: 6px 8px;
    font-family: var(--cx-mono);
    font-size: 12px;
    line-height: 1.5;
    resize: vertical;
}
.clustrix-widget .cx-mono input { font-family: var(--cx-mono); font-size: 12px; }
.clustrix-widget .cx-field input::placeholder,
.clustrix-widget .cx-field textarea::placeholder { color: var(--cx-fg-muted); opacity: 1; }
.clustrix-widget .cx-field input:focus,
.clustrix-widget .cx-field select:focus,
.clustrix-widget .cx-field textarea:focus,
.clustrix-widget .jupyter-button:focus-visible {
    outline: none;
    border-color: var(--cx-accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--cx-accent) 25%, transparent);
}

/* Checkboxes carry their label beside the box, not above it. */
.clustrix-widget .cx-check {
    width: auto;
    height: var(--cx-control-h);
    align-items: center;
    overflow: visible;
}
.clustrix-widget .cx-check .widget-label-basic {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--cx-fg);
}
.clustrix-widget .cx-check input[type="checkbox"] { margin: 0; accent-color: var(--cx-accent); }
.clustrix-widget .cx-check input[type="checkbox"]:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--cx-accent) 25%, transparent);
}

/* Buttons ------------------------------------------------------------ */
.clustrix-widget .jupyter-button {
    flex: 0 0 auto;
    width: auto;
    height: var(--cx-control-h);
    padding: 0 14px;
    font-family: var(--cx-font);
    font-size: 13px;
    font-weight: 500;
    line-height: calc(var(--cx-control-h) - 2px);
    transition: background-color .12s, border-color .12s;
    color: var(--cx-fg);
    background-color: var(--cx-bg);
    border: 1px solid var(--cx-border);
    border-radius: var(--cx-radius);
    box-shadow: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.clustrix-widget .jupyter-button:hover:enabled { background-color: var(--cx-bg-sunken); box-shadow: none; }
.clustrix-widget .jupyter-button:disabled { opacity: .55; cursor: default; }
.clustrix-widget .jupyter-button.cx-primary {
    color: var(--cx-on-accent);
    background-color: var(--cx-accent);
    border-color: var(--cx-accent);
    font-weight: 600;
}
.clustrix-widget .jupyter-button.cx-danger { color: var(--cx-err); }
/* Delete, after its first click: the next click is the real one. */
.clustrix-widget .jupyter-button.cx-danger.cx-armed {
    color: #fff;
    background-color: var(--cx-err);
    border-color: var(--cx-err);
}
/* A secret field and the button that forgets what it holds. The button is
   only there while something is held, and the field takes the room. */
.clustrix-widget .cx-secret {
    display: flex;
    flex-flow: row nowrap;
    align-items: flex-end;
    gap: 8px;
    min-width: 0;
    overflow: visible;
}
.clustrix-widget .cx-secret > .cx-field { flex: 1 1 auto; min-width: 0; }
.clustrix-widget .jupyter-button.cx-forget { color: var(--cx-fg-muted); }
/* Buttons that belong together stay on one line when their row wraps. */
.clustrix-widget .cx-group {
    display: flex;
    flex-flow: row nowrap;
    gap: 8px;
    overflow: visible;
}
.clustrix-widget .jupyter-button.cx-primary:hover:enabled {
    background-color: var(--cx-accent);
    filter: brightness(1.1);
}

/* Backend selector: one tile per backend. Each tile's second line is its
   tooltip, drawn with attr(title), so the name and what it means are read
   together and the text has exactly one source. */
.clustrix-widget .cx-backend { width: 100%; height: auto; overflow: visible; }
.clustrix-widget .cx-backend > div {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 8px;
    width: 100%;
}
/* Two by two in a narrow panel, never three and one. */
@container (max-width: 560px) {
    .clustrix-widget .cx-backend > div { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.clustrix-widget .cx-backend .widget-toggle-button {
    display: block;
    width: auto;
    height: auto;
    margin: 0;
    padding: 9px 12px 10px;
    font-size: 13px;
    font-weight: 600;
    line-height: 1.35;
    text-align: left;
    white-space: normal;
}
.clustrix-widget .cx-backend .widget-toggle-button::after {
    content: attr(title);
    display: block;
    margin-top: 1px;
    font-size: 12px;
    font-weight: 400;
    color: var(--cx-fg-muted);
}
.clustrix-widget .cx-backend .widget-toggle-button.mod-active {
    color: var(--cx-fg);
    background-color: var(--cx-tint);
    border-color: var(--cx-accent);
    box-shadow: inset 0 0 0 1px var(--cx-accent);
}

/* Actions ------------------------------------------------------------ */
.clustrix-widget .cx-actions { justify-content: space-between; align-items: center; }
.clustrix-widget .cx-result-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 24px;
    margin: 16px 0 8px;
    overflow: visible;
}
.clustrix-widget .cx-result-bar .cx-heading { margin: 0; }

/* Output -------------------------------------------------------------
   Framed and given a floor height, so an idle widget reads as a console
   waiting for a result rather than as a gap in the layout. */
.clustrix-widget .cx-output {
    min-height: 44px;
    max-height: 360px;
    padding: 10px 12px;
    background: var(--cx-bg-sunken);
    border-left: 3px solid var(--cx-rule);
    border-radius: var(--cx-radius);
    overflow: auto;
}
.clustrix-widget .cx-output.cx-state-busy  { border-left-color: var(--cx-warn); }
.clustrix-widget .cx-output.cx-state-ok    { border-left-color: var(--cx-ok); }
.clustrix-widget .cx-output.cx-state-error { border-left-color: var(--cx-err); }
/* ipywidgets always renders a .jp-OutputArea child, so :empty on the
   container never matches; the area itself is childless until printed to. */
.clustrix-widget .cx-output .jp-OutputArea:empty::before {
    content: "Results of Apply, Test connection and Run test job appear here.";
    font-size: 12px;
    color: var(--cx-fg-muted);
}
.clustrix-widget .cx-output .jp-OutputArea-prompt { display: none; }
.clustrix-widget .cx-output .jp-OutputArea-output,
.clustrix-widget .cx-output .jp-OutputArea-output pre {
    margin: 0;
    padding: 0;
    font-family: var(--cx-mono);
    font-size: 12px;
    line-height: 1.6;
    color: var(--cx-fg);
    background: transparent;
    white-space: pre-wrap;
    word-break: break-word;
}
</style>
"""
