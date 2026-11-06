"""OpenRouter — the single AI provider for the Agent.

WHY ONE PROVIDER
----------------
This module used to speak Groq and Gemini directly, and both hard-coded model
names had already been retired by the time anyone noticed. The operator's rule
replaces that with one door: **OpenRouter**, which is itself a router in front
of every major lab. One key, one API, and the whole catalogue — including the
provider *behind* each model — comes from a live listing instead of a constant
in this file.

The rule this module still encodes, unchanged: **the app never guesses a model
name.** A model is only ever chosen from OpenRouter's live ``/models`` list, so
a retired model cannot come back from the dead through a constant.

TWO WAYS TO PICK, BOTH FROM OPENROUTER ITSELF
---------------------------------------------
* **Provider (vendor)** — every model id on OpenRouter is ``vendor/model``, so
  the live list is grouped by vendor and the operator can pin routing to one of
  them (``provider.only`` on the request). Choosing a vendor never invents a
  model name: it filters the live list.
* **Preset** — a preset created at ``openrouter.ai/settings/presets`` carries
  the system prompt, temperature and tool settings the operator already tuned.
  Naming one here adds ``presets: "<name>"`` to every request, so the agent
  behaves exactly as configured on OpenRouter. Presets are discovered from the
  API when OpenRouter serves that listing, and can always be typed by name.

Everything provider-specific lives here:

  * key storage (encrypted, never returned; only a masked hint leaves the API)
  * live model listing (``GET /models``), grouped by vendor
  * preset discovery (``GET /presets``) and validation
  * model / vendor / preset selection, persisted in ``system_settings``
  * one ``complete()`` and one ``stream_complete()`` (OpenAI wire shape)
  * classification of every failure into one of eight stable codes
  * automatic recovery: a stale model is replaced from the live list and the
    call is retried once

The agent service imports this and stays about *tools*; the API layer imports
this and stays about *HTTP*.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemSetting
from app.security.encryption import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

#: One provider. Kept as a tuple because callers iterate it and the health card
#: renders one row per entry; a second entry would mean a second key.
PROVIDERS = ("openrouter",)
PROVIDER = "openrouter"
#: How the provider is named in UI copy. ``"Openrouter".title()`` would read
#: "Openrouter", which is wrong enough to look like a typo.
PROVIDER_LABEL = {"openrouter": "OpenRouter"}

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE}/chat/completions"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE}/models"
OPENROUTER_KEY_URL = f"{OPENROUTER_BASE}/key"
#: Where an operator's presets are listed. OpenRouter has not always exposed a
#: listing endpoint, so a failure here degrades to "type the name" rather than
#: breaking the settings screen — see :func:`list_presets`.
OPENROUTER_PRESETS_URL = f"{OPENROUTER_BASE}/presets"
OPENROUTER_CONSOLE_URL = "https://openrouter.ai/settings/keys"
OPENROUTER_PRESET_CONSOLE_URL = "https://openrouter.ai/settings/presets"

# Where the operator's choices live. Keys are namespaced ``ai_agent.*``. The
# legacy ``ai_agent.groq_api_key`` / ``ai_agent.gemini_api_key`` rows are left
# untouched in the database — nothing reads them any more — so removing this
# code path can never delete a key someone still needs.
KEY_SETTING = {"openrouter": "ai_agent.openrouter_api_key"}
MODEL_SETTING = {"openrouter": "ai_agent.model.openrouter"}
DEFAULT_PROVIDER_SETTING = "ai_agent.default_provider"
AUTO_FALLBACK_SETTING = "ai_agent.auto_fallback"
AUTO_APPROVE_SETTING = "ai_agent.auto_approve"
VOICE_SETTING = "ai_agent.voice"
GLOBAL_MEMORY_SETTING = "ai_agent.global_memory"  # legacy single-string memory
#: Which vendor the operator pinned routing to ("" = OpenRouter chooses).
VENDOR_SETTING = "ai_agent.vendor.openrouter"
#: Which OpenRouter preset to attach to every request ("" = none).
PRESET_SETTING = "ai_agent.preset.openrouter"

#: Cache for the live model list. OpenRouter's catalogue changes weekly, not
#: per request, and a chat turn must not pay a listing round trip every time.
_MODEL_CACHE: dict[str, tuple[float, list["ModelInfo"]]] = {}
_MODEL_TTL = 900.0

#: Models a chat turn cannot use. OpenRouter lists embedding, moderation, image
#: and TTS models alongside chat ones; offering them in a dropdown means the
#: dropdown offers a 400.
_EXCLUDE = re.compile(
    r"(embedding|embeddings|-embed|moderation|guard|tts|speech|imagen|image-gen|"
    r"image-generation|sora|veo|music|audio-generation|reranker|rerank|"
    # Transcription models are used by the voice box (``transcribe``) and cannot
    # answer a chat turn; offering one in the dropdown offers a 400.
    r"whisper|transcrib|transcription|paraformer|seamless)", re.I
)
#: Auto-select preference, as *fragments* of a live id — never a full name. The
#: first pattern that matches a model OpenRouter actually serves wins, which is
#: what keeps a fresh install working without a constant that can go stale.
_PREFERENCE = (
    re.compile(r"^anthropic/claude-3\.5-haiku"),
    re.compile(r"^anthropic/claude-3\.5-sonnet"),
    re.compile(r"^openai/gpt-4o-mini"),
    re.compile(r"^openai/gpt-4o"),
    re.compile(r"^google/gemini-2\.5-flash"),
    re.compile(r"^google/gemini-flash"),
    re.compile(r"^google/gemini-2\.0-flash"),
    re.compile(r"^meta-llama/llama-3\.3-70b"),
    re.compile(r"^meta-llama/llama-3\.1-70b"),
    re.compile(r"^mistralai/mistral-small"),
    re.compile(r"^deepseek/deepseek-chat"),
    re.compile(r"^qwen/qwen-2\.5-72b"),
    re.compile(r"claude"),
    re.compile(r"gpt-4o"),
    re.compile(r"gemini"),
    re.compile(r"llama"),
)


def label(provider: str) -> str:
    """How this provider is written in operator-facing copy."""
    return PROVIDER_LABEL.get(provider, (provider or "").title())


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
    "key_missing": "Add an OpenRouter key in Settings → AI Agent. The agent cannot call a model without one.",
    "key_invalid": "OpenRouter rejected the saved key. Paste a fresh one from openrouter.ai/settings/keys and press Test.",
    "model_unavailable": "Automatic recovery could not find a working model. Open Agent settings → Model and pick from the live list.",
    "rate_limited": "OpenRouter is limiting this key or model. Retry shortly, or pick another model in Agent settings.",
    "timeout": "OpenRouter did not answer in time. Retry, or choose a faster model.",
    "safety_blocked": "The upstream model refused this request on safety grounds. Rephrase it and try again.",
    "provider_down": "OpenRouter could not be reached. Check the connection, then retry.",
    "bad_request": "OpenRouter rejected the request. This is a bug in the agent — retry, and if it persists pick another model.",
    "upstream_error": "OpenRouter returned an error. Retry in a moment.",
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


def _classify(provider: str, status: Optional[int], body: str,
              model: Optional[str]) -> ProviderError:
    """Turn an HTTP failure into one of the stable codes.

    OpenRouter reports the *upstream* model's failure in its own envelope, so
    the body is matched as well as the status: a 402 (no credit) and a 404
    (model retired) look identical at the status line and need different
    messages, and an upstream moderation refusal arrives as a 400 that must not
    be presented as "the agent sent a bad request".
    """
    text = (body or "")[:2000]
    low = text.lower()
    status = status or 0

    if status in (401, 403) or "invalid api key" in low or "invalid_api_key" in low:
        return ProviderError(
            code="key_invalid", provider=provider, status=status, model=model,
            message=f"{label(provider)} rejected the saved API key.",
            detail=text[:600] or None,
        )
    if status == 402 or "no credits" in low or "insufficient credit" in low:
        return ProviderError(
            code="key_invalid", provider=provider, status=status, model=model,
            message=(f"{label(provider)} has no credit left on this key. Top it up at "
                     "openrouter.ai/credits, or switch to a free model."),
            detail=text[:600] or None,
        )
    if status in (404, 410) or "not a valid model" in low or "model_not_found" in low \
            or "no endpoints found" in low or "is not supported" in low:
        return ProviderError(
            code="model_unavailable", provider=provider, status=status, model=model,
            message=f"{label(provider)} does not offer the model “{model}”.",
            detail=text[:600] or None,
        )
    if status == 429 or "rate limit" in low or "quota" in low or "resource_exhausted" in low:
        return ProviderError(
            code="rate_limited", provider=provider, status=status, model=model,
            message=f"{label(provider)} is rate-limiting this key.",
            detail=text[:600] or None,
        )
    if "moderation" in low or "safety" in low or "blocked" in low or "content policy" in low \
            or "recitation" in low:
        return ProviderError(
            code="safety_blocked", provider=provider, status=status, model=model,
            message=f"The upstream model refused the request on safety grounds.",
            detail=text[:600] or None,
        )
    if status in (400, 422):
        return ProviderError(
            code="bad_request", provider=provider, status=status, model=model,
            message=f"{label(provider)} rejected the request.",
            detail=text[:600] or None,
        )
    if status >= 500:
        return ProviderError(
            code="provider_down", provider=provider, status=status, model=model,
            message=f"{label(provider)} returned a server error ({status}).",
            detail=text[:600] or None,
        )
    return ProviderError(
        code="upstream_error", provider=provider, status=status, model=model,
        message=f"{label(provider)} returned an error ({status or 'no status'}).",
        detail=text[:600] or None,
    )


def _timeout(provider: str, model: Optional[str], exc: Exception) -> ProviderError:
    return ProviderError(
        code="timeout", provider=provider, model=model,
        message=f"{label(provider)} did not answer in time.",
        detail=str(exc)[:300],
    )


def _unreachable(provider: str, model: Optional[str], exc: Exception) -> ProviderError:
    return ProviderError(
        code="provider_down", provider=provider, model=model,
        message=f"Could not reach {label(provider)}.",
        detail=str(exc)[:300],
    )


# ---------------------------------------------------------------------------
# Settings (key + model + vendor + preset), encrypted at rest
# ---------------------------------------------------------------------------
async def _get_row(db: AsyncSession, key: str) -> Optional[SystemSetting]:
    return (await db.execute(select(SystemSetting).where(SystemSetting.key == key))).scalar_one_or_none()


async def _get_value(db: AsyncSession, key: str) -> Optional[str]:
    row = await _get_row(db, key)
    return row.value if row is not None and row.value is not None else None


async def _set_value(db: AsyncSession, key: str, value: str, *,
                     category: str = "ai_agent",
                     description: str | None = None) -> None:
    row = await _get_row(db, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value, category=category, description=description))
    else:
        row.value = value
    await db.flush()


async def get_api_key(db: AsyncSession, provider: str = PROVIDER) -> Optional[str]:
    """Read the key from the DB, else from the environment, and decrypt it.

    The environment fallback matters for fresh deployments: an operator who sets
    ``OPENROUTER_API_KEY`` in Render's dashboard should not also have to paste
    the key into the UI before the agent works.
    """
    raw = await _get_value(db, KEY_SETTING.get(provider, KEY_SETTING[PROVIDER]))
    if raw:
        try:
            return decrypt_value(raw)
        except Exception:
            # Historical rows were written before encryption existed; use as-is.
            return raw
    from app.config import settings as app_settings

    return getattr(app_settings, "OPENROUTER_API_KEY", None) or None


async def set_api_key(db: AsyncSession, provider: str, api_key: Optional[str]) -> None:
    if api_key is None:
        return
    key = KEY_SETTING.get(provider, KEY_SETTING[PROVIDER])
    if not api_key.strip():
        await _set_value(db, key, "", description="OpenRouter API key for the AI Agent (cleared)")
        return
    _MODEL_CACHE.pop(provider, None)
    await _set_value(
        db, key, encrypt_value(api_key.strip()),
        description="OpenRouter API key for the AI Agent (encrypted at rest)",
    )


async def get_keys(db: AsyncSession) -> dict[str, Optional[str]]:
    return {p: await get_api_key(db, p) for p in PROVIDERS}


def mask(value: Optional[str]) -> Optional[str]:
    """The only form of a key that may leave the server.

    ``sk-or-abc…ZJGp`` style: enough to recognise *which* key is installed,
    useless to anyone who reads it.
    """
    if not value:
        return None
    if len(value) <= 12:
        return "***"
    return f"{value[:7]}***{value[-4:]}"


async def keys_summary(db: AsyncSession) -> dict:
    """Non-secret view of the key, for the settings card."""
    out: dict[str, Any] = {}
    for provider in PROVIDERS:
        key = await get_api_key(db, provider)
        out[f"{provider}_configured"] = bool(key)
        out[f"{provider}_hint"] = mask(key)
    out["console_url"] = OPENROUTER_CONSOLE_URL
    out["preset_console_url"] = OPENROUTER_PRESET_CONSOLE_URL
    out["vendor"] = await get_vendor(db)
    out["preset"] = await get_preset(db)
    return out


async def get_model(db: AsyncSession, provider: str = PROVIDER) -> Optional[str]:
    return await _get_value(db, MODEL_SETTING.get(provider, MODEL_SETTING[PROVIDER]))


async def set_model(db: AsyncSession, provider: str, model: str) -> None:
    await _set_value(
        db, MODEL_SETTING.get(provider, MODEL_SETTING[PROVIDER]), model,
        description="AI Agent model (chosen from OpenRouter's live list)",
    )


async def get_default_provider(db: AsyncSession) -> str:
    """Always OpenRouter now.

    Older rows say ``groq`` or ``gemini``; those providers are gone, so anything
    that is not in :data:`PROVIDERS` resolves to OpenRouter rather than to a
    key that no longer exists.
    """
    value = await _get_value(db, DEFAULT_PROVIDER_SETTING)
    return value if value in PROVIDERS else PROVIDER


async def get_vendor(db: AsyncSession, provider: str = PROVIDER) -> str:
    """The vendor routing is pinned to, or "" to let OpenRouter choose."""
    return (await _get_value(db, VENDOR_SETTING) or "").strip()


async def set_vendor(db: AsyncSession, vendor: str | None) -> None:
    await _set_value(
        db, VENDOR_SETTING, (vendor or "").strip(),
        description="OpenRouter vendor routing is pinned to (empty = OpenRouter chooses)",
    )


async def get_preset(db: AsyncSession, provider: str = PROVIDER) -> str:
    """The OpenRouter preset name attached to every request, or "" for none."""
    return (await _get_value(db, PRESET_SETTING) or "").strip()


async def set_preset(db: AsyncSession, preset: str | None) -> None:
    await _set_value(
        db, PRESET_SETTING, (preset or "").strip(),
        description="OpenRouter preset name attached to every AI Agent request",
    )


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
    #: True when the model can call tools. The agent's whole point is tools, so
    #: a model that cannot is marked rather than hidden.
    tools: bool = True
    context: Optional[int] = None
    #: The vendor behind the model — the part before the slash in the id.
    vendor: str = ""
    #: ``image`` / ``audio`` / ``file`` when the model accepts them as input.
    input_modalities: tuple[str, ...] = ()
    #: US dollars per token, as strings — OpenRouter sends high-precision
    #: decimals that must not be rounded on the way through.
    prompt_price: Optional[str] = None
    completion_price: Optional[str] = None


#: Capitalisation only, for when the catalogue gives no display name. OpenRouter's
#: own ``name`` field is the real source (see :func:`_vendor_of`) — "OpenAI:
#: GPT-4o mini" carries the vendor spelled the way the vendor spells it, which
#: title-casing the id author ("openai" -> "Openai") would get wrong.
_VENDOR_ACRONYMS = {"openai": "OpenAI", "openrouter": "OpenRouter", "deepseek": "DeepSeek",
                    "mistralai": "Mistral", "meta-llama": "Meta", "x-ai": "xAI",
                    "ai21": "AI21", "ibm": "IBM", "aws": "AWS"}


def _vendor_of(model_id: str, display_name: str | None = None) -> str:
    """The vendor behind a model: ``openai/gpt-4o-mini`` -> ``OpenAI``.

    Read off OpenRouter's own data, never a lookup table, so a vendor added next
    month appears in the picker with no code change. The catalogue's display
    name ("OpenAI: GPT-4o mini") is preferred because it carries the vendor's
    own spelling; the id's author is the fallback.
    """
    name = str(display_name or "")
    if ":" in name:
        head = name.split(":", 1)[0].strip()
        if head and len(head) <= 40:
            return head
    author = str(model_id or "").split("/", 1)[0].strip()
    if not author or author == str(model_id or "").strip():
        return "Other"
    if author in _VENDOR_ACRONYMS:
        return _VENDOR_ACRONYMS[author]
    return " ".join(word.capitalize() for word in author.split("-"))


def _usable(entry: dict) -> bool:
    """Can a chat turn actually use this entry?"""
    model_id = str(entry.get("id") or "")
    if not model_id or _EXCLUDE.search(model_id):
        return False
    params = entry.get("supported_parameters") or []
    if params and "tools" not in params and "tool_calls" not in params:
        # Some models list no tools support. They can still answer, so they are
        # kept — but flagged, which is what ModelInfo.tools is for.
        return True
    return True


def _preference_rank(provider: str, model_id: str) -> int:
    for index, pattern in enumerate(_PREFERENCE):
        if pattern.search(model_id):
            return index
    return len(_PREFERENCE) + 1


def pick_default(provider: str, models: list[ModelInfo]) -> Optional[str]:
    """Choose a model from a *live* list — never a name from the source code."""
    if not models:
        return None
    ranked = sorted(models, key=lambda m: (_preference_rank(provider, m.id), m.id))
    with_tools = [m for m in ranked if m.tools]
    return (with_tools or ranked)[0].id


def is_vision_model(provider: str, model: str) -> bool:
    """Whether a model id is from a family that accepts image input."""
    model_id = (model or "").lower()
    return bool(re.search(r"(vision|gpt-4o|gpt-4\.1|claude|gemini|llama-4|llama-3\.2|"
                          r"qwen[^/]*[-.]vl|pixtral|/[^/]*vl-)", model_id))


def pick_vision_model(provider: str, models: list[ModelInfo]) -> Optional[str]:
    """Choose an image-capable model from the live catalogue."""
    candidates = [
        m for m in models
        if is_vision_model(provider, m.id) or "image" in m.input_modalities
    ]
    if not candidates:
        return None
    return pick_default(provider, candidates)


async def resolve_vision_model(
    db: AsyncSession, provider: str, *, api_key: Optional[str] = None
) -> str:
    """Resolve an image-capable model from the live list when one is needed."""
    saved = await get_model(db, provider)
    if saved and is_vision_model(provider, saved):
        return saved
    models = await list_models(db, provider, api_key=api_key)
    selected = pick_vision_model(provider, models)
    if selected:
        return selected
    raise ProviderError(
        code="model_unavailable", provider=provider, model=saved,
        message=("No image-capable model is available for this picture. Choose a model "
                 "that accepts image input in Agent settings."),
    )


def _models_from_payload(payload: dict) -> list[ModelInfo]:
    """OpenRouter's ``/models`` body -> the curated list the UI shows."""
    entries = payload.get("data") if isinstance(payload, dict) else None
    out: list[ModelInfo] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not _usable(entry):
            continue
        model_id = str(entry.get("id") or "").strip()
        params = entry.get("supported_parameters") or []
        architecture = entry.get("architecture") or {}
        modalities = tuple(str(m).lower() for m in (architecture.get("input_modalities") or []))
        pricing = entry.get("pricing") or {}
        try:
            context = int(entry.get("context_length") or 0) or None
        except (TypeError, ValueError):
            context = None
        out.append(ModelInfo(
            id=model_id,
            label=str(entry.get("name") or model_id)[:200],
            tools=("tools" in params or "tool_calls" in params) if params else True,
            context=context,
            vendor=_vendor_of(model_id, entry.get("name")),
            input_modalities=modalities,
            prompt_price=str(pricing.get("prompt")) if pricing.get("prompt") is not None else None,
            completion_price=(
                str(pricing.get("completion")) if pricing.get("completion") is not None else None
            ),
        ))
    return out


def _headers(api_key: str) -> dict[str, str]:
    """Auth plus the two optional headers OpenRouter asks deployments to send.

    ``HTTP-Referer`` / ``X-Title`` are how OpenRouter attributes and ranks an
    app in its own rankings. They are optional, and the app's public address is
    best-effort — a missing one must never block a request.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "SENDERSMS AI Agent",
    }
    try:
        from app.config import settings

        base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
        if base:
            headers["HTTP-Referer"] = base
    except Exception:  # noqa: BLE001 — attribution headers are optional
        pass
    return headers


async def list_models(
    db: AsyncSession,
    provider: str = PROVIDER,
    *,
    api_key: Optional[str] = None,
    timeout: float = 25.0,
    refresh: bool = False,
) -> list[ModelInfo]:
    """Ask OpenRouter what it currently serves.

    Cached for 15 minutes because a chat turn must not pay a catalogue round
    trip every message; ``refresh=True`` (the Settings screen's Refresh button,
    and automatic recovery) bypasses the cache.

    Raises :class:`ProviderError` with ``key_missing`` / ``key_invalid`` /
    ``provider_down`` so callers can show the right message instead of an empty
    dropdown.
    """
    key = api_key or await get_api_key(db, provider)
    if not key:
        raise ProviderError(
            code="key_missing", provider=provider,
            message=f"No {label(provider)} API key is configured.",
        )

    cached = _MODEL_CACHE.get(provider)
    if not refresh and cached and cached[0] > time.time():
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(OPENROUTER_MODELS_URL, headers=_headers(key))
    except httpx.TimeoutException as exc:
        raise _timeout(provider, None, exc) from exc
    except Exception as exc:
        raise _unreachable(provider, None, exc) from exc

    if response.status_code != 200:
        raise _classify(provider, response.status_code, response.text, None)
    try:
        models = _models_from_payload(response.json())
    except ValueError as exc:
        raise ProviderError(
            code="upstream_error", provider=provider,
            message=f"{label(provider)} returned an unreadable model list.",
            detail=str(exc)[:300],
        ) from exc
    if not models:
        raise ProviderError(
            code="upstream_error", provider=provider,
            message=f"{label(provider)} returned an empty model list.",
        )
    _MODEL_CACHE[provider] = (time.time() + _MODEL_TTL, models)
    return models


def group_models_by_vendor(models: list[ModelInfo]) -> list[dict]:
    """The vendor picker: every vendor in the live catalogue, with its models.

    Vendors are ordered by how many models they serve, then by name, so the
    picker reads like OpenRouter's own list rather than alphabetically.
    """
    buckets: dict[str, list[ModelInfo]] = {}
    for model in models:
        buckets.setdefault(model.vendor or "Other", []).append(model)
    return [
        {
            "vendor": vendor,
            "count": len(items),
            "models": sorted(items, key=lambda m: m.id),
        }
        for vendor, items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


async def list_vendors(db: AsyncSession, provider: str = PROVIDER, *,
                       api_key: Optional[str] = None) -> list[dict]:
    """Vendors available right now, straight from OpenRouter's catalogue."""
    models = await list_models(db, provider, api_key=api_key)
    return group_models_by_vendor(models)


async def list_presets(db: AsyncSession, provider: str = PROVIDER, *,
                       api_key: Optional[str] = None,
                       timeout: float = 15.0) -> dict:
    """The operator's OpenRouter presets, when OpenRouter lists them.

    Returns ``{"available": bool, "items": [...], "saved": str}``. ``available``
    is False when OpenRouter has no listing endpoint for this key — which is a
    normal outcome, not an error: the UI then offers a free-text box and the
    preset is validated by a real request (:func:`validate_preset`) instead.
    """
    key = api_key or await get_api_key(db, provider)
    saved = await get_preset(db, provider)
    if not key:
        return {"available": False, "items": [], "saved": saved,
                "error": f"No {label(provider)} API key is configured."}
    items: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(OPENROUTER_PRESETS_URL, headers=_headers(key))
    except Exception as exc:  # noqa: BLE001 — preset listing is a convenience
        logger.info("OPENROUTER: preset listing unavailable (%s)", exc)
        return {"available": False, "items": [], "saved": saved}
    if response.status_code != 200:
        logger.info("OPENROUTER: preset listing returned %s", response.status_code)
        return {"available": False, "items": [], "saved": saved}
    try:
        payload = response.json()
    except ValueError:
        return {"available": False, "items": [], "saved": saved}
    for entry in (payload.get("data") or payload.get("presets") or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("title") or entry.get("id") or "").strip()
        if not name:
            continue
        items.append({
            "name": name[:200],
            "description": str(entry.get("description") or "")[:300] or None,
            "system_prompt": bool(entry.get("system_prompt") or entry.get("instructions")),
        })
    return {"available": True, "items": items, "saved": saved}


async def validate_preset(db: AsyncSession, preset: str, *,
                          api_key: Optional[str] = None) -> dict:
    """Prove a preset name exists by asking OpenRouter to use it.

    OpenRouter has no "get one preset" endpoint, so the honest test is a real
    one-token request that names the preset: it either answers, or it says the
    preset is unknown. That distinguishes a typo from a working preset, which is
    the only thing the settings screen actually needs to know.
    """
    name = (preset or "").strip()
    if not name:
        return {"success": False, "error": "Enter the preset's name exactly as it appears "
                                           "on openrouter.ai/settings/presets."}
    key = api_key or await get_api_key(db, PROVIDER)
    if not key:
        return {"success": False, "error": f"No {label(PROVIDER)} API key is configured."}
    model = await get_model(db, PROVIDER)
    if not model:
        try:
            model = pick_default(PROVIDER, await list_models(db, PROVIDER, api_key=key))
        except ProviderError as exc:
            return {"success": False, "error": exc.message}
    if not model:
        return {"success": False, "error": "No model is available to test the preset with."}
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "presets": name,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(OPENROUTER_CHAT_URL, headers=_headers(key), json=body)
    except httpx.TimeoutException as exc:
        return {"success": False, "error": f"{label(PROVIDER)} did not answer in time.",
                "detail": str(exc)[:200]}
    except Exception as exc:
        return {"success": False, "error": f"Could not reach {label(PROVIDER)}.",
                "detail": str(exc)[:200]}
    if response.status_code == 200:
        return {"success": True, "preset": name, "model": model}
    low = response.text.lower()
    if "preset" in low and ("not found" in low or "invalid" in low or "unknown" in low):
        return {"success": False,
                "error": f"OpenRouter does not know a preset called “{name}”. Check the name at "
                         "openrouter.ai/settings/presets.",
                "detail": response.text[:300]}
    error = _classify(PROVIDER, response.status_code, response.text, model)
    # Anything that is not a preset complaint means the preset was accepted and
    # something else went wrong — say so plainly instead of blaming the name.
    if error.code in ("key_invalid", "rate_limited", "provider_down", "timeout"):
        return {"success": False, "error": error.message, "detail": error.detail,
                "preset_accepted": True}
    return {"success": False, "error": error.message, "detail": error.detail}


async def resolve_model(db: AsyncSession, provider: str, *,
                        api_key: Optional[str] = None) -> str:
    """The model to use: the saved one if OpenRouter still serves it, else a new one."""
    saved = await get_model(db, provider)
    models = await list_models(db, provider, api_key=api_key)
    ids = {m.id for m in models}
    if saved and saved in ids:
        return saved
    replacement = pick_default(provider, models)
    if not replacement:
        raise ProviderError(
            code="model_unavailable", provider=provider, model=saved,
            message=f"{label(provider)} has no usable models right now.",
        )
    await set_model(db, provider, replacement)
    if saved:
        logger.info("AI Agent: saved model %s is gone; selected %s", saved, replacement)
    return replacement


async def ensure_model(db: AsyncSession, provider: str, *,
                       api_key: Optional[str] = None) -> tuple[str, bool]:
    """``(model, was_recovered)`` — never raises for a merely-stale model."""
    saved = await get_model(db, provider)
    try:
        resolved = await resolve_model(db, provider, api_key=api_key)
    except ProviderError:
        if saved:
            return saved, False
        raise
    return resolved, bool(saved and saved != resolved)


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------
def sanitise_schema(schema: Any) -> Any:
    """Strip JSON-Schema keywords strict validators reject.

    OpenRouter forwards tool schemas to the upstream model, and several of them
    refuse ``$schema``, ``additionalProperties`` and friends. Stripping them
    costs nothing and stops a whole class of 400s.
    """
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key in ("$schema", "$id", "$defs", "definitions"):
                continue
            out[key] = sanitise_schema(value)
        return out
    if isinstance(schema, list):
        return [sanitise_schema(item) for item in schema]
    return schema


def _image_data_url(image: Any) -> str | None:
    """One attached image -> a ``data:`` URL, whatever shape it arrived in.

    The agent runtime stores uploads as ``{"mime_type", "data"}`` (so the real
    type survives); older callers pass a bare base64 string; the API may hand
    over an already-built data URL. All three are accepted, because guessing
    ``image/jpeg`` for a PNG is how an image silently fails to render upstream.
    """
    if not image:
        return None
    if isinstance(image, dict):
        data = str(image.get("data") or "")
        if not data:
            return None
        if data.startswith("data:"):
            return data
        mime = str(image.get("mime_type") or "image/png")
        return f"data:{mime};base64,{data}"
    url = str(image).strip()
    if not url:
        return None
    if url.startswith("data:"):
        return url
    return f"data:image/png;base64,{url}"


def _messages_for_openrouter(messages: list[dict], system_text: str = "") -> list[dict]:
    """The agent's message list -> OpenRouter's (OpenAI-shaped) wire format.

    Three things the agent stores that are not plain OpenAI turns:

    * ``images`` on a user turn becomes a multimodal ``content`` array;
    * ``audio`` on a user turn becomes an ``input_audio`` part, which is what
      the voice box sends;
    * a ``tool`` role turn keeps ``tool_call_id`` so the upstream model can pair
      the result with the call it asked for.
    """
    out: list[dict] = []
    if system_text:
        out.append({"role": "system", "content": system_text})
    for message in messages or []:
        role = str(message.get("role") or "user")
        content = message.get("content")
        images = message.get("images") or []
        audio = message.get("audio")

        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": message.get("tool_call_id") or "",
                "content": content if isinstance(content, str) else json.dumps(content or {}),
            })
            continue
        if role == "assistant":
            turn: dict[str, Any] = {"role": "assistant",
                                    "content": content if isinstance(content, str) else ""}
            if message.get("tool_calls"):
                turn["tool_calls"] = message["tool_calls"]
            out.append(turn)
            continue

        text = content if isinstance(content, str) else json.dumps(content or {})
        if not images and not audio:
            out.append({"role": role, "content": text})
            continue
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for image in images:
            data_url = _image_data_url(image)
            if data_url:
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if audio:
            data = str(audio.get("data") or "") if isinstance(audio, dict) else str(audio)
            fmt = str((audio.get("format") if isinstance(audio, dict) else None) or "wav")
            parts.append({"type": "input_audio", "input_audio": {"data": data, "format": fmt}})
        out.append({"role": role, "content": parts})
    return out


def _routing(provider: dict | None, vendor: str | None) -> dict | None:
    """OpenRouter's ``provider`` object: pin routing to one vendor.

    Only sent when the operator picked a vendor, and only the parts that are
    unambiguous: ``only`` names the vendor and ``order`` puts it first. The
    model id already pins the model exactly, so this is a routing *preference*
    on top — which is why a rejection of it is retried without it (see
    :func:`complete`).
    """
    routing = dict(provider or {})
    name = (vendor or "").strip()
    if name:
        order = [name] + [v for v in (routing.get("order") or []) if v != name]
        routing["order"] = order
        only = list(dict.fromkeys([name, *(routing.get("only") or [])]))
        routing["only"] = only
        routing.setdefault("allow_fallbacks", True)
    return routing or None


def _request_body(*, model: str, messages: list[dict], tools: Optional[list[dict]],
                  system_text: str, temperature: float, max_tokens: int,
                  preset: str = "", vendor: str = "",
                  extra: Optional[dict] = None, stream: bool = False) -> dict[str, Any]:
    """One OpenRouter chat-completions body, built in exactly one place.

    Every path (ping, chat, stream, transcription, preset validation) uses this,
    so a change to how the app talks to OpenRouter cannot drift between them.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": _messages_for_openrouter(messages, system_text),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if stream:
        body["stream"] = True
    if tools:
        body["tools"] = [{"type": "function",
                          "function": {**t.get("function", t),
                                       "parameters": sanitise_schema(
                                           (t.get("function") or t).get("parameters")
                                           or {"type": "object", "properties": {}})}}
                         for t in tools]
        body["tool_choice"] = "auto"
    routing = _routing(None, vendor)
    if routing:
        body["provider"] = routing
    preset_name = (preset or "").strip()
    if preset_name:
        body["presets"] = preset_name
    if extra:
        body.update(extra)
    return body


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
    preset: str = "",
    vendor: str = "",
) -> dict:
    """One non-streaming model call through OpenRouter.

    The response is already the OpenAI shape the agent loop reads, so it is
    returned as-is.
    """
    if not api_key:
        raise ProviderError(
            code="key_missing", provider=provider, model=model,
            message=f"No {label(provider)} API key is configured.",
        )
    body = _request_body(
        model=model, messages=messages, tools=tools, system_text=system_text,
        temperature=temperature, max_tokens=max_tokens, preset=preset, vendor=vendor,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(OPENROUTER_CHAT_URL, headers=_headers(api_key),
                                         json=body)
            if response.status_code != 200 and "provider" in body:
                # A vendor pin OpenRouter cannot honour is a routing complaint,
                # not a broken agent. Drop it and retry once — the model id
                # still pins the model exactly.
                retry = {k: v for k, v in body.items() if k != "provider"}
                response = await client.post(OPENROUTER_CHAT_URL, headers=_headers(api_key),
                                             json=retry)
            if response.status_code != 200:
                raise _classify(provider, response.status_code, response.text, model)
            return response.json()
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
    preset, vendor = await get_preset(db, provider), await get_vendor(db, provider)
    try:
        return await complete(
            provider, api_key=api_key, model=model, messages=messages, tools=tools,
            system_text=system_text, temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, preset=preset, vendor=vendor,
        ), model
    except ProviderError as exc:
        if exc.code != "model_unavailable":
            raise
        logger.warning("AI Agent: model %s/%s unavailable, refreshing list", provider, model)
        replacement: Optional[str] = None
        try:
            models = await list_models(db, provider, api_key=api_key, refresh=True)
            replacement = pick_default(provider, [m for m in models if m.id != model])
        except ProviderError:
            replacement = None
        if not replacement:
            raise
        await set_model(db, provider, replacement)
        note = (f"The saved model “{model}” is no longer available on {label(provider)}. "
                f"Switched to “{replacement}” and retried.")
        if on_notice is not None:
            result = on_notice(note)
            if asyncio.iscoroutine(result):
                await result
        logger.info("AI Agent: recovered %s from %s -> %s", provider, model, replacement)
        try:
            response = await complete(
                provider, api_key=api_key, model=replacement, messages=messages, tools=tools,
                system_text=system_text, temperature=temperature, max_tokens=max_tokens,
                timeout=timeout, preset=preset, vendor=vendor,
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


async def ping(db: AsyncSession, provider: str = PROVIDER) -> ProviderHealth:
    """Prove the provider works end to end with a one-token completion.

    A configured key is not the same as a working provider — that was the
    original health bug, which reported both providers "configured" while every
    chat failed on a dead model name.
    """
    from datetime import datetime, timezone

    health = ProviderHealth(provider=provider, checked_at=datetime.now(timezone.utc).isoformat())
    key = await get_api_key(db, provider)
    if not key:
        health.state = "key_missing"
        health.message = f"No {label(provider)} key is saved."
        health.hint = _ERROR_HINTS["key_missing"]
        return health

    started = time.perf_counter()
    try:
        model, was_recovered = await ensure_model(db, provider, api_key=key)
        health.model = model
        if was_recovered:
            health.extra["note"] = "The previously saved model was retired; a current one was selected."
        vendor, preset = await get_vendor(db, provider), await get_preset(db, provider)
        if vendor:
            health.extra["vendor"] = vendor
        if preset:
            health.extra["preset"] = preset
        await complete(
            provider, api_key=key, model=model,
            messages=[{"role": "user", "content": "ping"}],
            tools=None, system_text="Reply with the single word: pong",
            max_tokens=16, timeout=25.0, preset=preset, vendor=vendor,
        )
        health.state = "ok"
        health.message = f"{label(provider)} answered with {model}."
    except ProviderError as exc:
        health.state = _HEALTH_STATE.get(exc.code, "unreachable")
        health.model = exc.model or health.model
        health.message = exc.message
        health.hint = exc.hint
        health.extra["code"] = exc.code
    except Exception as exc:  # pragma: no cover - defensive
        health.state = "unreachable"
        health.message = f"Could not reach {label(provider)}."
        health.hint = _ERROR_HINTS["provider_down"]
        health.extra["detail"] = str(exc)[:200]
    health.latency_ms = int((time.perf_counter() - started) * 1000)
    return health


async def ping_all(db: AsyncSession) -> list[ProviderHealth]:
    return [await ping(db, provider) for provider in PROVIDERS]


# ---------------------------------------------------------------------------
# Speech to text
# ---------------------------------------------------------------------------
async def audio_model(db: AsyncSession, *, api_key: Optional[str] = None) -> Optional[str]:
    """A model that accepts audio input, from the live catalogue.

    OpenRouter exposes ``architecture.input_modalities``, so this asks the
    catalogue instead of naming a model — the same rule that keeps chat models
    honest.
    """
    try:
        models = await list_models(db, PROVIDER, api_key=api_key)
    except ProviderError:
        return None
    candidates = [m for m in models if "audio" in m.input_modalities]
    if not candidates:
        return None
    return pick_default(PROVIDER, candidates)


def _audio_format(filename: str, content_type: str) -> str:
    low = f"{filename or ''} {content_type or ''}".lower()
    for token in ("mp3", "mpeg", "wav", "wave", "ogg", "m4a", "aac", "flac", "webm"):
        if token in low:
            return "mp3" if token == "mpeg" else ("wav" if token == "wave" else token)
    return "wav"


async def transcribe(
    db: AsyncSession,
    audio: bytes,
    filename: str,
    content_type: str,
    *,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> dict:
    """Transcribe audio through an audio-capable model on OpenRouter.

    ``language`` is optional by design: Nigerian English and Pidgin are detected
    far more reliably by letting the model auto-detect than by forcing ``en``.
    The UI exposes an override for anyone who wants it.

    Returns ``{"text", "provider", "model", "language"}``.
    """
    key = await get_api_key(db, PROVIDER)
    if not key:
        raise ProviderError(
            code="key_missing", provider=PROVIDER,
            message=f"No {label(PROVIDER)} API key is configured, so voice input cannot be "
                    "transcribed.",
        )
    if not audio:
        raise ProviderError(
            code="bad_request", provider=PROVIDER,
            message="The recording is empty.",
        )
    model = await audio_model(db, api_key=key)
    if not model:
        raise ProviderError(
            code="model_unavailable", provider=PROVIDER,
            message=("No model on this OpenRouter key accepts audio input. Choose one in "
                     "openrouter.ai/models that lists audio as an input modality."),
        )

    encoded = base64.b64encode(audio).decode()
    instruction = (
        "Transcribe this audio exactly as spoken. Reply with the transcript only, "
        "no commentary and no quotation marks."
    )
    if language:
        instruction += f" The language is {language}."
    if prompt:
        instruction += f" Context that may help with names and terms: {prompt[:400]}"
    messages = [{
        "role": "user",
        "content": "Transcribe the attached audio.",
        "audio": {"data": encoded, "format": _audio_format(filename, content_type)},
    }]
    try:
        data = await complete(
            PROVIDER, api_key=key, model=model, messages=messages, tools=None,
            system_text=instruction, temperature=0.0, max_tokens=2048, timeout=120.0,
            preset=await get_preset(db, PROVIDER), vendor=await get_vendor(db, PROVIDER),
        )
    except ProviderError:
        raise
    choice = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    text = str(choice.get("content") or "").strip()
    if not text:
        raise ProviderError(
            code="upstream_error", provider=PROVIDER, model=model,
            message="The model returned an empty transcript.",
        )
    return {"text": text, "provider": PROVIDER, "model": model, "language": language}


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
    timeout: float = 180.0,
    preset: str = "",
    vendor: str = "",
) -> AsyncIterator[dict]:
    """Stream one model call as ``{"type": "text"|"done"}`` events.

    Text is yielded the moment OpenRouter produces it, which is what makes the
    chat feel alive. The last event is always ``{"type": "done", "response":
    <OpenAI-shaped response>}`` so the caller's tool loop is identical to the
    non-streaming path — tool calls arrive in the same shape.
    """
    if not api_key:
        raise ProviderError(
            code="key_missing", provider=provider, model=model,
            message=f"No {label(provider)} API key is configured.",
        )
    body = _request_body(
        model=model, messages=messages, tools=tools, system_text=system_text,
        temperature=temperature, max_tokens=max_tokens, preset=preset, vendor=vendor,
        stream=True,
    )
    text_parts: list[str] = []
    #: Tool calls arrive in fragments keyed by index; this is where they are
    #: reassembled into the same shape the non-streaming path returns.
    calls: dict[int, dict] = {}
    finish_reason = "stop"
    usage: dict = {}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", OPENROUTER_CHAT_URL, headers=_headers(api_key),
                                     json=body) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", errors="replace")
                    raise _classify(provider, response.status_code, detail, model)
                async for line in response.aiter_lines():
                    raw = (line or "").strip()
                    if not raw or not raw.startswith("data:"):
                        continue
                    payload = raw[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    if chunk.get("error"):
                        error = chunk["error"]
                        message = str((error or {}).get("message") or error or "")
                        raise ProviderError(
                            code="upstream_error", provider=provider, model=model,
                            message=message or f"{label(provider)} returned an error.",
                            detail=str(error)[:400],
                        )
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(str(piece))
                            yield {"type": "text", "text": str(piece)}
                        for call in delta.get("tool_calls") or []:
                            index = int(call.get("index") or 0)
                            slot = calls.setdefault(index, {
                                "id": "", "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                            slot["id"] = slot["id"] or str(call.get("id") or "")
                            function = call.get("function") or {}
                            if function.get("name"):
                                slot["function"]["name"] += str(function["name"])
                            if function.get("arguments"):
                                slot["function"]["arguments"] += str(function["arguments"])
                        if choice.get("finish_reason"):
                            finish_reason = str(choice["finish_reason"])
    except ProviderError:
        raise
    except httpx.TimeoutException as exc:
        raise _timeout(provider, model, exc) from exc
    except Exception as exc:
        raise _unreachable(provider, model, exc) from exc

    tool_calls = [calls[i] for i in sorted(calls)]
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    done: dict[str, Any] = {
        "type": "done",
        "response": {"choices": [{"message": message, "finish_reason": finish_reason}]},
    }
    if usage:
        done["response"]["usage"] = usage
    yield done


async def stream_with_recovery(
    db: AsyncSession,
    provider: str,
    **kwargs: Any,
) -> AsyncIterator[dict]:
    """Stream with bounded model recovery.

    A 429 is often scoped to one model, not the whole account, and a retired
    model is a certainty on a catalogue this large. One replacement from the
    live list is tried, the working route is saved so the next message does not
    repeat the known-bad request, and everything is bounded so a broken
    catalogue can never leave a chat spinning forever.
    """
    model = kwargs.pop("model")
    kwargs.pop("allow_fallback", None)  # one provider now: nothing to fall back to
    requires_vision = bool(kwargs.pop("requires_vision", False))
    current_key = kwargs.get("api_key")
    kwargs.setdefault("preset", await get_preset(db, provider))
    kwargs.setdefault("vendor", await get_vendor(db, provider))
    attempted: set[str] = {model}
    recoveries = 0

    while True:
        try:
            async for event in stream_complete(provider, model=model, **kwargs):
                if event.get("type") == "done":
                    event = {**event, "provider": provider, "model": model}
                yield event
            return
        except ProviderError as exc:
            if exc.code not in ("model_unavailable", "rate_limited") or recoveries >= 1:
                raise
            replacement = None
            try:
                models = await list_models(db, provider, api_key=current_key,
                                           refresh=exc.code == "model_unavailable")
                remaining = [m for m in models if m.id not in attempted]
                replacement = (
                    pick_vision_model(provider, remaining)
                    if requires_vision else pick_default(provider, remaining)
                )
            except ProviderError:
                replacement = None
            if not replacement:
                raise
            old_model = model
            model = replacement
            attempted.add(model)
            recoveries += 1
            await set_model(db, provider, model)
            note = (
                f"The saved model “{old_model}” is no longer available on {label(provider)}. "
                f"Switched to “{model}”."
                if exc.code == "model_unavailable"
                else f"{label(provider)} hit a limit on {old_model}; switched to {model}."
            )
            yield {"type": "notice", "kind": "recovery", "provider": provider,
                   "model": model, "text": note}
