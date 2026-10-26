Operations log
==============

Append-only. One row per change to production, newest first (add a new ``* -`` block right under the header row). Secrets never go here — they go in ``private/notes.md`` on the Mac (gitignored).

.. list-table::
   :header-rows: 1
   :widths: 16 74 10

   * - date (PT)
     - what
     - by
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
