"""
LLM configuration store.

In-memory config initialized from environment variables at startup,
updatable at runtime via API endpoints.
"""

import os
import logging

logger = logging.getLogger("investigation.llm_config")

PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": [
            "gemini-2.0-flash",
        ],
    },
}

_config: dict = {}


def init_llm_config():
    """Initialize config from environment variables. Call once at startup."""
    global _config
    provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if provider not in PROVIDERS:
        provider = "gemini"

    _config = {
        "provider": provider,
        "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        "api_key": os.environ.get("GEMINI_API_KEY", ""),
        "base_url": PROVIDERS[provider]["base_url"],
    }
    masked = _mask_key(_config["api_key"])
    logger.info(f"LLM config initialized: provider={provider}, model={_config['model']}, api_key={masked}")


def get_llm_config() -> dict:
    """Return the current LLM configuration."""
    if not _config:
        init_llm_config()
    return dict(_config)


def update_llm_config(provider: str, model: str, api_key: str | None = None):
    """Update LLM config at runtime."""
    global _config
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}. Available: {list(PROVIDERS.keys())}")

    info = PROVIDERS[provider]
    if model not in info["models"]:
        raise ValueError(f"Unknown model '{model}' for provider '{provider}'. Available: {info['models']}")

    _config["provider"] = provider
    _config["model"] = model
    _config["base_url"] = info["base_url"]
    if api_key is not None:
        _config["api_key"] = api_key

    masked = _mask_key(_config["api_key"])
    logger.info(f"LLM config updated: provider={provider}, model={model}, api_key={masked}")


def get_llm_config_safe() -> dict:
    """Return config with the API key masked (safe for API responses)."""
    cfg = get_llm_config()
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "api_key_set": bool(cfg["api_key"]),
        "api_key_hint": _mask_key(cfg["api_key"]),
    }


def get_available_providers() -> list:
    """Return provider list with their models for frontend dropdowns."""
    return [
        {
            "id": pid,
            "name": info["name"],
            "models": info["models"],
        }
        for pid, info in PROVIDERS.items()
    ]


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "..." + key[-4:]
