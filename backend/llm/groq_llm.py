"""Groq LLM provider — fallback when Gemini is unavailable.

Groq exposes an OpenAI-compatible REST API, so this uses the standard
openai Python SDK pointed at Groq's base URL. The implementation mirrors
openai.py closely; the only real differences are the base_url, the API key,
and the model name. No new packages needed.

Current model: llama-3.3-70b-versatile (free tier, fast, capable enough for fallback).
"""

from openai import AsyncOpenAI
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
)
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


class GroqLLMProvider(BaseProvider):
    name = "groq"

    def __init__(self) -> None:
        # Groq accepts the OpenAI SDK — just point base_url at Groq's servers.
        # Empty key is allowed at construction (same pattern as GeminiProvider);
        # a missing key surfaces as LLMAuthError on the first real call.
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.groq_api_key or "not-set",
            base_url="https://api.groq.com/openai/v1",
        )
        self._model = settings.groq_model
        self._groq_api_key = settings.groq_api_key

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        # tools: list[dict] | None = None,  # Day 20 — function calling
    ) -> LLMResponse:
        # Fail fast with a clear message if the key was never set.
        if not self._groq_api_key:
            raise LLMAuthError("GROQ_API_KEY is not set in .env")

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except AuthenticationError as e:
            raise LLMAuthError(str(e)) from e
        except APIConnectionError as e:
            raise LLMUnavailableError(str(e)) from e
        except APIStatusError as e:
            if e.status_code and 500 <= e.status_code < 600:
                raise LLMUnavailableError(str(e)) from e
            raise LLMError(str(e)) from e
        except Exception as e:
            raise LLMUnavailableError(str(e)) from e

        text = response.choices[0].message.content or ""
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)

        logger.debug(
            "groq_response",
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
