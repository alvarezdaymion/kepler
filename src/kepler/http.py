"""A tiny JSON-over-HTTPS client. Stdlib only.

`transport` is swappable so tests never touch the network. Errors omit provider text, request
details and transport messages, which can reflect credentials.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]

USER_AGENT = "kepler/0.1 (+https://github.com/alvarezdaymion/kepler)"


class HttpError(RuntimeError):
    pass


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                     timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Http:
    def __init__(self, headers: dict[str, str] | None = None, transport: Transport | None = None,
                 timeout: float = 60.0):
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        self.transport = transport or urllib_transport
        self.timeout = timeout

    def _do(self, method: str, url: str, body: bytes | None, extra: dict[str, str]) -> Any:
        try:
            status, raw = self.transport(method, url, {**self.headers, **extra}, body, self.timeout)
        except Exception:
            raise HttpError("%s request failed in transport" % method) from None
        if status >= 400:
            raise HttpError("%s request returned HTTP %d" % (method, status))
        try:
            data = json.loads(raw, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError):
            raise HttpError("%s request returned invalid JSON" % method) from None
        if isinstance(data, dict) and "error" in data:
            raise HttpError("%s request returned a provider error" % method)
        return data

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return self._do("GET", url, None, {})

    def post_form(self, url: str, form: dict[str, str]) -> Any:
        body = urllib.parse.urlencode(form).encode()
        return self._do("POST", url, body, {"Content-Type": "application/x-www-form-urlencoded"})


def _invalid_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")
