Claim your bot
==============

Operators can claim a bot that appears on BotSpotter, prove they control it,
and then manage its public identity: display name, description, homepage,
operator and category. The generated name (``Copper Heron #0042``) remains a
permanent alias, so old links and searches keep working.

Why proof is required
---------------------

A name is public, so anyone could type it into a form. The **fingerprint** is
the bot's real identity and only the operator can make the bot send a request.
Verification is therefore a challenge the bot itself (or the operator's
domain) must answer.

Verification methods
--------------------

.. list-table::
   :header-rows: 1
   :widths: 14 46 40

   * - method
     - how it works
     - for whom
   * - ``fetch``
     - We issue a one-time token. The operator makes **the bot** request
       ``https://botspotter.app/verify/<token>`` with its normal User-Agent,
       headers and network. That request is fingerprinted exactly like a
       sensor hit; if the digest equals the claimed bot's ``fp``, the claim is
       verified. A browser or a different bot is rejected (and counted as a
       mismatched attempt).
     - Any bot, including unidentified ones. The only method that proves
       control of the *software*.
   * - ``wellknown``
     - We tell the operator to publish ``botspotter-verify=<token>`` at
       ``https://<host>/.well-known/botspotter-verify.txt``. ``<host>`` is the
       registry homepage (known bots) or the domain the bot names in its own
       User-Agent (recorded at first sight as ``ua_host``). We fetch the file
       on ``POST /v1/claims/<token>/check``.
     - Operators who control the bot's published domain; convenient for large
       crawlers whose fleet can't easily be pointed at a URL.

Flow
----

1. ``POST /v1/claims`` ``{"slug", "email", "method"}`` → ``201`` with ``token`` and
   instructions. Tokens expire after 7 days. 5 claims per IP per hour.
2. Verify (bot fetch, or publish file + ``POST …/check``).
3. ``GET /v1/claims/<token>`` → ``status: verified`` and the **owner key** (shown
   to whoever holds the claim token; keep it — it cannot be recovered, only
   re-issued by re-verifying).
4. ``PATCH /v1/bots/<slug>`` with header ``X-BS-Owner: <owner key>`` and any of
   ``display_name``, ``description``, ``homepage``, ``operator``, ``category``,
   ``contact_url``.

The portal page ``/claim`` walks through all four steps; ``/bot/<slug>`` has a
*"this is my bot"* button.

Rules
-----

- Display names: 3–40 characters, letters/digits/space/``.#-'_``; must be
  unique across display names **and** generated names; reserved words refused.
- ``category`` ∈ training · search · agent · scraper · seo · other.
- A claimed bot cannot be claimed again; disputes go to
  ``bhushanladde02@gmail.com`` (Terms §12). Operators can revoke/edit via the key.
- Claimed bots show a ✓ and their display name everywhere; the generated name
  is shown as *aka*.

What is stored
--------------

On the bot's Redis hash: ``owner_hash`` (salted hash of the owner key),
``claimed_at``, ``claim_method``, ``claimed_contact`` (salted hash of the
email — the plaintext email lives only in the pending claim record, which
expires in 7 days), and the public fields above. ``bs:claims:log`` keeps the
last 500 claim events. Nothing about the person beyond the hashed email.

Abuse controls
--------------

Rate limit per IP, 7-day token expiry, mismatch counter per claim, name
validation and uniqueness, reserved slugs, HTML escaping on render,
revocation by editing the bot hash (remove ``owner_hash``/``claimed_at``).
