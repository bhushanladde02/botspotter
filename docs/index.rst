BotSpotter
==========

**Every AI bot on the internet, counted and named.**

Live site: https://botspotter.app · Code: https://github.com/bhushanladde02/botspotter

BotSpotter is a crowd-sourced, real-time census of AI crawlers and agents.
Websites install a tiny sensor; every bot visit lands on a live world map;
every bot gets a deterministic name that stays the same on every site. This
documentation covers the whole project from the first idea to the production
deployment, and is the place every setting, procedure and decision is recorded.

.. toctree::
   :maxdepth: 2
   :caption: Start here

   journey
   setup

.. toctree::
   :maxdepth: 2
   :caption: Design

   architecture
   decisions
   privacy
   free-tier
   growth

.. toctree::
   :maxdepth: 2
   :caption: Operate

   runbook
   cicd
   operations-log
   sensors
   claims

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api
   reference/modules

Where things live
-----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Thing
     - Where
   * - Production
     - ``ubuntu@129.213.153.181`` (Oracle A1, shared with Sharpen), behind Cloudflare
   * - Demo (ephemeral)
     - https://botspotter-demo.onrender.com — in-memory, sleeps when idle
   * - Secrets, keys, account details
     - ``private/notes.md`` on Bhushan's Mac — **never committed**; see :doc:`runbook`
   * - This documentation
     - ``docs/`` in the repo, published by CI to GitHub Pages
