"""Email validator — Reacher (https://reacher.email) plus an in-app twin.

WHAT THIS IS
------------
Reacher is the open-source email validator (github.com/reacherhq/check-if-email-
exists). This module talks to a Reacher instance when one is configured
(``REACHER_API_URL``), and otherwise runs built-in checks at the same three stages, so
the app has a working validator with zero external services:

  1. syntax     — RFC-shaped address, normalised form, typo suggestions
  2. domain     — MX records (A fallback), disposable / role / free flags
  3. mailbox    — live SMTP probe (``RCPT TO``) with catch-all detection,
                  unless ``deep=False`` or a Reacher instance answered first

VERDICT VOCABULARY (shared with ``email_enrichment``)
-----------------------------------------------------
``deliverable``    — confirmed good (provider said so, or SMTP accepted the
                     exact mailbox on a non-catch-all domain)
``undeliverable``  — confirmed bad (invalid syntax, no MX, SMTP rejected)
``risky``          — accepts everything (catch-all), disposable, role account,
                     or SMTP accepted but the domain cannot be pinned down
``unknown``        — could not be proven either way (no DNS, blocked SMTP,
                     timeout). NEVER treated as a hard bounce.

THE RULE THAT MATTERS
---------------------
Only ``deliverable`` marks a contact ``email_verified``. Only a confirmed
``undeliverable`` quarantines an address. An ``unknown`` result is recorded
(``email_verdict = "unknown"``) but never quarantines and never undoes what an
earlier probe proved — silence is not proof an address is dead, and a wrong
"invalid" mark costs a real customer. It is not a yes either: campaigns do not
send to ``unknown`` addresses (P0-5), so the verdict is also what keeps an
unprovable address out of a bulk send.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import string
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.services.email_enrichment import (
    DISPOSABLE_DOMAINS,
    VERDICT_DELIVERABLE,
    VERDICT_RISKY,
    VERDICT_UNDELIVERABLE,
    VERDICT_UNKNOWN,
    diagnose,
)
from app.utils.contact_identity import is_role_address, normalize_email

logger = logging.getLogger(__name__)

#: Roughly RFC 5321/5322 — deliberately stricter than "has an @ somewhere".
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def valid_syntax(address: str) -> bool:
    if not _EMAIL_RE.fullmatch(address) or len(address) > 254:
        return False
    local, domain = address.rsplit("@", 1)
    return (
        len(local) <= 64 and not local.startswith(".") and not local.endswith(".")
        and ".." not in local
        and all(label and len(label) <= 63 and not label.startswith("-")
                and not label.endswith("-") for label in domain.split("."))
    )


#: Client-facing status for each verdict.
VERDICT_STATUS = {
    VERDICT_DELIVERABLE: "valid",
    VERDICT_UNDELIVERABLE: "invalid",
    VERDICT_RISKY: "risky",
    VERDICT_UNKNOWN: "unknown",
}

#: 0-100 stored in ``Contact.email_confidence``. ``unknown`` has no confidence: it is
#: the absence of an answer, not a low-confidence one.
VERDICT_CONFIDENCE = {
    VERDICT_DELIVERABLE: 95,
    VERDICT_RISKY: 60,
    VERDICT_UNDELIVERABLE: 0,
}


@dataclass
class Verdict:
    """One validation outcome, Reacher-shaped."""

    address: Optional[str] = None
    verdict: str = VERDICT_UNKNOWN
    is_reachable: str = "unknown"  # safe | invalid | risky | unknown
    accepts_mail: Optional[bool] = None
    is_catch_all: Optional[bool] = None
    is_disposable: bool = False
    is_role_account: bool = False
    is_free: bool = False
    is_valid_syntax: bool = False
    suggested_email: Optional[str] = None
    smtp_can_connect: Optional[bool] = None
    provider: str = "builtin"
    checked_at: float = field(default_factory=lambda: time.time())
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "email": self.address,
            "verdict": self.verdict,
            # The normalised, client-facing word for the verdict: valid | invalid |
            # risky | unknown. Only "valid" means "confirmed deliverable".
            "status": VERDICT_STATUS.get(self.verdict, "unknown"),
            "confidence": VERDICT_CONFIDENCE.get(self.verdict),
            "is_reachable": self.is_reachable,
            "accepts_mail": self.accepts_mail,
            "is_catch_all": self.is_catch_all,
            "is_disposable": self.is_disposable,
            "is_role_account": self.is_role_account,
            "is_free": self.is_free,
            "is_valid_syntax": self.is_valid_syntax,
            "suggested_email": self.suggested_email,
            "smtp_can_connect": self.smtp_can_connect,
            "provider": self.provider,
            "problems": list(self.problems),
            "checked_at": datetime.fromtimestamp(self.checked_at, timezone.utc).isoformat(),
        }


# ==========================================================================
# Reacher service client (github.com/reacherhq/check-if-email-exists)
# ==========================================================================

_REACHABLE_MAP = {
    "safe": VERDICT_DELIVERABLE,
    "invalid": VERDICT_UNDELIVERABLE,
    "risky": VERDICT_RISKY,
    "unknown": VERDICT_UNKNOWN,
}


def reacher_configured() -> bool:
    return bool((getattr(settings, "REACHER_API_URL", None) or "").strip())


async def check_with_reacher(address: str) -> Optional[Verdict]:
    """Ask a Reacher instance. ``None`` when none is configured or it failed.

    Reacher's v2 API: ``POST {REACHER_API_URL}`` with ``{"to_email": ...}``
    and (optionally) an ``Authorization: Bearer`` header. The response is the
    ``CheckEmailOutput`` struct — ``is_reachable`` plus the ``syntax`` / ``mx``
    / ``smtp`` / ``misc`` sub-checks.
    """
    url = (getattr(settings, "REACHER_API_URL", None) or "").strip()
    if not url:
        return None

    import httpx

    headers = {"Content-Type": "application/json"}
    api_key = (getattr(settings, "REACHER_API_KEY", None) or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=settings.EMAIL_ENRICHMENT_TIMEOUT) as client:
            response = await client.post(
                url, json={"to_email": address, "hello_name": "reacher"}, headers=headers
            )
    except Exception as exc:  # network down / timeout / DNS failure
        logger.warning("Reacher unreachable (%s); falling back to built-in checks", exc)
        return None
    if response.status_code >= 400:
        logger.warning("Reacher returned HTTP %s; falling back to built-in checks", response.status_code)
        return None
    try:
        data = response.json()
    except Exception:
        return None

    if not isinstance(data, dict) or data.get("is_reachable") not in _REACHABLE_MAP:
        return None
    if any(not isinstance(data.get(key, {}), (dict, type(None))) for key in ("syntax", "mx", "smtp", "misc")):
        return None
    reachable = str(data["is_reachable"]).lower()
    syntax = data.get("syntax") or {}
    mx = data.get("mx") or {}
    smtp = data.get("smtp") or {}
    misc = data.get("misc") or {}
    verdict = _REACHABLE_MAP.get(reachable, VERDICT_UNKNOWN)
    return Verdict(
        address=normalize_email(address),
        verdict=verdict,
        is_reachable=reachable,
        accepts_mail=mx.get("accepts_mail") if isinstance(mx.get("accepts_mail"), bool) else None,
        is_catch_all=smtp.get("is_catch_all") if isinstance(smtp.get("is_catch_all"), bool) else None,
        is_disposable=bool(misc.get("is_disposable")),
        is_role_account=bool(misc.get("is_role_account")),
        is_valid_syntax=bool(syntax.get("is_valid_syntax")),
        suggested_email=normalize_email(syntax.get("suggested_email") or "") or None,
        smtp_can_connect=smtp.get("can_connect_smtp") if isinstance(smtp.get("can_connect_smtp"), bool) else None,
        provider="reacher",
        problems={
            "safe": ["Mailbox accepted by Reacher"],
            "invalid": ["Reacher reports an undeliverable address"],
            "risky": ["Reacher reports a risky address"],
            "unknown": ["Reacher could not confirm the mailbox"],
        }[reachable],
    )


# ==========================================================================
# Built-in twin of the Reacher pipeline
# ==========================================================================

def _mx_hosts(domain: str) -> list[tuple[int, str]]:
    from app.services.mail_dns import mail_route
    return list(mail_route(domain).hosts)


def _public_mail_ips(host: str) -> list[str]:
    """Resolve once and connect to a public IP, never a private MX target."""
    import ipaddress
    import dns.resolver

    ips = []
    for kind in ("A", "AAAA"):
        try:
            records = dns.resolver.resolve(host, kind, lifetime=min(settings.EMAIL_ENRICHMENT_TIMEOUT, 8))
            for record in records:
                value = str(record)
                if ipaddress.ip_address(value).is_global:
                    ips.append(value)
        except Exception:
            continue
    return ips


def _smtp_probe(address: str, domain: str) -> Verdict:
    """RCPT only, never DATA: no email is sent. Policy rejects are unknown.

    A generic 550/554 can mean an IP/policy block, not a nonexistent mailbox.
    Only an explicit unknown-user response is enough to quarantine a contact.
    """
    import smtplib

    result = Verdict(address=address, is_valid_syntax=True)
    hosts = _mx_hosts(domain)
    if not hosts:
        result.problems.append("Mail routing could not be resolved")
        return result
    probe_address = "validator-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=20)) + "@" + domain

    def rejected(code, message) -> bool:
        text = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else str(message)
        return code in {550, 551, 553} and bool(re.search(
            r"5\.1\.[13]\b|no such (?:user|mailbox)|user unknown|unknown (?:user|recipient)|mailbox (?:not found|does not exist)|recipient (?:not found|does not exist)",
            text, re.I,
        ))

    for _priority, host in hosts[:2]:
        try:
            ips = _public_mail_ips(host)
            if not ips:
                result.problems.append("MX has no reachable public address")
                continue
            with smtplib.SMTP(ips[0], 25, timeout=max(1, min(settings.EMAIL_ENRICHMENT_TIMEOUT, 8))) as smtp:
                result.smtp_can_connect = True
                smtp.ehlo_or_helo_if_needed()
                # Null reverse path is legal; a refused sender is NOT a bad recipient.
                code, _ = smtp.mail("")
                if not 200 <= code < 300:
                    result.problems.append(f"SMTP sender/policy refused the probe ({code})")
                    continue
                code, message = smtp.rcpt(address)
                if rejected(code, message):
                    result.verdict = VERDICT_UNDELIVERABLE
                    result.is_reachable = "invalid"
                    result.problems.append("Mailbox does not exist (SMTP recipient rejection)")
                    return result
                if not 200 <= code < 300:
                    result.problems.append(f"SMTP recipient check inconclusive ({code}); may be policy or temporary failure")
                    continue
                code, message = smtp.rcpt(probe_address)
                if 200 <= code < 300:
                    result.is_catch_all = True
                    result.verdict = VERDICT_RISKY
                    result.is_reachable = "risky"
                    result.problems.append("Domain accepts any address (catch-all)")
                elif rejected(code, message):
                    result.is_catch_all = False
                    result.verdict = VERDICT_DELIVERABLE
                    result.is_reachable = "safe"
                    result.problems.append("Mailbox accepted; random mailbox rejected")
                else:
                    result.verdict = VERDICT_RISKY
                    result.is_reachable = "risky"
                    result.problems.append("Mailbox accepted but catch-all check is inconclusive")
                return result
        except Exception as exc:
            result.problems.append(f"SMTP unavailable: {type(exc).__name__}")
    if result.smtp_can_connect is None:
        result.smtp_can_connect = False
    result.problems.append("SMTP inconclusive (blocked, refused or timed out); not proof of a bad address")
    return result


# --------------------------------------------------------------------------
# Can this server confirm a mailbox at all?
# --------------------------------------------------------------------------
#
# Confirming a mailbox means an SMTP conversation on port 25, and most hosting
# providers block outbound port 25. Without it every mailbox comes back "unknown" --
# which is correct, and not sendable -- but the operator must be TOLD that is why, not
# left to wonder why nothing ever verifies.

EGRESS_PROBE_HOST = "gmail-smtp-in.l.google.com"
_EGRESS_TTL_SECONDS = 600
_egress_cache: dict[str, tuple[float, bool, Optional[str]]] = {}


async def _probe_port_25() -> tuple[bool, Optional[str]]:
    """Can this server hold an SMTP conversation? Connect AND wait for the ``220`` greeting.

    A TCP connect alone proves nothing: some networks accept the connection on port 25
    (a transparent proxy or NAT) and then close it without a word. Only a real server
    greets with ``220``.
    """
    import socket

    def connect() -> tuple[bool, Optional[str]]:
        try:
            with socket.create_connection((EGRESS_PROBE_HOST, 25), timeout=4) as sock:
                sock.settimeout(4)
                try:
                    banner = sock.recv(200)
                except OSError as exc:
                    return False, (
                        f"connected to {EGRESS_PROBE_HOST}:25 but no SMTP greeting arrived "
                        f"({type(exc).__name__})"
                    )
                if banner.startswith(b"220"):
                    return True, None
                shown = banner[:60].decode("ascii", "replace") if banner else "nothing"
                return False, (
                    f"connected to {EGRESS_PROBE_HOST}:25 but received no SMTP greeting "
                    f"(got {shown!r}); a firewall or proxy is probably intercepting port 25"
                )
        except OSError as exc:
            return False, f"connection to {EGRESS_PROBE_HOST}:25 failed ({type(exc).__name__}: {exc})"[:240]

    return await asyncio.to_thread(connect)


async def smtp_capability() -> dict:
    """``{"enabled", "reason", "via"}``: can mailboxes be confirmed from here?"""
    if reacher_configured():
        # The Reacher service makes the SMTP connection; this server's port 25 is moot.
        return {"enabled": True, "reason": None, "via": "reacher"}
    if not settings.EMAIL_VALIDATOR_SMTP:
        return {
            "enabled": False,
            "via": None,
            "reason": (
                "Built-in SMTP probing is switched off (EMAIL_VALIDATOR_SMTP=false) and no "
                "Reacher service is configured (REACHER_API_URL). Syntax and MX can be "
                "checked, but mailboxes cannot be confirmed, so they stay 'unknown' and are "
                "not sendable."
            ),
        }
    cached = _egress_cache.get("port25")
    if cached and time.time() - cached[0] < _EGRESS_TTL_SECONDS:
        _, ok, why = cached
    else:
        ok, why = await _probe_port_25()
        _egress_cache["port25"] = (time.time(), ok, why)
    if ok:
        return {"enabled": True, "reason": None, "via": "builtin"}
    return {
        "enabled": False,
        "via": None,
        "reason": (
            f"Outbound port 25 is blocked from this server ({why}). Hosting providers "
            "commonly block it. Set REACHER_API_URL to a verifier that can reach port 25 "
            "(Reacher), or run the app where port 25 is open. Until then mailboxes cannot "
            "be confirmed: they stay 'unknown' and are not sendable."
        ),
    }


async def validate_email(address: Optional[str], *, deep: bool = True) -> Verdict:
    """Validate one address: Reacher if configured, else the built-in pipeline.

    ``deep=False`` stops after the offline/DNS checks — instant, free, and
    enough to condemn an address (bad syntax, dead domain). ``deep=True`` adds
    the live mailbox check, which is the part that can *confirm* one.
    Never raises.
    """
    cleaned = normalize_email(address)
    if not cleaned or not valid_syntax(cleaned):
        return Verdict(
            address=address,
            verdict=VERDICT_UNDELIVERABLE,
            is_reachable="invalid",
            is_valid_syntax=False,
            problems=["invalid syntax"],
        )

    verdict = Verdict(address=cleaned, is_valid_syntax=True)
    domain = cleaned.rsplit("@", 1)[1].lower()
    verdict.is_role_account = is_role_address(cleaned)
    verdict.is_disposable = domain in DISPOSABLE_DOMAINS

    # Stage 1-2: free, DNS-only — the same diagnosis enrichment uses, so typo
    # suggestions and MX state are identical in both places. DNS runs even in
    # a shallow pass: a dead domain is worth condemning without a mailbox
    # probe.
    diagnosis = await asyncio.to_thread(diagnose, cleaned, check_dns=True)
    verdict.is_free = diagnosis.is_free_mail
    verdict.suggested_email = diagnosis.suggestion
    verdict.accepts_mail = diagnosis.mx_found
    if diagnosis.problems:
        verdict.problems.extend(diagnosis.problems)
    if diagnosis.mx_found is False:
        verdict.verdict = VERDICT_UNDELIVERABLE
        verdict.is_reachable = "invalid"
        return verdict
    if verdict.is_role_account:
        verdict.problems.append("Shared/role mailbox")
    if diagnosis.mx_found is None:
        verdict.problems.append("DNS unavailable or timed out; domain is not confirmed bad")
    if not deep:
        # Offline-only run: flag what is clearly bad, leave the rest unknown.
        if verdict.is_disposable or verdict.is_role_account:
            verdict.verdict = VERDICT_RISKY
            verdict.is_reachable = "risky"
        verdict.problems.append("Mailbox not checked (quick mode)")
        return verdict

    # Stage 3: a real Reacher instance answers authoritatively when present.
    reacher = await check_with_reacher(cleaned)
    if reacher is not None:
        # Keep the offline flags even when Reacher answered: its response may
        # omit them, and they are the reason a "risky" is risky.
        reacher.is_disposable = reacher.is_disposable or verdict.is_disposable
        reacher.is_role_account = reacher.is_role_account or verdict.is_role_account
        reacher.is_free = verdict.is_free
        reacher.is_valid_syntax = verdict.is_valid_syntax
        reacher.problems = verdict.problems + reacher.problems
        if reacher.is_catch_all:
            reacher.problems.append("Domain accepts any address (catch-all)")
        reacher.suggested_email = reacher.suggested_email or verdict.suggested_email
        if reacher.verdict == VERDICT_DELIVERABLE and (
            reacher.is_disposable or reacher.is_role_account or reacher.is_catch_all
        ):
            reacher.verdict = VERDICT_RISKY
            reacher.is_reachable = "risky"
        return reacher

    # Stage 3 fallback: do the mailbox probe ourselves.
    if not settings.EMAIL_VALIDATOR_SMTP:
        verdict.problems.append("Built-in SMTP checks are disabled; mailbox not checked")
        if verdict.is_disposable or verdict.is_role_account:
            verdict.verdict = VERDICT_RISKY
            verdict.is_reachable = "risky"
        return verdict
    probed = await asyncio.to_thread(_smtp_probe, cleaned, domain)
    probed.is_valid_syntax = True
    if reacher_configured():
        probed.problems.append("Reacher unavailable or invalid response; used built-in fallback")
    probed.is_disposable = verdict.is_disposable
    probed.is_role_account = verdict.is_role_account
    probed.is_free = verdict.is_free
    probed.suggested_email = verdict.suggested_email
    probed.accepts_mail = verdict.accepts_mail
    probed.problems = verdict.problems + probed.problems
    if probed.verdict == VERDICT_DELIVERABLE and (
        probed.is_disposable or probed.is_role_account or probed.is_catch_all
    ):
        probed.verdict = VERDICT_RISKY
        probed.is_reachable = "risky"
    return probed


# ==========================================================================
# Writing verdicts onto contacts + batch runs
# ==========================================================================


def apply_verdict(contact: Contact, verdict: Verdict) -> None:
    """Store a validation outcome on the contact.

    Mirrors the enrichment write policy exactly: only ``deliverable`` marks
    the address verified, only a confirmed ``undeliverable`` quarantines it,
    ``risky`` removes verification, and ``unknown`` preserves prior flags.
    """
    now = datetime.now(timezone.utc)
    if not verdict.address:
        return
    contact.email_enriched_at = now
    note = "; ".join(verdict.problems) or verdict.verdict
    contact.email_enrichment_note = f"{verdict.provider}: {note}"[:255]

    if verdict.verdict == VERDICT_DELIVERABLE:
        contact.email_verified = True
        contact.email_verified_at = now
        contact.email_verdict = VERDICT_DELIVERABLE
        contact.email_confidence = VERDICT_CONFIDENCE[VERDICT_DELIVERABLE]
    elif verdict.verdict == VERDICT_UNDELIVERABLE:
        contact.email_verified = False
        contact.email_verified_at = None
        contact.email_status = "invalid"
        contact.is_email_undeliverable = True
        contact.email_verdict = VERDICT_UNDELIVERABLE
        contact.email_confidence = VERDICT_CONFIDENCE[VERDICT_UNDELIVERABLE]
    elif verdict.verdict == VERDICT_RISKY:
        contact.email_verified = False
        contact.email_verified_at = None
        contact.email_verdict = VERDICT_RISKY
        contact.email_confidence = VERDICT_CONFIDENCE[VERDICT_RISKY]
    elif (contact.email_verdict or VERDICT_UNKNOWN) == VERDICT_UNKNOWN and not contact.email_verified:
        # The verifier could not decide. Say so -- but one timeout must never undo what a
        # mailbox probe proved earlier (deliverable / risky are kept as they are).
        contact.email_verdict = VERDICT_UNKNOWN
    # risky / unknown: no hard flags. A catch-all or a timeout must never
    # quarantine a real customer's address.


async def validate_contacts(
    db: AsyncSession,
    contacts: Iterable[Contact],
    *,
    deep: bool = True,
    mark: bool = True,
) -> dict:
    """Validate a batch of contacts and (optionally) write the verdicts.

    Returns counters plus complete per-contact detail — the same shape the list
    cleaner returns, so one UI can show both.
    """
    counters = {
        "scanned": 0,
        "with_email": 0,
        "no_email": 0,
        "deliverable": 0,
        "undeliverable": 0,
        "risky": 0,
        "unknown": 0,
        "already_verified": 0,
        "invalid_syntax": 0,
    }
    items: list[dict] = []
    for contact in contacts:
        counters["scanned"] += 1
        address = (contact.email or "").strip()
        if not address:
            counters["no_email"] += 1
            continue
        counters["with_email"] += 1
        if contact.email_verified:
            counters["already_verified"] += 1
        verdict = await validate_email(address, deep=deep)
        if "invalid syntax" in verdict.problems:
            counters["invalid_syntax"] += 1
        counters[verdict.verdict] = counters.get(verdict.verdict, 0) + 1
        if mark:
            apply_verdict(contact, verdict)
        items.append({"contact_id": contact.id, **verdict.as_dict()})

    await db.flush()
    return {
        "success": True,
        **counters,
        "deep": deep,
        "engine": "reacher" if reacher_configured() else "builtin",
        "items": items,
    }
