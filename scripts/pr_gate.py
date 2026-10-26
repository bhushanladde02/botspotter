#!/usr/bin/env python3
"""Merge gate: refuse a pull request that looks fishy, before a human spends time on it.

Runs in CI on every PR (deploy/github/workflows/gate.yml) and locally via scripts/mr.sh.
It is deterministic and explainable: every finding names the file, the line and the
rule, so the fix is obvious. It is NOT a substitute for the owner's review — it is the
tripwire that fails fast and notifies.

    python scripts/pr_gate.py origin/main            # diff this branch against main
    python scripts/pr_gate.py <base-sha> <head-sha>  # what CI runs

Exit 0 = clean, 1 = blocking findings (CI fails), 2 = could not diff.

Rules (B = block, W = warn — warnings never fail, they are listed for the reviewer):
  B secret-like strings (keys, tokens, private keys, DSNs with passwords)
  B files that must never be committed (private/, .env, *.pem, *.key, *.tgz, .sync/)
  B destructive operations in code/scripts/compose (FLUSHDB/FLUSHALL, DROP TABLE/DATABASE,
    TRUNCATE, `docker compose down -v`, `rm -rf /`, remove_retention_policy without a re-add,
    retention shortened, git push --force to main)
  B rotation of identity secrets (BS_NAMING_SALT, BS_VISITOR_SECRET defaults changed)
  B the approval gates themselves weakened (ruleset bypass widened, CODEOWNERS loosened,
    `environment: production` removed from deploy, apply-gates.sh neutered)
  B remote code execution patterns (curl|sh, eval(, exec(, pickle.loads, subprocess shell=True on input)
  B new outbound network destinations in app code (hosts other than the known allow-list)
  W workflow files changed, Dockerfile base image changed, dependencies added/changed
  W privacy-relevant text or storage changed (terms/privacy wording, models.PortalVisit, enrich.portal_visit)
  W large deletions (> 300 lines removed in one file) or binary files added
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field

BLOCK_PATHS = re.compile(r"^(private/|\.sync/|\.env($|\.)|.*\.(pem|key|p12|pfx|tgz|tar\.gz|zip)$|.*id_rsa|.*id_ed25519)")
ALLOWED_HOSTS = ("botspotter.app", "github.com", "api.github.com", "openfreemap.org", "tiles.openfreemap.org",
                 "cdn.jsdelivr.net", "unpkg.com", "fonts.googleapis.com", "fonts.gstatic.com", "web.archive.org",
                 "search.google.com", "www.w3.org", "schema.org", "creativecommons.org", "example.org", "example.com",
                 "localhost", "127.0.0.1", "redis", "redpanda", "postgres", "api", "ingest", "edge")

SECRET_RES = [
    ("private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Cloudflare/Stripe-style token", re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{16,}\b")),
    ("DSN with password", re.compile(r"[a-z]+://[^:/\s]+:[^@/\s]{4,}@[^\s]+")),
    ("hex secret assigned", re.compile(r"(?i)\b(BS_ADMIN_KEY|BS_NAMING_SALT|BS_VISITOR_SECRET|PG_PASSWORD|BS_SENSOR_KEYS|VM_SSH_KEY)\s*[=:]\s*['\"]?[0-9a-f]{24,}")),
    ("generic secret assigned", re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[=:]\s*['\"][A-Za-z0-9+/=_\-]{20,}['\"]")),
]
DESTRUCTIVE_RES = [
    ("Redis flush", re.compile(r"\bFLUSH(DB|ALL)\b", re.I)),
    ("DROP TABLE/DATABASE", re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", re.I)),
    ("TRUNCATE", re.compile(r"\bTRUNCATE\s+(TABLE\s+)?\w", re.I)),
    ("compose down -v (deletes volumes)", re.compile(r"(compose|COMPOSE\)?)\s+down\b[^\n]*(\s-v\b|--volumes)")),
    ("rm -rf on root/home", re.compile(r"\brm\s+-rf?\s+(/|~|\$HOME)(\s|$|/\*)")),
    ("force push", re.compile(r"git\s+push[^\n]*(--force|-f)\b")),
    ("DELETE without WHERE", re.compile(r"\bDELETE\s+FROM\s+\w+\s*;", re.I)),
]
RCE_RES = [
    ("curl | sh", re.compile(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b")),
    ("eval()", re.compile(r"(?<![\w.])eval\(")),
    ("exec()", re.compile(r"(?<![\w.])exec\(")),
    ("pickle.loads", re.compile(r"\bpickle\.loads?\(")),
    ("shell=True", re.compile(r"shell\s*=\s*True")),
    ("yaml.load without Loader", re.compile(r"\byaml\.load\((?![^)]*Loader)")),
]
SALT_RE = re.compile(r'^\+.*(naming_salt|visitor_secret).*_env\("BS_(NAMING_SALT|VISITOR_SECRET)",\s*"(?!botspotter-v1"|change-me-in-prod")')
RETENTION_RE = re.compile(r"^\+.*add_retention_policy\([^,]+,\s*INTERVAL\s*'(\d+)\s*(day|days|hour|hours)'", re.I)
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")
CODE_EXT = (".py", ".sh", ".sql", ".yml", ".yaml", ".toml", ".json", ".js", ".html", ".conf", ".env.example", "Dockerfile", ".cfg", ".ini")

PRIVACY_FILES = ("botspotter/models.py", "botspotter/enrich.py", "botspotter/ingest/app.py", "docs/privacy.rst", "web/index.html")
GATE_FILES = (".github/CODEOWNERS", "deploy/github/main-ruleset.json", "deploy/github/apply-gates.sh",
              ".github/workflows/deploy.yml", ".github/workflows/gate.yml", "deploy/github/workflows/gate.yml",
              "scripts/pr_gate.py", "tests/test_pr_gate.py")
# The gate's own source and tests contain the dangerous patterns *as patterns*; content
# rules would flag themselves. They are gate files (label required) and still get the
# secret scan, but not the destructive/RCE/host rules.
SELF_FILES = ("scripts/pr_gate.py", "tests/test_pr_gate.py")


@dataclass
class Finding:
    level: str  # B or W
    rule: str
    path: str
    line: int
    text: str

    def fmt(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{'BLOCK' if self.level == 'B' else 'warn '}] {self.rule} — {where}: {self.text.strip()[:140]}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, rule: str, path: str, line: int, text: str) -> None:
        self.findings.append(Finding(level, rule, path, line, text))

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "B"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "W"]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def parse_diff(diff: str) -> dict[str, list[tuple[int, str]]]:
    """path -> [(new line number, added line text)]; also records deletions count via '-' marker lines."""
    files: dict[str, list[tuple[int, str]]] = {}
    path, n = None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            files.setdefault(path, [])
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            n = int(m.group(1)) - 1 if m else 0
        elif path is None:
            continue
        elif raw.startswith("+"):
            n += 1
            files[path].append((n, raw[1:]))
        elif raw.startswith("-"):
            files[path].append((0, raw))  # deletion marker (line 0)
        else:
            n += 1
    return files


def scan(diff: str, numstat: str, names: list[str]) -> Report:
    rep = Report()
    files = parse_diff(diff)
    for path in names:
        if BLOCK_PATHS.match(path):
            rep.add("B", "forbidden file committed", path, 0, "this path must never be in git")
        if path.startswith(".github/workflows/"):
            rep.add("W", "workflow changed", path, 0, "CI/CD definition changed — read it line by line")
        if path == "Dockerfile":
            rep.add("W", "Dockerfile changed", path, 0, "check the base image and pip install line")
        if path in ("pyproject.toml", "requirements.txt"):
            rep.add("W", "dependencies changed", path, 0, "new or changed packages — verify names (typosquatting) and pins")
        if path in PRIVACY_FILES:
            rep.add("W", "privacy-relevant change", path, 0, "what is stored or promised about humans may have changed — keep /terms, privacy.rst and code in sync")
        if path in GATE_FILES:
            rep.add("B", "approval gate touched", path, 0, "changes to the merge/deploy gates need an explicit owner decision — re-run with GATE_OK=1 after reading it")
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added, deleted, path = parts
            if added == "-" and deleted == "-":
                rep.add("W", "binary file", path, 0, "binary added/changed — confirm it is yours and licensed")
            elif deleted.isdigit() and int(deleted) > 300:
                rep.add("W", "large deletion", path, 0, f"{deleted} lines removed")
    for path, lines in files.items():
        is_code = path.endswith(CODE_EXT) or "/" not in path
        for n, text in lines:
            if n == 0:
                continue
            for rule, rx in SECRET_RES:
                if rx.search(text) and "example" not in text.lower() and "<" not in text:
                    rep.add("B", f"secret: {rule}", path, n, text)
            if is_code and path not in SELF_FILES:
                for rule, rx in DESTRUCTIVE_RES:
                    if rx.search(text) and not text.lstrip().startswith(("#", "//", "--", "*", "-  ", ".. ")):
                        rep.add("B", f"destructive: {rule}", path, n, text)
                if path.endswith((".py", ".sh", ".yml", ".yaml", "Dockerfile")):
                    for rule, rx in RCE_RES:
                        if rx.search(text) and not text.lstrip().startswith(("#", "//")):
                            rep.add("B", f"code execution: {rule}", path, n, text)
                if path == "botspotter/config.py" and SALT_RE.match("+" + text):
                    rep.add("B", "identity secret default changed", path, n, "BS_NAMING_SALT / BS_VISITOR_SECRET defaults must never change — every bot would be renamed")
                m = RETENTION_RE.match("+" + text)
                if m:
                    days = int(m.group(1)) / (24 if m.group(2).lower().startswith("hour") else 1)
                    if days < 90:
                        rep.add("B", "retention shortened", path, n, text)
                if path.startswith("botspotter/") and path.endswith(".py"):
                    for host in URL_RE.findall(text):
                        if not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
                            rep.add("B", "new outbound host in app code", path, n, host)
            if path not in SELF_FILES and "remove_retention_policy" in text and "add_retention_policy" not in diff:
                rep.add("B", "destructive: retention removed without re-add", path, n, text)
    return rep


def main(argv: list[str]) -> int:
    import os

    if len(argv) == 2:
        base, head = argv[1], "HEAD"
    elif len(argv) == 3:
        base, head = argv[1], argv[2]
    else:
        base, head = "origin/main", "HEAD"
    try:
        rng = f"{base}...{head}"
        diff = git("diff", "--no-color", "--unified=0", rng)
        numstat = git("diff", "--numstat", rng)
        names = [x for x in git("diff", "--name-only", rng).splitlines() if x]
    except subprocess.CalledProcessError as e:
        print(f"pr_gate: cannot diff {base}...{head}: {e.stderr.strip()}")
        return 2
    rep = scan(diff, numstat, names)
    if os.environ.get("GATE_OK") == "1":
        for f in rep.findings:
            if f.rule == "approval gate touched":
                f.level = "W"
    print(f"pr_gate: {len(names)} files changed against {base}")
    for f in rep.findings:
        print("  " + f.fmt())
    if rep.blocking:
        print(f"\nBLOCKED: {len(rep.blocking)} finding(s). Fix them and push again, or open a new MR with the change explained.")
        return 1
    print(f"\nclean ({len(rep.warnings)} warning(s) for the reviewer)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
