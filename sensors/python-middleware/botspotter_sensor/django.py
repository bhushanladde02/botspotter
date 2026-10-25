from __future__ import annotations

from django.conf import settings  # type: ignore

from . import DEFAULT_ENDPOINT, Reporter, _client_ip

_reporter: Reporter | None = None


class BotSpotterMiddleware:
    def __init__(self, get_response):
        global _reporter
        self.get_response = get_response
        if _reporter is None:
            _reporter = Reporter(getattr(settings, "BOTSPOTTER_KEY", ""), getattr(settings, "BOTSPOTTER_ENDPOINT", DEFAULT_ENDPOINT))

    def __call__(self, request):
        response = self.get_response(request)
        try:
            names = [k[5:].replace("_", "-").lower() for k in request.META if k.startswith("HTTP_")]
            hdrs = {k[5:].replace("_", "-").lower(): v for k, v in request.META.items() if k.startswith("HTTP_")}
            _reporter.record(request.META.get("HTTP_USER_AGENT", ""), names,
                             _client_ip(hdrs) or request.META.get("REMOTE_ADDR", ""), hdrs.get("cf-ipcountry", ""),
                             request.method, response.status_code, request.path, hdrs.get("x-bs-ja4", ""))
        except Exception:
            pass
        return response
