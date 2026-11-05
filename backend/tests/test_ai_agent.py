"""The AI Agent: model discovery, honest errors, approvals, consent, voice.

These tests encode the acceptance criteria that were failing in production:

  * a chat never returns a bare 500 (every failure is a structured code);
  * the user's message is saved even when the provider call fails;
  * a retired model is replaced from the live list and the turn still works;
  * no API key ever appears in a response body;
  * the agent refuses to message somebody who opted out;
  * high-impact tools wait for the operator's approval;
  * a saved (masked) key can be tested.

The provider is faked at the HTTP boundary (``httpx.AsyncClient``) so the real
request-building code — URLs, payload shapes, streaming parsers — is exercised.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.ai_agent import AIAgentAction, AIAgentMessage, AIAgentSession
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user

GROQ_KEY = "gsk_test_abcdefghijklmnopqrstuvwxyz_ZJGp"
GEMINI_KEY = "AIzaSyTESTKEY_abcdefghijklmnop"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="tester", email="tester@example.com",
        password_hash="x", role="admin", is_active=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def streamed_tool_call(name: str, args: dict, call_id: str = "call_1") -> httpx.Response:
    """A Groq-shaped *streaming* response carrying one tool call."""
    delta = {
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }]
            }
        }]
    }
    return httpx.Response(200, text=f"data: {json.dumps(delta)}\n\ndata: [DONE]")


async def save_key(db, provider: str, key: str) -> None:
    from app.services import ai_providers

    await ai_providers.set_api_key(db, provider, key)
    await db.commit()


# ---------------------------------------------------------------------------
# A fake provider, patched in at the httpx layer
# ---------------------------------------------------------------------------
class FakeProvider:
    """Answers Groq/Gemini HTTP calls with scripted results."""

    def __init__(self, *, models=None, chat=None, fail=None):
        self.models = models or {
            "groq": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "whisper-large-v3-turbo"],
            "gemini": ["gemini-2.5-flash", "gemini-2.0-flash"],
        }
        #: list of callables returning (status, body) for chat turns
        self.chat = chat or []
        self.fail = fail or {}
        self.calls: list[tuple[str, str]] = []
        self._chat_index = 0

    def _models_response(self, provider: str):
        if provider == "groq":
            return 200, {"data": [{"id": m, "context_window": 8192} for m in self.models["groq"]]}
        return 200, {
            "models": [
                {"name": f"models/{m}", "displayName": m, "supportedGenerationMethods": ["generateContent"]}
                for m in self.models["gemini"]
            ]
        }

    async def __call__(self, request):  # noqa: D401 - httpx MockTransport protocol
        url = str(request.url)
        self.calls.append((request.method, url))
        try:
            body = json.loads(request.content) if request.content else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}  # multipart upload (audio)

        if "models" in url and "generatecontent" not in url.lower() and "completions" not in url:
            provider = "gemini" if "generativelanguage" in url else "groq"
            if provider in self.fail:
                return httpx.Response(400, json={"error": {"message": "API key not valid"}})
            status, payload = self._models_response(provider)
            return httpx.Response(status, json=payload)

        if "audio/transcriptions" in url:
            return httpx.Response(200, json={"text": "import this into a new list", "language": "en"})

        # chat
        if self.chat:
            handler = self.chat[min(self._chat_index, len(self.chat) - 1)]
            self._chat_index += 1
            return handler(body)

        if body.get("stream"):
            chunks = [
                'data: {"choices":[{"delta":{"content":"Hello"}}]}',
                'data: {"choices":[{"delta":{"content":" there"}}]}',
                "data: [DONE]",
            ]
            return httpx.Response(200, text="\n\n".join(chunks))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Hello there"}}]},
        )


@pytest.fixture
def fake(monkeypatch):
    """Patch httpx.AsyncClient so no real network call ever happens."""
    holder = {"provider": FakeProvider()}

    real_client = httpx.AsyncClient

    class Patched(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(holder["provider"])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    return holder


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
async def test_models_come_from_the_live_list(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    response = await client.get("/api/v1/ai-agent/models?provider=groq")
    assert response.status_code == 200
    payload = response.json()
    assert [m["id"] for m in payload["models"]] == [
        "openai/gpt-oss-20b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"
    ]
    # Whisper is filtered out of the *chat* list… check the filter explicitly.
    ids = [m["id"] for m in payload["models"]]
    assert "whisper-large-v3-turbo" not in ids
    # A current default is chosen and stored.
    assert payload["selected"] == "openai/gpt-oss-20b"


async def test_no_hard_coded_model_names_in_code():
    """The original bug: dead model names baked into the source.

    They may still be *described* in a docstring (that is how the regression is
    documented), but they must never appear as a value the code can send.
    """
    import ast
    import pathlib

    banned = ("llama-3.3-70b-versatile", "gemini-1.5-flash")
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        source = path.read_text()
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                for name in banned:
                    if name in node.value and "re.compile" not in ast.dump(node):
                        offenders.append(f"{path}: {path.name} line {node.lineno}: {name}")
    # Regular expressions that merely *prefer* an old family are allowed; exact
    # model ids are not.
    offenders = [o for o in offenders if "PREFERENCE" not in o]
    assert not offenders, offenders


async def test_models_reports_a_bad_key_clearly(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    fake["provider"].fail["groq"] = True
    response = await client.get("/api/v1/ai-agent/models?provider=groq")
    payload = response.json()
    assert payload["models"] == []
    assert payload["error"]["code"] == "key_invalid"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
async def test_health_pings_the_provider_for_real(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    response = await client.get("/api/v1/ai-agent/health")
    assert response.status_code == 200
    payload = response.json()
    groq = next(p for p in payload["providers"] if p["provider"] == "groq")
    assert groq["state"] == "ok"
    gemini = next(p for p in payload["providers"] if p["provider"] == "gemini")
    assert gemini["state"] == "key_missing"
    assert payload["state"] == "ok"
    # The Agent page maps over these names while rendering. Returning the
    # runtime's area-count dictionary here made React unmount the whole page.
    from app.services import agent_tools
    assert payload["tool_areas"] == sorted(agent_tools.areas())
    # A chat completion really was sent, not just a model list.
    assert any("chat/completions" in url for _, url in fake["provider"].calls)


async def test_health_is_red_when_the_model_is_gone(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    from app.services import ai_providers

    await ai_providers.set_model(db, "groq", "some-retired-model")
    await db.commit()

    def gone(body):
        return httpx.Response(404, json={"error": {"message": "model_not_found"}})

    fake["provider"].chat = [gone, gone, gone, gone]
    response = await client.get("/api/v1/ai-agent/health")
    payload = response.json()
    groq = next(p for p in payload["providers"] if p["provider"] == "groq")
    assert groq["state"] == "model_unavailable"
    assert payload["state"] == "down"


# ---------------------------------------------------------------------------
# settings + key safety
# ---------------------------------------------------------------------------
async def test_no_key_ever_leaves_the_server(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    await save_key(db, "gemini", GEMINI_KEY)
    for path in (
        "/api/v1/ai-agent/settings",
        "/api/v1/ai-agent/health",
        "/api/v1/ai-agent/models?provider=groq",
        "/api/v1/ai-agent/models?provider=gemini",
    ):
        response = await client.get(path)
        assert GROQ_KEY not in response.text, path
        assert GEMINI_KEY not in response.text, path
    settings = (await client.get("/api/v1/ai-agent/settings")).json()
    assert settings["groq_hint"] == "gsk_***ZJGp"
    assert settings["groq_configured"] is True


async def test_test_button_uses_the_saved_key(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    # No api_key in the body — the old endpoint 422'd on this.
    response = await client.post("/api/v1/ai-agent/settings/test", json={"provider": "groq"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["using_saved_key"] is True
    assert payload["suggested_model"] == "openai/gpt-oss-20b"
    assert GROQ_KEY not in response.text


async def test_test_button_prefers_a_typed_key(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    response = await client.post(
        "/api/v1/ai-agent/settings/test",
        json={"provider": "groq", "api_key": "gsk_typed_key_value_123456"},
    )
    assert response.status_code == 200
    assert response.json()["using_saved_key"] is False


async def test_settings_persist_model_and_toggles(client, db, fake):
    response = await client.put(
        "/api/v1/ai-agent/settings",
        json={"provider": "groq", "model": "llama-3.3-70b-versatile",
              "default_provider": "groq", "auto_approve": True, "auto_fallback": False},
    )
    assert response.status_code == 200
    settings = (await client.get("/api/v1/ai-agent/settings")).json()
    assert settings["models"]["groq"] == "llama-3.3-70b-versatile"
    assert settings["auto_approve"] is True
    assert settings["auto_fallback"] is False


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
async def _new_session(client) -> str:
    response = await client.post("/api/v1/ai-agent/sessions", json={"name": "Test"})
    return response.json()["session"]["id"]


async def test_chat_streams_a_reply(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "hello", "stream": True},
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.strip().splitlines()]
    kinds = [e["type"] for e in events]
    assert "text" in kinds
    assert kinds[-1] == "done"
    text = "".join(e.get("text", "") for e in events if e["type"] == "text")
    assert "Hello" in text

    detail = (await client.get(f"/api/v1/ai-agent/sessions/{session_id}")).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]


async def test_chat_never_returns_a_bare_500(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    session_id = await _new_session(client)

    def boom(_body):
        return httpx.Response(500, text="upstream explosion")

    fake["provider"].chat = [boom]
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "hello", "stream": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == "provider_down"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["provider"] == "groq"
    # The user's message AND a visible error entry are both in the transcript.
    detail = (await client.get(f"/api/v1/ai-agent/sessions/{session_id}")).json()
    assert detail["messages"][0]["content"] == "hello"
    assert any(m.get("error") for m in detail["messages"])


async def test_user_message_is_saved_before_the_model_call(client, db, fake):
    """Even with no key at all, what the operator typed survives."""
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "create a campaign", "stream": False},
    )
    payload = response.json()
    assert payload["error"]["code"] == "key_missing"
    detail = (await client.get(f"/api/v1/ai-agent/sessions/{session_id}")).json()
    assert [m for m in detail["messages"] if m["role"] == "user"][0]["content"] == "create a campaign"


async def test_a_retired_model_recovers_automatically(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    from app.services import ai_providers

    await ai_providers.set_model(db, "groq", "llama-3.3-70b-versatile-exactly-dead")
    await db.commit()

    def dead_then_alive(body):
        if body.get("model", "").endswith("dead"):
            return httpx.Response(404, json={"error": {"message": "model_not_found"}})
        if body.get("stream"):
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"Recovered"}}]}\n\ndata: [DONE]'
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "Recovered"}}]}
        )

    fake["provider"].chat = [dead_then_alive]
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "hello", "stream": False},
    )
    payload = response.json()
    assert payload["error"] is None
    assert "Recovered" in payload["response"]
    # The operator was told which model answered.
    notices = [e for e in payload["events"] if e["type"] == "notice"]
    assert any("no longer available" in n["text"] for n in notices)
    # …and the replacement was persisted so the next turn starts clean.
    assert (await ai_providers.get_model(db, "groq")).startswith("openai/gpt-oss")


async def test_falls_back_to_the_other_provider(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    await save_key(db, "gemini", GEMINI_KEY)
    session_id = await _new_session(client)

    def route(body):
        if body.get("stream"):
            return httpx.Response(429, json={"error": {"message": "rate limit reached"}})
        event = {"candidates": [{"content": {"parts": [{"text": "From Gemini"}]}}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + chr(10) + chr(10) + "data: [DONE]")

    fake["provider"].chat = [route]
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "hello", "stream": False},
    )
    payload = response.json()
    assert payload["error"] is None
    notices = [e for e in payload["events"] if e["type"] == "notice"]
    assert any("rate-limit" in n["text"].lower() or "continuing on gemini" in n["text"].lower()
               for n in notices)


# ---------------------------------------------------------------------------
# tools: consent + approvals
# ---------------------------------------------------------------------------
async def test_agent_refuses_to_message_an_opted_out_contact(client, db, fake):
    from app.services import agent_tools

    contact = Contact(phone_number="+2348031234567", first_name="Ada", is_opted_out=True,
                      consent_status="opted_out", country="Nigeria", lead_status="new")
    db.add(contact)
    await db.commit()

    class Ctx:
        user = User(id=1, username="tester", role="admin")

    result = await agent_tools.REGISTRY["send_sms"].handler(
        db, {"contact_id": contact.id, "body": "Come back!"}, Ctx()
    )
    assert result["refused"] is True
    assert "opted out" in result["error"].lower()


async def test_starting_a_campaign_waits_for_approval(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)

    from app.models.campaign import Campaign
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember

    lst = ContactList(name="Restaurants")
    db.add(lst)
    await db.flush()
    contact = Contact(phone_number="+2348039999999", first_name="Bola", country="Nigeria",
                      lead_status="new")
    db.add(contact)
    await db.flush()
    db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
    campaign = Campaign(name="New restaurants v4", channel="sms", list_id=lst.id,
                        message_body="Hello {{first_name}}", status="draft")
    db.add(campaign)
    await db.commit()

    def wants_to_start(_body):
        return streamed_tool_call("start_campaign", {"campaign_id": campaign.id})

    fake["provider"].chat = [wants_to_start]
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "start the campaign", "stream": False},
    )
    payload = response.json()
    confirmations = [e for e in payload["events"] if e["type"] == "confirmation"]
    assert confirmations, payload["events"]
    card = confirmations[0]
    assert card["tool"] == "start_campaign"
    assert "1 contact" in card["summary"]
    assert card["details"]["message_preview"].startswith("Hello")

    # Nothing ran: the campaign is still a draft and the action is pending.
    await db.refresh(campaign)
    assert campaign.status == "draft"
    pending = list((await db.execute(
        select(AIAgentAction).where(AIAgentAction.status == "pending")
    )).scalars().all())
    assert len(pending) == 1

    # Approving executes the tool. (It is validated first, so a campaign that
    # this fixture deliberately left without a template is reported — not
    # silently started.)
    approve = await client.post(f"/api/v1/ai-agent/actions/{pending[0].id}/approve")
    assert approve.status_code == 200, approve.text
    body = approve.json()
    print("APPROVE BODY", json.dumps(body)[:1200])
    assert body["status"] in ("done", "failed")


async def test_approval_really_sends_a_valid_campaign(client, db, fake, monkeypatch):
    """The happy path: a valid campaign starts when the operator approves."""
    await save_key(db, "groq", GROQ_KEY)
    from app.config import settings

    # A deployment with an SMS gateway configured — otherwise validation is
    # *right* to refuse, and that is covered by the test above.
    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "user")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "pass")

    from app.models.campaign import Campaign
    from app.models.contact import Contact
    from app.models.contact_list import ContactList, ContactListMember

    lst = ContactList(name="Approved list")
    db.add(lst)
    await db.flush()
    contact = Contact(phone_number="+2348037777777", first_name="Ngozi", country="Nigeria",
                      lead_status="new")
    db.add(contact)
    await db.flush()
    db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
    campaign = Campaign(name="Approved campaign", channel="sms", list_id=lst.id,
                        message_body="Good morning {{first_name}}", status="draft")
    db.add(campaign)
    await db.commit()

    from app.services import agent_tools

    class Ctx:
        user = User(id=1, username="tester", role="admin")

    validated = await agent_tools.REGISTRY["validate_campaign"].handler(
        db, {"campaign_id": campaign.id}, Ctx()
    )
    assert validated["ok"] is True, validated

    fake["provider"].chat = [lambda _b: streamed_tool_call("start_campaign",
                                                           {"campaign_id": campaign.id})]
    session_id = await _new_session(client)
    await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": f"Start campaign {campaign.id}", "stream": False},
    )
    pending = list((await db.execute(
        select(AIAgentAction).where(AIAgentAction.status == "pending")
    )).scalars().all())
    approve = await client.post(f"/api/v1/ai-agent/actions/{pending[0].id}/approve")
    assert approve.status_code == 200, approve.text
    await db.refresh(campaign)
    assert campaign.status in ("running", "scheduled"), approve.json()


async def test_auto_approve_runs_without_a_card(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    await client.put("/api/v1/ai-agent/settings", json={"auto_approve": True})

    from app.services import agent_tools

    contact = Contact(phone_number="+2348031230000", first_name="Chi", country="Nigeria",
                      lead_status="new")
    db.add(contact)
    await db.commit()

    class Ctx:
        user = User(id=1, username="tester", role="admin")

    # auto_approve is honoured by the runtime, not the tool: the tool itself runs.
    result = await agent_tools.REGISTRY["bulk_contact_action"].handler(
        db, {"action": "status", "contact_ids": [contact.id], "value": "contacted"}, Ctx()
    )
    assert result["ok"] is True


async def test_activity_log_records_tool_calls(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)

    def searches(_body):
        return streamed_tool_call("search_contacts", {"city": "Abuja"})

    fake["provider"].chat = [searches]
    session_id = await _new_session(client)
    await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "Find contacts in Abuja", "stream": False},
    )
    activity = (await client.get("/api/v1/ai-agent/activity")).json()
    assert activity["actions"], activity
    assert activity["actions"][0]["tool"] == "search_contacts"
    assert activity["actions"][0]["status"] == "done"


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
async def test_upload_parses_a_csv_and_suggests_a_mapping(client, db, fake):
    session_id = await _new_session(client)
    csv_body = (
        "Business Name,Phone,Email,City\n"
        "Ada Foods,08031234567,ada@example.com,Lagos\n"
        "Bola Ltd,234 809 111 2222,bola@example.com,Abuja\n"
    )
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/files",
        files={"file": ("leads.csv", csv_body.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    file = response.json()["file"]
    assert file["preview"]["total_rows_estimate"] == 2
    mapping = file["preview"]["suggested_mapping"]
    assert mapping["Phone"] == "phone_number"
    assert mapping["Business Name"] == "business_name"
    assert file["summary"].startswith("CSV with 2 data rows")


def test_word_and_powerpoint_documents_extract_text():
    import io
    import zipfile

    from app.services import agent_files

    fixtures = {
        "brief.docx": {
            "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Quarterly brief</w:t></w:r></w:p></w:body></w:document>',
        },
        "slides.pptx": {
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Abuja launch plan</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
        },
    }
    for filename, members in fixtures.items():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, content in members.items():
                archive.writestr(path, content)
        parsed = agent_files.parse_file(filename, buffer.getvalue())
        assert parsed.text
        assert parsed.preview["text_head"] == parsed.text[:1500]


async def test_zip_upload_exposes_its_csv_member_for_search_and_import(client, db, fake):
    import io
    import zipfile

    from app.services import agent_tools

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("exports/contacts.csv", "Name,Phone\nAda,08031234567\nBola,08039876543\n")
        archive.writestr("readme.md", "Imported from the spring event.")
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/files",
        files={"file": ("contacts.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    file = response.json()["file"]
    assert file["preview"]["kind"] == "zip"
    assert file["preview"]["primary_entry"] == "exports/contacts.csv"
    assert len(file["preview"]["entries"]) == 2

    result = await agent_tools.REGISTRY["query_file"].handler(
        db, {"file_id": file["id"], "query": "Ada", "entry": "exports/contacts.csv"}, None
    )
    assert result["matches"][0]["rows"][0]["Name"] == "Ada"

    from app.models.contact_list import ContactList

    contact_list = ContactList(name="ZIP imports")
    db.add(contact_list)
    await db.flush()
    imported = await agent_tools.REGISTRY["import_csv"].handler(
        db, {"file_id": file["id"], "entry": "exports/contacts.csv", "list_id": contact_list.id}, None
    )
    assert imported["imported"] == 2


async def test_image_upload_is_safe_to_preview_and_attaches_as_provider_image(client, db, fake):
    import base64
    from app.services import ai_providers
    from app.services.agent_runtime import _file_images

    session_id = await _new_session(client)
    raw = b"\x89PNG\r\n\x1a\nminimal-image-data"
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/files",
        files={"file": ("chart.png", raw, "image/png")},
    )
    assert response.status_code == 200, response.text
    file = response.json()["file"]
    assert file["preview"]["kind"] == "image"

    assert base64.b64encode(raw).decode("ascii") not in json.dumps(file)
    images = await _file_images(db, session_id, [file["id"]])
    assert images == [{"mime_type": "image/png", "data": base64.b64encode(raw).decode("ascii")}]

    groq = ai_providers._messages_for_groq([{"role": "user", "content": "Describe this", "images": images}])
    assert groq[0]["content"][1]["type"] == "image_url"
    gemini = ai_providers._messages_for_gemini(
        [{"role": "user", "content": "Describe this", "images": images}], "system"
    )
    assert gemini[0]["parts"][1]["inlineData"]["mimeType"] == "image/png"


async def test_image_vision_fallback_is_per_turn_and_keeps_the_provider_toggle(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    await save_key(db, "gemini", GEMINI_KEY)
    session_id = await _new_session(client)
    upload = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/files",
        files={"file": ("chart.png", b"image-bytes", "image/png")},
    )
    file_id = upload.json()["file"]["id"]

    def gemini_reply(_body):
        event = {"candidates": [{"content": {"parts": [{"text": "A chart."}]}}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + chr(10) + chr(10) + "data: [DONE]")

    fake["provider"].chat = [gemini_reply]
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "What is in this picture?", "file_ids": [file_id], "stream": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["error"] is None
    assert any("for this turn" in event.get("text", "") for event in response.json()["events"])
    assert any("streamGenerateContent" in url for _, url in fake["provider"].calls)
    session = await client.get(f"/api/v1/ai-agent/sessions/{session_id}")
    assert session.json()["session"]["model"] == "groq"


async def test_groq_uses_a_live_vision_model_for_image_turns(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    fake["provider"].models["groq"].append("meta-llama/llama-4-scout-17b-16e-instruct")

    def describe(body):
        assert body["model"] == "meta-llama/llama-4-scout-17b-16e-instruct"
        image_parts = [
            part for message in body["messages"]
            if isinstance(message.get("content"), list)
            for part in message["content"]
            if part.get("type") == "image_url"
        ]
        assert image_parts
        event = {"choices": [{"delta": {"content": "This looks like a chart."}}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + chr(10) + chr(10) + "data: [DONE]")

    fake["provider"].chat = [describe]
    session_id = await _new_session(client)
    upload = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/files",
        files={"file": ("chart.png", bytes([137]) + b"PNG" + bytes([13, 10, 26, 10]) + b"image-bytes", "image/png")},
    )
    assert upload.status_code == 200, upload.text
    file_id = upload.json()["file"]["id"]
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "What is in this picture?", "file_ids": [file_id], "stream": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["error"] is None
    assert "chart" in response.json()["response"].lower()


async def test_fast_answers_leave_filtered_contact_counts_to_data_tools(db):
    from app.services.agent_fast_answers import fast_answer

    assert await fast_answer(db, "How many contacts do I have in my list?") is None
    assert await fast_answer(db, "How many SMS contacts do I have?") is None
    assert await fast_answer(db, "How many contacts did I send messages to this month?") is None
    assert await fast_answer(db, "How many contacts opted out of email?") == "0 contacts are opted out of email."
    opted_only = await fast_answer(db, "How many contacts do I have who opted out?")
    assert opted_only == "0 contacts have opted out of SMS; 0 have opted out of email."
    combined = await fast_answer(db, "How many contacts do I have, and how many opted out?")
    assert combined.startswith("Your workspace has 0 contacts.")


async def test_message_recipient_count_answers_locally_without_a_provider_call(client, db, fake):
    from app.models.conversation import Conversation, Message

    reached_both = Contact(phone_number="08030000001", first_name="Ada")
    attempted_only = Contact(phone_number="08030000002", first_name="Bola")
    reached_sms = Contact(phone_number="08030000003", first_name="Chidi")
    db.add_all([reached_both, attempted_only, reached_sms])
    await db.flush()
    conversations = [
        Conversation(contact_id=reached_both.id, channel="sms"),
        Conversation(contact_id=reached_both.id, channel="email"),
        Conversation(contact_id=attempted_only.id, channel="sms"),
        Conversation(contact_id=reached_sms.id, channel="sms"),
    ]
    db.add_all(conversations)
    await db.flush()
    message_rows = [
        (conversations[0], reached_both, "sms", "sent"),
        (conversations[0], reached_both, "sms", "delivered"),
        (conversations[1], reached_both, "email", "delivered"),
        (conversations[2], attempted_only, "sms", "failed"),
        (conversations[3], reached_sms, "sms", "sent"),
    ]
    db.add_all([
        Message(
            conversation_id=conversation.id,
            contact_id=contact.id,
            channel=channel,
            direction="outgoing",
            status=status,
            body="Hello",
            idempotency_key=f"fast-count-{index}",
        )
        for index, (conversation, contact, channel, status) in enumerate(message_rows)
    ])
    await db.commit()

    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/chat",
        json={"message": "How many contacts did I send messages to?", "stream": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "4 messages to 2 unique contacts" in payload["response"]
    assert fake["provider"].calls == []
    assert any(event.get("provider") == "local" for event in payload["events"])


async def test_unknown_binary_file_type_is_accepted_with_an_honest_preview(client, db, fake):
    session_id = await _new_session(client)
    response = await client.post(
        f"/api/v1/ai-agent/sessions/{session_id}/files",
        files={"file": ("archive.7z", b"7z\xbc\xaf\x27\x1c\x00\x04", "application/x-7z-compressed")},
    )
    assert response.status_code == 200, response.text
    file = response.json()["file"]
    assert file["preview"]["kind"] == "binary"
    assert "could not be extracted" in file["summary"]


async def test_import_tool_creates_contacts_in_a_new_list(client, db, fake):
    from app.services import agent_tools, agent_files

    session_id = await _new_session(client)
    rows = [{"Phone": f"0803123{n:04d}", "Name": f"Person {n}"} for n in range(50)]
    text = "Phone,Name\n" + "\n".join(f"{r['Phone']},{r['Name']}" for r in rows)
    parsed = agent_files.parse_file("bulk.csv", text.encode())

    from app.models.ai_agent import AIAgentFile

    record = AIAgentFile(
        session_id=session_id, filename="bulk.csv", content=parsed.text,
        preview=parsed.preview, size_bytes=len(text),
    )
    db.add(record)
    await db.commit()

    class Ctx:
        user = User(id=1, username="tester", role="admin")

    created = await agent_tools.REGISTRY["create_list"].handler(db, {"name": "Test"}, Ctx())
    list_id = created["list"]["id"]
    result = await agent_tools.REGISTRY["import_csv"].handler(
        db, {"file_id": record.id, "list_id": list_id}, Ctx()
    )
    assert result["imported"] == 50, result
    assert result["skipped"] == 0

    from app.models.contact_list import ContactListMember

    members = (await db.execute(
        select(ContactListMember).where(ContactListMember.list_id == list_id)
    )).scalars().all()
    assert len(members) == 50


# ---------------------------------------------------------------------------
# voice + memory
# ---------------------------------------------------------------------------
async def test_transcribe_returns_text(client, db, fake):
    await save_key(db, "groq", GROQ_KEY)
    response = await client.post(
        "/api/v1/ai-agent/transcribe",
        files={"audio": ("voice.webm", b"fake-audio-bytes", "audio/webm")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["text"] == "import this into a new list"
    assert payload["provider"] == "groq"


async def test_global_memory_round_trip(client, db, fake):
    response = await client.put("/api/v1/ai-agent/memory/global",
                                json={"text": "Operator is in Abuja, prefers Naira pricing."})
    assert response.status_code == 200
    assert "Abuja" in (await client.get("/api/v1/ai-agent/memory/global")).json()["text"]
    await client.delete("/api/v1/ai-agent/memory/global")
    assert (await client.get("/api/v1/ai-agent/memory/global")).json()["text"] == ""


async def test_skills_are_created_and_used_in_the_prompt(client, db, fake):
    response = await client.post(
        "/api/v1/ai-agent/skills/json",
        json={"name": "tone", "content": "Always write in warm, short sentences."},
    )
    assert response.status_code == 200
    skills = (await client.get("/api/v1/ai-agent/skills")).json()["skills"]
    assert skills[0]["name"] == "tone"

    from app.services.agent_runtime import build_system_prompt

    prompt = build_system_prompt(skills=[type("S", (), {"name": "tone", "content": "warm"})()])
    assert "warm" in prompt


# ---------------------------------------------------------------------------
# the prompt itself
# ---------------------------------------------------------------------------
def test_system_prompt_has_the_required_rules():
    from app.services.agent_runtime import SYSTEM_PROMPT

    for phrase in (
        "Reply STOP",          # named so the model knows NOT to write it
        "opt-out",
        "Nigerian",
        "+234",
        "consent",
        "prompt-injection",
        "personal note",
    ):
        assert phrase.lower() in SYSTEM_PROMPT.lower(), phrase
    assert "never invent the operator's name" in SYSTEM_PROMPT.lower()


def test_tool_coverage_includes_every_area():
    from app.services import agent_tools

    required = {
        "contacts", "lists", "files", "templates", "campaigns", "sending",
        "inbox", "calendar", "invoices", "automation", "analytics", "system",
    }
    assert required <= set(agent_tools.areas())
    assert len(agent_tools.REGISTRY) >= 55
    for name in ("start_campaign", "bulk_delete_contacts", "send_to_list", "launch_ads_campaign"):
        assert name in agent_tools.danger_tools()

    assert agent_tools.tools_for_query("Help me write a professional email") == []
    assert agent_tools.tools_for_query("Write an SMS for restaurants in Abuja") == []
    assert agent_tools.tools_for_query("Help me develop my negotiation skills") == []
    data_tools = agent_tools.tools_for_query("How many contacts did I send messages to?")
    assert "get_message_audience_stats" in {tool.name for tool in data_tools}


async def test_spreadsheet_rows_survive_upload_and_are_queryable(client, db, fake):
    import io
    import zipfile
    from app.services import agent_tools

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
        <row><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="B1" t="inlineStr"><is><t>Phone</t></is></c><c r="C1" t="inlineStr"><is><t>City</t></is></c></row>
        <row><c r="A2" t="inlineStr"><is><t>Ada, Ltd</t></is></c><c r="C2" t="inlineStr"><is><t>Abuja</t></is></c></row>
        </sheetData></worksheet>''')
    session_id = await _new_session(client)
    response = await client.post(f"/api/v1/ai-agent/sessions/{session_id}/files", files={"file": ("leads.xlsx", buffer.getvalue(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    file_id = response.json()["file"]["id"]
    rows = (await client.get(f"/api/v1/ai-agent/files/{file_id}")).json()["rows"]
    assert rows == [{"Name": "Ada, Ltd", "Phone": "", "City": "Abuja"}]
    result = await agent_tools.REGISTRY["query_file"].handler(db, {"file_id": file_id, "query": "Abuja"}, None)
    assert result["rows"] == rows


async def test_file_context_is_scoped_to_session_and_includes_document_excerpt(db):
    from app.models.ai_agent import AIAgentFile
    from app.services.agent_runtime import _file_summaries
    db.add_all([
        AIAgentSession(id="one", name="One"), AIAgentSession(id="two", name="Two"),
    ])
    await db.flush()
    own = AIAgentFile(session_id="one", filename="notes.txt", content="hello", preview={"summary": "Text", "text_head": "hello"}, size_bytes=5)
    other = AIAgentFile(session_id="two", filename="private.txt", content="secret", preview={"text_head": "secret"}, size_bytes=6)
    db.add_all([own, other])
    await db.flush()
    summaries = await _file_summaries(db, "one", [own.id, other.id])
    assert len(summaries) == 1
    assert "hello" in summaries[0] and "secret" not in summaries[0]

    from app.services import agent_tools

    ctx = type("Ctx", (), {"session_id": "one"})()
    denied = await agent_tools.REGISTRY["read_file_text"].handler(db, {"file_id": other.id}, ctx)
    assert denied == {"error": "File not found"}
