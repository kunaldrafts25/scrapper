from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


class FetchError(Exception):
    def __init__(self, code: str, message: str, http_requests_started: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_requests_started = http_requests_started


def canonical_url(url: str) -> str:
    try:
        if not isinstance(url, str) or url != url.strip() or any(ord(char) < 32 for char in url):
            raise ValueError()
        p = urlsplit(url)
        if p.scheme.lower() not in ("http", "https") or not p.hostname or p.username or p.password:
            raise ValueError()
        if p.port and not 1 <= p.port <= 65535:
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or host.endswith(".local") or host.endswith(".internal"):
            raise ValueError()
        port = f":{p.port}" if p.port else ""
        netloc = f"[{host}]{port}" if ":" in host else host + port
        return urlunsplit((p.scheme.lower(), netloc, p.path or "/", p.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise FetchError("INVALID_URL", "Only public HTTP(S) URLs without credentials are accepted") from exc


def tracking_key(url: str) -> str:
    p = urlsplit(canonical_url(url))
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}])
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def resolve_public(host: str, port: int) -> str:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise FetchError("DNS_FAILED", "Hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise FetchError("PRIVATE_TARGET", "Target resolves to a non-public address")
    return sorted(addresses)[0]


def check_scope(url: str, allowed: set[str]) -> str:
    clean = canonical_url(url)
    if urlsplit(clean).hostname not in allowed:
        raise FetchError("OUT_OF_SCOPE", "Hostname is outside the allowed scope")
    return clean
