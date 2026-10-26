"""Sphinx configuration for BotSpotter. Build: sphinx-build -b html docs docs/_build/html"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(".."))
import botspotter  # noqa: E402

project = "BotSpotter"
author = "Bhushan Ladde"
copyright = f"{date.today().year}, {author}"
release = botspotter.__version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
]
myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
myst_heading_anchors = 3
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
autodoc_default_options = {"members": True, "undoc-members": True, "show-inheritance": True}
autodoc_mock_imports = ["aiokafka", "asyncpg", "maxminddb", "django"]
autosummary_generate = True
suppress_warnings = ["sphinx_autodoc_typehints.forward_reference"]

html_theme = "furo"
html_title = "BotSpotter"
html_static_path = ["_static"]
html_theme_options = {
    "light_css_variables": {"color-brand-primary": "#4a7d00", "color-brand-content": "#4a7d00"},
    "dark_css_variables": {"color-brand-primary": "#c6ff4d", "color-brand-content": "#c6ff4d"},
    "source_repository": "https://github.com/bhushanladde02/botspotter",
    "source_branch": "main",
    "source_directory": "docs/",
}
