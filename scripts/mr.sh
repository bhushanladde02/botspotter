#!/usr/bin/env bash
# The whole merge-request flow in one command (docs/cicd.rst "The flow"):
#   checkout feature branch -> run tests + gate locally -> commit -> push -> open the PR
#   -> CI (ci, guard, gate) runs -> owner reviews -> merge -> deploy waits for approval.
#
#   scripts/mr.sh <branch> "<commit message>"       # from a dirty tree on main or on <branch>
#
# Refuses to commit on main, refuses if tests or the gate fail, never force-pushes.
set -euo pipefail
BR="${1:?branch name, e.g. feat/admin-ledger}"; MSG="${2:?commit message}"
cd "$(git rev-parse --show-toplevel)"
cur=$(git branch --show-current)
if [ "$cur" = "main" ]; then
  git fetch -q origin main && git switch -q -c "$BR" 2>/dev/null || git switch -q "$BR"
elif [ "$cur" != "$BR" ]; then echo "on '$cur', expected main or '$BR'"; exit 1; fi
echo "== tests";  python -m pytest -q
echo "== commit"; git add -A; git commit -q -m "$MSG" || echo "nothing new to commit"
echo "== gate";   git fetch -q origin main
python scripts/pr_gate.py origin/main HEAD || { echo "gate blocked: fix, then 'git commit --amend --no-edit' and re-run"; exit 1; }
echo "== push"; git push -q -u origin "$BR"
echo "== merge request"
if gh pr view "$BR" >/dev/null 2>&1; then gh pr view "$BR" --web
else
  BODY=$(mktemp)
  {
    echo "## What"; echo "$MSG"; echo
    echo "## Checks"
    echo "- [x] pytest green locally"
    echo "- [x] scripts/pr_gate.py clean locally"
    echo "- [ ] docs updated in the same change (runbook / ops log / api / privacy as applicable)"
    echo "- [ ] no secrets, no private/ files, no deletions of prod data"; echo
    echo "## Deploy"
    echo 'Merging triggers `deploy`, which pauses at the production environment for the owner'"'"'s approval.'
  } > "$BODY"
  gh pr create --base main --head "$BR" --title "$MSG" --body-file "$BODY" --web
fi
echo "next: review the diff, wait for ci/guard/gate, Merge; then Actions → deploy → Review deployments → Approve"
