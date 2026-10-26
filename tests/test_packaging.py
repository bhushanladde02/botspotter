"""The Docker image installs the package non-editably. Everything the services
need at runtime must therefore be inside the wheel — on 2026-10-26 the
aggregator crash-looped in production because botspotter/data/ (no
__init__.py) was never shipped and importlib.resources could not find it."""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_ships_data_files(tmp_path):
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-q", "-w", str(tmp_path)],
                   check=True, capture_output=True)
    wheel = next(tmp_path.glob("botspotter-*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    for f in ("botspotter/data/__init__.py", "botspotter/data/known_bots.json", "botspotter/data/country_centroids.json"):
        assert f in names, f"{f} missing from wheel"
