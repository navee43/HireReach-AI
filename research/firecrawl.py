"""Firecrawl API (v2). Has a free starter allowance. Key from https://www.firecrawl.dev"""
from __future__ import annotations

from typing import List

from research.base import Page, ResearchError, ResearchProvider, SearchResult
from utils.helpers import clean_text

API = "https://api.firecrawl.dev/v2"


class FirecrawlProvider(ResearchProvider):
    name = "firecrawl"
    has_page_api = True
    QUOTA_CODES = (402,)  # 402 = out of credits

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        data = self._api_post(f"{API}/search", {"query": query, "limit": max_results})
        block = data.get("data")
        items = block.get("web", []) if isinstance(block, dict) else (block or [])
        return [SearchResult(title=clean_text(r.get("title"), 200), url=r.get("url", ""),
                             snippet=clean_text(r.get("description") or r.get("markdown"), 600))
                for r in items if isinstance(r, dict) and r.get("url")]

    def _api_fetch(self, url: str) -> Page:
        data = self._api_post(f"{API}/scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True})
        page = data.get("data") or {}
        meta = page.get("metadata") or {}
        if not page.get("markdown"):
            raise ResearchError(f"Firecrawl could not scrape {url}")
        return Page(url=url, title=clean_text(meta.get("title"), 200),
                    description=clean_text(meta.get("description"), 400),
                    text=clean_text(page["markdown"], 20000), via="firecrawl_scrape")
