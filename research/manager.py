"""Pick the research provider from config. The rest of the program only sees
the ResearchProvider interface (search / fetch_page)."""
from __future__ import annotations

from research.base import ResearchAuthError, ResearchError, ResearchProvider, WebsiteOnlyProvider
from research.firecrawl import FirecrawlProvider
from research.tavily import TavilyProvider

RESEARCH_PROVIDERS = {
    "tavily": TavilyProvider,
    "firecrawl": FirecrawlProvider,
    "website": WebsiteOnlyProvider,   # free, no key, no web search
}


def get_research_provider(name: str, api_key: str, timeout: float = 30, retries: int = 3,
                          retry_base_delay: float = 2.0) -> ResearchProvider:
    key = (name or "").strip().lower()
    if key not in RESEARCH_PROVIDERS:
        raise ResearchError(f"Unknown SEARCH_PROVIDER '{name}'. Choose one of: {', '.join(RESEARCH_PROVIDERS)}")
    cls = RESEARCH_PROVIDERS[key]
    if cls.needs_api_key and not api_key:
        raise ResearchAuthError(
            f"SEARCH_API_KEY is empty. Add your {key} key to .env, "
            "or set SEARCH_PROVIDER=website to use the free website-only mode.")
    return cls(api_key=api_key, timeout=timeout, retries=retries, retry_base_delay=retry_base_delay)


def test_research(provider: ResearchProvider) -> str:
    """Quick check that the research provider works. Uses 1 search credit."""
    if provider.can_search:
        results = provider.search("Tavily Firecrawl web search API", max_results=2)
        return f"search returned {len(results)} result(s)"
    page = provider.fetch_page("https://example.com")
    return f"fetched a test page ('{page.title or page.url}')"
