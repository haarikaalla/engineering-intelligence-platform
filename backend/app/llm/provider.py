"""LLM provider abstraction over LangChain chat models (OpenAI / Anthropic Claude)."""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, provider: str, model) -> None:
        self.provider = provider
        self.model = model

    @property
    def name(self) -> str:
        return f"{self.provider}:{getattr(self.model, 'model_name', None) or getattr(self.model, 'model', '')}"

    def complete(self, system: str, messages: list[tuple[str, str]], max_tokens: int = 1800) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        lc = [SystemMessage(content=system)]
        for role, content in messages:
            lc.append(HumanMessage(content=content) if role == "user" else AIMessage(content=content))
        resp = self.model.bind(max_tokens=max_tokens).invoke(lc)
        content = resp.content
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        return str(content)


@lru_cache
def get_llm() -> LLMClient | None:
    s = get_settings()
    provider = s.llm_provider
    if provider == "none":
        return None
    if provider == "auto":
        provider = "anthropic" if s.anthropic_api_key else "openai" if s.openai_api_key else "none"
    try:
        if provider == "anthropic" and s.anthropic_api_key:
            from langchain_anthropic import ChatAnthropic

            return LLMClient("anthropic", ChatAnthropic(model=s.anthropic_model, api_key=s.anthropic_api_key, temperature=0.1, timeout=120))
        if provider == "openai" and s.openai_api_key:
            from langchain_openai import ChatOpenAI

            return LLMClient("openai", ChatOpenAI(model=s.openai_model, api_key=s.openai_api_key, temperature=0.1, timeout=120))
    except ImportError as exc:
        log.warning("LLM provider '%s' requested but LangChain integration is not installed: %s", provider, exc)
    return None
