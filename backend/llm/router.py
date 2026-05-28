"""LLM router — tries primary (Gemini), falls back to Groq on transient failures.

The router doesn't know which SDK either provider uses. It only sees LLMResponse
and the three shared exception types. That's the payoff from base.py's abstraction.

Fallback logic:
  - RateLimitError / UnavailableError from primary → log WARNING, try fallback
  - AuthError from primary → log ERROR (config problem), still try fallback
  - Any error from fallback too → raise LLMError("all providers failed")
  - AuthError from primary does NOT skip fallback — the user may have only one
    key working (e.g. Gemini key bad, Groq key good).
"""

from loguru import logger

from backend.llm.base import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMUnavailableError,
    BaseProvider,
)
from backend.llm.gemini import GeminiProvider
from backend.llm.groq_llm import GroqLLMProvider
from backend.services.cost_tracker import cost_tracker


class LLMRouter:
    def __init__(self, primary: BaseProvider, fallback: BaseProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    async def generate(
        self,
        prompt: str | list,  # str for single-turn; list[types.Content] for multi-turn tool loop
        *,
        system_prompt: str | None = None,
        tools: list | None = None,  # list[types.Tool] from registry; None = no function calling
    ) -> LLMResponse:
        primary_error: Exception | None = None

        # --- Try primary ---
        try:
            # tools is threaded to the primary only — Gemini supports function calling.
            response = await self.primary.generate(prompt, system_prompt=system_prompt, tools=tools)
            await cost_tracker.record(response)
            return response

        except LLMAuthError as e:
            # Auth failure is a config problem, not transient — log loudly.
            # Still fall through to the fallback; the other provider may work.
            logger.error(
                f"primary auth error, falling back to {self.fallback.name}",
                extra={"provider": self.primary.name, "reason": str(e)},
            )
            primary_error = e

        except (LLMRateLimitError, LLMUnavailableError) as e:
            logger.warning(
                f"primary unavailable, falling back to {self.fallback.name}",
                extra={"provider": self.primary.name, "reason": type(e).__name__},
            )
            primary_error = e

        # LLMError (e.g. bad prompt 400) is NOT caught here — it re-raises
        # immediately because falling back is unlikely to help.

        # --- Try fallback ---
        # Fallback (Groq LLM) does not support function calling — pass tools=None.
        # It returns TextResponse, which the orchestrator handles normally.
        try:
            response = await self.fallback.generate(prompt, system_prompt=system_prompt, tools=None)
            await cost_tracker.record(response)
            return response

        except LLMError as fallback_error:
            raise LLMError(
                f"all providers failed — "
                f"primary ({self.primary.name}): {primary_error}; "
                f"fallback ({self.fallback.name}): {fallback_error}"
            ) from fallback_error


# Module-level singleton. Built once on first call; reused for all requests.
# Both providers construct their SDK clients here (cheap — no network call).
_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter(primary=GeminiProvider(), fallback=GroqLLMProvider())
    return _router
