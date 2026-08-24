from __future__ import annotations
from .config import settings, provider_client_options

def openai_models() -> list[str]:
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key, **provider_client_options())
    return sorted(m.id for m in client.models.list())

def anthropic_models() -> list[str]:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, **provider_client_options())
    return sorted(m.id for m in client.models.list())

def openrouter_free_vision_models() -> list[str]:
    import httpx
    r = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
    r.raise_for_status()
    free_vision = []
    for m in r.json()["data"]:
        pricing = m.get("pricing", {})
        is_free = str(pricing.get("prompt")) in ("0", "0.0") and str(pricing.get("completion")) in ("0", "0.0")
        input_mods = (m.get("architecture") or {}).get("input_modalities") or []
        if is_free and "image" in input_mods:
            free_vision.append(m["id"])
    return sorted(free_vision)

def gemini_models() -> list[str]:
    from openai import OpenAI
    client = OpenAI(
        api_key=settings.gemini_api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        **provider_client_options(),
    )
    return sorted(m.id for m in client.models.list())

MODEL_LISTERS = {"openai": openai_models, "anthropic": anthropic_models, "openrouter": openrouter_free_vision_models, "gemini": gemini_models}
