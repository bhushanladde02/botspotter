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

Approval gates (since 2026-10-26)
---------------------------------

The repository is public and the VM is production, so two human approvals are mandatory. Both are GitHub settings,
not workflow code:

1. **Merge gate** — Settings → Branches → rule for ``main``: *Require a pull request before merging* (0 required
   approvals: the owner cannot approve his own PR and is the only reviewer), *Require status checks* (``ci``,
   ``guard``), *Do not allow bypassing*. Nobody — including the owner — pushes to ``main`` directly; every change is a
   branch + PR whose diff is read before Merge.
2. **Deploy gate** — Settings → Environments → ``production`` → *Required reviewers* = owner. The ``deploy`` job already
   runs under ``environment: production``, so every run (push to ``main`` or manual) pauses after the tests with a
   *Review deployments* button. Nothing reaches the VM until it is pressed.

Working flow::

   git switch -c <branch>            # change on a branch
   pytest -q                         # locally green first
   git push -u origin <branch>
   gh pr create --fill --web         # read the diff, wait for ci/guard, Merge
   # Actions → deploy → Review deployments → Approve
   git switch main && git pull

An emergency fix follows the same path; the PR is just small. ``deploy/oracle.sh`` from a laptop bypasses both gates
and is for a dead CI only (operations log 2026-10-25).
