"""LLM provider interface — the contract every provider implements.

This module is pure abstraction: it imports no SDK, so it can't break from
version drift. Gemini and OpenAI each subclass BaseProvider and translate their
own SDK errors into the shared exception types below, so the router can react
uniformly without knowing anything provider-specific.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel


# Shared fields across both response types.
# raw holds the original SDK response object — the orchestrator needs
# raw.candidates[0].content to build the multi-turn contents list for Gemini.
# Nothing in normal flow should depend on raw's shape beyond that one access.
class _LLMResponseBase(BaseModel):
    provider: str           # 'gemini' or 'openai'
    model: str              # exact model id, e.g. 'gemini-2.5-flash'
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: Any                # SDK response object; arbitrary_types_allowed covers this

    model_config = {"arbitrary_types_allowed": True}


# Returned when the model produces a plain text reply (no tool call).
# parsed is populated only when the caller passed response_schema — it holds
# the already-validated Pydantic instance so callers don't have to parse r.text.
class TextResponse(_LLMResponseBase):
    type: Literal["text"] = "text"
    text: str
    parsed: Any = None


# Returned when the model requests a tool call instead of producing text.
# tool_name and tool_args come directly from the SDK's function_calls property.
class ToolCallResponse(_LLMResponseBase):
    type: Literal["tool_call"] = "tool_call"
    tool_name: str
    tool_args: dict[str, Any]


# Union alias used as the return type throughout the LLM layer and orchestrator.
# Switch on response.type ("text" | "tool_call") or isinstance() — both work.
LLMResponse = TextResponse | ToolCallResponse


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
        prompt: str | list,  # str for single-turn; list[types.Content] for multi-turn tool loop
        *,
        system_prompt: str | None = None,
        tools: list | None = None,       # list[types.Tool] for Gemini; None for fallback
        model: str | None = None,        # per-call model override; None = use provider default
        response_schema: type | None = None,  # Pydantic class for JSON-mode structured output
    ) -> LLMResponse:
        """Send a single user prompt; return the reply.

        Implementations MUST:
        - catch SDK-specific exceptions and re-raise them as LLMRateLimitError /
          LLMAuthError / LLMUnavailableError so the router reacts uniformly
        - populate token counts when the SDK provides them (None otherwise)
        - return ToolCallResponse when the model requests a tool call
        """
        ...
