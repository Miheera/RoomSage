"""Structured-output LLM clients. Every failure raises LLMUnavailable, and callers fall
back to the deterministic rules, so a bad key or an outage never breaks the design flow."""

import logging
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMUnavailable(Exception):
    pass


class LLMClient(Protocol):
    def generate(self, schema: type[SchemaT], system: str, user: str) -> SchemaT: ...


class AnthropicClient:
    """Claude through the official SDK. Server-side fallbacks retry a safety decline
    on another model, and any decline that remains becomes LLMUnavailable."""

    def __init__(self, settings: Settings, http_client=None):
        import anthropic

        self._errors = (anthropic.APIConnectionError, anthropic.APIStatusError)
        self._client = anthropic.Anthropic(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            http_client=http_client,
        )
        self._model = settings.llm_model

    def generate(self, schema: type[SchemaT], system: str, user: str) -> SchemaT:
        try:
            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "medium"},
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except self._errors as exc:
            raise LLMUnavailable(str(exc)) from exc
        except ValueError as exc:  # output failed schema validation
            raise LLMUnavailable(str(exc)) from exc
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise LLMUnavailable(f"No usable output (stop_reason={response.stop_reason}).")
        return response.parsed_output


class OpenAICompatibleClient:
    """Any OpenAI-compatible endpoint (Ollama, Groq, Gemini, OpenAI) through instructor,
    which feeds validation errors back to the model and retries."""

    def __init__(self, settings: Settings, http_client=None):
        import instructor
        import openai
        from instructor.core import InstructorRetryException

        self._errors = (openai.OpenAIError, InstructorRetryException)
        self._client = instructor.from_openai(
            openai.OpenAI(
                api_key=settings.llm_api_key or "not-needed",
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
                http_client=http_client,
            ),
            mode=instructor.Mode.JSON,
        )
        self._model = settings.llm_model
        self._retries = settings.llm_max_retries

    def generate(self, schema: type[SchemaT], system: str, user: str) -> SchemaT:
        try:
            return self._client.chat.completions.create(
                model=self._model,
                response_model=schema,
                max_retries=self._retries,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except self._errors as exc:
            raise LLMUnavailable(str(exc)) from exc


def build_client(settings: Settings) -> LLMClient | None:
    if settings.llm_provider == "offline":
        return None
    if settings.llm_provider == "anthropic":
        return AnthropicClient(settings)
    return OpenAICompatibleClient(settings)
