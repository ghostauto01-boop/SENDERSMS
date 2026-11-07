"""Preset-only OpenRouter request routing and settings API coverage."""

import pytest

from app.services import ai_providers
from app.services.ai_providers import ProviderError


@pytest.mark.asyncio
async def test_selected_preset_owns_routing_and_ignores_legacy_model_chain(api_db, monkeypatch):
    """Legacy local model-chain values must never replace an OpenRouter preset."""
    await ai_providers.set_api_key(api_db, "openrouter", "sk-or-v1-testkey-1234567890")
    await ai_providers.set_preset(api_db, "@preset/ops-chain")
    await ai_providers.set_preset_locked(api_db, True)
    await ai_providers.set_preset_models(
        api_db,
        ["anthropic/claude-sonnet-4", "openai/gpt-4.1-mini"],
    )
    await api_db.commit()

    calls: list[dict] = []

    async def _fake_stream_complete(provider, **kwargs):
        calls.append({"provider": provider, **kwargs})
        yield {"type": "text", "text": "Preset answered."}
        yield {
            "type": "done",
            "response": {"choices": [{"message": {"role": "assistant", "content": "Preset answered."}}]},
        }

    monkeypatch.setattr(ai_providers, "stream_complete", _fake_stream_complete)
    events = [
        event
        async for event in ai_providers.stream_with_recovery(
            api_db,
            "openrouter",
            api_key="sk-or-v1-testkey-1234567890",
            model="legacy/model-choice",
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert len(calls) == 1
    assert calls[0]["model"] == "@preset/ops-chain"
    assert calls[0]["preset"] == "ops-chain"
    assert "vendor" not in calls[0]
    assert [event["type"] for event in events] == ["text", "done"]
    assert events[-1]["model"] == "@preset/ops-chain"


@pytest.mark.asyncio
async def test_nonstream_recovery_respects_auto_fallback_setting(api_db, monkeypatch):
    await ai_providers.set_flag(api_db, ai_providers.AUTO_FALLBACK_SETTING, False)
    await api_db.commit()
    calls = []

    async def limited(_provider, **kwargs):
        calls.append(kwargs["model"])
        raise ProviderError(
            code="rate_limited", provider="openrouter", model=kwargs["model"],
            message="Rate limit reached.",
        )

    async def unexpected_model_list(*_args, **_kwargs):
        raise AssertionError("Disabled automatic recovery must not load replacement models")

    monkeypatch.setattr(ai_providers, "complete", limited)
    monkeypatch.setattr(ai_providers, "list_models", unexpected_model_list)

    with pytest.raises(ProviderError, match="Rate limit reached"):
        await ai_providers.complete_with_recovery(
            api_db, "openrouter", api_key="key", model="openai/model",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert calls == ["openai/model"]


@pytest.mark.asyncio
async def test_nonstream_recovery_uses_a_live_replacement_when_enabled(api_db, monkeypatch):
    await ai_providers.set_flag(api_db, ai_providers.AUTO_FALLBACK_SETTING, True)
    await api_db.commit()
    calls = []

    async def unavailable_then_success(_provider, **kwargs):
        calls.append(kwargs["model"])
        if len(calls) == 1:
            raise ProviderError(
                code="model_unavailable", provider="openrouter", model=kwargs["model"],
                message="Model is unavailable.",
            )
        return {"model": kwargs["model"]}

    async def live_models(_db, _provider, **_kwargs):
        return [ai_providers.ModelInfo(id="openai/gpt-4.1-mini", label="GPT-4.1 mini")]

    monkeypatch.setattr(ai_providers, "complete", unavailable_then_success)
    monkeypatch.setattr(ai_providers, "list_models", live_models)

    response, selected_model = await ai_providers.complete_with_recovery(
        api_db, "openrouter", api_key="key", model="legacy/retired-model",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert calls == ["legacy/retired-model", "openai/gpt-4.1-mini"]
    assert response["model"] == selected_model == "openai/gpt-4.1-mini"


@pytest.mark.asyncio
async def test_settings_endpoint_saves_and_returns_preset_only_configuration(api_client):
    saved = await api_client.put(
        "/api/v1/ai-agent/settings",
        json={"api_key": "sk-or-v1-valid-preset-key-9999", "auto_approve": True},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["provider"] == "openrouter"

    preset_response = await api_client.put(
        "/api/v1/ai-agent/presets",
        json={"preset": "fast-pair"},
    )
    assert preset_response.status_code == 200, preset_response.text
    assert preset_response.json()["success"] is True
    assert preset_response.json()["preset"] == "fast-pair"

    settings = (await api_client.get("/api/v1/ai-agent/settings")).json()
    assert settings["preset"] == "fast-pair"
    assert settings["auto_approve"] is True
    assert "models" not in settings
    assert "vendor" not in settings
    assert "preset_models" not in settings


def test_sigv4_mismatch_from_upstream_classified_as_retryable_upstream_error():
    """Bedrock SigV4 failures via OpenRouter remain recoverable upstream errors."""
    err = ai_providers._classify(
        "openrouter",
        403,
        "The request signature we calculated does not match the signature you provided. Check your secret access key and signing method.",
        "anthropic/claude-sonnet-4",
    )
    assert err.code == "upstream_error"
    assert err.retryable is True
