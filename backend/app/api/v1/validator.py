"""Validation: bounded batches with complete row reports, and queued jobs.

Two ways to run the verifier:

* ``POST /batch`` -- a small synchronous batch (up to ``BATCH_SIZE`` rows) with a complete
  row report. Read-only unless ``save`` is set; the browser's "check these" uses it, and
  CSV rows (which are not contacts yet) are checked the same way.
* ``POST /jobs`` -- a durable background job over any number of contacts: it snapshots the
  ids, verifies in chunks on the server, WRITES every verdict onto the contact, publishes
  progress and resumes after a restart (see ``services/verification_jobs``). This is how a
  whole list gets verified, which is what unlocks it for sending (P0-5).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, PositiveInt, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.contacts import _apply_channel_filters, _apply_contact_filters
from app.config import settings
from app.database import get_db
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.email import EmailSuppression
from app.models.suppression import SuppressionEntry
from app.models.user import User
from app.security.auth import get_current_user
from app.services import email_validator as ev
from app.services import verification_jobs
from app.services.email_enrichment import _dns_available
from app.services.number_filter import classify_number, looks_like_test_data

router = APIRouter(tags=["Validator"])
# Includes offloaded DNS/SMTP work; the API event loop stays responsive.
_check_slots = asyncio.Semaphore(10)
#: Rows per synchronous batch. It was 5, which made a 1,297-contact check 260 round trips
#: with the tab held open; whole lists go through ``POST /jobs`` instead.
BATCH_SIZE = 25


class Selection(BaseModel):
    scope: Literal["all", "list", "ids"] = "all"
    contact_ids: list[PositiveInt] = Field(default_factory=list, max_length=1000)
    list_id: PositiveInt | None = None
    search: str | None = Field(default=None, max_length=255)
    lead_status: str | None = Field(default=None, max_length=50)
    channel: Literal["sms", "email"] | None = None
    email_state: str | None = Field(default=None, max_length=50)


class Entry(BaseModel):
    # Keep invalid addresses verbatim: a validation input is NOT an EmailStr.
    email: str | None = Field(default=None, max_length=1000)
    phone_number: str | None = Field(default=None, max_length=1000)
    name: str = Field(default="", max_length=500)
    row: PositiveInt | None = None


class Batch(BaseModel):
    items: list[Entry] = Field(default_factory=list, max_length=BATCH_SIZE)
    contact_ids: list[PositiveInt] = Field(default_factory=list, max_length=BATCH_SIZE)
    check: Literal["both", "email", "phone"] = "both"
    deep: bool = True
    save: bool = False

    @model_validator(mode="after")
    def unambiguous(self):
        if bool(self.items) == bool(self.contact_ids):
            raise ValueError("Provide either items or contact_ids, not both or neither")
        if self.save and not self.contact_ids:
            raise ValueError("Only existing contacts can be updated; CSV and single checks are previews")
        if len(set(self.contact_ids)) != len(self.contact_ids):
            raise ValueError("Duplicate contact IDs in batch")
        return self


@router.get("/status")
async def status(current_user: User = Depends(get_current_user)):
    # Never expose a configured URL (it may contain a secret), or a key.
    smtp = await ev.smtp_capability()
    return {
        "engine": "reacher" if ev.reacher_configured() else "builtin",
        "api_key_required": False,
        "reacher_configured": ev.reacher_configured(),
        "reacher_key_configured": bool(settings.REACHER_API_KEY),
        "dns_available": _dns_available(),
        # Whether a mailbox can actually be CONFIRMED from here -- not merely whether the
        # setting is on. When it is false, `smtp_reason` says why and what to do, and every
        # mailbox stays "unknown" (not sendable).
        "smtp_enabled": smtp["enabled"],
        "smtp_reason": smtp["reason"],
        "can_confirm_mailboxes": smtp["enabled"],
        "batch_size": BATCH_SIZE,
        "phone_region": "NG",
        "notice": "No key is needed for built-in checks. Reacher is optional; a hosted Reacher service may require its own key. SMTP/DNS failures stay Unknown, and Unknown addresses are not sent to by campaigns. Phone checks confirm Nigerian mobile format, not whether a SIM is active. No messages are sent.",
    }


@router.post("/self-test")
async def self_test(current_user: User = Depends(get_current_user)):
    malformed = await ev.validate_email("not-an-email", deep=False)
    reserved = await ev.validate_email("person@validator.invalid", deep=False)
    checks = [
        {"name": "Reject malformed email", "passed": malformed.verdict == "undeliverable"},
        {"name": "Reject reserved .invalid domain", "passed": reserved.verdict == "undeliverable"},
        {"name": "Accept Nigerian mobile format", "passed": classify_number("08034567891").sendable},
        {"name": "Reject short phone number", "passed": not classify_number("123").sendable},
    ]
    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "notice": "Local engine tests only. This does not test Reacher credentials, live DNS/SMTP connectivity or actual delivery. Validate an address in Deep mode to inspect those results.",
    }


async def _selected_ids(db: AsyncSession, data: "Selection") -> list[int]:
    """The contact ids a selection names, in id order."""
    if data.scope == "ids":
        if not data.contact_ids:
            raise HTTPException(400, "No contacts selected")
        query = select(Contact.id).where(Contact.id.in_(data.contact_ids))
    else:
        if data.scope == "list" and data.list_id is None:
            raise HTTPException(400, "Choose a list")
        query = _apply_contact_filters(select(Contact.id), data.search, data.lead_status, None)
        query = _apply_channel_filters(query, data.email_state, data.channel)
        if data.list_id is not None:
            if await db.get(ContactList, data.list_id) is None:
                raise HTTPException(404, "List not found")
            query = query.where(Contact.id.in_(
                select(ContactListMember.contact_id).where(ContactListMember.list_id == data.list_id)
            ))
    return list((await db.execute(query.order_by(Contact.id))).scalars().all())


@router.post("/selection")
async def selection(
    data: Selection,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ids = await _selected_ids(db, data)
    return {"contact_ids": ids, "total": len(ids)}


class JobRequest(Selection):
    deep: bool = True
    # Re-check contacts that are already proven deliverable (normally skipped).
    recheck: bool = False


@router.post("/jobs", status_code=202)
async def create_job(
    data: JobRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue a verification of any number of contacts; poll ``GET /jobs/{id}``.

    Every verdict is written onto the contact as it is produced (``email_verified``,
    ``email_verified_at``, ``email_confidence``, ``email_verdict``) -- that is what makes
    a contact sendable in a campaign. Contacts with no address, and (unless ``recheck``)
    those already proven deliverable, are left out of ``total``.
    """
    ids = await _selected_ids(db, data)
    job = await verification_jobs.create_job(
        db, ids, owner_id=current_user.id, deep=data.deep, recheck=data.recheck
    )
    await db.commit()
    if job.total:
        verification_jobs.spawn(job.id)
    else:
        job.status = "done"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()
    return verification_jobs.job_dict(job)


@router.get("/jobs")
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.verification_job import EmailVerificationJob

    rows = (await db.execute(
        select(EmailVerificationJob).order_by(EmailVerificationJob.created_at.desc()).limit(25)
    )).scalars().all()
    return {"items": [verification_jobs.job_dict(job) for job in rows]}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = await verification_jobs.get_job(db, job_id)
    if job is None:
        raise HTTPException(404, "Verification job not found")
    return verification_jobs.job_dict(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = await verification_jobs.get_job(db, job_id)
    if job is None:
        raise HTTPException(404, "Verification job not found")
    if job.status not in verification_jobs.ACTIVE:
        raise HTTPException(409, f"This job is already {job.status}")
    await verification_jobs.cancel_job(db, job)
    await db.commit()
    return verification_jobs.job_dict(job)


def _phone(raw: str | None) -> dict:
    if not (raw or "").strip():
        return {"verdict": "missing", "normalized": None, "problems": ["No phone number"]}
    result = classify_number(raw)
    soft = looks_like_test_data(raw) if result.sendable else None
    return {
        "verdict": "risky" if soft else "good" if result.sendable else "bad",
        "normalized": result.normalized,
        "reason": soft or result.reason,
        "problems": [
            "Looks like sample data; review manually" if soft else
            "Valid Nigerian mobile format; active SIM and delivery not verified" if result.sendable else result.detail
        ],
    }


def _overall(email: dict | None, phone: dict | None) -> str:
    mapping = {"deliverable": "good", "undeliverable": "bad"}
    values = [mapping.get(v["verdict"], v["verdict"]) for v in (email, phone) if v and v["verdict"] != "missing"]
    for state in ("bad", "risky", "unknown", "good"):
        if state in values:
            return state
    return "missing"


async def _check(entry: Entry, check: str, deep: bool) -> tuple[dict, ev.Verdict | None]:
    email = phone = verdict = None
    async with _check_slots:
        if check in {"email", "both"}:
            if (entry.email or "").strip():
                try:
                    verdict = await asyncio.wait_for(ev.validate_email(entry.email, deep=deep), timeout=70)
                except Exception:
                    # A failed provider/individual row must not lose the rest of a CSV.
                    verdict = ev.Verdict(address=entry.email, problems=["Check unavailable; retry this address"])
                email = verdict.as_dict()
            else:
                email = {"verdict": "missing", "problems": ["No email address"]}
        if check in {"phone", "both"}:
            phone = _phone(entry.phone_number)
    return ({
        "row": entry.row,
        "name": entry.name,
        "input_email": entry.email,
        "input_phone": entry.phone_number,
        "email": email,
        "phone": phone,
        "status": _overall(email, phone),
        "saved": False,
        "blocked": [],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }, verdict)


@router.post("/batch")
async def batch(
    data: Batch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contacts: dict[int, Contact] = {}
    if data.contact_ids:
        contacts = {c.id: c for c in (await db.execute(
            select(Contact).where(Contact.id.in_(data.contact_ids))
        )).scalars()}
        entries = [Entry(
            email=contacts[cid].email,
            phone_number=contacts[cid].phone_number,
            name=" ".join(filter(None, [contacts[cid].first_name, contacts[cid].last_name])) or contacts[cid].business_name or f"Contact #{cid}",
        ) if cid in contacts else Entry(name=f"Contact #{cid} (deleted)") for cid in data.contact_ids]
    else:
        entries = data.items
    checked = await asyncio.gather(*(_check(entry, data.check, data.deep) for entry in entries))
    # Re-read and lock only AFTER network checks, avoiding long database locks.
    # Never apply an old verdict to an address edited while a check was running.
    if data.save:
        contacts = {c.id: c for c in (await db.execute(
            select(Contact).where(Contact.id.in_(data.contact_ids)).with_for_update()
            .execution_options(populate_existing=True)
        )).scalars()}
    phone_values = [c.phone_number for c in contacts.values() if c.phone_number]
    email_values = [c.email.lower() for c in contacts.values() if c.email]
    sms_suppressed = set((await db.execute(select(SuppressionEntry.phone_number).where(
        SuppressionEntry.phone_number.in_(phone_values)
    ))).scalars()) if phone_values else set()
    email_suppressed = {address.lower() for address in (await db.execute(select(EmailSuppression.email_address).where(
        func.lower(EmailSuppression.email_address).in_(email_values)
    ))).scalars()} if email_values else set()
    rows = []
    for index, (row, verdict) in enumerate(checked):
        cid = data.contact_ids[index] if data.contact_ids else None
        row["contact_id"] = cid
        contact = contacts.get(cid)
        if cid is not None and contact is None:
            row.update(status="unknown", note="Contact was deleted; no changes saved")
        if contact:
            if data.save:
                same = contact.email == entries[index].email and contact.phone_number == entries[index].phone_number
                if same:
                    if verdict:
                        ev.apply_verdict(contact, verdict)
                    if row["phone"] and row["phone"]["verdict"] == "bad" and not contact.is_undeliverable:
                        from app.services.list_hygiene import mark_undeliverable
                        await mark_undeliverable(contact, row["phone"]["reason"] or "invalid_format")
                    row["saved"] = bool(verdict or (row["phone"] and row["phone"]["verdict"] == "bad"))
                else:
                    row["note"] = "Contact changed during validation; results not saved. Run again."
            if contact.is_opted_out:
                row["blocked"].append("SMS opted out")
            if contact.is_undeliverable:
                row["blocked"].append("SMS quarantined")
            if contact.phone_number in sms_suppressed:
                row["blocked"].append("SMS suppressed")
            if contact.is_email_opted_out:
                row["blocked"].append("Email unsubscribed")
            if contact.is_email_undeliverable:
                row["blocked"].append("Email quarantined")
            if (contact.email or "").lower() in email_suppressed:
                row["blocked"].append("Email suppressed")
        rows.append(row)
    if data.save:
        await db.commit()
    return {"items": rows, "processed": len(rows)}
