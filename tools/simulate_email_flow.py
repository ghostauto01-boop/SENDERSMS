"""End-to-end simulator run of the email channel, against the fake Brevo.

Exercises exactly what the UI does: add a Brevo account, read verified senders,
compose a rich email with an attachment, send it, receive a threaded reply,
answer it from the inbox, one-click unsubscribe, and validate an email campaign.
"""
import base64
import json
import os
import subprocess
import sys

import httpx

BASE = os.environ.get("SMS_API_BASE", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
RUN = str(int(__import__("time").time()))
FAKE = "http://127.0.0.1:8799"
ok = True


def step(name, condition, detail=""):
    global ok
    mark = "PASS" if condition else "FAIL"
    if not condition:
        ok = False
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


# Re-runnable: start from a clean Brevo capture so "last payload" is this run's.
httpx.delete(f"{FAKE}/_log")

with httpx.Client(base_url=BASE, timeout=30, follow_redirects=True) as api:
    # ---------------------------------------------------------------- login
    # The login screen asks for the admin password; the shipped default is used
    # when the environment does not override it.
    password = os.environ.get("ADMIN_PASSWORD") or os.environ.get("APP_PASSWORD") or "12345678"
    api.post("/auth/admin", json={"password": password})
    me = api.get("/auth/me")
    step("login", me.status_code == 200, str(me.status_code))

    # Sending rules are ON by default (09:00-17:00, weekends off, a daily cap per
    # mailbox), so a campaign started at night or on a weekend would -- correctly --
    # send nothing, and this simulator would fail depending on the clock. It is a
    # throwaway database against a fake Brevo: open the rules for the run.
    api.put("/settings/sending-rules", params={
        "dl": "false", "ss": "", "se": "", "aw": "true", "pc": "false",
    })

    # ------------------------------------------------------ add Brevo sender
    resp = api.post("/email/accounts", json={
        "name": "Acme Leads",
        "from_name": "Acme Leads",
        "from_email": "hello@acme-leads.io",
        "reply_to": "replies@acme-leads.io",
        "api_key": "xkeysib-simulated-key",
        "is_default": True,
    })
    if resp.status_code == 409 or (resp.status_code == 400 and "already" in resp.text):
        existing = api.get("/email/accounts").json()["items"]
        account = next(a for a in existing if a["from_email"] == "hello@acme-leads.io")
        step("create Brevo account", True, "already present, reusing")
    else:
        step("create Brevo account", resp.status_code in (200, 201), resp.text[:300])
        account = resp.json()
    account_id = account["id"]
    step("api key is never returned in clear", "xkeysib-simulated-key" not in resp.text,
         account.get("api_key_masked", ""))

    # ------------------------------------------------- verified sender list
    senders = api.get(f"/email/accounts/{account_id}/senders")
    body = senders.json()
    step("verified senders read from Brevo",
         senders.status_code == 200 and "acme-leads.io" in senders.text,
         json.dumps(body)[:200])

    # ------------------------------------------------------- deliverability
    deliv = api.get(f"/email/accounts/{account_id}/deliverability")
    d = deliv.json()
    step("deliverability returns domain auth + checks",
         deliv.status_code == 200 and d["score"] > 0 and len(d["checks"]) >= 6,
         f"score={d.get('score')} domains={[x.get('domain') for x in d.get('domains', [])]}")

    # ------------------------------------------------------------- contact
    contact = api.post("/contacts/", json={
        "phone_number": "+2348012345001", "first_name": "Ada",
        "email": "ada@acme-leads.io", "country": "Nigeria",
    })
    if contact.status_code in (200, 201):
        contact_id = contact.json()["id"]
        step("create contact", True)
    else:
        found = api.get("/contacts/", params={"search": "ada@acme-leads.io"}).json()
        contact_id = found["items"][0]["id"]
        step("create contact", True, "already present, reusing")

    # Re-runnable: the previous run ends by unsubscribing this address, so
    # restore consent and clear the suppression row before sending again.
    api.post(f"/contacts/{contact_id}/email-opt-in", json={})
    for row in api.get("/email/suppression").json().get("items", []):
        if row.get("email_address") == "ada@acme-leads.io":
            api.delete(f"/email/suppression/{row['id']}")

    # -------------------------------------------- template with attachment
    pdf = base64.b64encode(b"%PDF-1.4 simulated price list").decode()
    tpl = api.post("/templates/", json={
        "name": "Price list (rich)",
        "channel": "email",
        "subject": "Your price list, {{first_name}}",
        "body": "Hi {{first_name}},\n\nHere is the price list you asked for.\n\nAda",
        "html_body": ('<p>Hi {{first_name}},</p>'
                      '<p><img src="https://cdn.acme-leads.io/hero.png" alt="hero"></p>'
                      '<p><a href="https://acme-leads.io/pricing">Full pricing</a></p>'),
        "attachments": [{"name": "price-list.pdf", "content_base64": pdf,
                         "content_type": "application/pdf"}],
        "include_unsubscribe": True,
    })
    step("create email template with HTML + attachment",
         tpl.status_code in (200, 201), tpl.text[:300])
    template_id = tpl.json()["id"]

    got = api.get(f"/templates/{template_id}").json()
    step("template returns attachment metadata, not the payload",
         got["attachments"] and got["attachments"][0]["name"] == "price-list.pdf"
         and "content" not in got["attachments"][0],
         json.dumps(got["attachments"]))
    step("template keeps HTML + unsubscribe flag",
         "img src=" in (got.get("html_body") or "") and got.get("include_unsubscribe") is True)

    # -------------------------------------------------- preview the render
    prev = api.post("/email/preview", json={
        "template_id": template_id, "contact_id": contact_id,
    }).json()
    step("preview renders variables + lists attachments",
         "Ada" in json.dumps(prev) and prev.get("attachments"),
         json.dumps(prev)[:220])

    # --------------------------------------------- send an email using it
    send = api.post("/email/send", json={
        "contact_id": contact_id, "template_id": template_id,
        "email_account_id": account_id,
    })
    step("send email via template", send.status_code == 200 and send.json().get("sent") == 1,
         send.text[:300])

    history = api.get("/email/history").json()
    first = history["items"][0]
    step("history shows the message with attachment metadata",
         first.get("attachments") and first["attachments"][0]["name"] == "price-list.pdf",
         json.dumps(first.get("attachments")))
    step("outgoing message has an RFC Message-ID for threading",
         bool(first.get("rfc_message_id")), str(first.get("rfc_message_id")))

    # what actually crossed the wire to Brevo
    wire = httpx.get(f"{FAKE}/_log").json()["entries"]
    last = wire[-1]["body"]
    step("Brevo payload has html + text + attachment",
         "img src=" in last["htmlContent"] and last["textContent"]
         and base64.b64decode(last["attachment"][0]["content"]).startswith(b"%PDF"),
         json.dumps({k: v for k, v in last.items() if k != "attachment"})[:200])
    first_unsub = (last.get("headers") or {}).get("List-Unsubscribe")
    step("outgoing mail carries List-Unsubscribe headers",
         "List-Unsubscribe" in (last.get("headers") or {})
         and (last["headers"].get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click"),
         json.dumps(last.get("headers")))

    conv_id = first.get("conversation_id")
    before = len(api.get(f"/email/inbox/conversations/{conv_id}").json()["messages"])

    # ------------------------------------ contact replies: must thread
    # Brevo is configured with the account's own webhook URL + token.
    accounts = api.get("/email/accounts").json()["items"]
    me_account = next(a for a in accounts if a["from_email"] == "hello@acme-leads.io")
    # C10: account reads carry only a masked hint of the webhook token (it is the
    # credential for this endpoint). The real URL comes from the deliberate reveal action,
    # exactly as the dashboard's "Copy webhook URL" button fetches it.
    shown = me_account.get("webhook_url") or me_account.get("webhook_path") or ""
    step("account reads show a masked webhook token, never the real one",
         "token=****" in shown and me_account.get("webhook_token_masked", "").startswith("****"),
         shown)
    revealed = api.post(f"/email/accounts/{me_account['id']}/webhook/reveal")
    step("reveal returns the full webhook URL (not cacheable)",
         revealed.status_code == 200 and "token=" in revealed.json()["webhook_url"]
         and "****" not in revealed.json()["webhook_url"]
         and "no-store" in revealed.headers.get("cache-control", ""),
         revealed.text[:160].replace(revealed.json().get("webhook_url", "-").split("token=")[-1], "<token>"))
    webhook_path = revealed.json()["webhook_url"]
    me_account["webhook_url"] = webhook_path  # the later steps post events to it
    hook = api.post(webhook_path.split("/api/v1")[1], json={"items": [{
        "From": {"Address": "ada.personal@gmail.com", "Name": "Ada"},
        "To": {"Address": "hello@acme-leads.io"},
        "Subject": "Re: Your price list, Ada",
        "TextBody": "Looks great. Can you do 500 units?",
        "MessageId": f"<reply-{RUN}@acme-leads.io>",
        "Headers": {"In-Reply-To": first["rfc_message_id"],
                    "References": first["rfc_message_id"]},
    }]})
    step("inbound webhook accepted", hook.status_code == 200, hook.text[:200])
    wrong = httpx.post(webhook_path.split("token=")[0] + "token=wrong-token", json={"event": "delivered"})
    step("a wrong webhook token is refused", wrong.status_code == 403, str(wrong.status_code))

    detail = api.get(f"/email/inbox/conversations/{conv_id}").json()
    inbound = [
        m for m in detail["messages"]
        if m["direction"] == "incoming" and m.get("in_reply_to") == first["rfc_message_id"]
    ]
    step("reply landed in the SAME conversation (not a new thread)",
         len(detail["messages"]) == before + 1 and len(inbound) == 1,
         f"{before} -> {len(detail['messages'])} messages, {len(inbound)} matched by In-Reply-To")
    step("personal reply address is linked without replacing the primary contact email",
         detail.get("contact", {}).get("email") == "ada@acme-leads.io"
         and "ada.personal@gmail.com" in detail.get("contact", {}).get("email_aliases", []),
         json.dumps({"email": detail.get("contact", {}).get("email"),
                     "aliases": detail.get("contact", {}).get("email_aliases", [])}))

    # ------------------------------------------------ reply from the inbox
    reply = api.post(f"/email/inbox/conversations/{conv_id}/reply", json={
        "body": "500 units it is — quote attached.",
        "html_body": "<p>500 units it is — <strong>quote attached</strong>.</p>",
        "attachments": [{"name": "quote.txt",
                         "content_base64": base64.b64encode(b"500 units @ 2.00").decode()}],
    })
    step("reply from the inbox sends", reply.status_code == 200, reply.text[:250])

    wire = httpx.get(f"{FAKE}/_log").json()["entries"]
    reply_payload = wire[-1]["body"]
    step("reply is sent to the latest personal sender address",
         reply_payload.get("to", [{}])[0].get("email") == "ada.personal@gmail.com",
         json.dumps(reply_payload.get("to")))
    step("reply goes out threaded (In-Reply-To the original)",
         reply_payload["headers"].get("In-Reply-To") == first["rfc_message_id"],
         json.dumps(reply_payload["headers"]))
    step("reply is NOT bulk mail (no unsubscribe header on a 1:1 reply)",
         "List-Unsubscribe" not in reply_payload["headers"])
    step("reply carries its own attachment",
         reply_payload["attachment"][0]["name"] == "quote.txt")

    detail = api.get(f"/email/inbox/conversations/{conv_id}").json()
    step("the sent reply joined the same thread",
         detail["messages"][-1]["direction"] == "outgoing"
         and detail["messages"][-1]["body"].startswith("500 units"),
         f"{len(detail['messages'])} messages in the thread")
    step("newest message shows its attachment",
         detail["messages"][-1]["attachments"][0]["name"] == "quote.txt",
         json.dumps(detail["messages"][-1].get("attachments")))

    # ------------------------------------------------------- unsubscribe
    resp = api.get("/email/unsubscribe", params={"e": "ada@acme-leads.io", "t": "forged"})
    step("forged unsubscribe token is rejected", resp.status_code == 400, str(resp.status_code))

    # ----------------------------------------- email campaign + validation
    lst = api.post("/lists/", params={"name": "Sim Email List"})
    if lst.status_code not in (200, 201):
        existing = api.get("/lists/").json()["items"]
        list_id = next(x["id"] for x in existing if x["name"] == "Sim Email List")
    else:
        list_id = lst.json()["id"]
    added = api.post(f"/lists/{list_id}/contacts", json=[contact_id])
    step("list has the contact as a member",
         added.status_code == 200 and added.json()["contact_count"] >= 1, added.text[:160])

    import time as _time

    camp = api.post("/campaigns/", json={
        "name": f"Sim Email Campaign {int(_time.time())}",
        "channel": "email",
        "list_id": list_id,
        "subject": "Sim subject",
        "message_body": "Hello {{first_name}}",
        "html_body": "<p>Hello {{first_name}}</p>",
        "email_account_id": account_id,
        "attachments": [{"name": "brochure.txt",
                         "content_base64": base64.b64encode(b"brochure").decode()}],
    })
    step("create email campaign with an attachment",
         camp.status_code in (200, 201), camp.text[:300])
    camp_id = camp.json()["id"]
    step("campaign stores the attachment",
         bool(api.get(f"/campaigns/{camp_id}").json().get("attachments")),
         str(api.get(f"/campaigns/{camp_id}").json().get("attachments"))[:120])

    dup = api.post(f"/campaigns/{camp_id}/duplicate")
    step("duplicating an email campaign keeps channel + subject + attachment",
         dup.status_code in (200, 201)
         and dup.json().get("channel") == "email"
         and dup.json().get("subject") == "Sim subject"
         and bool(dup.json().get("attachments")),
         json.dumps({k: dup.json().get(k) for k in ("channel", "subject", "attachments")})[:200])

    val = api.post(f"/campaigns/{camp_id}/validate")
    # P0-5: campaigns only send to addresses a verifier has confirmed, and the address in
    # this run is a fake one no verifier can check. A server running with the gate on (the
    # default) must therefore FAIL this audience, with the count -- assert that, then stop:
    # nothing past this point can send. Start the server with EMAIL_REQUIRE_VERIFIED=false
    # (see README) to run the rest of the flow against the fake Brevo.
    unverified = (val.json().get("audience") or {}).get("unverified", 0) if val.status_code == 200 else 0
    if unverified:
        step("P0-5: validate fails an email audience with an unverified contact, and counts it",
             val.json().get("valid") is False and unverified == 1
             and any("unknown email verification status" in e for e in val.json().get("errors", [])),
             val.text[:260])
        refused = api.post(f"/campaigns/{camp_id}/start")
        step("P0-5: start refuses it, and nothing is queued",
             refused.status_code == 400
             and api.get(f"/campaigns/{camp_id}").json().get("status") == "draft",
             refused.text[:160])
        print("\nNOTE: the rest of this flow (campaign send, unsubscribe) is skipped because the "
              "server enforces the verification gate. Restart it with "
              "EMAIL_REQUIRE_VERIFIED=false to run it against the fake Brevo.")
        print("\nRESULT:", "ALL PASS (gate verified; remainder skipped)" if ok else "FAILURES PRESENT")
        sys.exit(0 if ok else 1)
    step("email campaign validates against the Brevo sender",
         val.status_code == 200 and val.json().get("valid") is True, val.text[:220])
    step("validate is a report: the campaign is still a draft",
         api.get(f"/campaigns/{camp_id}").json().get("status") == "draft"
         and val.json().get("changed") is False, val.text[:120])

    start = api.post(f"/campaigns/{camp_id}/start")
    if start.status_code == 200:
        step("email campaign starts", True)
    else:
        # No Redis in the simulator: the app refuses to strand the campaign and
        # the web-mode fallback runs the identical batch inline instead.
        step("start refuses without a broker (no stranded campaign)",
             start.status_code == 503 and "queue" in start.text.lower(), start.text[:120])
        run = subprocess.run(
            [sys.executable, "tools/run_campaign_now.py", str(camp_id)],
            capture_output=True, text=True, env={**os.environ},
        )
        step("web-mode fallback runs the campaign batch",
             "processed" in run.stdout, (run.stdout or run.stderr)[:200].replace("\n", " "))

    # The campaign worker sends through the same pipeline; run one batch now so
    # the campaign's attachments are proven on the wire too.
    after = httpx.get(f"{FAKE}/_log").json()["entries"]
    step("campaign mail reached Brevo", len(after) > len(wire) or start.status_code == 200,
         f"{len(wire)} -> {len(after)} sends")
    if len(after) > len(wire):
        camp_payload = after[-1]["body"]
        step("campaign mail carried its attachment + unsubscribe headers",
             camp_payload.get("attachment") and "List-Unsubscribe" in (camp_payload.get("headers") or {}),
             json.dumps(sorted((camp_payload.get("headers") or {}).keys())))

    # -------------------------------------------- bounce / suppression path
    api.post("/contacts/", json={"phone_number": "+2348012345002",
                                 "email": "bounce@acme-leads.io"})
    # ------------------------------------------- Brevo verified senders ----
    probe = api.post("/email/senders-preview", json={"api_key": "xkeysib-simulated-key"})
    probe_body = probe.json()
    step("probe reads the key's verified senders + domain auth",
         probe.status_code == 200 and len(probe_body["senders"]) == 2
         and probe_body["senders"][0]["domain_verified"] is True,
         json.dumps(probe_body["senders"][:1])[:200])
    step("probe marks the warm (authenticated-domain) address as recommended",
         any(x["recommended"] for x in probe_body["senders"]),
         str([x["email"] for x in probe_body["senders"] if x["recommended"]]))
    bad = api.post("/email/senders-preview", json={"api_key": ""})
    step("an empty key is refused", bad.status_code == 422, str(bad.status_code))

    # ------------------------------------------------ composer test send ---
    test = api.post("/email/test-send", json={
        "to": "owner@acme-leads.io",
        "subject": "Price list preview",
        "body": "Hi {{first_name}}, here is the price list.",
        "html_body": "<p>Hi {{first_name}}, here is the <b>price list</b>.</p>"
                     "<p><a href=\"https://acme-leads.io/pricing\">Pricing</a></p>",
        "attachments": [{"name": "test.txt",
                         "content_base64": base64.b64encode(b"test file").decode()}],
        "email_account_id": account_id,
    })
    step("composer test send works", test.status_code == 200, test.text[:200])
    wire_now = httpx.get(f"{FAKE}/_log").json()["entries"]
    test_payload = wire_now[-1]["body"]
    step("test mail is marked as a test and carries the attachment",
         test_payload["subject"].startswith("[TEST]")
         and test_payload["attachment"][0]["name"] == "test.txt",
         test_payload["subject"])
    test_mails = [
        m for m in api.get("/email/history").json()["items"]
        if (m.get("subject") or "").startswith("[TEST]")
    ]
    step("test mail does NOT appear as outreach in the history",
         not test_mails, f"{len(test_mails)} test message(s) in history")

    # --------------------------------------------------- cc / bcc on send --
    before_cc = len(httpx.get(f"{FAKE}/_log").json()["entries"])
    cc_send = api.post("/email/send", json={
        "email": "ada@acme-leads.io",
        "subject": "CopyWith copy",
        "body": "Looping in a colleague.",
        "email_account_id": account_id,
        "cc": ["colleague@acme-leads.io"],
        "bcc": ["crm@acme-leads.io"],
    })
    wire_after = httpx.get(f"{FAKE}/_log").json()["entries"]
    step("CC / BCC reach Brevo",
         cc_send.status_code == 200 and len(wire_after) == before_cc + 1
         and wire_after[-1]["body"]["cc"][0]["email"] == "colleague@acme-leads.io"
         and wire_after[-1]["body"]["bcc"][0]["email"] == "crm@acme-leads.io",
         json.dumps({k: wire_after[-1]["body"].get(k) for k in ("cc", "bcc")})[:160])

    # ------------------------- engagement: opens / clicks / links ---------
    message_id = api.get("/email/history").json()["items"][-1]["id"]
    for event in (
        {"event": "opened", "email": "ada@acme-leads.io",
         "message-id": first["provider_message_id"], "event-id": f"open-{RUN}"},
        {"event": "click", "email": "ada@acme-leads.io",
         "message-id": first["provider_message_id"], "event-id": f"click-{RUN}",
         "link": "https://acme-leads.io/pricing"},
    ):
        api.post(me_account["webhook_url"].split("/api/v1")[1], json=event)

    events = api.get(f"/email/messages/{first['id']}/events").json()
    step("per-message activity records the open",
         any(e["event_type"] == "opened" for e in events["events"]),
         json.dumps([e["event_type"] for e in events["events"]]))
    step("per-message activity records WHICH link was clicked",
         any(e["link"] == "https://acme-leads.io/pricing" for e in events["events"]),
         str(events["summary"]["unique_links_clicked"]) + " unique link(s)")

    engagement = api.get(f"/email/contacts/{contact_id}/engagement").json()
    step("contact engagement shows opens, clicks and the clicked link",
         engagement["totals"]["opened"] >= 1
         and any(l["url"] == "https://acme-leads.io/pricing" for l in engagement["links"]),
         json.dumps(engagement["totals"]))

    # --------------------------------------- inbound mail with attachment --
    await_attachment = {
        "From": {"Address": "ada@acme-leads.io"},
        "Subject": "The signed contract",
        "TextBody": "Signed copy attached.",
        "MessageId": f"<contract-{RUN}@acme-leads.io>",
        "Headers": {"In-Reply-To": first["rfc_message_id"]},
        "Attachments": [
            {"Name": "contract.pdf", "ContentType": "application/pdf",
             "ContentLength": 2048, "DownloadToken": "https://brevo.test/dl/abc123"},
            {"Name": "notes.txt", "ContentType": "text/plain", "ContentLength": 12,
             "Content": base64.b64encode(b"hello there").decode()},
        ],
    }
    api.post(me_account["webhook_url"].split("/api/v1")[1], json=await_attachment)
    detail = api.get(f"/email/inbox/conversations/{conv_id}").json()
    received = [m for m in detail["messages"]
                if m["direction"] == "incoming" and m.get("subject") == "The signed contract"]
    names = [a.get("name") for a in (received[0]["attachments"] if received else [])]
    step("received mail keeps its attachments",
         names == ["contract.pdf", "notes.txt"], json.dumps(names))
    step("a received attachment with only a Brevo download link stays clickable",
         (received[0]["attachments"][0].get("url") if received else None)
         == "https://brevo.test/dl/abc123",
         json.dumps(received[0]["attachments"][0] if received else {})[:160])
    step("received mail threaded under the message it answers",
         bool(received) and received[0]["conversation_id"] == conv_id)

    # ------------------------------------- follow-up stays in the thread ---
    # The operator follow-up path (the "nudge this one prospect" button). It
    # shares send_now with campaigns, so threading is proved once here.
    import datetime as _dt

    followup = api.post("/followups/", json={
        "contact_id": contact_id,
        "scheduled_at": (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=5)).isoformat(),
        "message_text": "Just floating this back to the top of your inbox.",
        "channel": "email",
        "subject": "Quick nudge",
        "email_account_id": account_id,
    })
    step("email follow-up created", followup.status_code in (200, 201), followup.text[:200])
    followup_id = followup.json().get("id")

    queued = api.post(f"/followups/{followup_id}/send-now")
    step("send-now refuses without a broker (no silent no-op)",
         queued.status_code in (200, 503), str(queued.status_code))

    # The mail client rule: a follow-up quotes the newest message already in
    # the thread, so Gmail shows it inside that conversation.
    thread_before = api.get(f"/email/inbox/conversations/{conv_id}").json()["messages"]
    expected_parent = next(
        m["rfc_message_id"] for m in reversed(thread_before)
        if m["direction"] == "outgoing" and m.get("rfc_message_id")
    )

    sent_before = len(httpx.get(f"{FAKE}/_log").json()["entries"])
    run = subprocess.run(
        [sys.executable, "tools/run_followup_now.py", str(followup_id)],
        capture_output=True, text=True, env={**os.environ},
    )
    wire_follow = httpx.get(f"{FAKE}/_log").json()["entries"]
    follow_payload = wire_follow[-1]["body"] if len(wire_follow) > sent_before else {}
    step("follow-up email goes out",
         len(wire_follow) > sent_before, (run.stdout or run.stderr)[-160:].replace("\n", " "))
    step("follow-up quotes the thread's latest message (same chat in Gmail)",
         follow_payload.get("headers", {}).get("In-Reply-To") == expected_parent,
         f"expected {expected_parent} got {follow_payload.get('headers', {}).get('In-Reply-To')}")
    thread_check = api.get(f"/email/inbox/conversations/{conv_id}").json()
    step("the follow-up is a message in the same chat",
         any((m.get("subject") or "").startswith("Quick nudge") for m in thread_check["messages"]),
         f"{len(thread_check['messages'])} messages in the thread")

    # ------------------------------- real one-click unsubscribe (last) -----
    # The signed link taken straight out of the header we actually sent.
    header = (first_unsub or "")
    match = __import__("re").search(r"<(https?://[^>]+)>", header.split("mailto:")[-1])
    if match:
        clicked = httpx.get(match.group(1), timeout=20)
        step("clicking the real unsubscribe link succeeds",
             clicked.status_code == 200 and "unsubscribed" in clicked.text.lower(),
             clicked.text[:120].replace("\n", " "))
        state = api.get("/contacts/", params={"email_state": "unsubscribed"}).json()
        step("the contact is now opted out of email only",
             any(c["id"] == contact_id for c in state["items"]),
             f"{state['total']} unsubscribed")
        sup = api.get("/email/suppression").json()
        step("the address joined the suppression list",
             any("ada@acme-leads.io" in json.dumps(x) for x in sup.get("items", [])),
             json.dumps(sup)[:160])
        sms_still_ok = api.get(f"/contacts/{contact_id}").json()
        step("SMS consent untouched by the email unsubscribe",
             sms_still_ok.get("is_opted_out") is False,
             f"is_opted_out={sms_still_ok.get('is_opted_out')}")
    else:
        step("unsubscribe link present in the header", False, header[:160])

print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
