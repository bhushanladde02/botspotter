"""Data files that are refreshed on the server outside git (crawler IP ranges,
sensor locations, GeoIP databases). A file in BS_RUNTIME_DIR overrides the
packaged copy in botspotter/data/; the packaged copy is the fallback so tests and
the demo run with no setup."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .config import settings


def read_text(name: str) -> str:
    if settings.runtime_dir:
        p = Path(settings.runtime_dir) / name
        if p.exists():
            return p.read_text()
    return resources.files("botspotter.data").joinpath(name).read_text()


def read_json(name: str, default=None):
    try:
        return json.loads(read_text(name))
    except Exception:
        return default if default is not None else {}
