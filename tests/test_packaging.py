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
    for f in ("botspotter/data/__init__.py", "botspotter/data/known_bots.json", "botspotter/data/country_centroids.json", "botspotter/data/migrations.sql", "botspotter/data/crawler_ranges.json", "botspotter/data/sensors.json"):
        assert f in names, f"{f} missing from wheel"


def test_migrations_split_keeps_statement_under_header():
    """Every statement in migrations.sql must survive the splitter, including the
    first one under the file's comment header (the one that was lost in prod)."""
    from importlib import resources

    from botspotter.store.postgres import split_sql

    stmts = split_sql(resources.files("botspotter.data").joinpath("migrations.sql").read_text())
    assert stmts and stmts[0].startswith("ALTER TABLE portal_visits ADD COLUMN IF NOT EXISTS vs")
    assert sum("ADD COLUMN IF NOT EXISTS" in x for x in stmts) == 8
    assert not any("--" in x for x in stmts)
    assert split_sql("-- only a comment\n\n-- another\nSELECT 1;\n-- trailing") == ["SELECT 1"]
