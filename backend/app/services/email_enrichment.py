"""Email enrichment — fill in missing addresses and prove the ones we hold.

Four stages, cheapest first. Stages 1-2 are free, offline and always on;
stages 3-4 need a provider key and degrade cleanly when there is none.

===========  ==========================================================
Stage        What it does
===========  ==========================================================
1 clean      Trim/normalise the address, strip the junk a CSV cell
             carries (``<mailto:…>``, stray commas, zero-width spaces).
2 diagnose   MX records for the domain, common typo correction
             (``gmial.com`` -> ``gmail.com``), shared-inbox and
             disposable-domain flags. Free: DNS only.
3 find       Ask a finder provider (Hunter) for the address belonging
             to a contact whose company domain we already know.
4 verify     Ask a verifier (ZeroBounce / NeverBounce / Hunter) whether
             the mailbox really exists, and store the verdict.
===========  ==========================================================

THE RULE THAT MATTERS
---------------------
An address that was **guessed** is stored with ``email_verified = False`` and
``email_source = "inferred"``. It is never sent to unless a human opts in,
because a bounce is not a cosmetic problem: it costs sender reputation for
every other email the deployment sends. Provider-verified addresses are the
only ones marked verified.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.utils.contact_identity import (
    email_domain,
    email_local_part,
    is_placeholder_email,
    is_role_address,
    normalize_email,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Stage 1/2 data tables
# --------------------------------------------------------------------------

#: Typos that account for the overwhelming majority of mistyped consumer
#: addresses. Ordered long-first so "gmial.com" is not partially rewritten.
DOMAIN_TYPOS: dict[str, str] = {
    "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmil.com": "gmail.com",
    "gmail.co": "gmail.com", "gmail.cm": "gmail.com", "gmail.con": "gmail.com",
    "gmail.comm": "gmail.com", "gmaill.com": "gmail.com", "gamil.com": "gmail.com",
    "gnail.com": "gmail.com", "gmaul.com": "gmail.com", "gmal.com": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com",
    "hotmal.com": "hotmail.com", "hotmail.co": "hotmail.com",
    "homail.com": "hotmail.com", "hotamil.com": "hotmail.com",
    "outlok.com": "outlook.com", "outllook.com": "outlook.com",
    "outlook.co": "outlook.com", "otlook.com": "outlook.com",
    "yaho.com": "yahoo.com", "yahooo.com": "yahoo.com", "yahoo.co": "yahoo.com",
    "yahho.com": "yahoo.com", "yahoo.cm": "yahoo.com",
    "yandex.co": "yandex.com", "yandx.com": "yandex.com",
    "iclod.com": "icloud.com", "icloud.co": "icloud.com", "icoud.com": "icloud.com",
    "protonmai.com": "protonmail.com", "protonmal.com": "protonmail.com",
    "zohomail.com": "zoho.com", "zoh.com": "zoho.com",
    "googlmail.com": "gmail.com", "mail.ru.com": "mail.ru",
    # Nigerian ISP / business mail hosts seen mistyped in the wild.
    "yahoo.co.uk": "yahoo.co.uk",  # valid, kept to document intent
}

#: Domains that hand out throwaway mailboxes. Not "invalid" — just a strong
#: signal that the address will not reach a real customer next month.
DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "10minutemail.com",
    "tempmail.com", "temp-mail.org", "throwawaymail.com", "trashmail.com",
    "yopmail.com", "yopmail.fr", "sharklasers.com", "getnada.com", "nada.email",
    "dispostable.com", "maildrop.cc", "fakeinbox.com", "mailnesia.com",
    "spamgourmet.com", "mytrashmail.com", "tempinbox.com", "mailcatch.com",
    "mohmal.com", "emailondeck.com", "mintemail.com", "tempr.email",
    "discard.email", "spam4.me", "grr.la", "mail-temporaire.fr",
}

#: Free consumer mailbox providers. Useful context (a ``gmail.com`` address
#: means there is no company domain to mine) rather than a red flag.
FREE_MAIL_DOMAINS: set[str] = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "icloud.com", "me.com",
    "aol.com", "protonmail.com", "proton.me", "zoho.com", "gmx.com", "gmx.net",
    "mail.com", "yandex.com", "yandex.ru", "mail.ru", "web.de", "inbox.com",
}

#: Verdict vocabulary shared by every provider adapter.
VERDICT_DELIVERABLE = "deliverable"
VERDICT_UNDELIVERABLE = "undeliverable"
VERDICT_RISKY = "risky"
VERDICT_UNKNOWN = "unknown"


@dataclass
class Diagnosis:
    """Stage 1-2 outcome for one address — no network, apart from DNS."""

    address: Optional[str] = None
    valid_syntax: bool = False
    domain: str = ""
    mx_found: Optional[bool] = None  # None = not checked (no dnspython)
    is_role: bool = False
    is_disposable: bool = False
    is_free_mail: bool = False
    is_placeholder: bool = False
    suggestion: Optional[str] = None  # corrected address, when a typo was found
    problems: list[str] = field(default_factory=list)

    @property
    def deliverable_hint(self) -> bool:
        """True when nothing local rules the address out."""
        return (
            self.valid_syntax
            and not self.is_placeholder
            and self.mx_found is not False
        )


@dataclass
class EnrichmentResult:
    """What happened to one contact."""

    contact_id: Optional[int] = None
    action: str = "skipped"  # cleaned | verified | found | inferred | skipped | failed
    address: Optional[str] = None
    source: Optional[str] = None
    provider: Optional[str] = None
    verified: bool = False
    confidence: Optional[int] = None
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "contact_id": self.contact_id,
            "action": self.action,
            "email": self.address,
            "source": self.source,
            "provider": self.provider,
            "verified": self.verified,
            "confidence": self.confidence,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# Stage 2 — DNS (optional dependency, cached)
# --------------------------------------------------------------------------

def _dns_available() -> bool:
    """True when MX lookups are possible in this process.

    Checked with ``find_spec`` rather than a try/except import so the probe
    never leaves a half-imported module behind, and so callers can use the
    answer as documentation instead of catching ImportError themselves.
    """
    from importlib.util import find_spec

    try:
        return find_spec("dns.resolver") is not None
    except (ImportError, ValueError):  # pragma: no cover - odd environments
        return False


def mx_records_exist(domain: str) -> Optional[bool]:
    """True/False only for definitive DNS answers; outages stay unknown."""
    from app.services.mail_dns import mail_route

    return mail_route(domain).accepts_mail


# --------------------------------------------------------------------------
# Stage 1-2 — diagnose a single address
# --------------------------------------------------------------------------


def diagnose(address: Optional[str], *, check_dns: bool = True) -> Diagnosis:
    """Clean and analyse one address. Pure apart from the cached DNS lookup."""
    result = Diagnosis()
    cleaned = normalize_email(address)
    if not cleaned:
        result.problems.append("not a valid email address")
        return result

    result.address = cleaned
    result.valid_syntax = True
    result.domain = email_domain(cleaned)
    result.is_role = is_role_address(cleaned)
    result.is_disposable = result.domain in DISPOSABLE_DOMAINS
    result.is_free_mail = result.domain in FREE_MAIL_DOMAINS
    result.is_placeholder = is_placeholder_email(cleaned)

    corrected_domain = DOMAIN_TYPOS.get(result.domain)
    if corrected_domain and corrected_domain != result.domain:
        local = email_local_part(cleaned)
        result.suggestion = f"{local}@{corrected_domain}"
        result.problems.append(f"domain '{result.domain}' looks like a typo")

    if result.is_disposable:
        result.problems.append("disposable/throwaway domain")
    if result.is_placeholder:
        result.problems.append("placeholder address")
    if check_dns and result.domain:
        result.mx_found = mx_records_exist(result.domain)
        if result.mx_found is False:
            result.problems.append(f"domain '{result.domain}' has no MX records")
    return result


# --------------------------------------------------------------------------
# Pattern inference (stage 3 fallback) — always stored as UNVERIFIED
# --------------------------------------------------------------------------

#: Order matters: the first pattern that the domain's MX accepts as plausible
#: is not "correct", it is simply the most common convention.
COMMON_PATTERNS = ("first.last", "firstlast", "first", "f.last", "first_last")


def _slug_part(value: Optional[str]) -> str:
    """Letters and digits only: ``O'Brien`` -> ``obrien``, ``Ada`` -> ``ada``."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def infer_candidates(
    first_name: Optional[str],
    last_name: Optional[str],
    domain: str,
    *,
    limit: int = 5,
) -> list[str]:
    """Candidate addresses for a person at a domain, most-likely first.

    These are guesses. The caller MUST store them unverified — see the module
    docstring.
    """
    first = _slug_part(first_name)
    last = _slug_part(last_name)
    domain = (domain or "").strip().lower().lstrip("@")
    if not domain or "." not in domain:
        return []
    if not first and not last:
        return []

    candidates: list[str] = []
    if first and last:
        candidates.append(f"{first}.{last}@{domain}")
        candidates.append(f"{first}{last}@{domain}")
        candidates.append(f"{first}@{domain}")
        candidates.append(f"{first[0]}.{last}@{domain}")
        candidates.append(f"{first}_{last}@{domain}")
    elif first:
        candidates.append(f"{first}@{domain}")
    else:
        candidates.append(f"{last}@{domain}")

    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen[:limit]


def domain_from_website(website: Optional[str]) -> str:
    """Turn a stored website into a bare domain (``https://a.com/x`` -> ``a.com``)."""
    if not website:
        return ""
    from urllib.parse import urlsplit

    raw = str(website).strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = f"https://{raw}"
    try:
        host = urlsplit(raw).netloc or urlsplit(raw).path
    except Exception:
        return ""
    host = host.split("@")[-1].split(":")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else ""


# --------------------------------------------------------------------------
# Stage 3-4 — provider adapters
# --------------------------------------------------------------------------


@dataclass
class ProviderAnswer:
    address: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[int] = None
    provider: str = ""
    error: str = ""


async def _http_get_json(url: str, params: dict, provider: str) -> tuple[Optional[dict], str]:
    """GET JSON, returning ``(payload, error)`` — never raises."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=settings.EMAIL_ENRICHMENT_TIMEOUT) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # network down, DNS failure, timeout
        return None, f"{provider}: {type(exc).__name__}"

    if response.status_code in (401, 403):
        return None, f"{provider}: key rejected"
    if response.status_code == 429:
        return None, f"{provider}: rate/credit limit reached"
    if response.status_code >= 400:
        return None, f"{provider}: HTTP {response.status_code}"
    try:
        return response.json(), ""
    except Exception:
        return None, f"{provider}: malformed response"


_HUNTER_VERDICTS = {
    "deliverable": VERDICT_DELIVERABLE,
    "undeliverable": VERDICT_UNDELIVERABLE,
    "risky": VERDICT_RISKY,
    "unknown": VERDICT_UNKNOWN,
    "accept_all": VERDICT_RISKY,
}

_ZEROBOUNCE_VERDICTS = {
    "valid": VERDICT_DELIVERABLE,
    "invalid": VERDICT_UNDELIVERABLE,
    "catch-all": VERDICT_RISKY,
    "spamtrap": VERDICT_UNDELIVERABLE,
    "abuse": VERDICT_UNDELIVERABLE,
    "do_not_mail": VERDICT_UNDELIVERABLE,
    "unknown": VERDICT_UNKNOWN,
}

_NEVERBOUNCE_VERDICTS = {
    "valid": VERDICT_DELIVERABLE,
    "invalid": VERDICT_UNDELIVERABLE,
    "disposable": VERDICT_UNDELIVERABLE,
    "accept_all": VERDICT_RISKY,
    "unknown": VERDICT_UNKNOWN,
}


async def hunter_find(
    domain: str, first_name: str = "", last_name: str = "", *, api_key: str
) -> ProviderAnswer:
    """Hunter.io email finder — the address for a named person at a domain."""
    payload, error = await _http_get_json(
        "https://api.hunter.io/v2/email-finder",
        {
            "domain": domain,
            "first_name": first_name or "",
            "last_name": last_name or "",
            "api_key": api_key,
        },
        "hunter",
    )
    if error:
        return ProviderAnswer(provider="hunter", error=error)
    data = (payload or {}).get("data") or {}
    address = normalize_email(data.get("email"))
    if not address:
        return ProviderAnswer(provider="hunter", error="hunter: no address found")
    status = ((data.get("verification") or {}).get("status") or "").lower()
    score = data.get("score")
    return ProviderAnswer(
        address=address,
        verdict=_HUNTER_VERDICTS.get(status),
        confidence=int(score) if isinstance(score, (int, float)) else None,
        provider="hunter",
    )


async def hunter_verify(address: str, *, api_key: str) -> ProviderAnswer:
    payload, error = await _http_get_json(
        "https://api.hunter.io/v2/email-verifier",
        {"email": address, "api_key": api_key},
        "hunter",
    )
    if error:
        return ProviderAnswer(provider="hunter", error=error)
    data = (payload or {}).get("data") or {}
    status = (data.get("status") or "").lower()
    score = data.get("score")
    return ProviderAnswer(
        address=normalize_email(address),
        verdict=_HUNTER_VERDICTS.get(status, VERDICT_UNKNOWN),
        confidence=int(score) if isinstance(score, (int, float)) else None,
        provider="hunter",
    )


async def zerobounce_verify(address: str, *, api_key: str) -> ProviderAnswer:
    payload, error = await _http_get_json(
        "https://api.zerobounce.net/v2/validate",
        {"api_key": api_key, "email": address, "ip_address": ""},
        "zerobounce",
    )
    if error:
        return ProviderAnswer(provider="zerobounce", error=error)
    status = str((payload or {}).get("status") or "").lower()
    verdict = _ZEROBOUNCE_VERDICTS.get(status, VERDICT_UNKNOWN)
    confidence = {VERDICT_DELIVERABLE: 95, VERDICT_RISKY: 60}.get(verdict)
    return ProviderAnswer(
        address=normalize_email(address), verdict=verdict,
        confidence=confidence, provider="zerobounce",
    )


async def neverbounce_verify(address: str, *, api_key: str) -> ProviderAnswer:
    payload, error = await _http_get_json(
        "https://api.neverbounce.com/v4/single/check",
        {"key": api_key, "email": address},
        "neverbounce",
    )
    if error:
        return ProviderAnswer(provider="neverbounce", error=error)
    result = str((payload or {}).get("result") or "").lower()
    verdict = _NEVERBOUNCE_VERDICTS.get(result, VERDICT_UNKNOWN)
    confidence = {VERDICT_DELIVERABLE: 95, VERDICT_RISKY: 60}.get(verdict)
    return ProviderAnswer(
        address=normalize_email(address), verdict=verdict,
        confidence=confidence, provider="neverbounce",
    )


def configured_providers() -> dict:
    """Which providers have keys set (see ``settings.email_enrichment_providers``)."""
    return dict(settings.email_enrichment_providers)


async def verify_address(address: str) -> ProviderAnswer:
    """Run the first configured verifier. Returns an error answer when none is.

    Order: Reacher (open source, free when self-hosted) first, then the paid
    providers. When a Reacher instance is configured but unreachable, the
    built-in twin of its pipeline answers instead of failing the row.
    """
    providers = configured_providers()
    if "reacher" in providers:
        from app.services.email_validator import validate_email

        verdict = await validate_email(address, deep=True)
        confidence = {
            VERDICT_DELIVERABLE: 95,
            VERDICT_RISKY: 55,
            VERDICT_UNDELIVERABLE: 90,
        }.get(verdict.verdict)
        return ProviderAnswer(
            address=normalize_email(address),
            verdict=verdict.verdict,
            confidence=confidence,
            provider=verdict.provider,
        )
    if "zerobounce" in providers:
        return await zerobounce_verify(address, api_key=providers["zerobounce"])
    if "neverbounce" in providers:
        return await neverbounce_verify(address, api_key=providers["neverbounce"])
    if "hunter" in providers:
        return await hunter_verify(address, api_key=providers["hunter"])
    return ProviderAnswer(provider="", error="no verification provider configured")


async def find_address(
    domain: str, first_name: str = "", last_name: str = ""
) -> ProviderAnswer:
    """Ask a finder provider for a missing address."""
    providers = configured_providers()
    if "hunter" not in providers:
        return ProviderAnswer(provider="", error="no finder provider configured")
    return await hunter_find(
        domain, first_name, last_name, api_key=providers["hunter"]
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def collect_extra_emails(contact: Contact) -> list[str]:
    """Every other address this contact row mentions, deduplicated.

    Looks at the JSON custom fields, because that is where a CSV parks the
    columns the importer did not recognise — ``accounts_email``, ``cc``,
    ``booking address`` and so on. Values are scanned for anything
    address-shaped, so ``"Ada <ada@x.com>, billing@x.com"`` yields both.
    """
    import json

    raw = getattr(contact, "custom_fields", None)
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(values, dict):
        return []

    found: list[str] = []
    for key, value in values.items():
        if not isinstance(value, str):
            continue
        haystack = value
        # Only look inside fields whose name or content suggests an address;
        # scanning every custom value would invent addresses out of notes.
        looks_like_field = "mail" in str(key).lower() or "@" in haystack
        if not looks_like_field:
            continue
        for token in re.split(r"[;,\s]+", haystack):
            cleaned = normalize_email(token)
            if cleaned and cleaned not in found and not is_placeholder_email(cleaned):
                found.append(cleaned)
    return found


async def _email_taken(db: AsyncSession, address: str, contact_id: Optional[int]) -> bool:
    """Is this address already the primary of a different contact?"""
    value = normalize_email(address)
    if not value:
        return False
    query = select(Contact.id).where(func.lower(Contact.email) == value)
    if contact_id is not None:
        query = query.where(Contact.id != contact_id)
    return (await db.execute(query.limit(1))).scalar_one_or_none() is not None


def _apply(db_contact: Contact, *, address: str, source: str, verified: bool,
           confidence: Optional[int], note: str, mark_primary: bool) -> None:
    """Write an enrichment outcome onto the contact.

    ``mark_primary`` decides whether the found address becomes ``Contact.email``
    or stays a secondary fact about the contact. When the contact already had a
    human-supplied primary, the newcomer does not overwrite it — the caller
    records it as an alias instead (see :func:`store_secondary_email`).
    """
    db_contact.email_enriched_at = _now()
    db_contact.email_enrichment_note = (note or "")[:255] or None
    if mark_primary or not db_contact.email:
        db_contact.email = address
        db_contact.email_lower = normalize_email(address)
    if verified:
        db_contact.email_verified = True
        db_contact.email_verified_at = _now()
        db_contact.email_verdict = VERDICT_DELIVERABLE
    if confidence is not None:
        db_contact.email_confidence = confidence
    # Never downgrade a human-supplied source ("csv"/"manual") to a machine one.
    if source and (db_contact.email_source in (None, "") or mark_primary):
        db_contact.email_source = source


async def store_secondary_email(db: AsyncSession, contact: Contact, address: str) -> bool:
    """Keep an extra address on the contact without touching the primary.

    Reuses the existing reply-alias table, so an enriched secondary address
    also becomes a recognised sender when that person replies.
    """
    from app.services.email_service import remember_contact_email

    try:
        return await remember_contact_email(db, contact, address)
    except Exception as exc:  # never let bookkeeping break an enrichment run
        logger.debug("Could not store secondary address for %s: %s", contact.id, exc)
        return False


async def enrich_contact(
    db: AsyncSession,
    contact: Contact,
    *,
    use_providers: Optional[bool] = None,
    allow_inferred: Optional[bool] = None,
    verify_existing: bool = True,
    find_missing: bool = True,
) -> EnrichmentResult:
    """Enrich one contact: clean, diagnose, then find/verify if configured.

    Never raises for a network or provider problem — a failed enrichment is a
    note on the contact, not an exception that would abort an import.
    """
    if not settings.EMAIL_ENRICHMENT_ENABLED:
        return EnrichmentResult(contact_id=contact.id, action="skipped",
                                note="enrichment disabled")

    providers_ok = (
        settings.email_enrichment_active if use_providers is None else bool(use_providers)
    )
    inferred_ok = (
        settings.EMAIL_ENRICHMENT_ALLOW_INFERRED
        if allow_inferred is None
        else bool(allow_inferred)
    )
    result = EnrichmentResult(contact_id=contact.id)

    # --- Stage 1-2: what do we already have? -------------------------------
    current = normalize_email(contact.email)
    if current and current != (contact.email or "").strip():
        # The CSV cell carried junk that rolled in from Excel/paste.
        contact.email = current
        contact.email_lower = current
        result.action = "cleaned"
        result.note = "normalised punctuation/whitespace"
    if current:
        contact.email_lower = current

    diagnosis = diagnose(current) if current else None
    if diagnosis and diagnosis.suggestion and not diagnosis.mx_found:
        result.note = (result.note + "; " if result.note else "") + (
            f"'{diagnosis.domain}' looks misspelled — try {diagnosis.suggestion}"
        )
    if diagnosis and diagnosis.is_disposable:
        result.note = (result.note + "; " if result.note else "") + "disposable domain"

    if current and contact.email_source is None:
        contact.email_source = "csv"

    # --- Stage 4: verify what we hold -------------------------------------
    if current and verify_existing and not contact.email_verified and providers_ok:
        answer = await verify_address(current)
        contact.email_enriched_at = _now()
        result.address = current
        result.provider = answer.provider or None
        result.source = contact.email_source
        if answer.error:
            # A missing key, an exhausted free tier or a network blip must not
            # look like a bad address — record why and keep the address usable.
            contact.email_enrichment_note = answer.error[:255]
            result.action = "failed"
            result.note = answer.error
            result.confidence = contact.email_confidence
            return result

        if answer.confidence is not None:
            contact.email_confidence = answer.confidence
        result.confidence = contact.email_confidence
        # Remember WHAT the provider concluded -- `email_verified` alone cannot tell
        # "never checked" from "a catch-all" from "could not decide".
        contact.email_verdict = answer.verdict or VERDICT_UNKNOWN

        if answer.verdict == VERDICT_DELIVERABLE:
            contact.email_verified = True
            contact.email_verified_at = _now()
            contact.email_enrichment_note = f"{answer.provider}: deliverable"[:255]
            result.verified = True
            result.action = "verified"
            result.note = f"verified by {answer.provider}"
        elif answer.verdict == VERDICT_UNDELIVERABLE:
            # A verifier said this mailbox does not exist. Quarantine it for
            # email the same way a hard bounce does, so nothing sends to it.
            contact.email_status = "invalid"
            contact.is_email_undeliverable = True
            contact.email_enrichment_note = f"{answer.provider}: undeliverable"[:255]
            result.action = "verified"
            result.note = f"{answer.provider} says undeliverable"
        else:
            contact.email_enrichment_note = (
                f"{answer.provider}: {answer.verdict or 'unknown'}"
            )[:255]
            result.action = "verified"
            result.note = f"{answer.provider}: {answer.verdict or 'unknown'}"
        return result

    if current:
        # --- Merge any further addresses this contact already carries -------
        # A CSV often holds more than one address for the same business (a
        # primary column plus "Accounts Email" as a custom field). Those extra
        # addresses are kept on the contact as secondary reply addresses so a
        # reply from either one is recognised — and when the provider can
        # verify them, a verified address is promoted over an unverified
        # primary, because that is the one that will actually land.
        extras = [
            value
            for value in collect_extra_emails(contact)
            if value != current and not await _email_taken(db, value, contact.id)
        ]
        if extras:
            stored = 0
            for extra in extras:
                if providers_ok:
                    answer = await verify_address(extra)
                    if not answer.error and answer.verdict == VERDICT_DELIVERABLE:
                        if not contact.email_verified:
                            # Promote: keep the old address as a secondary so
                            # nothing is lost, make the deliverable one primary.
                            await store_secondary_email(db, contact, current)
                            contact.email = extra
                            contact.email_lower = extra
                            contact.email_verified = True
                            contact.email_verified_at = _now()
                            contact.email_verdict = VERDICT_DELIVERABLE
                            contact.email_confidence = answer.confidence
                            contact.email_source = contact.email_source or "csv"
                            current = extra
                        break
                if await store_secondary_email(db, contact, extra):
                    stored += 1
            if stored or contact.email == current:
                result.note = (
                    f"{len(extras)} additional address(es) kept as reply aliases"
                )
                result.action = "cleaned" if result.action == "skipped" else result.action

        if result.action == "skipped":
            result.note = result.note or "already has an address"
        result.address = current
        result.source = contact.email_source
        result.confidence = contact.email_confidence
        result.verified = bool(contact.email_verified)
        return result

    # --- Stage 3: we have no address; try to find one ---------------------
    domain = domain_from_website(contact.website)
    if not domain:
        # A phone-only contact with no website cannot be enriched without
        # guessing a domain, which would be fabrication rather than enrichment.
        result.action = "skipped"
        result.note = "no email and no website to look up"
        contact.email_enriched_at = _now()
        contact.email_enrichment_note = result.note
        return result

    if providers_ok and find_missing:
        answer = await find_address(
            domain, contact.first_name or "", contact.last_name or ""
        )
        if answer.error:
            result.note = answer.error
        elif answer.address and not await _email_taken(db, answer.address, contact.id):
            verified = answer.verdict == VERDICT_DELIVERABLE
            _apply(
                contact,
                address=answer.address,
                source="hunter",
                verified=verified,
                confidence=answer.confidence,
                note=f"{answer.provider} find"
                + (f" ({answer.verdict})" if answer.verdict else "")
                + ("" if verified else " — not verified"),
                mark_primary=not settings.EMAIL_ENRICHMENT_KEEP_PRIMARY or not contact.email,
            )
            result.action = "found"
            result.address = answer.address
            result.source = "hunter"
            result.provider = answer.provider or None
            result.verified = verified
            result.confidence = answer.confidence
            if not verified:
                result.note = f"{answer.provider}: found, not verified by the provider"
            return result

    # --- Stage 3 fallback: pattern inference (UNVERIFIED, opt-in) ---------
    if inferred_ok:
        for candidate in infer_candidates(contact.first_name, contact.last_name, domain):
            if is_placeholder_email(candidate) or await _email_taken(db, candidate, contact.id):
                continue
            if mx_records_exist(domain) is False:
                break
            _apply(
                contact,
                address=candidate,
                source="inferred",
                verified=False,
                confidence=30,
                note="pattern guess — NOT verified",
                mark_primary=not settings.EMAIL_ENRICHMENT_KEEP_PRIMARY or not contact.email,
            )
            result.action = "inferred"
            result.address = candidate
            result.source = "inferred"
            result.verified = False
            result.confidence = 30
            result.note = "guessed pattern address — excluded from sends until verified"
            return result

    contact.email_enriched_at = _now()
    if result.note:
        contact.email_enrichment_note = result.note[:255]
    result.action = result.action if result.action != "skipped" else "skipped"
    result.note = result.note or "no address found"
    return result


async def enrich_contacts(
    db: AsyncSession,
    contacts: Iterable[Contact],
    *,
    use_providers: Optional[bool] = None,
    allow_inferred: Optional[bool] = None,
    verify_existing: bool = True,
    find_missing: bool = True,
) -> dict:
    """Enrich a batch. Returns counters plus per-contact outcomes."""
    items: list[dict] = []
    counters = {
        "processed": 0, "verified": 0, "found": 0, "inferred": 0,
        "cleaned": 0, "skipped": 0, "failed": 0, "with_email": 0,
    }
    for contact in contacts:
        outcome = await enrich_contact(
            db,
            contact,
            use_providers=use_providers,
            allow_inferred=allow_inferred,
            verify_existing=verify_existing,
            find_missing=find_missing,
        )
        counters["processed"] += 1
        if outcome.action in counters:
            counters[outcome.action] += 1
        if outcome.address:
            counters["with_email"] += 1
        items.append(outcome.as_dict())

    await db.flush()
    return {
        "success": True,
        **counters,
        "providers": list(configured_providers().keys()),
        "providers_active": settings.email_enrichment_active,
        "items": items,
    }
