from __future__ import annotations

import http.client
import socket
import ssl
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .security import FetchError, check_scope, resolve_public

USER_AGENT = "VerifiedExtractionMVP/0.1 (+local-review; respects robots.txt)"
MAX_BYTES = 1_000_000


@dataclass
class Page:
    url: str
    html: str
    fetched_at: str
    redirects: list[str]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_ip = ip

    def connect(self):
        raw = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self.pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)


class HTTPFetcher:
    _locks_guard = threading.Lock()
    _host_locks: dict[str, threading.Lock] = {}
    _last_request: dict[str, float] = {}

    def __init__(self, allowed: set[str], deadline: float):
        self.allowed = allowed
        self.deadline = deadline
        self.robots: dict[str, RobotFileParser] = {}

    def _request(self, url: str) -> tuple[int, dict[str, str], bytes]:
        p = urlsplit(check_scope(url, self.allowed))
        with self._locks_guard:
            lock = self._host_locks.setdefault(p.hostname, threading.Lock())
        with lock:
            return self._request_locked(url, p)

    def _request_locked(self, url: str, p) -> tuple[int, dict[str, str], bytes]:
        port = p.port or (443 if p.scheme == "https" else 80)
        ip = resolve_public(p.hostname, port)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("DEADLINE", "Job deadline reached")
        parser = self.robots.get(self._robots_key(url))
        crawl_delay = parser.crawl_delay(USER_AGENT) if parser else None
        wait = max(0.5, float(crawl_delay or 0)) - (time.monotonic() - self._last_request.get(p.hostname, 0))
        if wait > 0:
            if wait >= remaining:
                raise FetchError("DEADLINE", "Job deadline reached")
            time.sleep(wait)
        self._last_request[p.hostname] = time.monotonic()
        conn_type = PinnedHTTPS if p.scheme == "https" else PinnedHTTP
        conn = conn_type(p.hostname, ip, port, min(8.0, self.deadline - time.monotonic()))
        try:
            conn.request("GET", p.path + ("?" + p.query if p.query else ""),
                         headers={"Host": p.netloc, "User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.8", "Connection": "close"})
            response = conn.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            try:
                content_length = int(headers.get("content-length", "0"))
            except ValueError as exc:
                raise FetchError("BAD_RESPONSE", "Invalid Content-Length") from exc
            if content_length > MAX_BYTES:
                raise FetchError("TOO_LARGE", "Response exceeds size limit")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise FetchError("TOO_LARGE", "Response exceeds size limit")
            return response.status, headers, body
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise FetchError("FETCH_FAILED", "Network request failed") from exc
        finally:
            conn.close()

    def _robots_key(self, url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def _ensure_robots(self, url: str):
        key = self._robots_key(url)
        if key in self.robots:
            return
        try:
            _, _, status, _, body = self._follow(key + "/robots.txt")
            if status in (401, 403, 429) or status >= 500:
                raise FetchError("ROBOTS_UNAVAILABLE", "Robots policy unavailable")
            parser = RobotFileParser()
            parser.parse(body.decode("utf-8", "replace").splitlines() if status == 200 else [])
            self.robots[key] = parser
        except FetchError as exc:
            if exc.code == "FETCH_FAILED":
                raise FetchError("ROBOTS_UNAVAILABLE", "Robots policy unavailable") from exc
            raise

    def _follow(self, url: str, max_redirects: int = 4, check_robots: bool = False) -> tuple[str, list[str], int, dict[str, str], bytes]:
        chain = []
        for _ in range(max_redirects + 1):
            url = check_scope(url, self.allowed)
            if check_robots:
                self._ensure_robots(url)
                if not self.robots[self._robots_key(url)].can_fetch(USER_AGENT, url):
                    raise FetchError("ROBOTS_DENIED", "robots.txt denies this URL")
            status, headers, body = self._request(url)
            if status not in (301, 302, 303, 307, 308):
                return url, chain, status, headers, body
            location = headers.get("location")
            if not location:
                raise FetchError("BAD_REDIRECT", "Redirect has no location")
            chain.append(url)
            url = urljoin(url, location)
        raise FetchError("REDIRECT_LIMIT", "Too many redirects")

    def fetch(self, url: str) -> Page:
        url = check_scope(url, self.allowed)
        final, chain, status, headers, body = self._follow(url, check_robots=True)
        if status in (401, 403, 429):
            raise FetchError("ACCESS_DENIED", f"HTTP {status}; Retry-After: {headers.get('retry-after', 'none')}")
        if status != 200:
            raise FetchError("HTTP_ERROR", f"HTTP {status}")
        if not headers.get("content-type", "text/html").split(";")[0] in {"text/html", "text/plain"}:
            raise FetchError("UNSUPPORTED_CONTENT", "Only HTML or plain text is supported")
        return Page(final, body.decode("utf-8", "replace"), datetime.now(timezone.utc).isoformat(), chain)
