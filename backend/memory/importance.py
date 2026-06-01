"""Importance scorer for memory storage decisions.

Rates a "User: ...\nAssistant: ..." exchange 1–10 for how worth storing it is as
long-term memory. Only exchanges scoring >= importance_threshold (default 4.0) are
written to ChromaDB. Trivial messages (greetings, "ok", "thanks") score 1–3 and are
silently discarded.

Two scoring paths:
  - Heuristic (default): readable if/else rules, no network call.
  - LLM (optional, A/B): original Groq call, gated behind use_llm_importance_scorer.

Failure mode: any exception returns 0 (below every threshold), so a scorer failure
never breaks chat — the exchange is simply not stored.
"""

import re

from loguru import logger

from backend.config.settings import get_settings
from backend.llm.router import get_router

# LLM path prompt — kept intact so the toggle restores original behaviour exactly.
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

# Heuristic constants — single source of truth so rules are easy to tune.
_SAVE_SIGNALS = frozenset([
    'log this', 'remember this', 'note that', 'for the record',
    "don't forget", 'remember that', 'save this', 'keep this in mind',
])
_TRIVIAL_USER_WORDS = frozenset([
    'hi', 'hello', 'ok', 'okay', 'yes', 'no', 'thanks', 'thank you',
    'sure', 'got it', 'cool', 'great', 'perfect', 'alright', 'bye',
    'nope', 'yep', 'yup', 'good', 'fine',
])
_CONCLUSION_WORDS = [
    'we concluded', 'the result was', 'we found that', 'it turns out',
    'confirmed that', 'showed that', 'it seems that', 'key finding',
    'in conclusion', 'therefore', 'thus we',
]


def _heuristic_score(text: str) -> int:
    """Score importance 1–10 using readable if/else rules. No network call.

    Input is the combined "User: ...\\nAssistant: ..." string.
    Weights the user half more — user intent signals importance better than
    assistant verbosity. Default is 3 (below the 4.0 threshold), favouring
    less clutter in ChromaDB.
    """
    lower = text.lower()

    # Extract user side — intent signals importance better than assistant verbosity.
    user_match = re.search(r'user:\s*(.*?)(?=\nassistant:|$)', lower, re.DOTALL)
    user_text = user_match.group(1).strip() if user_match else ""

    # Rule 1 (highest priority): explicit save signal → 10.
    # Catches conversational "remember X" that didn't route through log_to_project.
    if any(sig in lower for sig in _SAVE_SIGNALS):
        return 10

    # Rule 2: trivial user input → 1.
    # Single-word filler, or the whole exchange is very short (under 60 chars).
    user_bare = user_text.strip().rstrip('.!?').strip()
    if user_bare in _TRIVIAL_USER_WORDS or len(text) < 60:
        return 1

    # Rule 3: domain or decision content → 7.
    # Numbers with units (bioinformatics / biochem), mutation notation (T315I, V600E),
    # gene identifiers (ABL1, TP53), or conclusion language.
    has_unit = bool(re.search(
        r'\d+[\s-]?(fold|mg|kg|μm|nm|mm|ml|hz|ms|%|bp|kb|aa|kda|rpm|°c|μg|ng)',
        lower,
    ))
    # [A-Z]{2,}\d+ matches ABL1, TP53; [A-Z]\d{2,}[A-Z] matches T315I, V600E
    has_identifier = bool(re.search(r'\b([A-Z]{2,}\d+[A-Z]?|[A-Z]\d{2,}[A-Z])\b', text))
    has_conclusion = any(w in lower for w in _CONCLUSION_WORDS)
    if has_unit or has_identifier or has_conclusion:
        return 7

    # Rule 4: substantive by length → 5.
    # Long enough that something useful was probably exchanged.
    if len(text) >= 250:
        return 5

    # Default: just below threshold — short, ambiguous exchanges are not stored.
    return 3


def _parse_score(raw: str) -> int:
    """Extract the first integer 1-10 from the LLM's raw response.

    Handles edge cases like "7", "7\\n", "Score: 7", or garbled output.
    Returns 0 if no valid integer is found — caller treats this as 'do not store'.
    """
    match = re.search(r'\b([1-9]|10)\b', raw.strip())
    if match:
        return int(match.group(1))
    logger.warning(f"importance._parse_score: could not parse integer from {raw!r}")
    return 0


async def score(text: str) -> int:
    """Score text importance 1–10 for memory storage decisions.

    Uses the heuristic scorer by default (use_llm_importance_scorer=False).
    Set USE_LLM_IMPORTANCE_SCORER=true in .env to restore the LLM path.
    Returns 0 on any failure so callers treat it as 'skip storage' without crashing.
    Never raises.
    """
    if not get_settings().use_llm_importance_scorer:
        return _heuristic_score(text)

    try:
        result = await get_router().generate(_SCORING_PROMPT.format(text=text))
        parsed = _parse_score(result.text)
        logger.debug(f"importance.score: {parsed} (raw={result.text.strip()!r})")
        return parsed
    except Exception as e:
        logger.warning(f"importance.score failed: {e} — defaulting to 0, will not store")
        return 0
