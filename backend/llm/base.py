"""LLM provider interface — the contract every provider implements.

This module is pure abstraction: it imports no SDK, so it can't break from
version drift. Gemini and OpenAI each subclass BaseProvider and translate their
own SDK errors into the shared exception types below, so the router can react
uniformly without knowing anything provider-specific.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# Provider-agnostic result. The API layer (ChatResponse) and the cost tracker
# read these fields and never touch an SDK-specific object — that isolation is
# the whole reason this dataclass exists. `raw` holds the original SDK response
# for debugging only; nothing in normal flow should depend on its shape.
@dataclass
class LLMResponse:
    """Normalized response returned by every BaseProvider.

    Fields:
        text: the model's reply
        provider: 'gemini' or 'openai' — which one actually answered
        model: the exact model id used (e.g. 'gemini-2.5-flash')
        prompt_tokens: input tokens (None if the provider didn't report them)
        completion_tokens: output tokens (None if not reported)
        raw: the original SDK response object (debugging only)
    """

    text: str
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: Any


# Exception hierarchy. Each provider maps its SDK-specific errors onto these
# three subclasses; the router branches on the type to decide whether to fall
# back and how loudly to log. LLMError is the catch-all both for "all providers
# failed" and for errors a fallback won't fix (e.g. a malformed 400 prompt).
class LLMError(Exception):
    """Base exception for any LLM call failure."""


class LLMRateLimitError(LLMError):
    """Provider hit a rate limit or quota. Router catches this and falls back."""


class LLMAuthError(LLMError):
    """Bad/missing API key. Router still falls back (the other key may work),
    but logs loudly — this is a config problem, not a transient one."""


class LLMUnavailableError(LLMError):
    """Network, 5xx, or timeout. Router catches this and falls back."""


class BaseProvider(ABC):
    """Abstract LLM provider. All providers are async-only."""

    name: str  # 'gemini' or 'openai' — used in logs and on LLMResponse

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        # tools: list[dict] | None = None,  # Day 20 — function calling
    ) -> LLMResponse:
        """Send a single user prompt; return the reply.

        Implementations MUST:
        - catch SDK-specific exceptions and re-raise them as LLMRateLimitError /
          LLMAuthError / LLMUnavailableError so the router reacts uniformly
        - populate token counts when the SDK provides them (None otherwise)
        """
        ...
