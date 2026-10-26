Add your site as a sensor
=========================

A sensor reports which **bots** visit your site. It never sends page content, cookies, header values, or human visitors. You get a spot on the leaderboard and you help name every new AI crawler on the internet.

You need a sensor key (``X-BS-Key``). Ask for one; it maps to your site id.

.. _python-apps-fastapi-starlette-flask-django--zero-dependencies:

Python apps (FastAPI, Starlette, Flask, Django…) — zero dependencies
--------------------------------------------------------------------

.. code:: bash

   pip install botspotter-sensor        # or copy sensors/python-middleware/botspotter_sensor/

.. code:: python

   # FastAPI / Starlette
   from botspotter_sensor import BotSpotterMiddleware
   app.add_middleware(BotSpotterMiddleware, key="YOUR_KEY")

   # Flask / any WSGI
   from botspotter_sensor import wsgi_sensor
   app.wsgi_app = wsgi_sensor(app.wsgi_app, key="YOUR_KEY")

   # Django: settings.py
   MIDDLEWARE += ["botspotter_sensor.django.BotSpotterMiddleware"]
   BOTSPOTTER_KEY = "YOUR_KEY"

Self-hosting BotSpotter? Pass ``endpoint="https://your-host/v1/ingest"``.

.. _nginx--apache--caddy--no-code-via-vector:

Nginx / Apache / Caddy — no code, via Vector
--------------------------------------------

1. Add ``sensors/nginx/log_format.conf`` to your ``http {}`` block and ``access_log /var/log/nginx/botspotter.log botspotter;`` to each ``server {}``.
2. Install `Vector <https://vector.dev/docs/setup/installation/>`__, copy ``sensors/vector/vector.toml`` to ``/etc/vector/vector.toml``, set ``BS_KEY`` (and ``BS_ENDPOINT`` if self-hosted) in ``/etc/default/vector``, ``systemctl restart vector``.

Apache: use ``LogFormat`` with the same JSON fields (``%{User-Agent}i``, ``%a``, ``%{CF-IPCountry}i``, ``%m``, ``%>s``) and point Vector at that file.

Behind Cloudflare?
------------------

Nothing to do — the sensor forwards ``CF-Connecting-IP`` and ``CF-IPCountry``, so your bots land in the right country on the map.

The wire format (for any other stack)
-------------------------------------

``POST /v1/ingest``, header ``X-BS-Key: <key>``, body:

.. code:: json

   {"events": [
     {"ts": 1761350000.1, "ua": "Mozilla/5.0 (compatible; GPTBot/1.2)", "headers": ["host","user-agent","accept","accept-encoding"],
      "ip": "20.171.207.5", "cf_country": "US", "method": "GET", "status": 200, "path": "/robots.txt"}
   ]}

A bare JSON array or newline-delimited JSON is also accepted. Max 1000 events per request. Response ``202 {"accepted": n, "bots": m, "site": "..."}``.

Caddy sidecar (any site behind Caddy, no code; how sharpen.com reports)
------------------------------------------------------------------------

Caddy streams its JSON access log over TCP to a small Vector container next to BotSpotter; Vector keeps non-browser
requests, reduces them to the wire format (header *names*, UA, IP, method, status, robots.txt flag) and posts them to
ingest on the Docker network. The site's own containers are untouched.

1. In the site's Caddyfile block add::

      log {
          output net tcp/bs-vector:9000 {
              soft_start
          }
          format json
      }

   ``soft_start`` means Caddy loads and serves even if Vector is unreachable (those log lines are dropped). Reload Caddy
   (``docker exec deploy-caddy-1 caddy reload --config /etc/caddy/Caddyfile`` or your usual deploy).
2. BotSpotter side (already in ``docker-compose.vm.yml`` as ``sensor-vector`` for ``sharpen.com``): config
   ``sensors/caddy/vector.toml``; env ``BS_HOST`` (only that host's lines are forwarded), ``BS_KEY`` (the site's sensor
   key — ``remote.sh`` derives ``SHARPEN_SENSOR_KEY`` from ``BS_SENSOR_KEYS``), ``BS_ENDPOINT``.
3. Check: ``docker compose -f docker-compose.yml -f docker-compose.vm.yml logs --tail 20 sensor-vector`` (no VRL
   errors) and the Live page's "Sensors with most catches" card shows ``sharpen`` within minutes.

Another host on the same Caddy = another ``log`` block in its site and a second ``sensor-vector`` service with its
own ``BS_HOST``/``BS_KEY`` (or one service and a routing transform — see the comments in the TOML).

Note on identity: Caddy serialises headers as a JSON object, so header *order* is Go-map order; fingerprints from Caddy
sensors are consistent with each other but can differ from the Python middleware's for the same bot. Known bots are
unaffected (identity = UA family); only unknown bots could appear twice across the two sensor types.
