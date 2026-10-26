Runbook
=======

   **Credentials and account details are not in this repo.** They live in ``private/notes.md`` on Bhushan's Mac (gitignored, guarded by ``.github/workflows/guard.yml`` and the pre-commit hook from ``scripts/install-hooks.sh``). This runbook says *how*; that file says *with what*.

Keep this current: every setting, procedure or credential location changes here in the same commit as the change itself.

Local, no Docker (fastest)
--------------------------

.. code:: bash

   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   pytest -q                              # must be green before any push
   python scripts/dev.py                  # portal+api+ingest+aggregator on :8000, in-memory
   python scripts/seed_traffic.py --url http://localhost:8000/v1/ingest --key devkey --hits 2000
   open http://localhost:8000

``devkey`` → site ``dev-site`` is the default in ``config.py`` when ``BS_SENSOR_KEYS`` is unset.

Free temporary demo (Render, in-memory)
---------------------------------------

``render.yaml`` deploys ``scripts/dev.py`` as a free Render web service: no DB, data resets when the service sleeps (15 min idle) or redeploys. Use it to show the product and test the UI on a public URL.

1. Push the repo to GitHub.
2. render.com → New → Blueprint → pick the repo → Apply. URL: ``https://botspotter-demo.onrender.com``.
3. Seed it: ``python scripts/seed_traffic.py --url https://botspotter-demo.onrender.com/v1/ingest --key demokey --hits 2000``
4. First request after sleep takes ~30 s (cold start). That is Render's free tier, not us.
5. ``CERTIFICATE_VERIFY_FAILED`` on macOS = python.org build without root CAs: run ``/Applications/Python 3.12/Install Certificates.command`` once, or ``pip install certifi`` (the seed script uses certifi when installed). Do **not** hand out ``demokey`` as a real sensor key; the demo forgets everything on restart.

Full stack with Docker (what runs on the VM)
--------------------------------------------

.. code:: bash

   cp .env.example .env    # fill every CHANGE value; openssl rand -hex 16 for keys
   docker compose up -d --build
   docker compose ps       # all healthy
   curl -s localhost/healthz; curl -s localhost/v1/snapshot | head -c 300
   python scripts/seed_traffic.py --url http://localhost/v1/ingest --key <a key from .env>

Schema is applied automatically from ``db/schema.sql`` on first Postgres start. Topics auto-create.

Production: shared Oracle A1 VM behind Sharpen's Caddy (current setup)
----------------------------------------------------------------------

VM ``ubuntu@129.213.153.181`` (A1, 1 OCPU / 6 GB, aarch64), key ``~/.ssh/sharpen_vm``. Sharpen's Compose project (``deploy-*`` containers) owns :80/:443 via Caddy; BotSpotter runs as a second site behind it.

.. code:: bash

   deploy/oracle.sh          # from your Mac; CI does the same on every push to main (docs/cicd.rst)

What it does: clone/pull ``main`` into ``~/botspotter`` on the VM → generate ``.env`` on first run (secrets, sensor keys, ``CADDY_NETWORK``) → ``docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d --build`` → wait for ``/healthz`` → install ``deploy/Caddyfile.botspotter`` as ``caddy-extra/botspotter.caddy`` (Sharpen's Caddyfile already imports ``extra/*``; if not, one import line is appended after a backup), validate, reload Caddy only when changed → **smoke test** a beacon through the whole pipeline → print the sensor keys. Cloudflare: DNS ``A @ → 129.213.153.181`` proxied, ``CNAME www → botspotter.app`` proxied; SSL mode **Full** until Caddy has its certificate, then **Full (strict)**. Rate-limit rule on ``/v1/ingest`` (see below). Memory budget on this box (``docker-compose.vm.yml``): Redpanda 896M, Postgres 640M, Redis 256M, 3× app 192M ≈ 2.4 GB. Logs: ``ssh -i ~/.ssh/sharpen_vm ubuntu@129.213.153.181 'cd botspotter && docker compose logs -f --tail 100 aggregator'``.

.. _oracle-cloud-deploy-on-a-dedicated-vm-always-free-ampere-a1-2-ocpu--12-gb:

Oracle Cloud deploy on a dedicated VM (Always Free, Ampere A1, 2 OCPU / 12 GB)
------------------------------------------------------------------------------

1. Create the instance (Ubuntu 24.04 aarch64). Open TCP 80 and 443 in the VCN security list.
2. ``sudo apt install docker.io docker-compose-v2 git -y && sudo usermod -aG docker $USER``
3. ``git clone <repo> ~/botspotter && cd ~/botspotter && cp .env.example .env && nano .env``
4. ``docker compose up -d --build`` (first build ~5 min on A1).
5. ``docker compose logs -f aggregator`` — expect "aggregator started".
6. Point Cloudflare DNS (proxied/orange cloud) at the VM's public IP. SSL mode: Flexible first, Full once you add Caddy/certbot (TODO in ``growth.md`` tier 1).
7. Cloudflare → Security → WAF → Rate limiting rule: path ``/v1/ingest``, 600 req / 1 min per IP, block 10 min. This is the one free rule — spend it here.
8. Cloudflare → Caching → Cache Rules: respect origin ``Cache-Control`` for ``/v1/*`` (default behaviour; verify ``cf-cache-status: HIT`` on ``/v1/snapshot``).

Sensor keys
-----------

-  v1: ``BS_SENSOR_KEYS="key:site_id,..."`` in ``.env``. Restart ``ingest`` after editing (``docker compose up -d ingest``).
-  Key requests arrive by email from the ``/sensors`` form (subject "BotSpotter sensor key: "); the form requires ticking acceptance of ``/terms``. Keep the email as the acceptance record. Then ``openssl rand -hex 16``, add ``hex:their-site``, reply with the key + link to ``/sensors``.
-  Set up the ``hello@botspotter.app`` mailbox (or change the address in ``web/index.html``) before launch.
-  To revoke: remove the pair, restart ingest. Their history stays under the ``site_id``.
-  Later (``growth.md`` tier 2): move to the ``sensors`` table (``key_hash = sha256(key)``); ingest already accepts sha256-hashed keys in the map.

Honeypots
---------

1. Buy one domain. Create ``hp1.``, ``hp2.``, … subdomains, each with its **own** certificate (Cloudflare origin cert per name, or certbot) so each appears in CT logs.
2. Serve anything (a one-page Nginx site) with the ``botspotter`` log format and Vector (``sensors/vector/``). One key per honeypot so they count as separate sites — that is what lets unknown bots cross the 3-site discovery threshold.
3. Spread them across regions if possible (Oracle has free regions; Fly.io/Vercel free tiers work for the static part but can't run Vector — use a Python middleware honeypot there instead).

Rotating secrets
----------------

-  ``PG_PASSWORD``: ``docker compose exec postgres psql -U botspotter -c "ALTER USER botspotter PASSWORD '...'"``, update ``.env``, ``docker compose up -d``.
-  ``BS_VISITOR_SECRET``: safe to rotate any time (only affects "unique today" continuity for one day).
-  ``BS_NAMING_SALT``: **never** rotate; it renames every bot. If it must happen, bump ``NAME_SCHEME_VERSION``, FLUSHDB, truncate ``bots``/``events``, announce it.

Data persistence and backups
----------------------------

Deploys **never** reset data: Redis (AOF), Postgres and Redpanda are named volumes; ``remote.sh`` only rebuilds app containers. Never run ``docker compose down -v`` or ``redis-cli FLUSHDB`` in production except as a deliberate reset.

Nightly backup: ``deploy/backup.sh`` via cron (09:17 UTC) → ``~/botspotter-backups/`` (Redis RDB + ``pg_dump``, 7 days). Restore commands are in the script header. Verify: ``ssh … 'tail -3 ~/botspotter-backups/backup.log; ls -la ~/botspotter-backups | tail -4'``.

Operations
----------

+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Task                                     | Command                                                                                                                    |
+==========================================+============================================================================================================================+
| Scale ingest                             | ``docker compose up -d --scale ingest=3``                                                                                  |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Rebuild Redis from Postgres              | see header of ``scripts/rebuild_counters.py``                                                                              |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Replay last 7 days into a fresh consumer | ``docker compose exec redpanda rpk group seek aggregator --to start`` (stop aggregator first)                              |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Check consumer lag                       | ``docker compose exec redpanda rpk group describe aggregator``                                                             |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Update bot registry                      | merge the daily ``bot/registry-sync`` PR, ``docker compose up -d --build ingest api``                                      |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Backup                                   | ``docker compose exec postgres pg_dump -U botspotter botspotter | gzip > backup.sql.gz`` (nightly cron, push to R2 — TODO) |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+
| Logs                                     | ``docker compose logs -f --tail 200 <service>``                                                                            |
+------------------------------------------+----------------------------------------------------------------------------------------------------------------------------+

Audience ledger (owner only)
----------------------------

``/admin`` lists every individual visitor (pseudonymous id, city, device, referrer, sessions, return visits) with CSV
export; API: ``/v1/admin/visitors``, ``/v1/admin/visitors.csv``, ``/v1/admin/visits.csv`` (``X-BS-Admin`` header or
``?key=``). What is stored and why: :doc:`privacy`.

-  Key: ``BS_ADMIN_KEY`` in the VM's ``.env``. ``deploy/remote.sh`` generates it once if missing; copy it into
   ``private/notes.md``. Unset → the routes 404. Rotate by editing ``.env`` and ``docker compose up -d api``.
-  The key is entered on ``/admin`` and kept in ``sessionStorage`` only (gone when the tab closes). "Forget key" clears it.
-  City/region require Cloudflare's free visitor-location headers: Cloudflare dashboard → botspotter.app → Rules →
   Settings (Managed Transforms) → **Add visitor location headers** → On. Until then rows have country only.
-  Schema changes: ``db/schema.sql`` runs only when the Postgres volume is created. Every later change goes in
   ``botspotter/data/migrations.sql`` (idempotent ``IF NOT EXISTS`` statements), which ``PostgresStore.start()`` applies
   on every boot of api and aggregator; a failing migration is logged and skipped, never fatal. Comment lines are
   stripped before statements are split on ``;``. ``tests/test_packaging.py`` checks the file ships and parses.

GeoIP + crawler ranges (live map data)
--------------------------------------

Runtime data lives in ``~/botspotter-data`` on the VM (``DATA_DIR`` in ``.env``), mounted read-only at ``/data``.
``deploy/refresh-data.sh`` fills it on every deploy and every Wednesday 07:23 UTC (cron, log in
``~/botspotter-backups/refresh.log``):

-  ``GeoLite2-City.mmdb``, ``GeoLite2-ASN.mmdb`` — MaxMind GeoLite2. Needs the GitHub secrets ``MAXMIND_ACCOUNT_ID`` and
   ``MAXMIND_LICENSE_KEY`` (account in ``private/notes.md``); the deploy passes them to ``remote.sh``, which also stores
   them in ``~/.botspotter-maxmind`` (0600) for the cron. Without them the map stays at country centroids.
-  ``crawler_ranges.json`` — built by ``jobs/crawler_ranges.py`` from the operators' official IP lists
   (``botspotter/verify.py`` ``SOURCES``). A vendor outage keeps the previous ranges for that operator.
-  ``sensors.json`` — sensor site locations (copied from the package on first run; edit the ``/data`` copy to add a
   site: ``{"site": {"city": "...", "lat": .., "lon": .., "url": ".."}}``, then ``docker compose ... up -d api aggregator``).

When a file changes the script recreates ingest/api/aggregator (they read the data at start). Manual run:
``MAXMIND_ACCOUNT_ID=.. MAXMIND_LICENSE_KEY=.. bash deploy/refresh-data.sh``. Check: ``ls -la ~/botspotter-data``;
the Live page's "Where bots live" card fills within minutes of the first hits.

Evidence routine (reach, for any future claim)
----------------------------------------------

Independent sources carry the weight; the ledger is the detailed companion. Set up once:

1. **Cloudflare Web Analytics** (free, cookie-free): dashboard → Analytics & Logs → Web Analytics → Add a site →
   botspotter.app → "Automatic setup" (Cloudflare injects the beacon at the edge; no code change). Shows visits, page
   views, countries, referrers, paths; data from the day it is enabled.
2. **Google Search Console**: https://search.google.com/search-console → Add property → Domain → ``botspotter.app`` →
   verify with the TXT record it gives you (Cloudflare DNS → Records → add TXT at ``@``). Then Sitemaps → submit
   ``https://botspotter.app/sitemap.xml``. Bing Webmaster Tools can import the Search Console property in one click.
3. **Cloudflare zone analytics** (already on): dashboard → Analytics & Logs → Traffic — requests and unique visitors at
   the edge (includes bots; useful as an upper bound).

Monthly, first week of the month, into ``private/evidence/YYYY-MM/`` (gitignored with the rest of ``private/``):

-  Cloudflare Web Analytics → date range = previous month → Export (CSV) and a screenshot of the overview;
-  Search Console → Performance → previous month → Export → Google Sheets/CSV, plus a screenshot;
-  ``https://botspotter.app/v1/stats/humans.csv`` (public daily aggregates) and, with the admin key,
   ``/v1/admin/visitors.csv?days=1100`` and ``/v1/admin/visits.csv?days=1100``;
-  ``gh api repos/bhushanladde02/botspotter`` → ``stargazers_count``, ``forks_count`` into a one-line note;
-  any press, posts or citations (URL + archived copy via https://web.archive.org/save/).

Each export is dated by the vendor, which is what makes it credible later; keep the originals unedited.

Health checks
-------------

-  ``GET /healthz`` on api and ingest. ``bs:snapshot.ts`` older than 10 s → aggregator is stuck.
-  **Counter frozen / page says "live · <old time>"**: the page renders whatever snapshot the aggregator last wrote, so a
   stale clock in the status line means the aggregator is down even though api and ingest answer. On the VM::

      ssh -i ~/.ssh/sharpen_vm ubuntu@<vm> 'cd ~/botspotter && docker compose -f docker-compose.yml -f docker-compose.vm.yml ps && docker compose -f docker-compose.yml -f docker-compose.vm.yml logs --tail 60 aggregator'

   ``Restarting (1)`` + a traceback names the line. Nothing is lost while it is down: Redpanda keeps the backlog and the
   aggregator replays it when it comes back. The deploy smoke test now fails on this condition (snapshot age > 10 s or
   aggregator not ``Up`` six seconds after the smoke hit). First occurrence: 2026-10-26, missing package data (see the
   operations log).
-  ``rpk cluster health`` must say ``Healthy: true``.
-  Redis ``INFO memory`` ``used_memory`` < 230 MB (eviction is ``noeviction``; a full Redis makes the aggregator error).

Known gaps (v1)
---------------

-  No TLS at origin (Cloudflare Flexible). Add Caddy or certbot before accepting third-party sensor data at scale.
-  ``rebuild_counters.py`` requires a stopped aggregator (no scratch prefix).
-  The WordPress and Go log-tailer sensors do not exist yet (``growth.md``).
