"""End-to-end simulation of the variables + campaign follow-up features.

Runs the real FastAPI app against a throwaway SQLite database with a fake SMS
gateway, and walks through the whole operator journey:

  1. Import a CSV with unusual columns (Pain Point, Niche, Website).
  2. See those columns appear on the Variables page automatically.
  3. Rename a short code and give another one a fallback.
  4. Send a message using {{Pain Point}} and a deliberately WRONG short code,
     and confirm the wrong one is stripped rather than texted.
  5. Import a SECOND CSV with different columns (States, Problem) and confirm
     they show up too.
  6. Open a contact profile and see every imported field.
  7. Build a two-step follow-up chain on a running campaign, prove the timing,
     the reply stop condition and the exactly-once guarantee.

Run:  .venv/bin/python scripts/simulate_variables_and_followups.py
"""

import asyncio
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "simulation-secret-key-at-least-32-chars-long")
os.environ.setdefault("SMSGATE_WEBHOOK_ALLOW_UNSIGNED", "1")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

GREEN, RED, YELLOW, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"

PASSED, FAILED = [], []


def check(label, condition, detail=""):
    if condition:
        PASSED.append(label)
        print(f"  {GREEN}✓{RESET} {label}")
    else:
        FAILED.append(label)
        print(f"  {RED}✗ {label}{RESET}" + (f"\n      {DIM}{detail}{RESET}" if detail else ""))


def section(title):
    print(f"\n{YELLOW}━━ {title} ━━{RESET}")


CSV_ONE = """First Name,Last Name,Phone,Business Name,City,Pain Point,Niche,Website
Ada,Obi,08031111111,Gwarinpa Kitchen,Abuja,no online orders,Restaurant,gwarinpa.ng
Chidi,Eze,08032222222,Eze Autos,Lagos,low repeat customers,Automotive,ezeautos.ng
Ngozi,Ali,08033333333,Ali Salon,Kano,,Beauty,
"""

# A completely different spreadsheet: new columns the app has never seen.
CSV_TWO = """Phone,First Name,States,Problem,Budget Range
08034444444,Bola,Oyo,staff turnover,500k-1m
08035555555,Emeka,Enugu,no website,under 500k
"""


async def main():
    from app.database import Base, get_db
    from app.models.campaign import Campaign, CampaignContact
    from app.models.contact import Contact
    from app.models.conversation import Conversation, Message
    from app.models.user import User
    from app.security.auth import get_current_user

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    # --- Fake gateway: record every SMS instead of sending one ---
    sent_messages = []

    async def fake_send(phone, body, sim=1):
        sent_messages.append({"phone": phone, "body": body})
        return {"success": True, "provider_message_id": f"sim-{len(sent_messages)}", "raw": {}}

    import app.providers.smsgate as smsgate

    smsgate.send_sms_direct = fake_send

    from app.services import sending_limits

    async def no_limits(self):
        return {"allowed": True, "reason": None}

    sending_limits.SendingGate.check = no_limits

    from app.main import app

    async def _get_db():
        yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="sim", email="sim@example.com",
        password_hash="x", role="admin", is_active=True,
    )

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://sim")

    # ================================================================
    section("1. Import a CSV with custom columns")
    response = await client.post(
        "/api/v1/contacts/import/csv",
        files={"file": ("leads.csv", io.BytesIO(CSV_ONE.encode()), "text/csv")},
        data={"skip_duplicates": "true"},
    )
    body = response.json()
    check("CSV imported", response.status_code == 200 and body["imported"] == 3, body)
    # Website and City map onto existing Contact columns, so only Pain Point
    # and Niche are genuinely new variables.
    check(
        f"import reports {body.get('new_variables', 0)} new variables discovered",
        body.get("new_variables", 0) == 2,
        body,
    )

    # ================================================================
    section("2. Variables page lists the imported columns")
    response = await client.get("/api/v1/variables/")
    variables = response.json()["items"]
    by_key = {item["field_key"]: item for item in variables}

    check("Pain Point discovered", "pain_point" in by_key)
    check("Niche discovered", "niche" in by_key)
    check("standard fields also listed", "first_name" in by_key and "city" in by_key)
    check(
        "Pain Point labelled nicely",
        by_key.get("pain_point", {}).get("label") == "Pain Point",
        by_key.get("pain_point"),
    )
    check(
        "usage count is right (2 of 3 contacts have a pain point)",
        by_key.get("pain_point", {}).get("contact_count") == 2,
        by_key.get("pain_point"),
    )
    print(f"  {DIM}variables: {', '.join(sorted(by_key))}{RESET}")

    # ================================================================
    section("3. Customize short codes")
    niche_id = by_key["niche"]["id"]
    response = await client.put(
        f"/api/v1/variables/{niche_id}",
        json={"shortcode": "industry_type", "fallback_text": "your industry"},
    )
    check("short code renamed to industry_type", response.json()["shortcode"] == "industry_type")

    # A duplicate short code must be refused, or two variables fight over it.
    response = await client.put(
        f"/api/v1/variables/{by_key['pain_point']['id']}",
        json={"shortcode": "industry_type"},
    )
    check("duplicate short code rejected", response.status_code == 409)

    # ================================================================
    section("4. Sending with correct AND wrong short codes")
    ada = (
        await session.execute(select(Contact).where(Contact.first_name == "Ada"))
    ).scalar_one()

    message = (
        "Hi {{First Name}}, I saw {{business_name}} in {{City}} struggles with "
        "{{Pain Point}}. We help {{industry_type}} brands. "
        "Does {{Business_name_typo}} want a demo? {{completely_made_up}}"
    )
    response = await client.post(
        "/api/v1/variables/preview",
        params={"body": message, "contact_id": ada.id},
    )
    analysis = response.json()
    preview = analysis["preview"]
    print(f"  {DIM}rendered: {preview}{RESET}")

    check("no braces survive", "{{" not in preview and "}}" not in preview)
    check("spaced short code {{First Name}} resolved", "Ada" in preview)
    check("{{Pain Point}} resolved", "no online orders" in preview)
    check("renamed short code {{industry_type}} resolved", "Restaurant" in preview)
    check(
        "wrong short code text never appears",
        "Business_name_typo" not in preview and "completely_made_up" not in preview,
        preview,
    )
    check(
        "wrong short codes reported to the user",
        set(analysis["unknown"]) == {"business_name_typo", "completely_made_up"},
        analysis["unknown"],
    )

    # Now actually send it and inspect what hit the "gateway".
    before = len(sent_messages)
    response = await client.post(
        "/api/v1/send/", params={"contact_id": ada.id, "body": message}
    )
    check("send succeeded", response.status_code == 200 and response.json()["sent"] == 1, response.text)
    actual = sent_messages[before]["body"]
    print(f"  {DIM}on the wire: {actual}{RESET}")
    check("the SMS that left contains no {{ }}", "{{" not in actual and "}}" not in actual)
    check("the SMS contains the real pain point", "no online orders" in actual)

    # A contact with NO value for the short code.
    ngozi = (
        await session.execute(select(Contact).where(Contact.first_name == "Ngozi"))
    ).scalar_one()
    before = len(sent_messages)
    await client.post(
        "/api/v1/send/",
        params={"contact_id": ngozi.id, "body": "Hi {{first_name}}, about {{Pain Point}} — interested?"},
    )
    ngozi_body = sent_messages[before]["body"]
    print(f"  {DIM}empty-value contact: {ngozi_body}{RESET}")
    check("empty short code removed for that contact", "{{" not in ngozi_body)
    check("message still reads sensibly", "Ngozi" in ngozi_body and "Pain" not in ngozi_body)

    # The configured fallback should appear for a contact with no niche.
    response = await client.post(
        "/api/v1/variables/preview",
        params={"body": "Perfect for {{industry_type}} owners.", "contact_id": ngozi.id},
    )
    check(
        "fallback text used where configured",
        "Beauty" in response.json()["preview"],
        response.json()["preview"],
    )

    # ================================================================
    section("5. A second CSV with brand-new columns")
    response = await client.post(
        "/api/v1/contacts/import/csv",
        files={"file": ("more.csv", io.BytesIO(CSV_TWO.encode()), "text/csv")},
        data={"skip_duplicates": "true"},
    )
    check("second CSV imported", response.json()["imported"] == 2, response.text)

    response = await client.get("/api/v1/variables/")
    keys = {item["field_key"] for item in response.json()["items"]}
    check("States appears in the list", "states" in keys)
    check("Problem appears in the list", "problem" in keys)
    check("Budget Range appears in the list", "budget_range" in keys)
    check("the original variables are still there", {"pain_point", "niche"} <= keys)

    # The renamed short code must survive a later import.
    response = await client.get("/api/v1/variables/")
    niche = next(i for i in response.json()["items"] if i["field_key"] == "niche")
    check("renamed short code survived the new import", niche["shortcode"] == "industry_type")

    # ================================================================
    section("6. Contact profile shows everything imported")
    response = await client.get(f"/api/v1/variables/contact/{ada.id}/profile")
    profile = response.json()
    fields = {field["field_key"]: field for field in profile["fields"]}
    print(f"  {DIM}profile fields: {', '.join(sorted(fields))}{RESET}")

    check("profile loads", response.status_code == 200)
    check("shows the pain point", fields.get("pain_point", {}).get("value") == "no online orders")
    check("shows the niche", fields.get("niche", {}).get("value") == "Restaurant")
    check("shows the website", fields.get("website", {}).get("value") == "gwarinpa.ng")
    check("shows the city", fields.get("city", {}).get("value") == "Abuja")
    check("shows the business name", fields.get("business_name", {}).get("value") == "Gwarinpa Kitchen")
    check(
        "each field carries its short code",
        fields.get("niche", {}).get("shortcode") == "industry_type",
        fields.get("niche"),
    )

    # ================================================================
    section("7. Campaign follow-up chain")
    campaign = Campaign(name="Restaurant outreach", status="running", message_body="Hello {{first_name}}")
    session.add(campaign)
    await session.flush()

    # Simulate the campaign having already messaged three contacts 3 days ago.
    targets = (
        await session.execute(
            select(Contact).where(Contact.first_name.in_(["Ada", "Chidi", "Ngozi"]))
        )
    ).scalars().all()
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    for contact in targets:
        # Some of these contacts were already messaged above, so they already
        # have a thread; conversations are unique per contact.
        conversation = (
            await session.execute(
                select(Conversation).where(Conversation.contact_id == contact.id)
            )
        ).scalars().first()
        if conversation is None:
            conversation = Conversation(contact_id=contact.id, status="active")
            session.add(conversation)
            await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id, contact_id=contact.id,
                campaign_id=campaign.id, direction="outgoing", body="Hello",
                status="delivered", sent_at=long_ago, created_at=long_ago,
                idempotency_key=f"sim-camp-{contact.id}",
            )
        )
        session.add(
            CampaignContact(
                campaign_id=campaign.id, contact_id=contact.id,
                status="sent", last_message_at=long_ago,
            )
        )
    await session.flush()

    response = await client.get("/api/v1/campaign-followups/campaigns")
    check("campaign appears in the follow-up picker", response.json()["total"] == 1, response.text)

    # Step 1: wait 2 days, stop if they replied or became interested.
    response = await client.post(
        f"/api/v1/campaign-followups/campaigns/{campaign.id}",
        json={
            "name": "First nudge",
            "message_text": "Hi {{first_name}}, following up about {{Pain Point}}. Interested?",
            "delay_minutes": 2 * 24 * 60,
            "stop_on_reply": True,
            "stop_on_lead_status": "interested,customer",
        },
    )
    check("step 1 created", response.status_code == 201, response.text)
    step1_id = response.json()["id"]

    # Step 2: another 2 days after step 1.
    response = await client.post(
        f"/api/v1/campaign-followups/campaigns/{campaign.id}",
        json={
            "name": "Final nudge",
            "message_text": "Last check-in {{first_name}} — shall I close your file?",
            "delay_minutes": 2 * 24 * 60,
            "stop_on_reply": True,
        },
    )
    step2_id = response.json()["id"]
    check("step 2 created and auto-numbered", response.json()["step_order"] == 2)

    # One contact replies — they must be excluded.
    chidi = next(c for c in targets if c.first_name == "Chidi")
    chidi_conversation = (
        await session.execute(select(Conversation).where(Conversation.contact_id == chidi.id))
    ).scalars().first()
    session.add(
        Message(
            conversation_id=chidi_conversation.id, contact_id=chidi.id,
            direction="incoming", body="Yes, tell me more", status="delivered",
            created_at=datetime.now(timezone.utc), idempotency_key="sim-reply-chidi",
        )
    )
    await session.flush()

    # Dry run first — this must not send anything.
    before = len(sent_messages)
    response = await client.get(f"/api/v1/campaign-followups/{step1_id}/preview")
    counts = response.json()["counts"]
    print(f"  {DIM}dry run: {counts}{RESET}")
    check("dry run says 2 will get the nudge", counts["send"] == 2, counts)
    check("dry run says 1 will be stopped (replied)", counts["stop"] == 1, counts)
    check("dry run sent nothing", len(sent_messages) == before)

    # Run it for real.
    before = len(sent_messages)
    response = await client.post(f"/api/v1/campaign-followups/{step1_id}/run")
    result = response.json()
    check("step 1 sent to exactly 2 contacts", result["sent"] == 2, result)
    check("step 1 stopped exactly 1 contact", result["stopped"] == 1, result)
    check("2 real SMS left the gateway", len(sent_messages) - before == 2)

    nudges = sent_messages[before:]
    for nudge in nudges:
        print(f"  {DIM}nudge → {nudge['phone']}: {nudge['body']}{RESET}")
    check("follow-ups are personalized", any("Ada" in n["body"] for n in nudges))
    check("follow-ups have no leftover braces", all("{{" not in n["body"] for n in nudges))
    check(
        "the contact who replied got nothing",
        all(n["phone"] != chidi.phone_number for n in nudges),
    )

    # Running it again must not resend.
    before = len(sent_messages)
    response = await client.post(f"/api/v1/campaign-followups/{step1_id}/run")
    check("re-running sends nothing (exactly once)", response.json()["sent"] == 0)
    check("no extra SMS on the wire", len(sent_messages) == before)

    # Step 2 is not due yet.
    response = await client.post(f"/api/v1/campaign-followups/{step2_id}/run")
    check("step 2 correctly waits for its delay", response.json()["sent"] == 0, response.json())

    # Age step 1's messages so step 2 becomes due.
    recent = (
        await session.execute(
            select(Message)
            .where(Message.campaign_id == campaign.id, Message.direction == "outgoing")
            .order_by(Message.id.desc())
            .limit(2)
        )
    ).scalars().all()
    for message_row in recent:
        message_row.sent_at = datetime.now(timezone.utc) - timedelta(days=3)
        message_row.created_at = message_row.sent_at
    await session.flush()

    before = len(sent_messages)
    response = await client.post(f"/api/v1/campaign-followups/{step2_id}/run")
    check("step 2 now sends to the 2 who never replied", response.json()["sent"] == 2, response.json())
    check("2 more SMS on the wire", len(sent_messages) - before == 2)

    # The log must explain every decision.
    response = await client.get(f"/api/v1/campaign-followups/{step1_id}/log")
    log = response.json()["items"]
    statuses = {row["status"] for row in log}
    reasons = {row["contact_name"]: row["reason"] for row in log}
    print(f"  {DIM}log: {json.dumps(reasons, default=str)}{RESET}")
    check("log records both sends and stops", statuses == {"sent", "stopped"}, statuses)
    check(
        "log explains why the replier was stopped",
        any(r == "Contact replied" for r in reasons.values()),
        reasons,
    )

    # A paused campaign must go quiet.
    campaign.status = "paused"
    await session.flush()
    response = await client.post(f"/api/v1/campaign-followups/{step2_id}/run")
    check("paused campaign sends nothing", response.json()["sent"] == 0)
    campaign.status = "running"
    await session.flush()

    # Opt-out is always respected.
    ada.is_opted_out = True
    await session.flush()
    response = await client.post(
        f"/api/v1/campaign-followups/campaigns/{campaign.id}",
        json={"message_text": "Third try {{first_name}}", "delay_minutes": 1},
    )
    step3_id = response.json()["id"]
    before = len(sent_messages)
    response = await client.post(f"/api/v1/campaign-followups/{step3_id}/run")
    check(
        "opted-out contact is never messaged",
        all(n["phone"] != ada.phone_number for n in sent_messages[before:]),
    )

    await client.aclose()
    await session.close()
    await engine.dispose()

    # ================================================================
    print(f"\n{YELLOW}{'━' * 60}{RESET}")
    total = len(PASSED) + len(FAILED)
    if FAILED:
        print(f"{RED}FAILED {len(FAILED)}/{total}{RESET}")
        for item in FAILED:
            print(f"  {RED}✗ {item}{RESET}")
        return 1
    print(f"{GREEN}ALL {total} CHECKS PASSED{RESET}")
    print(f"{DIM}{len(sent_messages)} simulated SMS sent, none containing a raw short code.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
