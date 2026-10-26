#!/usr/bin/env bash
# One-shot: enforce the two approval gates on GitHub. Idempotent (re-run to update).
#   bash deploy/github/apply-gates.sh            (needs: gh auth login, repo admin)
# 1. Ruleset on main: PR + 1 approval from the code owner + green ci/guard; only the
#    repository admin (the owner) may bypass — needed to merge his own PRs, since
#    GitHub never lets an author approve their own PR.
# 2. "production" environment: the owner is a required reviewer; only main may deploy.
# Docs: docs/cicd.rst "Approval gates".
set -euo pipefail
REPO="${REPO:-bhushanladde02/botspotter}"
OWNER="${REPO%%/*}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== ruleset on main"
existing=$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name=="main: owner approval required") | .id' || true)
if [ -n "$existing" ]; then
  gh api -X PUT "repos/$REPO/rulesets/$existing" --input "$HERE/main-ruleset.json" --jq '.name + " (updated)"'
else
  gh api -X POST "repos/$REPO/rulesets" --input "$HERE/main-ruleset.json" --jq '.name + " (created)"'
fi

echo "== production environment: required reviewer = $OWNER, branch policy = main only"
uid=$(gh api "users/$OWNER" --jq .id)
gh api -X PUT "repos/$REPO/environments/production" \
  --input - <<JSON --jq '.name + ": reviewers set"'
{ "reviewers": [ { "type": "User", "id": $uid } ],
  "prevent_self_review": false,
  "deployment_branch_policy": { "protected_branches": false, "custom_branch_policies": true } }
JSON
gh api -X POST "repos/$REPO/environments/production/deployment-branch-policies" \
  -f name=main -f type=branch --jq '.name + " allowed"' 2>/dev/null || echo "main already allowed"

echo "== actions hardening"
gh api -X PUT "repos/$REPO/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false >/dev/null && echo "workflow token: read-only"
gh api -X PUT "repos/$REPO/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors >/dev/null 2>&1 \
  && echo "fork PR workflows: every outside contributor needs approval to run CI" \
  || echo "fork PR approval: set it in Settings → Actions → General → 'Require approval for all external contributors'"

echo
echo "verify:  gh api repos/$REPO/rulesets --jq '.[].name'"
echo "         gh api repos/$REPO/environments/production --jq '.protection_rules'"
