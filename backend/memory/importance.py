"""LLM-based importance scorer for memory storage decisions.

Rates a piece of text 1-10 for how worth storing it is as long-term memory.
Used in /chat after each exchange: only exchanges scoring >= importance_threshold
are written to ChromaDB and SQLite. Trivial messages (greetings, "ok", "thanks")
score 1-3 and are silently discarded.

Failure mode: any exception returns 0 (below every threshold), so a scorer
failure never breaks chat — the exchange is simply not stored.
"""

import re

from loguru import logger

from backend.llm.router import get_router

# Prompt designed for consistent single-integer output.
# The scoring guide gives the LLM concrete anchors so it doesn't drift.
_SCORING_PROMPT = """\
You are a memory importance scorer. Rate the following text on a scale of 1-10
for how worth storing it is as a long-term memory for a research assistant.

Scoring guide:
1-3: Trivial (greetings, acknowledgements, simple yes/no, filler)
4-6: Somewhat useful (general facts, loose questions, minor decisions)
7-9: Highly useful (domain-specific facts, decisions, named entities with
     relationships, experimental results, key insights, commitments)
10: Critical (irreplaceable context — project goals, major conclusions,
    key constraints)

Text to score:
{text}

Respond with ONLY a single integer from 1 to 10. No explanation."""


def _parse_score(raw: str) -> int:
    """Extract the first integer 1-10 from the LLM's raw response.

    Handles edge cases like "7", "7\n", "Score: 7", or garbled output.
    Returns 0 if no valid integer is found — caller treats this as 'do not store'.
    """
    match = re.search(r'\b([1-9]|10)\b', raw.strip())
    if match:
        return int(match.group(1))
    logger.warning(f"importance._parse_score: could not parse integer from {raw!r}")
    return 0


async def score(text: str) -> int:
    """Ask the LLM to score text importance on a 1-10 scale.

    Returns 0 on any failure so callers can treat it as 'skip storage'
    without crashing. Never raises.
    """
    try:
        result = await get_router().generate(_SCORING_PROMPT.format(text=text))
        parsed = _parse_score(result.text)
        logger.debug(f"importance.score: {parsed} (raw={result.text.strip()!r})")
        return parsed
    except Exception as e:
        logger.warning(f"importance.score failed: {e} — defaulting to 0, will not store")
        return 0
