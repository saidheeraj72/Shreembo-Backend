"""Web search service using Serper API."""
from typing import List, Dict, Optional
import logging
import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class WebSearchService:
    """Service for web search via Serper API."""

    @staticmethod
    async def search(
        query: str,
        num_results: int = None
    ) -> List[Dict[str, str]]:
        """
        Search the web using Serper API.

        Args:
            query: Search query
            num_results: Number of results to return

        Returns:
            List of search results with title, url, snippet
        """
        if not settings.SERPER_API_KEY:
            return []

        num_results = num_results or settings.SERPER_MAX_RESULTS

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    settings.SERPER_SEARCH_ENDPOINT,
                    headers={
                        "X-API-KEY": settings.SERPER_API_KEY,
                        "Content-Type": "application/json"
                    },
                    json={
                        "q": query,
                        "num": num_results
                    },
                    timeout=10.0
                )
                response.raise_for_status()
                data = response.json()

                results = []
                for item in data.get("organic", [])[:num_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", "")
                    })

                return results

        except httpx.TimeoutException:
            logger.warning("Web search timeout")
            return []
        except httpx.HTTPStatusError as e:
            logger.error("Web search HTTP error: %s", e.response.status_code)
            return []
        except Exception as e:
            logger.error("Web search error: %s", e)
            return []

    @staticmethod
    async def search_news(
        query: str,
        num_results: int = 5
    ) -> List[Dict[str, str]]:
        """
        Search news using Serper API.

        Args:
            query: Search query
            num_results: Number of results to return

        Returns:
            List of news results with title, url, snippet, date
        """
        if not settings.SERPER_API_KEY:
            return []

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://google.serper.dev/news",
                    headers={
                        "X-API-KEY": settings.SERPER_API_KEY,
                        "Content-Type": "application/json"
                    },
                    json={
                        "q": query,
                        "num": num_results
                    },
                    timeout=10.0
                )
                response.raise_for_status()
                data = response.json()

                results = []
                for item in data.get("news", [])[:num_results]:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "date": item.get("date", ""),
                        "source": item.get("source", "")
                    })

                return results

        except Exception as e:
            logger.error("News search error: %s", e)
            return []

    @staticmethod
    def format_results_for_context(results: List[Dict[str, str]]) -> str:
        """Format search results for inclusion in LLM context."""
        if not results:
            return ""

        formatted = []
        for i, result in enumerate(results, 1):
            formatted.append(
                f"[{i}] {result['title']}\n"
                f"URL: {result['url']}\n"
                f"{result['snippet']}\n"
            )

        return "\n".join(formatted)


web_search_service = WebSearchService()
