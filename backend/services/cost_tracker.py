"""Cost tracker — logs every LLM call and persists it to cost_log.

Every call to record() writes a structured log line (for the log file)
and inserts a row into cost_log (for the DB). The router's call site is
unchanged from Day 4 — only this implementation changed.
"""

from datetime import datetime, timezone

from loguru import logger

from backend.config.logging import request_id_var
from backend.database.db import get_db
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

        # Persist to cost_log. Wrapped in try/except so a DB hiccup never
        # breaks the chat response — cost tracking is best-effort.
        try:
            conn = get_db()
            with conn:
                conn.execute(
                    """
                    INSERT INTO cost_log
                        (provider, model, prompt_tokens, completion_tokens,
                         estimated_usd, request_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        response.provider or "",
                        response.model or "",
                        response.prompt_tokens or 0,
                        response.completion_tokens or 0,
                        round(cost, 6),
                        request_id_var.get(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except Exception as exc:
            logger.error(f"cost_tracker: failed to write cost_log row: {exc}")


# Module-level singleton used by the router.
cost_tracker = CostTracker()
