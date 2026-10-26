Privacy
=======

The product is "name the bots, not the people". These are the rules; the tests in ``tests/test_pipeline.py::test_full_flow_counts_names_and_map`` assert the storage boundary.

What a sensor sends
-------------------

Header **names** (not values), User-Agent, client IP, ``CF-IPCountry``, method, status, and ``path`` only when it is ``robots.txt``. Sensors drop obvious browsers locally, so most human traffic never leaves the site.

What ingest keeps
-----------------

Nothing. The IP is used in-process for (a) the ``/16`` network prefix of *unknown* bots, (b) GeoIP, (c) the daily human-visitor hash — then discarded. Raw UA is reduced to a version-stripped family. Paths are reduced to one boolean.

What is stored
--------------

``BotEvent`` (see ``models.py``): timestamps, site id, fingerprint hash, name, UA family, operator/category, country, region, city (with the City database), geohash, ``hit_robots``, the hosting network's ASN organisation and label (public information about where a *bot* is hosted, not about a person), and ``verified`` (whether the IP was inside the operator's published crawler ranges — computed in-process, the IP still discarded). ``PortalVisit``: the pseudonymous human-visit row described under *Humans on the portal*. In Postgres and Redis. That is all.

Humans on the portal
--------------------

No IP address is stored and no cookie or browser identifier is set. Two salted hashes are derived in-process from
``VISITOR_SECRET + ip + ua`` and the IP is then discarded:

-  daily: ``blake2b(secret + UTC-day + ip + ua)`` → "humans today / this week" HyperLogLogs and today's peak hour;
   rotates daily so the public counters can't link days;
-  stable: ``blake2b(secret + "all" + ip + ua)`` → the "humans all time" HLL **and** the owner-only audience ledger
   (below). It cannot be reversed; the same person on a second device is a second id.

**Audience ledger (since 2026-10-26).** Every portal page view is written to ``portal_visits`` as a pseudonymous row:
timestamp, both hashes, country, city/region (from Cloudflare's visitor-location headers when enabled, else empty),
page, referrer *domain* (``linkedin.com``, ``direct``; own domain → ``internal``), browser family, OS family, device
class and first ``Accept-Language`` tag. Retention 1100 days. It is readable only with ``BS_ADMIN_KEY`` (``/admin``,
``/v1/admin/*``; 404 without it, ``Disallow`` in robots.txt) and exists so the owner can show, with per-individual
detail, how many real people use the site, from where, and how often they return — e.g. as evidence of reach. Public
pages (``/stats``) expose aggregates only. §5 of ``/terms`` discloses exactly this; keep the two in sync.

Why not raw IPs: they add legal exposure (GDPR/CCPA personal data, breach liability, access requests) and prove
nothing an evaluator can verify. Third-party sources (Cloudflare Web Analytics, Search Console) are the strong evidence;
the ledger is the detailed companion. See the runbook's "Evidence routine".

.. _bots-are-not-people--but:

Bots are not people — but
-------------------------

A one-person scraper running from a home connection is identified by ``UA family + headers + home ISP /16 + country``. That is not personal data under GDPR in any reasonable reading (a /16 is up to 65k addresses), and we never store the address itself. If someone asks for "their" bot to be removed, delete its ``bs:bot:<fp>`` keys and ``bots`` row; the name will re-appear on the next hit, so also add the fingerprint to a denylist (TODO when it first happens — see ``growth.md``).

Sensor owners
-------------

**Accounts (self-serve enrollment, since 2026-10-26):** GitHub OAuth with no scopes; stored: GitHub numeric id, login,
avatar URL, enrolled domains, verification time/method, SHA-256 of each sensor key. No e-mail, no tokens kept. The
session is a signed stateless cookie (30 days). Removing a site deletes its record and revokes its key.

The site owner installs the sensor knowingly; their key maps to a ``site_id`` that is shown publicly on the leaderboard. Keys are revocable (remove from ``BS_SENSOR_KEYS`` / ``sensors`` table).

Legal posture
-------------

We publish aggregated bot statistics derived from public HTTP requests to consenting sites — the same category of data as Cloudflare Radar's bot reports. No scraping of third parties, no deanonymization, no sale of data.

Public terms
------------

The user-facing Sensor Terms & Privacy page is ``/terms`` in ``web/index.html`` (section ``#terms``). Keep it in sync with this file; §5 there mirrors "What is stored" here. The draft was written without a lawyer — get it reviewed before accepting third-party sensors at scale.

Claims
------

A verified claim stores a salted hash of the owner key and of the contact email on the bot record, plus the public fields the operator chooses to publish. The plaintext email exists only in the pending claim record (7-day expiry). ``ua_host`` — the domain a bot names in its own User-Agent — is recorded at first sight; it is published operator information, not personal data. See :doc:`claims`.

Presence and the audience report
--------------------------------

"Watching now" is the number of daily visitor hashes with a heartbeat in the last 90 seconds (``bs:humans:active``; entries older than an hour are dropped). The public ``/stats`` page and ``/v1/stats/humans`` expose only daily aggregates: views, unique humans (HyperLogLog), bot hits, top countries and referrer domains, retained about three years. The per-visit rows behind them are the owner-only ledger described above.
