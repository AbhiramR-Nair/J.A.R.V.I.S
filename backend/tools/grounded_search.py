"""Tool: answer current-fact questions via Gemini Google-Search grounding.

This uses Gemini's built-in google_search tool via an isolated provider call.
It cannot share a request with our function-calling tools — that's why it is its
own tool whose handler runs a separate, function-tool-free Gemini call.

Use for: weather, sports scores, general news — short grounded answers.
Use web_search (Tavily) instead for: technical/research queries.
"""

from backend.llm.router import get_gemini_provider
from backend.tools import registry
from backend.tools.web_search import _broadcast_sources


@registry.register(
    name="grounded_search",
    description=(
        "Answer everyday current-fact questions using Google Search grounding. "
        "Use this for things like current weather, sports scores, or general news "
        "where a short grounded answer is enough (e.g. 'what's the weather today?', "
        "'who won the IPL final?'). For technical or research-paper queries, use "
        "web_search instead — it returns more detailed source content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question to answer with a grounded search.",
            },
        },
        "required": ["query"],
    },
)
async def grounded_search(query: str) -> dict:
    """Run a grounded Gemini call, broadcast source links to the UI.

    Soft-errors on any Gemini failure — grounding hits the same free-tier quota
    as Day 24, so a clean error dict lets the LLM apologise instead of crashing.
    """
    try:
        result = await get_gemini_provider().grounded_search(query)
    except Exception as exc:
        return {"error": str(exc), "type": exc.__class__.__name__}

    # Reuse web_search's broadcast helper — same search_results event shape.
    await _broadcast_sources(result["sources"])
    return result
