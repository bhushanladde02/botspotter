#!/usr/bin/env bash
# Local safety net: refuse to commit private/, .env or key files. Run once per clone.
set -e
cd "$(git rev-parse --show-toplevel)"
cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
bad=$(git diff --cached --name-only | grep -E '^(private/|\.env$|.*\.key$|.*\.pem$)' || true)
if [ -n "$bad" ]; then
  echo "pre-commit: refusing to commit private files:"; echo "$bad"; exit 1
fi
HOOK
chmod +x .git/hooks/pre-commit
echo "pre-commit hook installed"
