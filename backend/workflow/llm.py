"""
Pluggable LLM provider configuration.

``LLM_PROVIDER`` selects the backend (``gemini`` or ``ollama``). Each engine
maps that to its native client:

- ADK: Gemini model string, or ``LiteLlm`` for Ollama/OpenAI-compatible APIs.
- LangGraph: ``ChatGoogleGenerativeAI`` or ``ChatOllama``.
"""

import os
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LLMConfig:
    """Runtime LLM settings (from environment)."""

    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "mistral"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        return cls(
            provider=provider,
            model=os.environ.get("ADK_MODEL", "gemini-3.5-flash"),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "mistral"),
        )


def resolve_adk_model(config: LLMConfig) -> str:
    """Model identifier passed to ADK LlmAgent constructors."""
    if config.provider == "ollama":
        # LiteLlm format: ollama/<model>
        return f"ollama/{config.ollama_model}"
    return config.model


def build_langgraph_chat_model(config: LLMConfig) -> Any:
    """Return a LangChain chat model for LangGraph nodes."""
    if config.provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            base_url=config.ollama_base_url,
            model=config.ollama_model,
            temperature=0.3,
        )

    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    return ChatGoogleGenerativeAI(
        model=config.model,
        google_api_key=api_key,
        temperature=0.3,
    )
