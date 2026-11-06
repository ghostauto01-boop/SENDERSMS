"""OpenRouter is the AI Agent's only provider — pinned here, behaviour by behaviour.

The rules under test are the ones the operator set:

* one key, one provider; Groq and Gemini are gone from the code path entirely;
* no model name is ever invented — every model comes from OpenRouter's live
  ``/models`` list, and the vendor (the part before the slash in the id) is read
  off that list too, so "pick the provider" needs no hard-coded vendor table;
* a preset created on openrouter.ai is attached to every request by name;
* pinning a vendor is a routing preference, and one OpenRouter rejects is
  retried without it rather than turned into a failed chat.
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.services import ai_providers


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None, raising=False)
    ai_providers._MODEL_CACHE.clear()
    yield
    ai_providers._MODEL_CACHE.clear()


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


CATALOGUE = {
    "data": [
        {"id": "anthropic/claude-3.5-sonnet", "name": "Anthropic: Claude 3.5 Sonnet",
         "context_length": 200000, "supported_parameters": ["tools", "temperature"],
         "architecture": {"input_modalities": ["text", "image"]},
         "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
        {"id": "openai/gpt-4o-mini", "name": "OpenAI: GPT-4o mini",
         "context_length": 128000, "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text", "image", "audio"]},
         "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
        {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash",
         "context_length": 1000000, "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text", "image", "audio"]},
         "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}},
        {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Meta: Llama 3.3 70B",
         "context_length": 131072, "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text"]}, "pricing": {"prompt": "0.0000004"}},
        # Must never reach the dropdown: an embedding model cannot answer a chat.
        {"id": "openai/text-embedding-3-large", "name": "OpenAI: Embeddings",
         "context_length": 8191, "supported_parameters": []},
    ]
}


class _Responder:
    """Records every request so the wire format can be asserted on."""

    def __init__(self, *, models=CATALOGUE, status=200, body=None, presets_status=404):
        self.models = models
        self.status = status
        self.body = body if body is not None else {
            "choices": [{"message": {"role": "assistant", "content": "pong"},
                         "finish_reason": "stop"}]
        }
        self.presets_status = presets_status
        self.requests: list[tuple[str, dict]] = []

    def client(self, monkeypatch):
        responder = self

        class _Response:
            def __init__(self, status, payload):
                self.status_code = status
                self.text = json.dumps(payload)

            def json(self):
                return json.loads(self.text)

        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers=None, params=None):
                responder.requests.append(("GET", {"url": url, "headers": headers}))
                if url.endswith("/models"):
                    return _Response(200, responder.models)
                return _Response(responder.presets_status, {})

            async def post(self, url, headers=None, json=None, params=None):
                responder.requests.append(("POST", {"url": url, "headers": headers,
                                                   "json": json}))
                return _Response(responder.status, responder.body)

        monkeypatch.setattr(ai_providers.httpx, "AsyncClient", _Client)
        return responder


# ===========================================================================
# One provider
# ===========================================================================


def test_openrouter_is_the_only_provider():
    assert ai_providers.PROVIDERS == ("openrouter",)
    assert ai_providers.PROVIDER == "openrouter"
    # No Groq or Gemini URL can survive in the module.
    assert not any("groq" in name.lower() or "gemini" in name.lower()
                   for name in dir(ai_providers))


async def test_a_legacy_default_provider_resolves_to_openrouter(db):
    """Sessions created before the switch stored "groq"; that must not 404."""
    await ai_providers._set_value(db, ai_providers.DEFAULT_PROVIDER_SETTING, "groq")
    assert await ai_providers.get_default_provider(db) == "openrouter"


async def test_key_is_stored_encrypted_and_never_returned_in_full(db):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-abcdefghijklmnop")
    stored = await ai_providers._get_value(db, ai_providers.KEY_SETTING["openrouter"])
    assert "sk-or-v1-abcdefghijklmnop" not in (stored or "")
    assert await ai_providers.get_api_key(db, "openrouter") == "sk-or-v1-abcdefghijklmnop"
    summary = await ai_providers.keys_summary(db)
    assert summary["openrouter_configured"] is True
    assert summary["openrouter_hint"].startswith("sk-or-v")
    assert "abcdefghijklmnop" not in summary["openrouter_hint"]
    assert summary["console_url"] == "https://openrouter.ai/settings/keys"


async def test_environment_key_is_used_when_nothing_is_saved(db, monkeypatch):
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-from-environment")
    assert await ai_providers.get_api_key(db, "openrouter") == "sk-or-v1-from-environment"


# ===========================================================================
# The live catalogue is the only source of model names
# ===========================================================================


async def test_models_come_from_the_live_list_and_embeddings_are_filtered_out(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    _Responder().client(monkeypatch)
    models = await ai_providers.list_models(db, "openrouter")
    ids = {m.id for m in models}
    assert "anthropic/claude-3.5-sonnet" in ids
    assert "openai/gpt-4o-mini" in ids
    assert "openai/text-embedding-3-large" not in ids


async def test_vendor_is_read_off_the_model_id_not_a_hardcoded_table(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    _Responder().client(monkeypatch)
    models = await ai_providers.list_models(db, "openrouter")
    vendors = {m.id: m.vendor for m in models}
    assert vendors["anthropic/claude-3.5-sonnet"] == "Anthropic"
    assert vendors["openai/gpt-4o-mini"] == "OpenAI"
    assert vendors["google/gemini-2.5-flash"] == "Google"
    # A hyphenated author is pretty-printed, which is what the picker shows.
    assert vendors["meta-llama/llama-3.3-70b-instruct"] == "Meta"


async def test_vendor_picker_groups_the_live_list(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    _Responder().client(monkeypatch)
    vendors = await ai_providers.list_vendors(db, "openrouter")
    names = [v["vendor"] for v in vendors]
    assert names == sorted(names, key=lambda n: (-next(v["count"] for v in vendors
                                                       if v["vendor"] == n), n))
    assert {v["vendor"] for v in vendors} == {"Anthropic", "OpenAI", "Google", "Meta"}


async def test_picking_a_default_never_invents_a_name(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    _Responder().client(monkeypatch)
    models = await ai_providers.list_models(db, "openrouter")
    chosen = ai_providers.pick_default("openrouter", models)
    assert chosen in {m.id for m in models}
    assert chosen == "anthropic/claude-3.5-sonnet"  # first live preference match


async def test_a_missing_key_is_reported_not_guessed(db):
    with pytest.raises(ai_providers.ProviderError) as info:
        await ai_providers.list_models(db, "openrouter")
    assert info.value.code == "key_missing"
    assert "OpenRouter" in info.value.message


async def test_a_stale_model_is_replaced_from_the_live_list(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    _Responder().client(monkeypatch)
    await ai_providers.set_model(db, "openrouter", "openai/gpt-4-retired")
    model, was_recovered = await ai_providers.ensure_model(db, "openrouter")
    assert model != "openai/gpt-4-retired"
    assert was_recovered is True


# ===========================================================================
# Presets
# ===========================================================================


async def test_a_saved_preset_is_attached_to_every_request(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    responder = _Responder().client(monkeypatch)
    await ai_providers.set_preset(db, "my-sales-preset")
    await ai_providers.complete(
        "openrouter", api_key="sk-or-v1-key", model="anthropic/claude-3.5-sonnet",
        messages=[{"role": "user", "content": "hi"}], preset="my-sales-preset",
    )
    body = responder.requests[-1][1]["json"]
    assert body["presets"] == "my-sales-preset"


async def test_pinning_a_vendor_sets_openrouters_provider_filter(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    responder = _Responder().client(monkeypatch)
    await ai_providers.complete(
        "openrouter", api_key="sk-or-v1-key", model="anthropic/claude-3.5-sonnet",
        messages=[{"role": "user", "content": "hi"}], vendor="Anthropic",
    )
    body = responder.requests[-1][1]["json"]
    assert body["provider"]["only"] == ["Anthropic"]
    assert body["provider"]["order"][0] == "Anthropic"


async def test_a_vendor_pin_openrouter_rejects_is_retried_without_it(db, monkeypatch):
    """A routing preference must not cost the operator a failed chat."""
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    seen: list[dict] = []

    class _Response:
        def __init__(self, status, payload):
            self.status_code = status
            self.text = json.dumps(payload)

        def json(self):
            return json.loads(self.text)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None, params=None):
            seen.append(json)
            if "provider" in json:
                return _Response(400, {"error": {"message": "Unknown provider"}})
            return _Response(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(ai_providers.httpx, "AsyncClient", _Client)
    result = await ai_providers.complete(
        "openrouter", api_key="sk-or-v1-key", model="anthropic/claude-3.5-sonnet",
        messages=[{"role": "user", "content": "hi"}], vendor="Anthropic",
    )
    assert len(seen) == 2
    assert "provider" in seen[0] and "provider" not in seen[1]
    assert result["choices"][0]["message"]["content"] == "ok"


async def test_preset_listing_degrades_to_typing_the_name(db, monkeypatch):
    """OpenRouter has not always exposed a preset list; that is not an error."""
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    _Responder(presets_status=404).client(monkeypatch)
    result = await ai_providers.list_presets(db, "openrouter")
    assert result["available"] is False
    assert result["items"] == []


async def test_preset_listing_is_read_when_openrouter_serves_it(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")

    class _Response:
        status_code = 200
        text = json.dumps({"data": [{"name": "sales-voice", "description": "Warm, brief"}]})

        def json(self):
            return json.loads(self.text)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, params=None):
            return _Response()

    monkeypatch.setattr(ai_providers.httpx, "AsyncClient", _Client)
    result = await ai_providers.list_presets(db, "openrouter")
    assert result["available"] is True
    assert result["items"][0]["name"] == "sales-voice"


async def test_a_typo_preset_name_is_caught_by_a_real_request(db, monkeypatch):
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    await ai_providers.set_model(db, "openrouter", "anthropic/claude-3.5-sonnet")
    responder = _Responder(
        status=400,
        body={"error": {"message": "Preset 'sales-voiice' not found"}},
    ).client(monkeypatch)
    result = await ai_providers.validate_preset(db, "sales-voiice")
    assert result["success"] is False
    assert "does not know a preset" in result["error"]
    assert responder.requests[-1][1]["json"]["presets"] == "sales-voiice"


# ===========================================================================
# Wire format
# ===========================================================================


def test_tools_and_images_are_shaped_for_openrouter():
    messages = [{"role": "user", "content": "Describe this", "images": ["AAAA"]}]
    tools = [{"type": "function",
              "function": {"name": "lookup", "parameters": {
                  "$schema": "http://json-schema.org/draft-07/schema#",
                  "type": "object", "properties": {"q": {"type": "string"}}}}}]
    body = ai_providers._request_body(
        model="anthropic/claude-3.5-sonnet", messages=messages, tools=tools,
        system_text="You are helpful.", temperature=0.2, max_tokens=256,
    )
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"][0] == {"type": "text", "text": "Describe this"}
    assert body["messages"][1]["content"][1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    # `$schema` is stripped: strict upstream validators reject it.
    params = body["tools"][0]["function"]["parameters"]
    assert "$schema" not in params
    assert params["properties"]["q"]["type"] == "string"
    assert body["tool_choice"] == "auto"


def test_an_uploaded_image_keeps_its_real_mime_type():
    """The agent runtime stores {"mime_type", "data"}; a PNG must not claim to be JPEG."""
    shaped = ai_providers._messages_for_openrouter(
        [{"role": "user", "content": "What is this?",
          "images": [{"mime_type": "image/png", "data": "iVBOR"}]}], ""
    )
    assert shaped[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,iVBOR"
    already = ai_providers._messages_for_openrouter(
        [{"role": "user", "content": "x", "images": ["data:image/gif;base64,R0lG"]}], ""
    )
    assert already[0]["content"][1]["image_url"]["url"] == "data:image/gif;base64,R0lG"


def test_audio_becomes_an_input_audio_part():
    body = ai_providers._request_body(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Transcribe", "audio": {"data": "AAA=", "format": "mp3"}}],
        tools=None, system_text="", temperature=0.0, max_tokens=64,
    )
    part = body["messages"][0]["content"][1]
    assert part["type"] == "input_audio"
    assert part["input_audio"] == {"data": "AAA=", "format": "mp3"}


def test_a_tool_result_keeps_its_call_id():
    body = ai_providers._request_body(
        model="m", messages=[{"role": "tool", "tool_call_id": "call_1", "content": "42"}],
        tools=None, system_text="", temperature=0.0, max_tokens=16,
    )
    assert body["messages"][0] == {"role": "tool", "tool_call_id": "call_1", "content": "42"}


def test_errors_are_classified_into_the_stable_codes():
    assert ai_providers._classify("openrouter", 401, "", None).code == "key_invalid"
    assert ai_providers._classify("openrouter", 402, "No credits remaining", None).code == "key_invalid"
    assert ai_providers._classify("openrouter", 404, "not a valid model", "x").code == "model_unavailable"
    assert ai_providers._classify("openrouter", 429, "", None).code == "rate_limited"
    assert ai_providers._classify("openrouter", 400, "moderation blocked", None).code == "safety_blocked"
    assert ai_providers._classify("openrouter", 500, "", None).code == "provider_down"
    # Operator-facing copy says "OpenRouter", not "Openrouter".
    assert "OpenRouter" in ai_providers._classify("openrouter", 500, "", None).message


async def test_transcription_picks_an_audio_capable_model_from_the_catalogue(db, monkeypatch):
    """No hard-coded Whisper name: the catalogue says which models take audio."""
    await ai_providers.set_api_key(db, "openrouter", "sk-or-v1-key")
    responder = _Responder().client(monkeypatch)
    result = await ai_providers.transcribe(db, b"\x00\x01audio", "note.mp3", "audio/mpeg")
    assert result["model"] == "openai/gpt-4o-mini"  # first audio-capable model
    body = responder.requests[-1][1]["json"]
    # messages[0] is the transcription instruction; the audio rides on the turn.
    assert body["messages"][0]["role"] == "system"
    part = body["messages"][1]["content"][1]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "mp3"


async def test_transcription_without_a_key_says_what_is_missing(db):
    with pytest.raises(ai_providers.ProviderError) as info:
        await ai_providers.transcribe(db, b"audio", "a.wav", "audio/wav")
    assert info.value.code == "key_missing"
