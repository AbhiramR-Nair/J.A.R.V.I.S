"""Tool: search the web via Tavily and return ranked results for the LLM to synthesize.

Returns a short spoken synthesis plus source links broadcast to the UI.
No memory persistence — search is ephemeral retrieval; the user logs what matters.
"""

from tavily import AsyncTavilyClient

from backend.config.settings import get_settings
from backend.tools import registry

# Module-level lazy singleton. The client holds an httpx connection pool;
# building it once and reusing it avoids per-call construction overhead,
# matching the "construct once" pattern used for STT/TTS clients.
_client: AsyncTavilyClient | None = None


def _get_client() -> AsyncTavilyClient:
    global _client
    if _client is None:
        _client = AsyncTavilyClient(api_key=get_settings().tavily_api_key)
    return _client


@registry.register(
    name="web_search",
    description=(
        "Search the web for current information. Use this when the user asks about "
        "recent papers, latest research, news, or anything that may have happened "
        "after your training cutoff (e.g. 'latest ABL1 inhibitor papers', 'recent "
        "T315I resistance work', 'what's new in protein folding'). Returns ranked "
        "sources with titles, URLs, and content snippets. Prefer this over guessing "
        "at recent facts — always call this tool for anything time-sensitive."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query, phrased as you would type it into a search engine.",
            },
            "max_results": {
                "type": "integer",
                "description": "How many results to return. Default 5.",
            },
        },
        "required": ["query"],
    },
)
async def web_search(query: str, max_results: int = 5) -> dict:
    """Query Tavily, trim result content, broadcast links to the UI.

    Soft-errors on any Tavily failure so the LLM can apologise gracefully
    instead of crashing the turn to ERROR state.
    """
    settings = get_settings()
    client = _get_client()

    try:
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth=settings.tavily_search_depth,
            include_answer=settings.tavily_include_answer,
        )
    except Exception as exc:
        # Tavily raises several exception types (network, quota, bad key).
        # Treat all as soft errors so the orchestrator can carry on.
        return {"error": str(exc), "type": exc.__class__.__name__}

    # Cap each result's content to avoid flooding the LLM context with full
    # web pages. Five "advanced" results uncapped can be thousands of tokens.
    cap = settings.web_search_content_cap
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content", "") or "")[:cap],
        }
        for r in response.get("results", [])
    ]

    # Side effect: push clickable source links to the chat panel via WebSocket.
    # The LLM receives `results` for synthesis; URLs never appear in spoken text.
    await _broadcast_sources(results)

    return {
        "answer": response.get("answer"),   # Tavily's own synthesis; may be None
        "results": results,
    }


async def _broadcast_sources(results: list[dict]) -> None:
    """Broadcast a search_results event so ChatPanel can render clickable links."""
    # Local import avoids a circular import at module load time — tools are imported
    # during the lifespan block, and a top-level import of voice.manager would create
    # a dependency cycle before the app object is fully constructed.
    from backend.api.voice import manager as ws_manager

    sources = [{"title": r["title"], "url": r["url"]} for r in results if r["url"]]
    await ws_manager.broadcast({"type": "search_results", "sources": sources})
