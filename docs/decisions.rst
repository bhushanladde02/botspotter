Decision log (ADR-style, newest last)
=====================================

Each entry: context → decision → consequences. Add one when a non-obvious choice is made.

.. _001--all-python-not-java-core--python-batch-2026-10-25:

001 — All-Python, not Java core + Python batch (2026-10-25)
-----------------------------------------------------------

**Context.** Java ingest is 5–10× faster per core, and Bhushan is faster hands-on in Java. Project is solo, unpaid, free-tier, needs to ship. **Decision.** Python everywhere (FastAPI + Granian/uvloop, asyncio aggregator). One toolchain, one test suite, one deploy path. **Consequences.** Ceiling ~20–40k req/s per ingest process — years away. Rewrite trigger documented in ``growth.md``. Résumé-wise a Python data-infra project matches the positioning.

.. _002--redpanda-from-day-one-not-redis-streams-2026-10-25:

002 — Redpanda from day one, not Redis Streams (2026-10-25)
-----------------------------------------------------------

**Context.** Redis Streams would cost zero extra RAM; Redpanda ~1 GB. Asked for "the right way" with growth in mind. **Decision.** Redpanda (Kafka-compatible, single binary, no JVM). Durable, partitioned, 7-day replay. ``Stream`` interface keeps a swap to anything Kafka-shaped config-only; ``MemoryStream`` for tests/dev. **Consequences.** +1.3 GB RAM on the VM. Replays and multiple consumers are free later. Apache Kafka itself was rejected (JVM, ZooKeeper/KRaft, 2–4 GB).

.. _003--snapshot-served-from-the-api-with-cache-control-not-written-to-r2-2026-10-25:

003 — Snapshot served from the API with Cache-Control, not written to R2 (2026-10-25)
-------------------------------------------------------------------------------------

**Context.** Writing ``snapshot.json`` to R2 every second = 2.6M writes/month vs 1M free. **Decision.** Aggregator writes the snapshot to Redis; API serves it with ``max-age=2``; Nginx ``proxy_cache`` + Cloudflare absorb viewers. **Consequences.** Zero R2 cost; viewer count never reaches Python; one more dependency (api must be up for the portal, which it is anyway).

.. _004--airflow-and-prometheusgrafana-off-the-vm-in-v1-2026-10-25:

004 — Airflow and Prometheus/Grafana off the VM in v1 (2026-10-25)
------------------------------------------------------------------

**Context.** 12 GB VM shared with the swing bot. Airflow idles at 1.5–2 GB, Prom+Grafana ~600 MB. **Decision.** Batch jobs run on GitHub Actions cron; observability = structured logs + ``/healthz``. Triggers to bring them back in ``growth.md``.

.. _005--known-crawlers-are-one-identity-across-networks-unknown-bots-include-network-2026-10-25:

005 — Known crawlers are one identity across networks; unknown bots include network (2026-10-25)
------------------------------------------------------------------------------------------------

**Context.** First live seed showed CCBot splitting into dozens of names because the ``/16`` was in every fingerprint. **Decision.** ``include_network = (kind != "known")``. Known bots have a public operator → one name. Unknown automation keeps the ``/16`` so different operators of the same HTTP library are distinct. **Consequences.** Cleaner top list; unknown bots may still fragment across an operator's many ``/16``\ s (acceptable; JA4 will tighten it).

.. _006--ua-normalizer-keeps-parenthetical-tokens-2026-10-25:

006 — UA normalizer keeps parenthetical tokens (2026-10-25)
-----------------------------------------------------------

**Context.** Stripping ``(…)`` collapsed every ``Mozilla/5.0 (compatible; XBot/1.0)`` into ``mozilla/#``. **Decision.** Strip only version numbers, contact URLs and emails. Parentheses stay.

.. _007--counting-visits-labelled-honestly-2026-10-25:

007 — Counting "visits", labelled honestly (2026-10-25)
-------------------------------------------------------

**Context.** Nobody sees the whole internet; "total AI bots in the world" is unverifiable and invites ridicule. **Decision.** The portal says "AI bot visits spotted across N sensors in M countries". The sensor count is public. Growth = more sensors, not bigger claims.

.. _008--names-are-deterministic-and-append-only-2026-10-25:

008 — Names are deterministic and append-only (2026-10-25)
----------------------------------------------------------

**Context.** The whole point is that a bot keeps its name forever so people can "call" it. **Decision.** Name = f(fingerprint, NAMING_SALT, word lists). Word lists are append-only; salt is set once. ``NAME_SCHEME_VERSION`` exists for a deliberate break, never a casual one.

.. _009--basemap-openstreetmap-tiles-not-carto-2026-10-25:

009 — Basemap: OpenStreetMap tiles, not CARTO (2026-10-25)
----------------------------------------------------------

**Context.** First run on a real browser showed "API KEY REQUIRED" watermarks: CARTO's free basemaps now need a key. **Decision.** OpenFreeMap ``dark`` vector style (``tiles.openfreemap.org``, free, no key, MapLibre-native). Raster OSM tiles were tried first and looked flat/grey; a CSS-invert hack looked worse. Dots: halo + glow + core + animated sonar ring. **Consequences.** OpenFreeMap is donation-funded with no SLA; if it degrades, swap ``style`` for a MapTiler/Stadia keyed URL (one line) — tracked in ``growth.md``.

.. _010--where-is-the-bot-now--last-sighting-not-tracking-2026-10-25:

010 — "Where is the bot now" = last sighting, not tracking (2026-10-25)
-----------------------------------------------------------------------

**Context.** Asked whether a bot can be located. A crawler runs from many machines concurrently; there is no single location. **Decision.** Per-bot ``recent`` list (last 25 sightings) + ``last_country``/``last_site`` on the profile, and a global ``bs:sightings`` ticker. Presence resolution improves with sensor count; we don't claim more. **Consequences.** +2 Redis list writes per event (bounded by LTRIM). Category-by-hour ZSET added for the stacked chart and chips filter.

.. _011--free-public-demo-runs-the-in-memory-mode-on-render-2026-10-25:

011 — Free public demo runs the in-memory mode on Render (2026-10-25)
---------------------------------------------------------------------

**Context.** Wanted a public URL to try the product before buying a domain or touching the Oracle VM. **Decision.** ``render.yaml`` runs ``scripts/dev.py`` (memory stream + fakeredis + memory store) as a free Render web service. Zero infra, zero cost, data is ephemeral by design. **Consequences.** Not for real sensors. The Oracle/Compose stack remains the production path; the demo URL is retired once a domain + VM are live.

.. _012--production-on-the-shared-a1-vm-behind-sharpens-caddy-2026-10-25:

012 — Production on the shared A1 VM behind Sharpen's Caddy (2026-10-25)
------------------------------------------------------------------------

**Context.** The free-tier A1 allowance is already used by the Sharpen VM (1 OCPU / 6 GB, 4.6 GB free, Docker + Caddy on :80/:443). The other VM is a 1 GB AMD micro running the swing bot — too small. **Decision.** Deploy BotSpotter's Compose stack on the A1 without its own ``edge``; join Caddy's docker network and add a ``botspotter.app`` site block via one ``import`` line (``docker-compose.vm.yml``, ``deploy/Caddyfile.botspotter``, ``deploy/oracle.sh``). Memory trimmed to ≈2.4 GB; 1 ingest worker. **Consequences.** Shares 1 OCPU with Sharpen — fine for launch traffic; if either app gets busy, the next step is a second A1 (needs free-tier capacity) or paid. Caddy does TLS (ACME), so Cloudflare can run Full (strict). Nginx ``proxy_cache`` is gone; Cloudflare's edge cache covers ``/v1/snapshot``.

013 — Bot ownership is proven by a fingerprint challenge (2026-10-25)
---------------------------------------------------------------------

**Context.** Operators should be able to name and describe their bots, but a name is public and anyone could claim it.

**Decision.** The bot itself must fetch a one-time ``/verify/<token>`` URL; the request is fingerprinted like any sensor hit and must equal the claimed ``fp``. Second method for operators of a published domain: a token in ``/.well-known/botspotter-verify.txt`` on the registry homepage or the domain the bot names in its UA (now recorded as ``ua_host`` at first sight). Owner key hashed; generated name kept as permanent alias; display names unique across both namespaces.

**Consequences.** No accounts, no passwords, no email sending. Unidentified bots can be claimed too. Impersonation is bounded by the fingerprint, not by trust in a form. See :doc:`claims`.

014 — Data survives every deploy; backups nightly (2026-10-25)
--------------------------------------------------------------

**Context.** Asked to make sure new deploys never start from zero.

**Decision.** They never did: Redis (AOF), Postgres and Redpanda live in named Docker volumes; ``remote.sh`` only rebuilds app containers and never runs ``down -v`` or ``FLUSHDB``. The one flush on launch night was a manual, one-off cleanup of crash-loop duplicates. Added ``deploy/backup.sh`` (nightly 09:17 UTC via cron, installed by ``remote.sh``): Redis RDB + ``pg_dump``, 7-day retention in ``~/botspotter-backups``.

**Consequences.** Counters are continuous across releases. Restores documented in the script header and :doc:`runbook`.
