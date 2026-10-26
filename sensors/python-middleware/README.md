# botspotter-sensor

Report which **AI bots** visit your Python web app to [BotSpotter](https://botspotter.app) — the live map of AI
crawlers. Zero dependencies. Human visitors are never sent; the bot's IP is used once for country/network and discarded.

Get a key in two clicks at https://botspotter.app/sensors (sign in with GitHub, prove the domain), then:

```python
# FastAPI / Starlette
from botspotter_sensor import BotSpotterMiddleware
app.add_middleware(BotSpotterMiddleware, key="YOUR_KEY")

# Flask / any WSGI
from botspotter_sensor import wsgi_sensor
app.wsgi_app = wsgi_sensor(app.wsgi_app, key="YOUR_KEY")

# Django settings.py
MIDDLEWARE += ["botspotter_sensor.django.BotSpotterMiddleware"]
BOTSPOTTER_KEY = "YOUR_KEY"
```

What leaves your server per bot request: header *names*, User-Agent, client IP (discarded on arrival), method,
status, and whether the path was `robots.txt`. Terms: https://botspotter.app/terms. MIT © Bhushan Arun Ladde.
