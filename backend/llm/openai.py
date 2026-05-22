"""OpenAI fallback provider using the official openai SDK (AsyncOpenAI).

Current SDK: openai==2.38.0. Pin this in requirements.txt.
This provider is only called when Gemini fails (rate limit, auth, unavailable).
Uses gpt-4o-mini by default — cheap and fast enough for fallback duty.
"""

import openai as openai_sdk
from loguru import logger
from openai import AsyncOpenAI

from backend.config.settings import get_settings
from backend.llm.base import (
    BaseProvider,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMUnavailableError,
)


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self) -> None:
        # Same empty-key approach as GeminiProvider: don't fail at construction.
        # An empty/bad key raises AuthenticationError on first call → LLMAuthError.
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        # tools: list[dict] | None = None,  # Day 20 — function calling
    ) -> LLMResponse:
        # Build messages list. System message is optional; omit it entirely
        # rather than sending an empty string (some models behave oddly with
        # an empty system message).
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except openai_sdk.RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except openai_sdk.AuthenticationError as e:
            raise LLMAuthError(str(e)) from e
        except (openai_sdk.APIError, openai_sdk.APIConnectionError) as e:
            # Covers InternalServerError (5xx), timeouts, and network failures.
            raise LLMUnavailableError(str(e)) from e
        except Exception as e:
            raise LLMUnavailableError(str(e)) from e

        text = response.choices[0].message.content or ""

        # usage can technically be None if stream=True (not the case here),
        # but guard defensively.
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)

        logger.debug(
            "openai_response",
            extra={"model": self._model, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )

        return LLMResponse(
            text=text,
            provider=self.name,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw=response,
        )
