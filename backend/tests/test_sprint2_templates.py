"""Sprint 2 — C1, C2 and the template half of C8.

C1  ``render_text_preview`` ignored ``contact_id``: asking for "this message, for contact 7"
    rendered the sample data (John Doe, Acme Ltd) with no hint that the contact was ignored.
    The endpoint behind it did not even have the parameter, and it rendered with its own
    sample-values path instead of the registry-aware renderer the senders use, so a preview
    could disagree with what really goes out.

C2  A placeholder that names no variable at all (``{{frist_name}}``) was silently deleted:
    saved without a word, previewed as if it were fine, sent with a hole in the sentence.
    Now: rejected with a 422 at save (with a "did you mean"), shown as a visible marker in a
    preview, and reported by campaign validation. A REAL send still never prints the marker to
    a customer -- you can no longer get an unknown variable that far through the API.

C8  (templates) a bogus ``channel`` was silently turned into ``sms``, and an email template
    could be saved with no subject.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.models.contact import Contact
from app.models.template import Template
from app.models.variable import ContactVariable
from app.utils.templating import normalize_key, render_template


@pytest_asyncio.fixture
async def ada(api_db):
    contact = Contact(
        first_name="Ada", last_name="Obi", business_name="Zenith Foods", email="ada@zenith.io",
        phone_number="+2348031200001", city="Abuja",
        custom_fields=json.dumps({"Pain Point": "no online orders"}),
    )
    api_db.add(contact)
    await api_db.flush()
    return contact


async def _preview(client, **params):
    response = await client.post("/api/v1/templates/preview", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def _save(client, **body):
    payload = {"name": "T", "channel": "sms", "body": "Hello"} | body
    return await client.post("/api/v1/templates/", json=payload)


# --------------------------------------------------------------------------
# C1 -- the preview honours contact_id, through the renderer the senders use
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_renders_for_the_contact_it_is_given(api_client, ada):
    out = await _preview(api_client, body="Hi {{first_name}} at {{business_name}} in {{city}}",
                         contact_id=ada.id)

    assert out["preview"] == "Hi Ada at Zenith Foods in Abuja"
    assert out["contact"] == {"id": ada.id, "name": "Ada Obi"}


@pytest.mark.asyncio
async def test_preview_without_a_contact_still_uses_the_sample_data(api_client):
    out = await _preview(api_client, body="Hi {{first_name}} at {{business_name}}")

    assert out["preview"] == "Hi John at Acme Ltd"
    assert out["contact"] is None and out["sample"] is True


@pytest.mark.asyncio
async def test_explicit_sample_values_override_the_contact(api_client, ada):
    out = await _preview(api_client, body="Hi {{first_name}} at {{business_name}}",
                         contact_id=ada.id, first_name="Zed")

    assert out["preview"] == "Hi Zed at Zenith Foods"


@pytest.mark.asyncio
async def test_preview_for_an_unknown_contact_is_a_404_not_the_sample(api_client):
    response = await api_client.post("/api/v1/templates/preview",
                                     params={"body": "Hi {{first_name}}", "contact_id": 99999})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_preview_uses_the_variable_registry_like_a_real_send(api_client, api_db, ada):
    """Imported columns and the operator's fallback text are part of what goes out, so they
    must be part of the preview. The old preview path knew nothing about the registry."""
    api_db.add(ContactVariable(field_key="pain_point", label="Pain Point", shortcode="Pain Point",
                               source="imported"))
    api_db.add(ContactVariable(field_key="outlet_count", label="Outlet Count", shortcode="Outlets",
                               fallback_text="a few", source="imported"))
    await api_db.flush()

    out = await _preview(api_client, body="{{Pain Point}} / {{Outlets}}", contact_id=ada.id)

    assert out["preview"] == "no online orders / a few"
    assert out["unknown_variables"] == []


@pytest.mark.asyncio
async def test_preview_matches_what_the_real_renderer_sends(api_client, api_db, ada):
    from app.services.variable_service import render_for_contact

    body = "Hi {{first_name}}, {{Pain Point}} at {{ business_name }}. {{last_name|friend}}"
    real = await render_for_contact(api_db, body, ada)

    out = await _preview(api_client, body=body, contact_id=ada.id)

    assert out["preview"] == real


async def _tool(name, **args):
    from app.mcp import server
    from app.mcp.registry import TOOLS_BY_NAME

    class Stub:
        def add(self, row): pass
        async def flush(self): pass

    server.CURRENT_ADMIN_ID.set(1)
    outcome = await server.run_tool(
        Stub(), server.TokenView(id=1, name="t", scope="write", prefix="mcp_t"),
        TOOLS_BY_NAME[name], args)
    return json.loads(outcome.result["content"][0]["text"])


@pytest.mark.asyncio
async def test_the_assistant_tools_agree_with_each_other_and_honour_contact_id(
        api_client, api_db, ada):
    template = Template(name="Pitch", channel="sms", body="Hi {{first_name}} at {{business_name}}")
    api_db.add(template)
    await api_db.flush()

    saved = await _tool("preview_template", template_id=template.id, contact_id=ada.id)
    unsaved = await _tool("render_text_preview", body=template.body, contact_id=ada.id)

    assert saved["ok"] and unsaved["ok"], (saved, unsaved)
    text = lambda envelope: envelope["data"].get("preview") or envelope["data"].get("body")  # noqa: E731
    assert text(unsaved) == "Hi Ada at Zenith Foods", "render_text_preview honours contact_id"
    assert text(saved) == text(unsaved), "both tools render through the same code"


@pytest.mark.asyncio
async def test_render_text_preview_declares_contact_id_and_no_hidden_sample_defaults():
    """A default like first_name=John sent on every call would override the contact."""
    from app.mcp.registry import TOOLS_BY_NAME

    params = {p.name: p for p in TOOLS_BY_NAME["render_text_preview"].params}
    assert "contact_id" in params
    for name in ("first_name", "last_name", "business_name", "city", "website"):
        assert params[name].default is None, f"{name} would silently beat the contact's own value"


# --------------------------------------------------------------------------
# C2 -- an unknown variable is rejected at save, visible in a preview
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saving_a_template_with_an_unknown_variable_is_a_422(api_client):
    response = await _save(api_client, body="Hi {{frist_name}}, welcome")

    assert response.status_code == 422, response.text
    detail = response.json()
    assert detail["code"] == "UNKNOWN_TEMPLATE_VARIABLES"
    assert detail["unknown"] == ["frist_name"]
    assert detail["suggestions"] == {"frist_name": "first_name"}
    assert "frist_name" in detail["message"] and "first_name" in detail["message"]


@pytest.mark.asyncio
async def test_nothing_is_saved_when_it_is_refused(api_client, api_db):
    from sqlalchemy import func, select

    await _save(api_client, body="Hi {{frist_name}}")

    assert (await api_db.execute(select(func.count(Template.id)))).scalar_one() == 0


@pytest.mark.asyncio
async def test_updating_a_template_to_an_unknown_variable_is_a_422(api_client, api_db):
    created = await _save(api_client, body="Hi {{first_name}}")
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    response = await api_client.put(f"/api/v1/templates/{template_id}",
                                    json={"body": "Hi {{first_nmae}}"})

    assert response.status_code == 422 and response.json()["unknown"] == ["first_nmae"]
    assert (await api_client.get(f"/api/v1/templates/{template_id}")).json()["body"] == "Hi {{first_name}}"


@pytest.mark.asyncio
async def test_every_part_of_an_email_template_is_checked(api_client):
    for field, value in (("subject", "About {{compnay}}"), ("html_body", "<p>{{cty}}</p>"),
                         ("body", "Hello {{wesbite}}")):
        payload = {"channel": "email", "subject": "Hello", "body": "Hi", field: value}
        response = await _save(api_client, **payload)
        assert response.status_code == 422, f"{field}: {response.text}"
        assert response.json()["unknown"], field


@pytest.mark.asyncio
async def test_known_variables_in_every_spelling_are_accepted(api_client, api_db):
    api_db.add(ContactVariable(field_key="pain_point", label="Pain Point", shortcode="Pain Point",
                               source="imported"))
    await api_db.flush()
    body = ("{{first_name}} {{ First_Name }} {{FIRST_NAME}} {{first_name|there}} {{name}} {{brand}} "
            "{{full_name}} {{Pain Point}} {{pain_point}} {{city}} {{website}} and {not_a_variable} "
            "or { prose in braces }")

    response = await _save(api_client, body=body)

    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_a_column_that_came_in_with_an_import_counts_as_known(api_client, api_db):
    """The registry is filled by a scan; a column imported since the last scan must not make
    a legitimate template un-saveable."""
    api_db.add(Contact(first_name="X", phone_number="+2348031200009",
                       custom_fields=json.dumps({"Outlet Count": "4"})))
    await api_db.flush()

    ok = await _save(api_client, body="You run {{outlet_count}} outlets")
    typo = await _save(api_client, body="You run {{outlet_cuont}} outlets")

    assert ok.status_code == 201, ok.text
    assert typo.status_code == 422 and typo.json()["suggestions"] == {"outlet_cuont": "outlet_count"}


@pytest.mark.asyncio
async def test_a_preview_makes_an_unknown_variable_visible(api_client):
    out = await _preview(api_client, body="Hi {{frist_name}}, welcome")

    assert out["preview"] == "Hi [UNKNOWN VARIABLE: frist_name], welcome"
    assert out["unknown_variables"] == ["frist_name"]
    assert any("frist_name" in w and "first_name" in w for w in out["warnings"]), out["warnings"]


@pytest.mark.asyncio
async def test_a_preview_checks_the_subject_and_html_too(api_client):
    out = await _preview(api_client, body="ok", subject="About {{compnay}}",
                         html_body="<p>{{cty}}</p>", channel="email")

    assert out["subject"] == "About [UNKNOWN VARIABLE: compnay]"
    assert out["html"] == "<p>[UNKNOWN VARIABLE: cty]</p>"
    assert out["unknown_variables"] == ["compnay", "cty"]


def test_a_real_send_still_never_prints_the_marker_to_a_customer():
    """The default renderer (every send path) keeps removing what it cannot resolve; only an
    explicit unknown='mark' (previews) shows it. Leaking markers to customers is worse than a gap."""
    assert render_template("Hi {{first_name}}, about {{bogus}}.", None, first_name="Ada") == "Hi Ada, about."
    marked = render_template("Hi {{first_name}}, about {{bogus}}.", None, unknown="mark", first_name="Ada")
    assert marked == "Hi Ada, about [UNKNOWN VARIABLE: bogus]."


def test_a_known_variable_with_no_value_is_not_reported_as_unknown():
    """Missing is not the same as unknown: an empty value keeps its documented fallback."""
    out = render_template("Visit {{website}} {{industry|your trade}}", None, unknown="mark")

    assert "UNKNOWN" not in out and out == "Visit your trade"


@pytest.mark.asyncio
async def test_validate_fails_a_campaign_whose_message_has_an_unknown_variable(
        api_client, api_db, sms_gateway_configured):
    from tests.campaign_factory import make_campaign

    bad = await make_campaign(api_db, status="draft", message_body="Hi {{frist_name}}")
    good = await make_campaign(api_db, status="draft", message_body="Hi {{first_name}}")

    bad_report = (await api_client.post(f"/api/v1/campaigns/{bad.id}/validate")).json()
    good_report = (await api_client.post(f"/api/v1/campaigns/{good.id}/validate")).json()

    assert bad_report["valid"] is False
    assert any("frist_name" in e and "first_name" in e for e in bad_report["errors"]), bad_report["errors"]
    assert good_report["valid"] is True, good_report["errors"]


# --------------------------------------------------------------------------
# C8 -- a bogus channel and an email template without a subject
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bogus_channel_is_a_422_not_silently_sms(api_client, api_db):
    from sqlalchemy import func, select

    response = await _save(api_client, channel="whatsapp", body="Hi")

    assert response.status_code == 422
    assert "channel" in json.dumps(response.json()).lower()
    assert (await api_db.execute(select(func.count(Template.id)))).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", [None, "", "   "])
async def test_an_email_template_needs_a_subject(api_client, subject):
    response = await _save(api_client, channel="email", body="Hello there", subject=subject)

    assert response.status_code == 422, response.text
    assert "subject" in json.dumps(response.json()).lower()


@pytest.mark.asyncio
async def test_an_email_template_with_a_subject_and_an_sms_template_without_one_are_fine(api_client):
    email = await _save(api_client, channel="email", body="Hello", subject="Hi there")
    sms = await _save(api_client, channel="sms", body="Hello")

    assert email.status_code == 201 and sms.status_code == 201


@pytest.mark.asyncio
async def test_an_email_templates_subject_cannot_be_blanked_by_an_update(api_client):
    created = await _save(api_client, channel="email", body="Hello", subject="Hi there")
    template_id = created.json()["id"]

    blank = await api_client.put(f"/api/v1/templates/{template_id}", json={"subject": "  "})
    other = await api_client.put(f"/api/v1/templates/{template_id}", json={"name": "Renamed"})

    assert blank.status_code == 422
    assert other.status_code == 200 and other.json()["subject"] == "Hi there"
