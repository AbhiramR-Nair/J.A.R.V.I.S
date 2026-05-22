"""Cost tracker — log-only stub for Day 4.

Today: every LLM call writes one structured log line with token counts and
estimated USD cost. No DB write yet.
Day 5: this same record() method will also INSERT INTO cost_log once the
SQLite schema is in place. The router's call site does not change.
"""

from loguru import logger

from backend.llm.base import LLMResponse

# Pricing per million tokens (USD).
# Source: ai.google.dev/pricing and openai.com/api/pricing (checked: Day 4, 2026-05-22)
# TODO(end of Month 1): re-check these — Gemini pricing has shifted before.
_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.5-pro":   {"input": 1.25,  "output": 5.00},
    "gpt-4o-mini":      {"input": 0.15,  "output": 0.60},
}


def estimate_cost_usd(
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float:
    """Estimate USD cost for one LLM call. Returns 0.0 if tokens or model are unknown."""
    if not prompt_tokens or not completion_tokens or model not in _PRICING:
        return 0.0
    p = _PRICING[model]
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000


class CostTracker:
    async def record(self, response: LLMResponse) -> None:
        # Compute estimated cost and emit a structured log line.
        # The request_id flows in automatically via the Day-3 ContextVar patcher
        # when this is called from inside an HTTP request handler — no bind needed.
        cost = estimate_cost_usd(
            response.model, response.prompt_tokens, response.completion_tokens
        )
        logger.info(
            "llm_call",
            extra={
                "provider": response.provider,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "estimated_usd": round(cost, 6),
            },
        )
        # TODO(Day 5): INSERT INTO cost_log (provider, model, prompt_tokens,
        #              completion_tokens, estimated_usd, request_id, created_at)


# Module-level singleton used by the router.
cost_tracker = CostTracker()
