"""Common interface for research (search + page fetching) providers.

Every provider has:
    search(query)   -> list of SearchResult
    fetch_page(url) -> Page

Pages are fetched directly from the company's website first (free, no API
credits). Paid-per-credit APIs are only used as a fallback.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from utils.helpers import (ROBOTS_AGENT, clean_text, error_detail, http_request, is_http_url,
                           parse_retry_after, retry_call)

MAX_PAGE_BYTES = 2_000_000


class ResearchError(Exception):
    """Base class for research problems (one company can't be researched)."""


class ResearchAuthError(ResearchError):
    """Search API key missing or rejected - every search would fail."""


class ResearchQuotaError(ResearchError):
    """Free-tier / plan credits used up. The program stops using the API."""


class ResearchTemporaryError(ResearchError):
    """Rate limit, timeout or server error - retried with backoff."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class SiteUnreachable(ResearchError):
    """The website could not be reached at all (DNS failure, offline, timeout)."""


class RobotsDisallowed(ResearchError):
    """The site's robots.txt asks automated tools not to fetch this page."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class Page:
    url: str
    title: str = ""
    description: str = ""
    site_name: str = ""
    text: str = ""
    links: List[Tuple[str, str]] = field(default_factory=list)  # (link text, absolute url)
    via: str = "direct"


def parse_html(url: str, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")

    def meta(*keys: str) -> str:
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return clean_text(tag["content"], 400)
        return ""

    title = clean_text(soup.title.get_text() if soup.title else "", 200)
    description = meta("description", "og:description", "twitter:description")
    site_name = meta("og:site_name", "application-name")

    links: List[Tuple[str, str]] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        if is_http_url(href) and href not in seen:
            seen.add(href)
            links.append((clean_text(a.get_text(" "), 80), href))
        if len(links) >= 300:
            break

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()
    text = clean_text(soup.get_text(" "), 20000)
    return Page(url=url, title=title, description=description, site_name=site_name, text=text, links=links)


class ResearchProvider(ABC):
    name = "base"
    needs_api_key = True
    can_search = True
    has_page_api = False
    QUOTA_CODES: Tuple[int, ...] = ()

    def __init__(self, api_key: str = "", timeout: float = 30, retries: int = 3,
                 retry_base_delay: float = 2.0):
        self.api_key = api_key or ""
        self.timeout = timeout
        self.retries = retries
        self.retry_base_delay = retry_base_delay
        self.api_fallback_enabled = True
        self._robots: Dict[str, Optional[RobotFileParser]] = {}

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """Web search. Returns an empty list if the provider can't search."""

    def fetch_page(self, url: str, use_api: bool = True) -> Page:
        """Fetch a page directly from the website (free). If that fails and
        ``use_api`` is True, providers with a page-extraction API try that
        (costs credits). A robots.txt refusal is always respected."""
        try:
            return self._direct_fetch(url)
        except RobotsDisallowed:
            raise
        except ResearchError as direct_error:
            if not (use_api and self.api_fallback_enabled):
                raise
            try:
                return self._api_fetch(url)
            except (ResearchAuthError, ResearchQuotaError):
                raise
            except ResearchError:
                raise direct_error

    def fetch_via_api(self, url: str) -> Page:
        """Fetch a page only through the provider's extraction API (costs credits)."""
        if not (self.has_page_api and self.api_fallback_enabled):
            raise ResearchError("page-extraction API not available")
        return self._api_fetch(url)

    def _api_fetch(self, url: str) -> Page:
        """Provider-specific page extraction. Default: not available."""
        raise ResearchError("no page-extraction API for this provider")

    # ------------------------------------------------------------------ helpers
    def _allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            parser: Optional[RobotFileParser] = None
            try:
                resp = http_request("GET", origin + "/robots.txt", timeout=10)
                if resp.status_code == 200 and "html" not in resp.headers.get("Content-Type", ""):
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except requests.RequestException:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser.can_fetch(ROBOTS_AGENT, url) if parser else True

    def _direct_fetch(self, url: str) -> Page:
        if not is_http_url(url):
            raise ResearchError(f"not a web address: {url}")
        if not self._allowed_by_robots(url):
            raise RobotsDisallowed(f"robots.txt disallows automated access to {url}")
        try:
            resp = http_request("GET", url, timeout=self.timeout, allow_redirects=True, stream=True)
        except requests.Timeout:
            raise SiteUnreachable(f"{url} timed out")
        except requests.RequestException as exc:
            raise SiteUnreachable(f"could not reach {url} ({exc.__class__.__name__})")
        try:
            if resp.status_code >= 400:
                raise ResearchError(f"{url} returned HTTP {resp.status_code}")
            ctype = resp.headers.get("Content-Type", "")
            if ctype and "html" not in ctype.lower():
                raise ResearchError(f"{url} is not a web page ({ctype})")
            chunks, size = [], 0
            for chunk in resp.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_PAGE_BYTES:
                    break
            html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
            return parse_html(resp.url or url, html)
        finally:
            resp.close()

    def _api_post(self, url: str, payload: dict) -> dict:
        """POST to a research API with key, error mapping and retries."""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        def call() -> dict:
            try:
                resp = http_request("POST", url, headers=headers, json=payload, timeout=self.timeout)
            except requests.Timeout:
                raise ResearchTemporaryError(f"{self.name} timed out")
            except requests.RequestException as exc:
                raise ResearchTemporaryError(f"{self.name} connection problem ({exc.__class__.__name__})")
            code = resp.status_code
            if code < 400:
                try:
                    data = resp.json()
                except ValueError:
                    raise ResearchError(f"{self.name} returned a non-JSON response")
                if not isinstance(data, dict):
                    raise ResearchError(f"{self.name} returned an unexpected response")
                return data
            detail = error_detail(resp)
            if code in (401, 403):
                raise ResearchAuthError(f"{self.name} rejected SEARCH_API_KEY (HTTP {code}). {detail}")
            if code in self.QUOTA_CODES:
                raise ResearchQuotaError(f"{self.name} free-tier/plan limit reached (HTTP {code}). {detail}")
            if code == 429:
                raise ResearchTemporaryError(f"{self.name} rate limit (HTTP 429)", parse_retry_after(resp))
            if code == 408 or code >= 500:
                raise ResearchTemporaryError(f"{self.name} server problem (HTTP {code})")
            raise ResearchError(f"{self.name} error (HTTP {code}): {detail}")

        return retry_call(call, attempts=self.retries, base_delay=self.retry_base_delay,
                          retryable=(ResearchTemporaryError,), label=self.name)


class WebsiteOnlyProvider(ResearchProvider):
    """Free, no API key: reads the company's own website only (no web search)."""

    name = "website"
    needs_api_key = False
    can_search = False

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        return []
