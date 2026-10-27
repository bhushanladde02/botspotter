Operations log
==============

Append-only. One row per change to production, newest first (add a new ``* -`` block right under the header row). Secrets never go here — they go in ``private/notes.md`` on the Mac (gitignored).

.. list-table::
   :header-rows: 1
   :widths: 16 74 10

   * - date (PT)
     - what
     - by
   * - 2026-10-26 17:35
     - Verified on the VM that the no-downtime retry (``lb_try_duration 25s``) was still NOT in the running Caddy: ``remote.sh`` step 5 looked for a ``/etc/caddy`` *directory* mount, but Sharpen bind-mounts the Caddyfile as a single file (``deploy/Caddyfile → /etc/caddy/Caddyfile``), so the path resolved empty and the drop-in copy never happened. Step 5 now resolves the Caddyfile and ``extra/`` paths from the container's actual mounts, backs up the previous drop-in, validates before reloading (restores the old drop-in on failure), and fails the deploy if ``try_duration`` is absent from the adapted config. Check after deploy: ``ssh -i ~/.ssh/sharpen_vm ubuntu@129.213.153.181 "docker exec deploy-caddy-1 caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile | grep -o 'try_duration'"``
     - Bhushan
   * - 2026-10-26 17:10
     - "Bot hits today" dropped to 1 at 17:00 PDT = 00:00 UTC: daily counters are UTC days and the tiles showed them. Tiles now use rolling windows that never reset — bot hits and unique humans in the last 24 h (``hits_24h``; ``unique_24h`` = PFCOUNT over the 24 hourly HLLs), humans last 7 days (``unique_7d`` = union of 7 daily HLLs) — with "yesterday" / "previous hour" sublines for context. Daily/weekly UTC figures remain in the snapshot and on the Audience page; no data was lost
     - Bhushan
   * - 2026-10-26 17:05
     - Root cause of "map unavailable" on iPhone found via the new client error reports: ``Can't find variable: cv`` — WebKit hoists function declarations out of blocks so they cannot see the block's ``const``; Chrome tolerates it. The map section is now a function scope (``initMap``). Also: the by-kind card redesign below
     - Bhushan
   * - 2026-10-26 16:50
     - "Last 24 hours · by kind" card filled: taller chart with hour labels and hover breakdown, 24 h total, trend vs the previous 24 h (``snapshot.bots.hourly_prev``), peak hour, kind-share bar, named-vs-unidentified line with a sensor nudge. iPhone (Chrome iOS 148, iOS 17.2) shows "map unavailable": client error reports show only the consequence (``map.getZoom`` on null from the 10 s map refresh). Fix: refresh guarded when the map is off; the caught map-init error itself is now reported with its message/stack so the root cause is visible in ``/v1/admin/client-errors``
     - Bhushan
   * - 2026-10-26 16:40
     - Launch prep (PR pending): sensor package publishable to PyPI via trusted publishing (``publish-sensor.yml``, tag ``sensor-vX.Y.Z``, ``pypi`` environment with owner approval) + README/metadata; install line on /sensors shows the git fallback until the first release; map/live layer wrapped so a device where WebGL/canvas fails still gets counters (suspected cause of blank numbers on iPhone); browsers report uncaught JS errors to ``/v1/client-error`` (``/v1/admin/client-errors``); ticker collapses repeated hits of one bot into ``×N``; every list gets a filter box and a rows selector (5 on phones / 10 on desktop, remembered)
     - Bhushan
   * - 2026-10-26 14:30
     - Feature (PR pending): self-serve sensor enrollment — GitHub OAuth sign-in, domain ownership proof (DNS TXT or /.well-known), automatic key issuance (hash in ``bs:sensor_keys``, read by ingest with 60 s cache), rotate/remove, site location from GeoIP, "first bot caught" check; terms §5a accounts; ``remote.sh`` now always installs the Caddy drop-in, reloads and verifies ``try_duration`` is in the running config (the retry block had silently never been applied — cause of the 12:29 502s); per-sensor ``last_seen``
     - Bhushan
   * - 2026-10-26 11:45
     - Feature (PR pending): sharpen.com becomes the second sensor — Caddy JSON log over TCP → ``sensor-vector`` sidecar (``sensors/caddy/vector.toml``) → ingest; ``remote.sh`` wires ``SHARPEN_SENSOR_KEY`` from ``BS_SENSOR_KEYS``. Requires one ``log`` block in Sharpen's Caddyfile (docs/sensors.rst "Caddy sidecar"); Caddy ``soft_start`` keeps Sharpen unaffected if Vector is down. Gate: ``gate:allow <reason>`` per-line override for destructive/RCE rules (never secrets), used for the CI rehearsal teardown
     - Bhushan
   * - 2026-10-26 11:30
     - Hardening after a visible ~30 s gap on the 11:03 deploy: Caddy ``lb_try_duration 25s`` on api/ingest proxies (retry through the container recreate); ``deploy/rehearse.sh`` runs the real stack + production smoke in CI as required check ``rehearse``; verifier status (operators, ranges date) in ``snapshot.trust`` and honest wording on the "Real or impostor?" card
     - Bhushan
   * - 2026-10-26 11:05
     - Deploy of the live map failed in step 2b (after the data refresh succeeded: City 50 MB, ASN 9 MB, 1928 crawler prefixes) and **auto-rolled back** in 40 s — first real use of the rollback. Cause not visible (``set -e`` exit without message). Fix: the whole runtime-data/cron step is now non-fatal, and the EXIT trap prints the failing command (``ERR`` trap records ``$BASH_COMMAND``). ``gate.yml`` re-runs on label changes
     - Bhushan
   * - 2026-10-26 11:00
     - Feature (branch ``feat/live-map``, PR pending): live map — SSE stream ``/v1/live`` of every sighting, canvas arcs from bot origin to sensor site with labels, sensor markers, follow-bot mode, optional sound; GeoLite2 City + ASN on the VM (``deploy/refresh-data.sh``, weekly cron; MaxMind account 1436039, secrets set) → city-level origins and hosting labels; impostor verification against published crawler ranges (``verify.py``, ``jobs/crawler_ranges.py``); sensor locations ``sensors.json``; new cards "Real or impostor?" and "Where bots live"; Caddy: no compression for event streams, ``flush_interval -1``
     - Bhushan
   * - 2026-10-26 10:30
     - Process: mandatory MR flow documented (docs/cicd.rst "The flow"); ``scripts/mr.sh`` (tests + gate + commit + push + PR); safety gate ``scripts/pr_gate.py`` + ``gate.yml`` as a required check (secrets, destructive ops, salt rotation, gate weakening, RCE, unknown hosts); ``alarm.yml`` opens an owner-assigned incident issue when ``ci``/``deploy`` fail on main, with last-good SHA and a ``rollback.yml`` one-click redeploy (still behind the production approval)
     - Bhushan
   * - 2026-10-26 09:50
     - Process (strict): nothing merges to ``main`` or deploys without the owner's approval — ruleset on ``main`` (PR + 1 code-owner review via ``.github/CODEOWNERS`` + green ``test``/``no-secrets``, admin-only bypass), owner as required reviewer on the ``production`` environment, Actions token read-only, fork PRs need approval. Applied by ``deploy/github/apply-gates.sh``; all changes now arrive as branches/PRs (docs/cicd.rst "Approval gates")
     - Bhushan
   * - 2026-10-26 09:45
     - **Incident** (≈6 min): deploy of the ledger took api + aggregator down — ``migrations.sql`` splitter dropped the first statement (chunk began with the file's comment header), so ``ADD COLUMN vs`` never ran and the next statement failed; ``store.start()`` raised in both lifespans. Recovered by manual ``git checkout 259a674`` on the VM. Fixes: comment lines stripped before splitting (``split_sql`` + test); migration failures logged, never fatal; ``remote.sh`` now auto-rolls back to the previous revision when health or smoke fails (``ROLLBACK=0`` to disable)
     - Bhushan
   * - 2026-10-26 10:15
     - Feature: owner-only audience ledger ``/admin`` + ``/v1/admin/*`` (per-visitor rows, sessions, return visits, CSV exports), gated by ``BS_ADMIN_KEY`` (generated by ``remote.sh``, store in private notes). Visits now carry city/region (Cloudflare visitor-location headers — **enable the managed transform**), browser/OS/device/language; ``portal_visits`` retention 90 d → 1100 d; migrations in ``botspotter/data/migrations.sql`` applied on boot. Terms §5, about page and privacy.rst updated: pseudonymous per-visit rows, still no IP, no cookie. Evidence routine (Cloudflare Web Analytics, Search Console, monthly exports) in the runbook
     - Bhushan
   * - 2026-10-26 09:30
     - **Incident**: counters frozen 10.6 h (snapshot ts stuck at 05:37 UTC, right after the 22:35 deploy). Aggregator crash-looped: ``ModuleNotFoundError: botspotter.data`` from ``geo.py`` — ``botspotter/data/`` had no ``__init__.py`` so the wheel never shipped it; ingest/api survived only because granian imports the ``/app`` source tree. Fix: ``data/__init__.py`` + ``[tool.setuptools.package-data]``; new ``tests/test_packaging.py`` inspects a built wheel; ``remote.sh`` smoke now also requires a fresh snapshot (<10 s) and an ``Up`` aggregator 6 s later. No data lost (Redis/Postgres untouched; Redpanda retained the backlog, replayed on restart)
     - Bhushan
   * - 2026-10-25 22:35
     - Fix: hover-to-locate was slow (a request per hover) and sometimes dead (lists re-rendered every 2 s under the cursor). Snapshot now carries ``places`` for top bots/feed and lat/lon for ticker sightings, hover is instant; lists only re-render when changed and never while hovered; 150 ms hover-intent before the camera moves (sweeping the list = one move, not one per row), 180 ms leave grace; country-centroid fallback for bots without cell history
     - Bhushan
   * - 2026-10-25 22:05
     - UI: hovering a bot (lists, feed, ticker) highlights its origin cells on the map in pink and eases the camera there; per-bot geohash buckets (``bs:bot:<fp>:gh``, fill from new hits onward)
     - Bhushan
   * - 2026-10-25 22:00
     - Feature: live "humans watching now" (30 s heartbeat, 90 s window) in the header; public Audience page ``/stats`` + ``/v1/stats/humans`` JSON/CSV (daily uniques, views, countries, sources; 3-year retention of daily aggregates)
     - Bhushan
   * - 2026-10-25 21:55
     - Feature: procedural robot avatars per bot (``/avatar/<slug>.svg``), shown in lists, search and bot cards
     - Bhushan
   * - 2026-10-25 21:50
     - Visibility: SEO/OG meta + JSON-LD, ``robots.txt``, ``sitemap.xml``, ``feed.xml`` (RSS), live sensor badge ``/badge/<site>.svg``, OG image; layout now uses full width on wide screens
     - Bhushan
   * - 2026-10-25 21:45
     - UI: "Humans · where from" card (countries + referring sites, domain only); beacon sends ``document.referrer``
     - Bhushan
   * - 2026-10-25 21:40
     - Feature: claim-your-bot (``/claim``, fetch + well-known verification, owner key, rename/describe); ✓ badges; footer copyright + MIT link; nightly backup cron added to ``remote.sh``
     - Bhushan
   * - 2026-10-25 21:25
     - Docs converted from Markdown/MyST to reStructuredText (``docs/*.rst``); ops log now a ``list-table`` so rows are easy to append; MyST dependency dropped
     - Bhushan
   * - 2026-10-25 21:10
     - UI: humans tiles are all uniques (today / week / all-time via stable hash); "today's peak" line under the counter; fixed ``/sensors`` ``/terms`` ``/about`` blank page (duplicate element id)
     - Bhushan
   * - 2026-10-25 21:00
     - Bug 2: aggregator crash-looped on ``upsert_bots`` (Postgres rejected ``UPDATE … FROM LATERAL`` referencing the target); Redis counters were replayed per restart (inflated), visits never processed. Fix: correlated subquery; store writes now isolated (``bs:errors:*``) so they can't crash-loop or double count. Redis flushed after deploy to drop the inflated counts
     - Bhushan
   * - 2026-10-25 20:40
     - Bug: production counters stayed at 0 — ingest raised ``Compression library for lz4 not found`` on first Kafka publish (codec wheel missing). Fix: producer ``compression_type=None``; smoke test in ``remote.sh`` now catches this
     - Bhushan
   * - 2026-10-25 20:12
     - First production deploy on the shared A1 behind Caddy; Let's Encrypt certs issued for ``botspotter.app`` + ``www``; site live
     - Bhushan
   * - 2026-10-25 20:10
     - Caddy drop-in placed in ``~/sharpen/deploy/caddy-extra/botspotter.caddy`` (host dir mounted as ``/etc/caddy/extra``)
     - Bhushan
   * - 2026-10-25 20:01
     - Cloudflare SSL mode set to Full; DNS ``A @``, ``CNAME www`` proxied
     - Bhushan
   * - 2026-10-25 19:48
     - Domain ``botspotter.app`` registered at Cloudflare ($8.20 / renew $14.20)
     - Bhushan
   * - 2026-10-25 15:04
     - Render free demo live at ``botspotter-demo.onrender.com`` (ephemeral)
     - Bhushan
   * - 2026-10-25 14:59
     - Repo published: ``github.com/bhushanladde02/botspotter``
     - Bhushan

Open items
----------

-  ☐ Add GitHub secrets (``VM_SSH_KEY``, ``VM_HOST``, ``VM_USER``) and enable Pages — see :doc:`cicd`.
-  ☐ Cloudflare SSL → Full (strict); WAF rate-limit rule on ``/v1/ingest``.
-  ☐ Create ``hello@botspotter.app`` mailbox (sensor key requests land there).
-  ☐ Legal review of ``/terms``.
-  ☐ First real sensor: résumé site via Vector with the ``bhushan-profile`` key.
-  ☐ Retire the Render demo once the production site has data (or keep as a staging target).
