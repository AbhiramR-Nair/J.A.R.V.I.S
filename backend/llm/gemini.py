"""Gemini provider using google-genai (the new unified SDK).

NOT google-generativeai — that package is deprecated as of late 2025.
Current SDK: google-genai==2.6.0. Pin this in requirements.txt.
Async entry point: client.aio.models.generate_content(...)
Token fields confirmed: response.usage_metadata.prompt_token_count / .candidates_token_count
"""

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from loguru import logger

from backend.config.settings import get_settings
from backend.llm.base import (
    BaseProvider,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMUnavailableError,
)


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self) -> None:
        # Client construction is cheap; we build one per provider instance.
        # Deliberate: we do NOT check for an empty API key here. An empty key
        # will raise APIError(401) on first call, which get_settings() maps to
        # LLMAuthError — same path as a genuinely bad key. One code path, not two.
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        # tools: list[dict] | None = None,  # Day 20 — function calling
    ) -> LLMResponse:
        # Build a config object only when we have a system prompt; otherwise
        # passing config=None lets the SDK use its own defaults cleanly.
        config = None
        if system_prompt:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
            )

        try:
            # aio namespace is the async counterpart to the sync client.
            # contents= accepts a plain string for single-turn prompts.
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
        except genai_errors.APIError as e:
            # e.code is the raw HTTP status code (confirmed from SDK source).
            # ClientError subclasses APIError for 4xx; ServerError for 5xx.
            # We still branch on the code directly so the mapping is explicit.
            code = e.code
            if code == 429:
                raise LLMRateLimitError(str(e)) from e
            if code in (401, 403):
                raise LLMAuthError(str(e)) from e
            if code and 500 <= code < 600:
                raise LLMUnavailableError(str(e)) from e
            # Gemini returns 400 INVALID_ARGUMENT for bad API keys (not 401/403).
            # Detect by message so we don't misclassify real bad-prompt 400s.
            if code == 400 and "API key" in str(e):
                raise LLMAuthError(str(e)) from e
            # Other 400s (genuinely malformed prompt) — fallback won't help
            raise LLMError(str(e)) from e
        except Exception as e:
            # Network error, timeout, or anything non-API — treat as unavailable
            raise LLMUnavailableError(str(e)) from e

        # Defensively read token counts — usage_metadata can be None on some
        # response types (e.g. safety-filtered). getattr guards against that.
        meta = response.usage_metadata
        prompt_tokens = getattr(meta, "prompt_token_count", None)
        completion_tokens = getattr(meta, "candidates_token_count", None)

        logger.debug(
            "gemini_response",
            extra={"model": self._model, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )

        return LLMResponse(
            text=response.text,
            provider=self.name,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw=response,
        )
