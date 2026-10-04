"""Outbound HTTP to addresses the USER typed (their LiteLLM proxy, their System One server).

The server calls whatever URL it is given, so every such URL goes through `check_url` first:
  * http or https only, no credentials in the URL;
  * every address the host resolves to must be public or private, never link-local (cloud metadata lives at
    169.254.169.254), unspecified, multicast or reserved;
  * redirects are never followed, so a checked URL cannot bounce somewhere unchecked.
Private and loopback addresses ARE allowed by default, because a homelab LiteLLM lives on the LAN. On a shared or
internet-facing instance set BLOCK_PRIVATE_URLS=1 to refuse them too.
Known gap: the name is resolved here and again when connecting, so DNS rebinding is not covered.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request


class UnsafeURL(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def check_url(url: str) -> str:
    """Return the URL without a trailing slash, or raise UnsafeURL with a message fit to show the user."""
    u = urllib.parse.urlsplit((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeURL("The URL must start with http:// or https://")
    if u.username or u.password:
        raise UnsafeURL("The URL must not contain a user name or password")
    block_private = os.getenv("BLOCK_PRIVATE_URLS", "").lower() in ("1", "true", "yes")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsafeURL(f"Cannot resolve {u.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            raise UnsafeURL(f"{u.hostname} resolves to {ip}, which is not allowed")
        if block_private and (ip.is_private or ip.is_loopback):
            raise UnsafeURL(f"{u.hostname} resolves to a private address and this server blocks those")
    return url.strip().rstrip("/")


def fetch_json(url: str, key: str | None = None, timeout: float = 10.0, max_bytes: int = 5_000_000):
    """GET a JSON document without following redirects. Errors carry only the status, never the response body."""
    headers = {"accept": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    try:
        with OPENER.open(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            raw = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError("Could not connect") from e
    if len(raw) > max_bytes:
        raise RuntimeError("Response too large")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise RuntimeError("Response is not JSON") from e
