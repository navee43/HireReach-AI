"""Tavily search API. Free plan: 1,000 credits/month, no card needed.
Basic search = 1 credit; extract = 1 credit per 5 pages. Key from https://app.tavily.com
"""
from __future__ import annotations

from typing import List

from research.base import Page, ResearchError, ResearchProvider, SearchResult
from utils.helpers import clean_text

API = "https://api.tavily.com"


class TavilyProvider(ResearchProvider):
    name = "tavily"
    has_page_api = True
    QUOTA_CODES = (432, 433)  # 432 = plan limit, 433 = pay-as-you-go limit

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        data = self._api_post(f"{API}/search", {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",   # 1 credit
            "include_answer": False,
        })
        return [SearchResult(title=clean_text(r.get("title"), 200), url=r.get("url", ""),
                             snippet=clean_text(r.get("content"), 600))
                for r in data.get("results") or [] if isinstance(r, dict) and r.get("url")]

    def _api_fetch(self, url: str) -> Page:
        data = self._api_post(f"{API}/extract", {"urls": [url], "extract_depth": "basic", "format": "text"})
        results = data.get("results") or []
        if not results or not results[0].get("raw_content"):
            raise ResearchError(f"Tavily could not extract {url}")
        return Page(url=url, text=clean_text(results[0]["raw_content"], 20000), via="tavily_extract")
