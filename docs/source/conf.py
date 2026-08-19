# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys

sys.path.insert(0, os.path.abspath("../../"))

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "Clustrix"
copyright = "2025, Contextual Dynamics Laboratory"
author = "Contextual Dynamics Laboratory"
release = "0.2.0"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "nbsphinx",
    "sphinx_wagtail_theme",
]

# Add theme to HTML path
import sphinx_wagtail_theme

html_theme_path = [sphinx_wagtail_theme.get_html_theme_path()]

# sphinx_autodoc_typehints resolves annotations across a documented class's
# whole MRO. ``clustrix.notebook_magic.ClusterfyMagics`` subclasses IPython's
# ``Magics``, which annotates ``shell: InteractiveShell`` behind a
# ``TYPE_CHECKING`` guard -- so the name is genuinely absent from
# ``IPython.core.magic`` at runtime and ``typing.get_type_hints()`` cannot
# evaluate it. That produces a ``forward_reference`` warning, which is fatal
# under ``sphinx-build -W``. Bind the name so the reference resolves, rather
# than adding it to ``suppress_warnings`` -- suppressing the category would
# also hide the same warning if clustrix's own code ever developed one.
import IPython.core.magic
from IPython.core.interactiveshell import InteractiveShell

IPython.core.magic.InteractiveShell = InteractiveShell

templates_path = ["_templates"]
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "sphinx_wagtail_theme"
html_static_path = ["_static"]

# Wagtail theme options
html_theme_options = {
    "project_name": "Clustrix",
    "github_url": "https://github.com/ContextLab/clustrix/blob/master/docs/source/",
    "footer_links": "GitHub|PyPI",
}


html_title = "Clustrix Documentation"
html_short_title = "Clustrix"

# Force ReadTheDocs rebuild
html_last_updated_fmt = "%b %d, %Y"

# Additional context for ReadTheDocs
html_context = {
    "display_github": True,
    "github_user": "ContextLab",
    "github_repo": "clustrix",
    "github_version": "master",
    "conf_py_path": "/docs/source/",
}

# -- Extension configuration -------------------------------------------------

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True

# Autodoc settings
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
}

# Autosummary settings
autosummary_generate = True

# Intersphinx settings
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
}

# nbsphinx settings
nbsphinx_execute = "never"  # Don't execute notebooks during build
nbsphinx_allow_errors = True
nbsphinx_kernel_name = "python3"

# Custom CSS
html_css_files = [
    "custom.css",
]
