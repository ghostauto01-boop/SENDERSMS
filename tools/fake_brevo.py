"""Stand-in Brevo for the local simulator.

Accepts exactly the requests the app makes, records every payload so the test
scripts can assert on the wire format, and answers with Brevo-shaped bodies.
Run: uvicorn tools.fake_brevo:app --port 8799
"""
import json
import pathlib
import time

from fastapi import FastAPI, Request

app = FastAPI(title="fake-brevo")

LOG = pathlib.Path("/tmp/fake_brevo_log.jsonl")
SENDERS = [
    {"id": 1, "name": "Acme Leads", "email": "hello@acme-leads.io", "active": True},
    {"id": 2, "name": "Acme Billing", "email": "billing@acme-leads.io", "active": True},
]
DOMAINS = [
    {"domain": "acme-leads.io", "verified": True, "authenticated": True,
     "dkim": True, "spf": True},
]


def _record(path: str, body):
    with LOG.open("a") as handle:
        handle.write(json.dumps({"ts": time.time(), "path": path, "body": body}) + "\n")


@app.post("/v3/smtp/email")
async def send_email(request: Request):
    body = await request.json()
    _record("smtp/email", body)
    # A recipient at bounce@… simulates provider rejection so the failure path
    # (retry, hard-bounce suppression) can be exercised from the UI too.
    for to in body.get("to") or []:
        if (to.get("email") or "").lower().startswith("bounce@"):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=400,
                content={"code": "invalid_parameter", "message": "Recipient rejected"},
            )
    return {"messageId": f"<sim-{int(time.time()*1000)}@acme-leads.io>"}


@app.post("/v3/smtp/email/check")
async def check_email(request: Request):
    return {"messageId": "<sim-check@acme-leads.io>"}


@app.get("/v3/senders")
async def senders():
    return {"senders": SENDERS}


@app.get("/v3/senders/domains")
async def domains():
    return {"domains": DOMAINS}


@app.get("/v3/account")
async def account():
    return {"email": "sim@acme-leads.io", "plan": [{"type": "free", "credits": 300}]}


@app.get("/_log")
async def log():
    if not LOG.exists():
        return {"entries": []}
    entries = [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]
    return {"entries": entries}


@app.delete("/_log")
async def clear_log():
    LOG.unlink(missing_ok=True)
    return {"cleared": True}
