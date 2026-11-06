"""Tests for OpenRouter Permanent Preset Lock, ordered fallback chain, SigV4 upstream classification, and live SSE progress events."""

import uuid

import pytest

from app.models.ai_agent import AIAgentSession
from app.services import agent_runtime, ai_providers
from app.services.ai_providers import ProviderError


@pytest.mark.asyncio
async def test_permanent_preset_lock_runs_only_selected_models_and_falls_back(api_db, monkeypatch):
    """When preset_locked=True, ONLY preset_models run in exact order, falling back seamlessly without overwriting the saved primary model."""
    ai_providers.clear_model_cooldowns()
    await ai_providers.set_api_key(api_db, "openrouter", "sk-or-v1-testkey-1234567890")
    await ai_providers.set_preset_locked(api_db, True)
    await ai_providers.set_preset_models(
        api_db,
        [
            "anthropic/claude-sonnet-4",
            "openai/gpt-4.1-mini",
            "deepseek/deepseek-chat",
        ],
    )
    await ai_providers.set_model(api_db, "openrouter", "anthropic/claude-sonnet-4")
    await ai_providers.set_saved_presets(
        api_db,
        [
            {
                "id": "ops-chain",
                "name": "Abuja Ops Preset",
                "models": [
                    "anthropic/claude-sonnet-4",
                    "openai/gpt-4.1-mini",
                    "deepseek/deepseek-chat",
                ],
                "openrouter_preset": None,
            }
        ],
    )
    await ai_providers.set_active_saved_preset(api_db, "ops-chain")
    await api_db.commit()

    assert await ai_providers.get_preset_locked(api_db) is True
    assert await ai_providers.get_preset_models(api_db) == [
        "anthropic/claude-sonnet-4",
        "openai/gpt-4.1-mini",
        "deepseek/deepseek-chat",
    ]
    assert await ai_providers.get_model(api_db, "openrouter") == "anthropic/claude-sonnet-4"

    attempted_models: list[str] = []

    async def _fake_stream_complete(*args, **kwargs):
        model_id = kwargs.get("model")
        attempted_models.append(model_id)
        if model_id == "anthropic/claude-sonnet-4":
            raise ProviderError("openrouter", "rate_limit", "429 capacity", status=429, retryable=True)
        if model_id == "openai/gpt-4.1-mini":
            yield {"type": "thinking", "text": "Checking workspace state..."}
            yield {"type": "text", "text": "Fallback model answered seamlessly."}
            yield {
                "type": "done",
                "response": {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Fallback model answered seamlessly.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_tokens": 42},
                },
            }
            return
        raise AssertionError(f"Unexpected model {model_id} called outside fallback order")

    monkeypatch.setattr(ai_providers, "stream_complete", _fake_stream_complete)

    sess = AIAgentSession(id=str(uuid.uuid4()), name="Preset Lock Test")
    api_db.add(sess)
    await api_db.commit()

    from app.models.user import User

    test_user = User(
        id=1, username="tester", email="tester@example.com", password_hash="x", role="admin", is_active=True
    )
    events = [
        ev
        async for ev in agent_runtime.run_turn(
            api_db,
            session=sess,
            user_text="Hello agent",
            user=test_user,
        )
    ]

    assert attempted_models == ["anthropic/claude-sonnet-4", "openai/gpt-4.1-mini"]
    event_types = [e["type"] for e in events]
    assert "turn_start" in event_types
    assert "status" in event_types
    assert "notice" in event_types
    assert "thinking" in event_types
    assert "text" in event_types
    assert "done" in event_types

    done_ev = next(e for e in events if e["type"] == "done")
    assert done_ev["model"] == "openai/gpt-4.1-mini"
    # Primary model in DB MUST remain locked to the first preset model (not overwritten by fallback!)
    assert await ai_providers.get_model(api_db, "openrouter") == "anthropic/claude-sonnet-4"


def test_sigv4_mismatch_from_upstream_classified_as_retryable_upstream_error():
    """Upstream Bedrock SigV4 403s via OpenRouter must trigger model fallback instead of blaming the user's OpenRouter API key."""
    err = ai_providers._classify(
        "openrouter",
        403,
        "The request signature we calculated does not match the signature you provided. Check your secret access key and signing method.",
        "anthropic/claude-sonnet-4",
    )
    assert err.code == "upstream_error"
    assert err.retryable is True


@pytest.mark.asyncio
async def test_settings_endpoint_saves_and_returns_permanent_preset_lock(api_client):
    res = await api_client.put(
        "/api/v1/ai-agent/settings",
        json={
            "api_key": "sk-or-v1-valid-preset-key-9999",
            "preset_locked": True,
            "preset_models": ["openai/gpt-4.1", "google/gemini-2.5-flash"],
            "saved_presets": [
                {
                    "id": "fast-pair",
                    "name": "Fast Pair",
                    "models": ["openai/gpt-4.1", "google/gemini-2.5-flash"],
                }
            ],
            "active_saved_preset": "fast-pair",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["provider"] == "openrouter"
    assert body["preset_locked"] is True
    assert body["preset_models"] == ["openai/gpt-4.1", "google/gemini-2.5-flash"]
    assert body["active_saved_preset"] == "fast-pair"
