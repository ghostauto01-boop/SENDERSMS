"""Groq + Gemini provider layer for the AI Agent.

WHY THIS MODULE EXISTS
----------------------
The first version of the agent hard-coded two model names:

    GROQ_MODEL_DEFAULT   = "llama-3.3-70b-versatile"
    GEMINI_MODEL_DEFAULT = "gemini-1.5-flash"

Both were retired by the providers. Groq answered ``404 model_not_found`` and
Gemini answered ``404 models/gemini-1.5-flash is not found for API version
v1beta``. Because the agent turned every provider error into a bare HTTP 500,
the operator saw "Internal Server Error" on every chat and the user's message
was never even written to the database.

The rule this module encodes: **the app never guesses a model name.**
The live model list from the provider is the only source of truth. A model is
only ever chosen from that list, so a retired model cannot come back from the
dead through a constant tucked away in the code.

Everything provider-specific lives here:

  * key storage (encrypted, never returned; only a masked hint leaves the API)
  * live model listing (``/openai/v1/models`` and ``/v1beta/models``)
  * model selection + per-provider persistence in ``system_settings``
  * a single ``complete()`` call that normalises OpenAI and Gemini shapes
  * classification of every failure into one of seven stable codes
  * automatic recovery: a stale model is replaced from the live list and the
    call is retried once

The agent service imports this and stays about *tools*; the API layer imports
this and stays about *HTTP*.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemSetting
from app.security.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

PROVIDERS = ("groq", "gemini")

GROQ_BASE = "https://api.groq.com/openai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_CHAT_URL = f"{GROQ_BASE}/chat/completions"
GROQ_MODELS_URL = f"{GROQ_BASE}/models"
GROQ_TRANSCRIBE_URL = f"{GROQ_BASE}/audio/transcriptions"
GEMINI_MODELS_URL = f"{GEMINI_BASE}/models"

# Where the operator's choices live. Keys are namespaced ``ai_agent.*`` so they
# sit beside the legacy ``ai_agent.groq_api_key`` rows written by the previous
# implementation — no migration, no lost keys.
KEY_SETTING = {"groq": "ai_agent.groq_api_key", "gemini": "ai_agent.gemini_api_key"}
MODEL_SETTING = {"groq": "ai_agent.model.groq", "gemini": "ai_agent.model.gemini"}
DEFAULT_PROVIDER_SETTING = "ai_agent.default_provider"
AUTO_FALLBACK_SETTING = "ai_agent.auto_fallback"
AUTO_APPROVE_SETTING = "ai_agent.auto_approve"
VOICE_SETTING = "ai_agent.voice"
GLOBAL_MEMORY_SETTING = "ai_agent.global_memory"  # legacy single-string memory

#: Providers are asked for a *short* list. We only offer models that a chat
#: turn can actually use, so the dropdown never fills with audio/TTS/guard
#: models that would fail the moment they were picked.
_GROQ_EXCLUDE = re.compile(
    r"(whisper|tts|guard|distil-whisper|playai|prompt-guard|orpheus)", re.I
)
_GEMINI_EXCLUDE = re.compile(r"(embedding|aqa|imagen|veo|tts|image-generation)", re.I)
#: Preferences used to auto-select a default. Order matters: the first pattern
#: that matches a live model wins, so the newest capable model is chosen and
#: nothing is invented. Written as *fragments*, never full names.
_GROQ_PREFERENCE = (
    # Prefer a responsive, capable tool-calling model for new installations.
    # Larger models remain available in Settings for tasks that need them.
    re.compile(r"^openai/gpt-oss-20b$"),
    re.compile(r"^llama-3\.1-8b-instant$"),
    re.compile(r"^openai/gpt-oss-120b$"),
    re.compile(r"^moonshotai/kimi-k2"),
    re.compile(r"^qwen/qwen3"),
    re.compile(r"llama-3\.3-70b"),
    re.compile(r"llama-4"),
    re.compile(r"llama-3\.1-70b"),
    re.compile(r"^openai/gpt-oss"),
    re.compile(r"llama-3\.1-8b"),
    re.compile(r"llama3-70b"),
    re.compile(r"llama"),
)
_GEMINI_PREFERENCE = (
    re.compile(r"gemini-2\.5-flash-lite"),
    re.compile(r"gemini-2\.5-flash$"),
    re.compile(r"gemini-2\.5-pro"),
    re.compile(r"gemini-2\.0-flash$"),
    re.compile(r"gemini-2\.0-flash"),
    re.compile(r"gemini-flash"),
    re.compile(r"gemini.*pro"),
    re.compile(r"gemini"),
)


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------
#: Stable, documented codes. The UI switches on these, so they are part of the
#: public contract and must not be renamed casually.
ERROR_CODES = (
    "key_missing",
    "key_invalid",
    "model_unavailable",
    "rate_limited",
    "timeout",
    "safety_blocked",
    "provider_down",
    "bad_request",
    "upstream_error",
)

#: Human hint per code. Written for the operator, not for a developer: what
#: happened, and the one thing they can do about it.
_ERROR_HINTS = {
    "key_missing": "Add a key in Settings → AI Agent. The agent cannot call the model without one.",
    "key_invalid": "The saved key was rejected. Open Settings → AI Agent, paste a fresh key and press Test.",
    "model_unavailable": "The model no longer exists at the provider. Pick another model in Settings, or let the agent choose one for you.",
    "rate_limited": "The provider is throttling this key. Wait a moment and retry, or switch provider.",
    "timeout": "The provider did not answer in time. Retry, or switch provider.",
    "safety_blocked": "The provider refused this request on safety grounds. Rephrase it and try again.",
    "provider_down": "The provider could not be reached. Check the connection, then retry.",
    "bad_request": "The provider rejected the request. This is a bug in the agent — retry, and if it persists switch provider.",
    "upstream_error": "The provider returned an error. Retry in a moment.",
}

#: Whether the same request could succeed unchanged.
_RETRYABLE = {
    "key_missing": False,
    "key_invalid": False,
    "model_unavailable": True,
    "rate_limited": True,
    "timeout": True,
    "safety_blocked": False,
    "provider_down": True,
    "bad_request": False,
    "upstream_error": True,
}


@dataclass
class ProviderError(Exception):
    """A classified provider failure.

    Carries everything the UI needs to render an honest error bubble with the
    right buttons: the stable code, which provider/model was in play, the HTTP
    status, a one-line message and a hint.

    ``str(exc)`` is deliberately the human ``message`` — several call sites log
    ``str(exc)`` and must never leak a key or a raw provider body into a log.
    """

    code: str
    provider: str
    message: str
    status: Optional[int] = None
    model: Optional[str] = None
    retryable: bool = True
    detail: Optional[str] = None
    #: Set when a request had to be retried on a replacement model.
    recovered_from: Optional[str] = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)
        self.retryable = _RETRYABLE.get(self.code, True)

    @property
    def hint(self) -> str:
        return _ERROR_HINTS.get(self.code, "Retry the request.")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "provider": self.provider,
            "status": self.status,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "model": self.model,
            "detail": self.detail,
            "recovered_from": self.recovered_from,
        }


def _classify(provider: str, status: Optional[int], body: str, model: Optional[str]) -> ProviderError:
    """Turn an HTTP failure into one of the stable codes.

    Providers report the same condition differently — Groq sends
    ``{"error": {"message": "model_not_found"}}`` under a 404, Gemini sends a
    ``NOT_FOUND`` text under a 404 and a 400 with ``API key not valid``. The
    mapping below is written against their observed behaviour, not their docs.
    """
    text = (body or "")[:2000]
    low = text.lower()
    status = status or 0

    if status in (404, 410) or "model_not_found" in low or "is not found for api version" in low:
        return ProviderError(
            code="model_unavailable",
            provider=provider,
            status=status,
            model=model,
            message=f"{provider.title()} does not offer the model “{model}”.",
            detail=text[:600] or None,
        )
    if status == 429 or "rate limit" in low or "quota" in low or "resource_exhausted" in low:
        return ProviderError(
            code="rate_limited",
            provider=provider,
            status=status,
            model=model,
            message=f"{provider.title()} is rate-limiting this key.",
            detail=text[:600] or None,
        )
    if status in (401, 403) or "api key not valid" in low or "invalid_api_key" in low or "permission_denied" in low:
        return ProviderError(
            code="key_invalid",
            provider=provider,
            status=status,
            model=model,
            message=f"{provider.title()} rejected the saved API key.",
            detail=text[:600] or None,
        )
    if "safety" in low or "blocked" in low or "recitation" in low or "harassment" in low:
        return ProviderError(
            code="safety_blocked",
            provider=provider,
            status=status,
            model=model,
            message=f"{provider.title()} refused the request on safety grounds.",
            detail=text[:600] or None,
        )
    if status in (400, 422):
        return ProviderError(
            code="bad_request",
            provider=provider,
            status=status,
            model=model,
            message=f"{provider.title()} rejected the request.",
            detail=text[:600] or None,
        )
    if status >= 500:
        return ProviderError(
            code="provider_down",
            provider=provider,
            status=status,
            model=model,
            message=f"{provider.title()} returned a server error ({status}).",
            detail=text[:600] or None,
        )
    return ProviderError(
        code="upstream_error",
        provider=provider,
        status=status,
        model=model,
        message=f"{provider.title()} returned an error ({status or 'no status'}).",
        detail=text[:600] or None,
    )


def _timeout(provider: str, model: Optional[str], exc: Exception) -> ProviderError:
    return ProviderError(
        code="timeout",
        provider=provider,
        model=model,
        message=f"{provider.title()} did not answer in time.",
        detail=str(exc)[:300],
    )


def _unreachable(provider: str, model: Optional[str], exc: Exception) -> ProviderError:
    return ProviderError(
        code="provider_down",
        provider=provider,
        model=model,
        message=f"Could not reach {provider.title()}.",
        detail=str(exc)[:300],
    )


# ---------------------------------------------------------------------------
# Settings (keys + model choice), encrypted at rest
# ---------------------------------------------------------------------------
async def _get_row(db: AsyncSession, key: str) -> Optional[SystemSetting]:
    return (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()


async def _get_value(db: AsyncSession, key: str) -> Optional[str]:
    row = await _get_row(db, key)
    return row.value if row is not None and row.value is not None else None


async def _set_value(db: AsyncSession, key: str, value: str, *, category: str = "ai_agent", description: str | None = None) -> None:
    row = await _get_row(db, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value, category=category, description=description))
    else:
        row.value = value
    await db.flush()


async def get_api_key(db: AsyncSession, provider: str) -> Optional[str]:
    """Read a key from the DB, else from the environment, and decrypt it.

    The environment fallback matters for fresh deployments: an operator who
    sets ``GROQ_API_KEY`` in Render's dashboard should not also have to paste
    the key into the UI before the agent works.
    """
    raw = await _get_value(db, KEY_SETTING[provider])
    if raw:
        try:
            return decrypt_value(raw)
        except Exception:
            # Historical rows were written before encryption existed; use as-is.
            return raw
    from app.config import settings as app_settings

    env_name = "GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY"
    return getattr(app_settings, env_name, None) or None


async def set_api_key(db: AsyncSession, provider: str, api_key: Optional[str]) -> None:
    if api_key is None:
        return
    key = KEY_SETTING[provider]
    if not api_key.strip():
        await _set_value(db, key, "", description=f"{provider} API key for the AI Agent (cleared)")
        return
    await _set_value(
        db, key, encrypt_value(api_key.strip()),
        description=f"{provider} API key for the AI Agent (encrypted at rest)",
    )


async def get_keys(db: AsyncSession) -> dict[str, Optional[str]]:
    return {p: await get_api_key(db, p) for p in PROVIDERS}


def mask(value: Optional[str]) -> Optional[str]:
    """The only form of a key that may leave the server.

    ``gsk_abc…ZJGp`` style: enough to recognise *which* key is installed,
    useless to anyone who reads it.
    """
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


async def keys_summary(db: AsyncSession) -> dict:
    """Non-secret view of the two keys, for the settings card."""
    out: dict[str, Any] = {}
    for provider in PROVIDERS:
        key = await get_api_key(db, provider)
        out[f"{provider}_configured"] = bool(key)
        out[f"{provider}_hint"] = mask(key)
    return out


async def get_model(db: AsyncSession, provider: str) -> Optional[str]:
    return await _get_value(db, MODEL_SETTING[provider])


async def set_model(db: AsyncSession, provider: str, model: str) -> None:
    await _set_value(
        db, MODEL_SETTING[provider], model,
        description=f"AI Agent model for {provider} (chosen from the live provider list)",
    )


async def get_default_provider(db: AsyncSession) -> str:
    value = await _get_value(db, DEFAULT_PROVIDER_SETTING)
    return value if value in PROVIDERS else "groq"


async def get_flag(db: AsyncSession, setting: str, default: bool) -> bool:
    value = await _get_value(db, setting)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


async def set_flag(db: AsyncSession, setting: str, value: bool) -> None:
    await _set_value(db, setting, "true" if value else "false")


# ---------------------------------------------------------------------------
# Live model discovery
# ---------------------------------------------------------------------------
@dataclass
class ModelInfo:
    id: str
    label: str
    #: True when the model can call tools. Everything in the curated list can;
    #: the flags let the UI explain why a model was chosen.
    tools: bool = True
    context: Optional[int] = None


def _groq_usable(model_id: str) -> bool:
    if _GROQ_EXCLUDE.search(model_id):
        return False
    # Groq's catalogue also lists preview/experimental entries that a chat turn
    # cannot use with tool calling. Keep the list to what the docs call
    # production text models.
    return True


def _gemini_usable(model: dict) -> bool:
    methods = model.get("supportedGenerationMethods") or []
    if "generateContent" not in methods:
        return False
    name = (model.get("name") or "").replace("models/", "")
    if _GEMINI_EXCLUDE.search(name):
        return False
    return True


def _preference_rank(provider: str, model_id: str) -> int:
    patterns = _GROQ_PREFERENCE if provider == "groq" else _GEMINI_PREFERENCE
    for index, pattern in enumerate(patterns):
        if pattern.search(model_id):
            return index
    return len(patterns) + 1


def pick_default(provider: str, models: list[ModelInfo]) -> Optional[str]:
    """Choose a model from a *live* list — never a name from the source code."""
    if not models:
        return None
    ranked = sorted(models, key=lambda m: (_preference_rank(provider, m.id), m.id))
    with_tools = [m for m in ranked if m.tools]
    return (with_tools or ranked)[0].id


def is_vision_model(provider: str, model: str) -> bool:
    """Whether a model id is from a provider family that accepts image input."""
    model_id = (model or "").lower()
    if provider == "gemini":
        return model_id.startswith("gemini-")
    return bool(re.search(r"(vision|llama-4|qwen[^/]*[-.]vl|/[^/]*vl-)", model_id))


def pick_vision_model(provider: str, models: list[ModelInfo]) -> Optional[str]:
    """Choose an image-capable model from a provider's live catalogue."""
    candidates = [model for model in models if is_vision_model(provider, model.id)]
    if not candidates:
        return None
    if provider == "gemini":
        return pick_default(provider, candidates)
    return min(
        candidates,
        key=lambda model: (
            0 if "llama-4" in model.id.lower() else 1 if "vision" in model.id.lower() else 2,
            _preference_rank(provider, model.id),
            model.id,
        ),
    ).id


async def resolve_vision_model(
    db: AsyncSession, provider: str, *, api_key: Optional[str] = None
) -> str:
    """Resolve an image-capable model from a live provider list when needed."""
    saved = await get_model(db, provider)
    if saved and is_vision_model(provider, saved):
        return saved
    models = await list_models(db, provider, api_key=api_key)
    selected = pick_vision_model(provider, models)
    if selected:
        return selected
    raise ProviderError(
        code="model_unavailable",
        provider=provider,
        model=saved,
        message=f"No vision-capable {provider.title()} model is available for this image. Add a Gemini key or choose another model.",
    )


async def list_models(
    db: AsyncSession,
    provider: str,
    *,
    api_key: Optional[str] = None,
    timeout: float = 20.0,
) -> list[ModelInfo]:
    """Ask the provider what it currently serves.

    Raises :class:`ProviderError` with ``key_missing`` / ``key_invalid`` /
    ``provider_down`` so callers can show the right message instead of an
    empty dropdown.
    """
    key = api_key or await get_api_key(db, provider)
    if not key:
        raise ProviderError(
            code="key_missing",
            provider=provider,
            message=f"No {provider.title()} API key is configured.",
        )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider == "groq":
                response = await client.get(GROQ_MODELS_URL, headers={"Authorization": f"Bearer {key}"})
                if response.status_code != 200:
                    raise _classify(provider, response.status_code, response.text, None)
                payload = response.json()
                out: list[ModelInfo] = []
                for item in payload.get("data") or []:
                    model_id = item.get("id") or ""
                    if not model_id or not _groq_usable(model_id):
                        continue
                    ctx = item.get("context_window") or item.get("context_length")
                    out.append(ModelInfo(id=model_id, label=model_id, tools=True, context=ctx))
            else:
                response = await client.get(GEMINI_MODELS_URL, params={"key": key, "pageSize": 200})
                if response.status_code != 200:
                    raise _classify(provider, response.status_code, response.text, None)
                payload = response.json()
                out = []
                for item in payload.get("models") or []:
                    if not _gemini_usable(item):
                        continue
                    model_id = (item.get("name") or "").replace("models/", "")
                    out.append(
                        ModelInfo(
                            id=model_id,
                            label=item.get("displayName") or model_id,
                            tools=True,
                            context=item.get("inputTokenLimit"),
                        )
                    )
    except ProviderError:
        raise
    except httpx.TimeoutException as exc:
        raise _timeout(provider, None, exc) from exc
    except Exception as exc:  # network, JSON, DNS
        raise _unreachable(provider, None, exc) from exc

    out.sort(key=lambda m: (_preference_rank(provider, m.id), m.id))
    return out


async def resolve_model(
    db: AsyncSession,
    provider: str,
    *,
    refresh: bool = False,
    api_key: Optional[str] = None,
) -> str:
    """Return a model that the provider actually serves right now.

    Order of preference:
      1. the saved model, if the live list still contains it;
      2. the best model from the live list (and we persist that choice so the
         next call starts from a valid model);
      3. only if the live list cannot be fetched at all, the saved model so a
         transient network blip does not wipe the operator's choice.
    """
    saved = await get_model(db, provider)
    if saved and not refresh:
        return saved
    try:
        models = await list_models(db, provider, api_key=api_key)
    except ProviderError:
        if saved:
            return saved
        raise
    if not models:
        if saved:
            return saved
        raise ProviderError(
            code="model_unavailable",
            provider=provider,
            message=f"{provider.title()} returned no usable chat models.",
        )
    ids = {m.id for m in models}
    if saved and saved in ids:
        return saved
    chosen = pick_default(provider, models) or models[0].id
    if chosen != saved:
        await set_model(db, provider, chosen)
        logger.info("AI Agent: %s model re-selected from live list -> %s", provider, chosen)
    return chosen


async def ensure_model(db: AsyncSession, provider: str, *, api_key: Optional[str] = None) -> tuple[str, bool]:
    """Return ``(model, was_recovered)`` for a *first* use.

    ``was_recovered`` is True when the stored name was gone and a replacement
    was picked — the chat then tells the operator which model answered.
    """
    saved = await get_model(db, provider)
    model = await resolve_model(db, provider, refresh=not saved, api_key=api_key)
    return model, bool(saved and model != saved)


# ---------------------------------------------------------------------------
# Chat completion
# ---------------------------------------------------------------------------
def _convert_tools_for_gemini(tools: list[dict]) -> list[dict]:
    decls = []
    for tool in tools or []:
        fn = tool.get("function") or tool
        decls.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return [{"functionDeclarations": decls}] if decls else []


def _messages_for_gemini(messages: list[dict], system_text: str) -> list[dict]:
    """Translate OpenAI-shaped messages (including tool turns) to Gemini."""
    contents: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role == "user":
            parts = [{"text": message.get("content") or ""}]
            for image in message.get("images") or []:
                data = image.get("data")
                mime_type = image.get("mime_type")
                if data and mime_type:
                    parts.append({"inlineData": {"mimeType": mime_type, "data": data}})
            contents.append({"role": "user", "parts": parts})
        elif role == "assistant":
            parts: list[dict] = []
            text = message.get("content") or ""
            if text:
                parts.append({"text": text})
            for call in message.get("tool_calls") or []:
                fn = call.get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except Exception:
                    args = {}
                parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
            if parts:
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            name = message.get("name") or "tool"
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": {"result": message.get("content") or ""},
                            }
                        }
                    ],
                }
            )
    return contents


def _messages_for_groq(messages: list[dict]) -> list[dict]:
    """Convert our private ``images`` field to OpenAI-compatible image parts."""
    converted = []
    for message in messages:
        images = message.get("images") or []
        if not images:
            converted.append(message)
            continue
        content = message.get("content") or ""
        parts = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
        parts = list(parts)
        for image in images:
            data = image.get("data")
            mime_type = image.get("mime_type")
            if data and mime_type:
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{data}"},
                })
        item = {key: value for key, value in message.items() if key != "images"}
        item["content"] = parts
        converted.append(item)
    return converted


def _normalise_gemini(data: dict) -> dict:
    """Gemini response -> the OpenAI ``choices[0].message`` shape the loop uses."""
    candidates = data.get("candidates") or []
    if not candidates:
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            raise ProviderError(
                code="safety_blocked",
                provider="gemini",
                message=f"Gemini blocked the prompt ({blocked}).",
            )
        return {"choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]}
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = ""
    tool_calls = []
    for part in parts:
        if "text" in part:
            text += part["text"]
        call = part.get("functionCall")
        if call:
            tool_calls.append(
                {
                    "id": f"call_{int(time.time() * 1000) % 10**9}{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": call.get("name"),
                        "arguments": json.dumps(call.get("args") or {}),
                    },
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    finish = "tool_calls" if tool_calls else (candidate.get("finishReason") or "stop").lower()
    return {"choices": [{"message": message, "finish_reason": finish}]}


async def complete(
    provider: str,
    *,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    system_text: str = "",
    temperature: float = 0.4,
    max_tokens: int = 4096,
    timeout: float = 90.0,
) -> dict:
    """One non-streaming model call, normalised to the OpenAI response shape."""
    if not api_key:
        raise ProviderError(
            code="key_missing",
            provider=provider,
            model=model,
            message=f"No {provider.title()} API key is configured.",
        )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider == "groq":
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": _messages_for_groq(messages),
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                response = await client.post(
                    GROQ_CHAT_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if response.status_code != 200:
                    raise _classify(provider, response.status_code, response.text, model)
                return response.json()
            body: dict[str, Any] = {
                "contents": _messages_for_gemini(messages, system_text),
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            }
            if system_text:
                body["systemInstruction"] = {"parts": [{"text": system_text}]}
            if tools:
                body["tools"] = _convert_tools_for_gemini(tools)
            response = await client.post(
                f"{GEMINI_BASE}/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json=body,
            )
            if response.status_code != 200:
                raise _classify(provider, response.status_code, response.text, model)
            return _normalise_gemini(response.json())
    except ProviderError:
        raise
    except httpx.TimeoutException as exc:
        raise _timeout(provider, model, exc) from exc
    except Exception as exc:
        raise _unreachable(provider, model, exc) from exc


async def complete_with_recovery(
    db: AsyncSession,
    provider: str,
    *,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    system_text: str = "",
    temperature: float = 0.4,
    max_tokens: int = 4096,
    timeout: float = 90.0,
    on_notice=None,
) -> tuple[dict, str]:
    """Call the model; if the model name is stale, replace it and retry once.

    Returns ``(response, model_used)``. ``on_notice`` (sync or async callable)
    is invoked with a human sentence when a recovery happened, so the chat can
    say *which* model answered instead of silently switching.
    """
    try:
        return await complete(
            provider,
            api_key=api_key,
            model=model,
            messages=messages,
            tools=tools,
            system_text=system_text,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        ), model
    except ProviderError as exc:
        if exc.code != "model_unavailable":
            raise
        logger.warning("AI Agent: model %s/%s unavailable, refreshing list", provider, model)
        replacement: Optional[str] = None
        try:
            models = await list_models(db, provider, api_key=api_key)
            ids = {m.id for m in models}
            replacement = pick_default(provider, [m for m in models if m.id != model])
            if replacement and replacement not in ids:
                replacement = None
        except ProviderError:
            replacement = None
        if not replacement:
            raise
        await set_model(db, provider, replacement)
        note = (
            f"The saved model “{model}” is no longer available at "
            f"{provider.title()}. Switched to “{replacement}” and retried."
        )
        if on_notice is not None:
            result = on_notice(note)
            if asyncio.iscoroutine(result):
                await result
        logger.info("AI Agent: recovered %s from %s -> %s", provider, model, replacement)
        try:
            response = await complete(
                provider,
                api_key=api_key,
                model=replacement,
                messages=messages,
                tools=tools,
                system_text=system_text,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return response, replacement
        except ProviderError as retry_exc:
            retry_exc.recovered_from = model
            raise retry_exc from retry_exc


# ---------------------------------------------------------------------------
# Health ping
# ---------------------------------------------------------------------------
@dataclass
class ProviderHealth:
    provider: str
    #: ok | key_missing | key_invalid | model_unavailable | rate_limited | unreachable
    state: str = "key_missing"
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    message: str = ""
    hint: str = ""
    checked_at: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state,
            "ok": self.ok,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "hint": self.hint,
            "checked_at": self.checked_at,
            **self.extra,
        }


_HEALTH_STATE = {
    "key_missing": "key_missing",
    "key_invalid": "key_invalid",
    "model_unavailable": "model_unavailable",
    "rate_limited": "rate_limited",
    "timeout": "unreachable",
    "provider_down": "unreachable",
    "upstream_error": "unreachable",
    "bad_request": "unreachable",
    "safety_blocked": "ok",
}


async def ping(db: AsyncSession, provider: str) -> ProviderHealth:
    """Prove the provider works end to end with a one-token completion.

    A configured key is not the same as a working provider — that was the
    original health bug, which reported both providers "configured" while
    every chat failed on a dead model name.
    """
    from datetime import datetime, timezone

    health = ProviderHealth(provider=provider, checked_at=datetime.now(timezone.utc).isoformat())
    key = await get_api_key(db, provider)
    if not key:
        health.state = "key_missing"
        health.message = f"No {provider.title()} key is saved."
        health.hint = _ERROR_HINTS["key_missing"]
        return health

    started = time.perf_counter()
    try:
        model, was_recovered = await ensure_model(db, provider, api_key=key)
        health.model = model
        if was_recovered:
            health.extra["note"] = "The previously saved model was retired; a current one was selected."
        await complete(
            provider,
            api_key=key,
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            system_text="Reply with the single word: pong",
            max_tokens=16,
            timeout=20.0,
        )
        health.state = "ok"
        health.message = f"{provider.title()} answered with {model}."
    except ProviderError as exc:
        health.state = _HEALTH_STATE.get(exc.code, "unreachable")
        health.model = exc.model or health.model
        health.message = exc.message
        health.hint = exc.hint
        health.extra["code"] = exc.code
    except Exception as exc:  # pragma: no cover - defensive
        health.state = "unreachable"
        health.message = f"Could not reach {provider.title()}."
        health.hint = _ERROR_HINTS["provider_down"]
        health.extra["detail"] = str(exc)[:200]
    health.latency_ms = int((time.perf_counter() - started) * 1000)
    return health


async def ping_all(db: AsyncSession) -> list[ProviderHealth]:
    # Sequential on purpose: a shared key can be rate-limited, and two parallel
    # pings would make the second one lie.
    return [await ping(db, provider) for provider in PROVIDERS]


# ---------------------------------------------------------------------------
# Speech to text
# ---------------------------------------------------------------------------
async def whisper_model(db: AsyncSession) -> Optional[str]:
    """The newest Whisper model Groq currently serves, from the live list."""
    try:
        models = await list_models(db, "groq")
    except ProviderError:
        return None
    whisper = [m for m in models if "whisper" in m.id.lower()]
    if not whisper:
        return None
    # Prefer the largest/latest: turbo > large-v3 > large-v2 > base ...
    def rank(model: ModelInfo) -> int:
        low = model.id.lower()
        if "turbo" in low:
            return 0
        if "large-v3" in low:
            return 1
        if "large-v2" in low:
            return 2
        return 3

    return sorted(whisper, key=rank)[0].id


async def transcribe(
    db: AsyncSession,
    audio: bytes,
    filename: str,
    content_type: str,
    *,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> dict:
    """Transcribe audio with Groq Whisper, falling back to Gemini.

    ``language`` is optional by design: Nigerian English and Pidgin are
    detected far more reliably by letting the model auto-detect than by
    forcing ``en``. The UI exposes an override for anyone who wants it.

    Returns ``{"text", "provider", "model", "language"}``.
    """
    groq_key = await get_api_key(db, "groq")
    errors: list[dict] = []

    if groq_key:
        model = await whisper_model(db) or "whisper-large-v3-turbo"
        data: dict[str, str] = {"model": model, "response_format": "json"}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    GROQ_TRANSCRIBE_URL,
                    headers={"Authorization": f"Bearer {groq_key}"},
                    files={"file": (filename, audio, content_type or "application/octet-stream")},
                    data=data,
                )
                if response.status_code == 200:
                    payload = response.json()
                    return {
                        "text": (payload.get("text") or "").strip(),
                        "provider": "groq",
                        "model": model,
                        "language": payload.get("language") or language,
                    }
                errors.append(_classify("groq", response.status_code, response.text, model).to_dict())
        except httpx.TimeoutException as exc:
            errors.append(_timeout("groq", model, exc).to_dict())
        except Exception as exc:
            errors.append(_unreachable("groq", model, exc).to_dict())

    gemini_key = await get_api_key(db, "gemini")
    if gemini_key:
        try:
            models = await list_models(db, "gemini")
        except ProviderError as exc:
            errors.append(exc.to_dict())
            models = []
        # Any current Gemini flash model accepts inline audio.
        candidates = [m.id for m in models if "flash" in m.id.lower()] or [m.id for m in models]
        for model in candidates[:3]:
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{GEMINI_BASE}/models/{model}:generateContent",
                        params={"key": gemini_key},
                        json={
                            "contents": [
                                {
                                    "role": "user",
                                    "parts": [
                                        {
                                            "text": (
                                                "Transcribe this audio verbatim. Return only the "
                                                "transcript, no commentary. The speaker may use "
                                                "Nigerian English or Pidgin."
                                            )
                                        },
                                        {
                                            "inlineData": {
                                                "mimeType": content_type or "audio/webm",
                                                "data": __import__("base64").b64encode(audio).decode(),
                                            }
                                        },
                                    ],
                                }
                            ]
                        },
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        parts = (
                            ((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts")
                            or []
                        )
                        text = "".join(p.get("text", "") for p in parts).strip()
                        if text:
                            return {
                                "text": text,
                                "provider": "gemini",
                                "model": model,
                                "language": language,
                            }
                        errors.append({"code": "upstream_error", "provider": "gemini", "message": "Empty transcript"})
                        continue
                    errors.append(_classify("gemini", response.status_code, response.text, model).to_dict())
            except Exception as exc:
                errors.append(_unreachable("gemini", model, exc).to_dict())

    first = errors[0] if errors else {
        "code": "key_missing",
        "provider": "groq",
        "message": "No speech-to-text provider is configured.",
    }
    raise ProviderError(
        code=first.get("code") or "provider_down",
        provider=first.get("provider") or "groq",
        message=first.get("message") or "Transcription failed.",
        detail=json.dumps(errors)[:800],
        status=first.get("status"),
        model=first.get("model"),
    )


# ---------------------------------------------------------------------------
# OpenAI schema -> Gemini-safe schema (Gemini rejects unknown JSON-schema keys)
# ---------------------------------------------------------------------------
_ALLOWED_SCHEMA_KEYS = {
    "type", "format", "description", "nullable", "enum", "items",
    "properties", "required", "minimum", "maximum", "minItems", "maxItems",
    "minLength", "maxLength", "pattern", "default", "anyOf", "propertyOrdering",
}


def sanitise_schema(schema: Any) -> Any:
    """Strip JSON-Schema keywords Gemini's function declarations reject."""
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: sanitise_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = sanitise_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            out[key] = [sanitise_schema(v) for v in value]
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
async def stream_complete(
    provider: str,
    *,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    system_text: str = "",
    temperature: float = 0.4,
    max_tokens: int = 4096,
    timeout: float = 120.0,
) -> AsyncIterator[dict]:
    """Stream one model call as ``{"type": "text"|"done"}`` events.

    Text is yielded the moment the provider produces it, which is what makes
    the chat feel alive. The last event is always ``{"type": "done",
    "response": <OpenAI-shaped response>}`` so the caller's tool loop is
    identical to the non-streaming path — tool calls arrive in the same shape.
    """
    if not api_key:
        raise ProviderError(
            code="key_missing", provider=provider, model=model,
            message=f"No {provider.title()} API key is configured.",
        )

    if provider == "groq":
        payload: dict[str, Any] = {
            "model": model,
            "messages": _messages_for_groq(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        url = GROQ_CHAT_URL
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        params = None
    else:
        body: dict[str, Any] = {
            "contents": _messages_for_gemini(messages, system_text),
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            body["tools"] = _convert_tools_for_gemini(tools)
        url = f"{GEMINI_BASE}/models/{model}:streamGenerateContent"
        headers = {"Content-Type": "application/json"}
        params = {"key": api_key, "alt": "sse"}
        payload = body

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", url, headers=headers, params=params, json=payload
            ) as response:
                if response.status_code != 200:
                    raw = (await response.aread()).decode("utf-8", "ignore")
                    raise _classify(provider, response.status_code, raw, model)
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk in ("", "[DONE]"):
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if provider == "groq":
                        for choice in data.get("choices") or []:
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if piece:
                                text_parts.append(piece)
                                yield {"type": "text", "text": piece}
                            for call in delta.get("tool_calls") or []:
                                index = call.get("index", len(tool_calls))
                                while len(tool_calls) <= index:
                                    tool_calls.append(
                                        {"id": "", "type": "function",
                                         "function": {"name": "", "arguments": ""}}
                                    )
                                slot = tool_calls[index]
                                if call.get("id"):
                                    slot["id"] = call["id"]
                                fn = call.get("function") or {}
                                if fn.get("name"):
                                    slot["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["function"]["arguments"] += fn["arguments"]
                    else:
                        for candidate in data.get("candidates") or []:
                            for part in (candidate.get("content") or {}).get("parts") or []:
                                if part.get("text"):
                                    text_parts.append(part["text"])
                                    yield {"type": "text", "text": part["text"]}
                                call = part.get("functionCall")
                                if call:
                                    tool_calls.append(
                                        {
                                            "id": f"call_{uuid.uuid4().hex[:8]}",
                                            "type": "function",
                                            "function": {
                                                "name": call.get("name"),
                                                "arguments": json.dumps(call.get("args") or {}),
                                            },
                                        }
                                    )
    except ProviderError:
        raise
    except httpx.TimeoutException as exc:
        raise _timeout(provider, model, exc) from exc
    except Exception as exc:
        raise _unreachable(provider, model, exc) from exc

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    yield {
        "type": "done",
        "response": {
            "choices": [
                {
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ]
        },
    }


async def stream_with_recovery(
    db: AsyncSession,
    provider: str,
    **kwargs: Any,
) -> AsyncIterator[dict]:
    """``stream_complete`` plus stale-model recovery and optional provider fallback.

    The generator is *lazy*: nothing is called until the consumer starts
    iterating, so a failure is raised inside the async-for the caller already
    has around it. The final event identifies the provider/model that actually
    answered so the chat can display truthful status after an automatic switch.
    """
    model = kwargs.pop("model")
    allow_fallback = bool(kwargs.pop("allow_fallback", True))
    requires_vision = bool(kwargs.pop("requires_vision", False))
    keys = kwargs.get("api_key")
    tried_fallback = False
    while True:
        try:
            async for event in stream_complete(provider, model=model, **kwargs):
                if event.get("type") == "done":
                    event = {**event, "provider": provider, "model": model}
                yield event
            return
        except ProviderError as exc:
            if exc.code == "model_unavailable":
                try:
                    models = await list_models(db, provider, api_key=keys)
                    remaining = [m for m in models if m.id != model]
                    replacement = (
                        pick_vision_model(provider, remaining)
                        if requires_vision else pick_default(provider, remaining)
                    )
                except ProviderError:
                    replacement = None
                if replacement:
                    note = (
                        f"The saved model “{model}” is no longer available at "
                        f"{provider.title()}. Switched to “{replacement}”."
                    )
                    await set_model(db, provider, replacement)
                    yield {"type": "notice", "kind": "recovery", "text": note}
                    model = replacement
                    continue
            if allow_fallback and not tried_fallback:
                other = "gemini" if provider == "groq" else "groq"
                other_key = await get_api_key(db, other)
                if other_key and exc.code not in ("safety_blocked",):
                    try:
                        other_model = await resolve_model(db, other, api_key=other_key)
                    except ProviderError:
                        other_model = None
                    if other_model:
                        tried_fallback = True
                        yield {
                            "type": "notice",
                            "kind": "fallback",
                            "provider": other,
                            "model": other_model,
                            "text": (
                                f"{provider.title()} failed ({exc.message}) — "
                                f"continuing on {other.title()} with {other_model}."
                            ),
                        }
                        provider = other
                        model = other_model
                        kwargs["api_key"] = other_key
                        continue
            raise
