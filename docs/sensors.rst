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
