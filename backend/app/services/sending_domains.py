"""Sending domains: register one, get the DNS records it needs, check it, and ask "may this sender send?".

* ``register_domain`` -- creates the ``SendingDomain``, asks Brevo for the verification-code and DKIM records
  and plans the DMARC record. There is exactly ONE DMARC record in the plan: whatever the domain already
  publishes at ``_dmarc.<domain>`` is read and the requested ``rua`` is merged into it (``merge_dmarc``); the
  plan tells the operator to REPLACE the existing record, never to add a second one (two DMARC records make
  both invalid).
* ``refresh`` -- asks Brevo to re-check, re-reads DNS, and starts the warm-up clock the first time the domain
  is authenticated.
* ``campaign_sender_checks`` / ``sender_problems`` -- what a campaign must pass before it starts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sending_domain import SendingDomain
from app.providers import brevo
from app.services import email_service
from app.services.warmup import warmup_state
from app.utils.dns_lookup import txt_records
from app.utils.email_domains import (
    domain_of,
    is_free_mail,
    is_free_mail_domain,
    merge_dmarc,
    valid_domain,
)

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """A refusal with an HTTP status and a stable code, for the API layer to turn into a response."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


FREE_MAIL_MESSAGE = (
    "{domain} is a free-mail domain. Nobody but {domain} can authenticate mail from it (the app does not "
    "control its DNS), so Brevo's mail would fail DMARC and be junked or rejected. Use an address on a "
    "domain you own, and authenticate that domain (POST /api/v1/email/domains)."
)


def free_mail_error(address_or_domain: str) -> str:
    return FREE_MAIL_MESSAGE.format(domain=domain_of(address_or_domain) or address_or_domain)


async def get_by_domain(db: AsyncSession, domain: Optional[str]) -> Optional[SendingDomain]:
    if not domain:
        return None
    return (
        await db.execute(select(SendingDomain).where(SendingDomain.domain == domain.strip().lower()))
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# DMARC in the plan
# --------------------------------------------------------------------------


def _dmarc_record(domain: str, merged: dict, *, lookup_failed: bool) -> dict:
    host = f"_dmarc.{domain}"
    if lookup_failed:
        note = (
            "DNS could not be read from here, so this assumes nothing is published. If _dmarc."
            f"{domain} already has a TXT record, edit THAT record instead of adding this one: two DMARC "
            "records make both invalid."
        )
    elif merged["action"] == "create":
        note = f"Add this single TXT record at {host}. It starts in monitoring mode (p=none)."
    elif merged["action"] == "keep":
        note = "Already published with this report address; nothing to change."
    else:
        note = (
            f"REPLACE the existing TXT record at {host} with this one (do not add a second: two DMARC "
            "records make both invalid). Your other settings are kept; only the report address was merged in."
        )
        if merged["had_multiple"]:
            note = (
                f"{host} has several DMARC records, which makes DMARC invalid. DELETE them all and publish "
                "just this merged one."
            )
    return {"type": "TXT", "host": host, "value": merged["record"], "purpose": "dmarc",
            "action": merged["action"], "note": note, "verified": merged["action"] == "keep"}


async def _plan_dmarc(domain: str, rua: Optional[str]) -> tuple[dict, dict]:
    existing = await txt_records(f"_dmarc.{domain}")
    merged = merge_dmarc(existing or [], rua=rua)
    return _dmarc_record(domain, merged, lookup_failed=existing is None), merged


def _replace_dmarc(records: list[dict], dmarc: dict) -> list[dict]:
    return [r for r in records if r.get("purpose") != "dmarc"] + [dmarc]


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------


def serialize(row: SendingDomain, *, warnings: Optional[list[str]] = None, dmarc: Optional[dict] = None) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "status": row.status,
        "records": json.loads(row.records_json or "[]"),
        "dmarc_rua": row.dmarc_rua,
        "warmup": warmup_state(row.warmup_started_at) if row.status == "authenticated" else None,
        "dmarc": dmarc,
        "warnings": warnings or [],
        "email_account_id": row.email_account_id,
        "last_error": row.last_error,
        "authenticated_at": row.authenticated_at.isoformat() if row.authenticated_at else None,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# --------------------------------------------------------------------------
# register / verify
# --------------------------------------------------------------------------


async def _account_and_key(db: AsyncSession, account_id: Optional[int]):
    from app.security.encryption import decrypt_value

    account = await email_service.get_account(db, account_id) or await email_service.get_default_account(db)
    if account is None:
        raise DomainError(400, "NO_SENDER", "Add a Brevo sender (API key and From address) first: the domain "
                                            "is registered with that Brevo account.")
    try:
        key = decrypt_value(account.api_key_encrypted)
    except Exception:  # noqa: BLE001
        key = ""
    if not key:
        raise DomainError(400, "NO_API_KEY", f"The Brevo key of sender '{account.name}' cannot be read.")
    return account, key


async def register_domain(
    db: AsyncSession, *, domain: str, rua: Optional[str] = None, account_id: Optional[int] = None
) -> dict:
    name = (domain or "").strip().lower()
    if not valid_domain(name):
        raise DomainError(422, "INVALID_DOMAIN", f"'{domain}' is not a domain name (expected something like acme-leads.io).")
    if is_free_mail_domain(name):
        raise DomainError(422, "FREE_MAIL_DOMAIN", free_mail_error(name))
    if await get_by_domain(db, name) is not None:
        raise DomainError(409, "DOMAIN_EXISTS", f"{name} is already registered.")
    account, key = await _account_and_key(db, account_id)

    created = await brevo.create_domain(key, name)
    if not created.get("success"):
        raise DomainError(502, "BREVO_REFUSED", f"Brevo did not register {name}: {created.get('error')}")
    dmarc, merged = await _plan_dmarc(name, rua)
    records = _replace_dmarc(created.get("records") or [], dmarc)
    row = SendingDomain(
        domain=name, status="authenticated" if created.get("authenticated") else "pending",
        records_json=json.dumps(records), dmarc_rua=(rua or None), email_account_id=account.id,
    )
    if row.status == "authenticated":
        row.authenticated_at = row.warmup_started_at = datetime.now(timezone.utc)
    db.add(row)
    await db.flush()
    return serialize(row, dmarc=merged)


async def refresh(db: AsyncSession, row: SendingDomain) -> dict:
    """Ask Brevo to re-check, re-read DNS, and start the warm-up clock on first authentication."""
    _account, key = await _account_and_key(db, row.email_account_id)
    now = datetime.now(timezone.utc)
    warnings: list[str] = []
    checked = await brevo.authenticate_domain(key, row.domain)
    records = json.loads(row.records_json or "[]")
    if checked.get("success"):
        row.last_error = None
        verified = {(r["purpose"], r["host"]): r.get("verified") for r in checked.get("records") or []}
        for record in records:
            if (record["purpose"], record["host"]) in verified:
                record["verified"] = bool(verified[(record["purpose"], record["host"])])
        if checked.get("authenticated") and row.status != "authenticated":
            row.status = "authenticated"
            row.authenticated_at = now
            row.warmup_started_at = row.warmup_started_at or now
        elif not checked.get("authenticated"):
            warnings.append("Brevo has not authenticated this domain yet: publish the DNS records and check again "
                            "(DNS changes can take a while to spread).")
    else:
        row.last_error = str(checked.get("error"))[:500]
        warnings.append(f"Brevo could not check the domain: {row.last_error}")

    existing = await txt_records(f"_dmarc.{row.domain}")
    dmarc_record, merged = await _plan_dmarc(row.domain, row.dmarc_rua)
    records = _replace_dmarc(records, dmarc_record)
    if existing is None:
        warnings.append("DNS could not be read from here, so the DMARC state is unknown.")
    if merged["had_multiple"]:
        warnings.append(
            f"_dmarc.{row.domain} has two DMARC records. That invalidates both: delete them and publish only "
            "the merged record in this plan."
        )
    row.records_json = json.dumps(records)
    row.last_checked_at = now
    await db.flush()
    return serialize(row, warnings=warnings, dmarc=merged)


# --------------------------------------------------------------------------
# may this sender send?
# --------------------------------------------------------------------------


async def sender_problems(db: AsyncSession, account) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for a campaign about to send from ``account``."""
    errors: list[str] = []
    warnings: list[str] = []
    address = account.from_email
    domain = domain_of(address)
    if is_free_mail(address):
        errors.append(
            f"The sender {address} is a free-mail address. " + free_mail_error(address)
        )
        return errors, warnings
    row = await get_by_domain(db, domain)
    if row is None:
        warnings.append(
            f"No sending domain on file for {domain}: register it (POST /api/v1/email/domains) to get its DNS "
            "records, authentication checks and the warm-up ramp."
        )
    elif row.status != "authenticated":
        errors.append(
            f"The sending domain {domain} is not authenticated yet (status: {row.status}). Publish the DNS "
            f"records (GET /api/v1/email/domains/{row.id}) and verify the domain "
            f"(POST /api/v1/email/domains/{row.id}/verify) before sending."
        )
    else:
        state = warmup_state(row.warmup_started_at)
        if state and not state["complete"]:
            warnings.append(
                f"Warm-up: {domain} is on day {state['day']} of its ramp, so each mailbox may send "
                f"{state['cap']} a day until {state['full_cap_from']}."
            )
    return errors, warnings


async def campaign_sender_checks(db: AsyncSession, account_id: Optional[int]) -> tuple[list[str], list[str]]:
    account = await email_service.get_account(db, account_id) or await email_service.get_default_account(db)
    if account is None:
        return [], []   # "no sender configured" is reported by the gateway check
    return await sender_problems(db, account)
