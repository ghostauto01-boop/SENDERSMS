"""Email validator — Reacher (https://reacher.email) plus an in-app twin.

WHAT THIS IS
------------
Reacher is the open-source email validator (github.com/reacherhq/check-if-email-
exists). This module talks to a Reacher instance when one is configured
(``REACHER_API_URL``), and otherwise performs the **same pipeline itself**, so
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
``undeliverable`` quarantines an address. An ``unknown`` result changes
nothing except the note — silence is not proof an address is dead, and a
wrong "invalid" mark costs a real customer.
"""

from __future__ import annotations

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
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

#: Answers that mean "this mailbox does not exist here".
_SMTP_REJECT_CODES = {550, 551, 552, 553, 554}


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
    return bool(getattr(settings, "REACHER_API_URL", None))


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

    reachable = str((data or {}).get("is_reachable") or "unknown").lower()
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
    )


# ==========================================================================
# Built-in twin of the Reacher pipeline
# ==========================================================================

_MX_HOST_CACHE: dict[str, tuple[list[tuple[int, str]], float]] = {}


def _mx_hosts(domain: str) -> list[tuple[int, str]]:
    """(preference, host) pairs for a domain, cached; A fallback per RFC 5321."""
    domain = (domain or "").strip().lower()
    if not domain:
        return []
    now = time.time()
    cached = _MX_HOST_CACHE.get(domain)
    if cached and (now - cached[1]) < max(settings.EMAIL_ENRICHMENT_DNS_TTL, 0):
        return cached[0]

    hosts: list[tuple[int, str]] = []
    try:
        import dns.resolver

        try:
            answer = dns.resolver.resolve(domain, "MX", lifetime=settings.EMAIL_ENRICHMENT_TIMEOUT)
            for record in answer:
                hosts.append((int(record.preference), str(record.exchange).rstrip(".").lower()))
        except Exception:
            # RFC 5321: a domain with no MX falls back to its A record.
            try:
                dns.resolver.resolve(domain, "A", lifetime=settings.EMAIL_ENRICHMENT_TIMEOUT)
                hosts = [(0, domain)]
            except Exception:
                hosts = []
    except Exception:
        # dnspython not installed: cannot answer, do not cache the miss.
        return []
    hosts.sort()
    _MX_HOST_CACHE[domain] = (hosts, now)
    return hosts


def _smtp_probe(address: str, domain: str) -> Verdict:
    """Live ``RCPT TO`` probe against the domain's MX, with catch-all detection.

    The catch-all probe uses a random mailbox at the same domain: if the
    server accepts *that* too, it accepts everything and an accept proves
    nothing (Reacher reports the same condition as ``is_catch_all``).
    """
    import smtplib

    result = Verdict(address=address)
    hosts = _mx_hosts(domain)
    if not hosts:
        result.problems.append("no mail exchanger for domain")
        return result

    random_local = "reacher-probe-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    probe_address = f"{random_local}@{domain}"

    def _rcpt(target: str) -> Optional[bool]:
        """True = accepted, False = rejected, None = could not reach."""
        last_error = None
        for _prio, host in hosts[:2]:
            try:
                with smtplib.SMTP(host, 25, timeout=min(settings.EMAIL_ENRICHMENT_TIMEOUT, 8)) as smtp:
                    smtp.ehlo_or_helo_if_needed()
                    smtp.mail("probe@reacher.local")
                    code, _ = smtp.rcpt(target)
                    result.smtp_can_connect = True
                    if code in _SMTP_REJECT_CODES:
                        return False
                    if 200 <= code < 300:
                        return True
                    last_error = f"SMTP {code}"
            except Exception as exc:  # noqa: BLE001 - timeout/refused/blocked
                last_error = f"{type(exc).__name__}"
                continue
        result.smtp_can_connect = False
        if last_error:
            result.problems.append(f"smtp: {last_error}")
        return None

    catch_all = _rcpt(probe_address)
    accepted = _rcpt(address)
    result.is_catch_all = True if catch_all is True else (False if catch_all is False else None)

    if accepted is False:
        result.problems.append("mailbox rejected by SMTP server")
        return result
    if accepted is True:
        if result.is_catch_all:
            result.problems.append("domain accepts any address (catch-all)")
        else:
            result.verdict = VERDICT_DELIVERABLE
            result.is_reachable = "safe"
            return result
        result.verdict = VERDICT_RISKY
        result.is_reachable = "risky"
        return result
    result.problems.append("SMTP inconclusive (blocked or timed out)")
    return result


async def validate_email(address: Optional[str], *, deep: bool = True) -> Verdict:
    """Validate one address: Reacher if configured, else the built-in pipeline.

    ``deep=False`` stops after the offline/DNS checks — instant, free, and
    enough to condemn an address (bad syntax, dead domain). ``deep=True`` adds
    the live mailbox check, which is the part that can *confirm* one.
    Never raises.
    """
    cleaned = normalize_email(address)
    if not cleaned or not _EMAIL_RE.match(cleaned):
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
    diagnosis = diagnose(cleaned, check_dns=True)
    verdict.is_free = diagnosis.is_free_mail
    verdict.suggested_email = diagnosis.suggestion
    verdict.accepts_mail = diagnosis.mx_found
    if diagnosis.problems:
        verdict.problems.extend(diagnosis.problems)
    if diagnosis.mx_found is False:
        verdict.verdict = VERDICT_UNDELIVERABLE
        verdict.is_reachable = "invalid"
        return verdict
    if not deep:
        # Offline-only run: flag what is clearly bad, leave the rest unknown.
        if verdict.is_disposable or verdict.is_role_account:
            verdict.verdict = VERDICT_RISKY
            verdict.is_reachable = "risky"
        return verdict

    # Stage 3: a real Reacher instance answers authoritatively when present.
    reacher = await check_with_reacher(cleaned)
    if reacher is not None:
        # Keep the offline flags even when Reacher answered: its response may
        # omit them, and they are the reason a "risky" is risky.
        reacher.is_disposable = reacher.is_disposable or verdict.is_disposable
        reacher.is_role_account = reacher.is_role_account or verdict.is_role_account
        reacher.is_free = verdict.is_free
        reacher.suggested_email = reacher.suggested_email or verdict.suggested_email
        if reacher.verdict == VERDICT_DELIVERABLE and (
            reacher.is_disposable or reacher.is_role_account or reacher.is_catch_all
        ):
            reacher.verdict = VERDICT_RISKY
            reacher.is_reachable = "risky"
        return reacher

    # Stage 3 fallback: do the mailbox probe ourselves.
    probed = _smtp_probe(cleaned, domain)
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
    and ``risky`` / ``unknown`` leave the address usable but never verified.
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
        if verdict.address != (contact.email or "").strip() and verdict.suggested_email == verdict.address:
            pass  # spelling fix already applied by the caller, if any
    elif verdict.verdict == VERDICT_UNDELIVERABLE:
        contact.email_verified = False
        contact.email_status = "invalid"
        contact.is_email_undeliverable = True
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

    Returns counters plus capped per-contact detail — the same shape the list
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
        address = normalize_email(contact.email)
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
        if len(items) < 200:
            items.append({"contact_id": contact.id, **verdict.as_dict()})

    await db.flush()
    return {
        "success": True,
        **counters,
        "deep": deep,
        "engine": "reacher" if reacher_configured() else "builtin",
        "items": items,
    }
