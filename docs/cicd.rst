CI/CD
=====

Three GitHub Actions workflows in ``.github/workflows/``:

+----------------+------------------------------------------+------------------------------------------------------------------------------------------+
| workflow       | trigger                                  | does                                                                                     |
+================+==========================================+==========================================================================================+
| ``ci.yml``     | every push and PR                        | ``pytest`` on Python 3.10 and 3.12; multi-arch Docker build check                        |
+----------------+------------------------------------------+------------------------------------------------------------------------------------------+
| ``deploy.yml`` | push to ``main`` (code paths), or manual | tests → SSH to the VM → ``deploy/remote.sh`` → verify ``https://botspotter.app/healthz`` |
+----------------+------------------------------------------+------------------------------------------------------------------------------------------+
| ``docs.yml``   | push to ``main``, or manual              | Sphinx build (``-W``, warnings are errors) → GitHub Pages                                |
+----------------+------------------------------------------+------------------------------------------------------------------------------------------+
| ``jobs.yml``   | daily 06:17 UTC                          | registry sync → opens a PR when new bots are found                                       |
+----------------+------------------------------------------+------------------------------------------------------------------------------------------+

One-time setup
--------------

Deploy secrets
~~~~~~~~~~~~~~

Run once from the repo on your Mac (uses ``gh``):

.. code:: bash

   gh secret set VM_SSH_KEY < ~/.ssh/sharpen_vm
   gh secret set VM_HOST --body 129.213.153.181
   gh secret set VM_USER --body ubuntu

Optionally protect it: repo **Settings → Environments → production** → add yourself as a required reviewer. Deploys then wait for a click.

GitHub Pages
~~~~~~~~~~~~

Repo **Settings → Pages → Source: GitHub Actions**. First ``docs.yml`` run publishes to https://bhushanladde02.github.io/botspotter/.

How a deploy works
------------------

1. ``deploy.yml`` runs the test suite on a GitHub runner.
2. It writes the SSH key from secrets, then streams ``deploy/remote.sh`` to the VM over SSH (``bash -s``), so the VM always runs the script from the commit being deployed.
3. ``remote.sh`` on the VM: ``git fetch/checkout/pull`` → generate ``.env`` on first run → ``docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d --build`` → wait for ``/healthz`` → install/refresh the Caddy drop-in (``caddy-extra/botspotter.caddy``) and reload Caddy only if it changed → **smoke test**: POST a beacon through ingest and assert the snapshot's view count increased (proves ingest → Redpanda → aggregator → Redis → API) → print sensor keys.
4. The runner curls the public ``/healthz`` through Cloudflare.

Zero-downtime: Compose recreates only containers whose image or config changed; Caddy reloads gracefully. Sharpen's containers are never touched.

.. _manual-deploy--rollback:

Manual deploy / rollback
------------------------

.. code:: bash

   deploy/oracle.sh                       # same as CI, from your laptop
   GIT_REF=v0.1.0 deploy/oracle.sh        # deploy a tag or branch (rollback)

Rollback = deploy the previous commit. Data is unaffected (volumes persist).

Safety rails
------------

-  ``concurrency: deploy-prod`` — two pushes can't deploy at once.
-  ``paths-ignore`` — docs-only commits don't redeploy.
-  ``remote.sh`` exits non-zero (and the workflow turns red) if the API doesn't become healthy or the smoke test fails; the previous containers keep serving in the meantime only if the new image failed to *build*. If a new image starts but is unhealthy, redeploy the previous ref.
-  Nothing in CI can read ``.env`` on the VM; secrets are generated there and stay there.

Branch policy (suggested)
-------------------------

Work on branches, open a PR, let ``ci.yml`` pass, merge to ``main`` → auto deploy. Direct pushes to ``main`` also deploy; that's fine for a solo project.

Automatic rollback
------------------

``deploy/remote.sh`` records the revision running before the pull. If the health wait or the smoke test fails, it
checks that revision out again and rebuilds, so a bad push costs a few minutes rather than an outage; the workflow run
is still red and says "rolled back". Fix forward and push again. ``ROLLBACK=0 bash deploy/remote.sh`` disables it when
debugging directly on the VM. A deploy that fails *before* the build step (secrets, clone) changes nothing.

The flow (mandatory, since 2026-10-26)
--------------------------------------

Every change — the owner's included — takes this path. Nothing merges to ``main`` and nothing deploys without the
owner's explicit approval; GitHub enforces it (next section), the scripts make it convenient.

1. **Feature branch**: ``git switch -c feat/<name>`` (never commit on ``main``; the ruleset refuses the push anyway).
2. **Add + commit** the change, with its docs and tests in the same commit.
3. **Merge request**: ``scripts/mr.sh feat/<name> "<message>"`` runs the tests and the gate locally, commits, pushes and
   opens the PR with the checklist body (or ``gh pr create --web`` by hand).
4. **CI must pass thoroughly** — required checks on the PR: ``test (3.10)``/``test (3.12)`` (full pytest), ``rehearse``
   (the deploy rehearsal below), ``no-secrets`` (guard) and ``gate`` (the safety review below). The gate comments its report on the PR; a red check cannot be merged around.
5. **Request to merge → owner notified**: ``.github/CODEOWNERS`` makes the owner the required reviewer of every file,
   so any PR opened by someone else (a contributor, or Claude once the GitHub App is installed on the repo) lands as a
   *review request* in the owner's GitHub notifications and email. A PR the owner opens himself needs no notification.
6. **Review → approve → merge**: the owner reads the diff on the PR page and merges (squash or merge commit; linear
   history). For his *own* PRs GitHub cannot accept his approval (authors can't approve themselves), so the merge box
   shows *Merge without waiting for requirements to be met (bypass rules)* — that checkbox is the owner-only path and
   is logged as a bypass. Anyone else's PR cannot be merged without his approving review, and nobody else sees that box.
7. **Deploy waits for approval**: the ``deploy`` workflow pauses at the ``production`` environment; GitHub emails the
   owner "Review deployments". Approve → ``remote.sh`` deploys with health + smoke checks and auto-rollback on failure.
8. **If anything fails on ``main`` after the merge** (``ci`` or ``deploy``): ``alarm.yml`` opens an issue labelled
   ``incident``, assigned to the owner (= email + notification) with the failed run, the last known good SHA, a
   one-click **rollback** link (``rollback.yml`` → paste the SHA → approve) and the ask for a new MR with the fix.

The AI/safety gate (``scripts/pr_gate.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Runs on every PR (``gate.yml``) and in ``mr.sh``. Deterministic and explainable — each finding names file, line and
rule. **Blocks**: secret-like strings and DSNs with passwords; forbidden paths (``private/``, ``.env``, keys,
archives, ``.sync/``); destructive operations (``FLUSHDB``/``FLUSHALL``, ``DROP``, ``TRUNCATE``, ``compose down -v``,
``rm -rf /``, force-push, ``DELETE`` without ``WHERE``, retention removed or shortened below 90 days); rotation of
``BS_NAMING_SALT``/``BS_VISITOR_SECRET`` defaults; weakening of the approval gates (CODEOWNERS, ruleset, deploy
workflow — override only with the owner-applied label ``gate-change-approved``); code-execution patterns
(``curl | sh``, ``eval``/``exec``, ``pickle.loads``, ``shell=True``); outbound hosts in app code outside the known
list. **Warns** (listed for the reviewer, never fails): workflow, Dockerfile and dependency changes; privacy-relevant
files (``models.py``, ``enrich.py``, ingest, ``privacy.rst``, ``index.html``); large deletions; binaries. Tests:
``tests/test_pr_gate.py``. An LLM review (Anthropic's ``claude-code-action``) can be added on top later; it needs an
API key secret and costs per PR, so it is not on by default.

Approval gates (since 2026-10-26)
---------------------------------

Strict rule: **nothing merges to ``main`` and nothing deploys without the owner's explicit approval** — not a
collaborator, not a bot, not a workflow. The repository is public and the VM is production. Enforced by GitHub, not by
convention; applied with one script::

   bash deploy/github/apply-gates.sh      # idempotent; needs gh auth as the repo admin

What it sets:

1. **Ruleset on ``main``** (``deploy/github/main-ruleset.json``): pull request required; **1 approving review from a
   code owner** (``.github/CODEOWNERS`` makes the owner code owner of every file); stale approvals dismissed on new
   pushes; review threads resolved; status
   checks ``test (3.10)``, ``test (3.12)`` (ci matrix — the names must match exactly or the check waits forever),
   ``no-secrets`` (guard) and ``gate`` green and up to date with ``main``; linear history; no force-push, no
   deletion. Bypass list: **repository admin only** — that is the owner, and it is what lets him merge his own PRs
   (GitHub never lets an author approve their own PR). Anyone else's PR needs his review, full stop.
2. **``production`` environment**: owner is a required reviewer, so the ``deploy`` job (which runs under
   ``environment: production``) pauses with *Review deployments* on every run; only ``main`` may deploy to it.
3. **Actions hardening**: workflow token read-only; workflows cannot approve PRs; fork PRs from outside contributors
   need approval before CI even runs.
4. **Workflows** ``gate.yml``, ``rollback.yml``, ``alarm.yml`` copied from ``deploy/github/workflows/`` into
   ``.github/workflows/`` if missing (commit them). ``gate`` is a required status check.

Working flow (the owner's own changes go through the same door)::

   git switch -c <branch>
   pytest -q
   git push -u origin <branch>
   gh pr create --fill --web         # read the diff, wait for test/no-secrets, Merge (admin bypass)
   # Actions → deploy → Review deployments → Approve
   git switch main && git pull

Verify any time: ``gh api repos/bhushanladde02/botspotter/rulesets --jq '.[].name'`` and
``gh api repos/bhushanladde02/botspotter/environments/production --jq '.protection_rules'``. ``deploy/oracle.sh`` from a
laptop bypasses the deploy gate and is for a dead CI only (operations log 2026-10-25).

Deploy rehearsal (``deploy/rehearse.sh``, since 2026-10-26)
-----------------------------------------------------------

Two of the three outages on 2026-10-26 would have been caught before merge by simply starting the stack once. So
every PR now does exactly that in CI (``ci.yml`` job ``rehearse``, a required check): the real ``docker-compose.yml``
from fresh volumes (``schema.sql`` and ``migrations.sql`` both run), Caddy site block validated with ``caddy validate``,
api health, a bot hit and a human beacon through ingest → Redpanda → aggregator, then the same assertions
``remote.sh`` makes on the VM: snapshot younger than 10 s, counters moved, admin ledger readable, sensor sites present,
aggregator ``Up``, no "migrations.sql failed" in its log. ~2 minutes. Runs locally too (sets your ``.env`` aside and
restores it).

Zero-visible-downtime deploys
-----------------------------

A deploy recreates the three app containers; for 5–20 s there is no upstream. The Caddy site block now carries
``lb_try_duration 25s`` / ``lb_try_interval 500ms`` on both proxies, so Caddy holds a request and retries until the new
container answers — a slow page instead of a 502, and the live event stream reconnects by itself. Images are built
before containers are stopped, so the gap is only the process start.

Releasing the Python sensor to PyPI is a separate, tag-triggered workflow (``publish-sensor.yml``) behind the ``pypi``
environment's approval; see the runbook "Publishing the sensor".
