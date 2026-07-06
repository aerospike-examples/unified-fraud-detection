"""
Gemini access health-check.

Runs a single minimal ``generateContent`` call at startup to verify the
configured API key and model are actually reachable — so a misconfigured key,
disabled API, restricted key, or unavailable model surfaces immediately in the
logs instead of mid-investigation.

Uses the same SDK (``google-genai``) and the same Gemini Developer API that ADK
uses when ``GOOGLE_GENAI_USE_VERTEXAI=FALSE``.
"""

import os
import logging
from typing import Dict, Any

logger = logging.getLogger('investigation.health')


async def check_gemini_access(model: str) -> Dict[str, Any]:
    """Ping the Gemini API once. Returns {ok, error, hint}. Never raises."""
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        return {
            "ok": False,
            "error": "GOOGLE_API_KEY is not set",
            "hint": "Set GOOGLE_API_KEY in your .env (get one at https://aistudio.google.com/apikey).",
        }

    try:
        from google import genai
        from google.genai import types
        from google.genai import errors as genai_errors
    except Exception as e:  # pragma: no cover - import guard
        return {"ok": False, "error": f"google-genai not importable: {e}", "hint": "pip install google-adk"}

    try:
        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model=model,
            contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=1, temperature=0),
        )
        # A response object back at all means the key + model + quota are OK.
        return {"ok": True, "error": None, "hint": None, "model": model}

    except genai_errors.ClientError as e:
        code = getattr(e, "code", None)
        msg = getattr(e, "message", str(e))
        if code in (401, 403) or (code == 400 and "API key" in str(msg)):
            hint = ("API key is invalid/restricted, or the Generative Language API is not "
                    "enabled on its project. Check the key's API restrictions allow "
                    "'Generative Language API' and that the API is enabled.")
        elif code == 404:
            hint = (f"Model '{model}' is not available to this key. Verify the model id "
                    f"(ADK_MODEL) and that your account has access to it.")
        elif code == 429:
            hint = ("Rate limit / quota exceeded. Enable billing on the project or wait — "
                    "the free tier is heavily rate-limited for demos.")
        else:
            hint = "Check the API key and model configuration."
        return {"ok": False, "error": f"ClientError {code}: {msg}", "hint": hint}

    except genai_errors.ServerError as e:
        return {"ok": False, "error": f"ServerError: {e}", "hint": "Transient Gemini API error — retry shortly."}

    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "hint": "Unexpected error reaching the Gemini API."}


async def log_gemini_health(model: str) -> bool:
    """Run the check and log an actionable result. Returns True if healthy."""
    result = await check_gemini_access(model)
    if result["ok"]:
        logger.info(f"✅ Gemini access OK (model={model})")
        return True
    logger.warning("⚠️  Gemini access check FAILED — investigations will fall back to deterministic assessment.")
    logger.warning(f"    Reason: {result['error']}")
    if result.get("hint"):
        logger.warning(f"    Fix:    {result['hint']}")
    return False
