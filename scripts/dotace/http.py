"""Zdvořilý HTTP klient pro sběr dat z veřejných portálů.

Portály ministerstev a agentur jsou provozně křehké a některé mají WAF, který
při souběžných požadavcích vrací nestandardní kódy. Klient proto drží minimální
prodlevu mezi požadavky na stejný host, opakuje pokusy s exponenciálním
backoffem a respektuje robots.txt.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.robotparser
from typing import Any

import httpx

DEFAULT_USER_AGENT = (
    "DotaceManagerBot/2.0 (+https://dotace.praut.cz; kontakt: ceo@praut.cz)"
)
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Zdroj se nepodařilo stáhnout ani po opakovaných pokusech."""


class Fetcher:
    def __init__(
        self,
        timeout: float = 25.0,
        delay: float = 0.7,
        retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        respect_robots: bool = True,
    ) -> None:
        # Hosty, u kterých provozovatel webu vědomě rozhodl robots.txt obejít.
        self.robots_exempt_hosts: set[str] = set()
        # Některé portály odpovídají desítky sekund; delší čekání je lepší než
        # tichá ztráta celého zdroje.
        self.host_timeouts: dict[str, float] = {}
        self.delay = delay
        self.retries = retries
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "cs,en;q=0.8",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- robots -----------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = urllib.parse.urlparse(url).netloc
        if host in self.robots_exempt_hosts:
            return True
        if host not in self._robots:
            self._robots[host] = self._load_robots(url)
        parser = self._robots[host]
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    # -- fetching ---------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self.allowed(url):
            raise FetchError(f"robots.txt zakazuje {url}")
        host_timeout = self.host_timeouts.get(urllib.parse.urlparse(url).netloc)
        if host_timeout and "timeout" not in kwargs:
            kwargs["timeout"] = host_timeout
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(url)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    return response
                if response.status_code not in RETRY_STATUS:
                    raise FetchError(f"HTTP {response.status_code} pro {url}")
                last_error = FetchError(f"HTTP {response.status_code} pro {url}")
            if attempt + 1 < self.retries:
                time.sleep(1.5 * (2**attempt))
        raise FetchError(f"{url}: {last_error}")

    def text(self, url: str) -> str:
        return self.request("GET", url).text

    def json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs).json()

    def post_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs).json()
