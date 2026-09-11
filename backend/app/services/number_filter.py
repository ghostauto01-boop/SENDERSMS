"""Pre-send number filter — quarantine bad numbers BEFORE they cost money.

Every SMS attempt bills the SIM (carrier charge + gateway attempt), even when
the number can never receive it. Sending to dead/test/malformed numbers is the
biggest drag on delivery rate, so this filter runs:

* on import / list clean (``scan_contacts`` + ``POST /lists/{id}/clean``),
* as a dry-run preview (``POST /send/validate``),
* inline on every send path (single send, campaign, ads dispatch, follow-ups).

A number is SENDABLE only when ALL of these hold:
  1. non-empty after cleaning,
  2. valid Nigerian mobile in E.164 (carrier can route it),
  3. not a known test/fake pattern (all-same-digit, sequential, 08000000000…),
  4. contact not opted out / suppressed / already undeliverable.

Reasons are stable strings so the UI can group and explain them.
"""

import logging
import re
from dataclasses import dataclass, field

from app.utils.phone import (
    NG_MOBILE_PREFIXES,
    clean_phone_number,
    is_nigerian_mobile,
    normalize_nigerian_number,
)

logger = logging.getLogger(__name__)

# Example/test local numbers (national 10-digit form, no trunk) that show up in
# CSV samples and test data. These are SOFT flags (see looks_like_test_data):
# they are quarantined by explicit list-clean actions and shown in dry-run
# previews, but they never hard-block a direct send — a sequential number can
# still be a real subscriber, and blocking it would silently drop real people.
# NOTE: repeated-digit "golden" numbers are deliberately NOT in this set —
# they are real paid-for lines.
_EXAMPLE_LOCAL = {
    "8123456789", "8012345678", "8001234567",
}

_SEQUENTIAL_FULL = re.compile(r"^(0123456789|9876543210)$")


@dataclass
class FilterResult:
    sendable: bool
    normalized: str | None = None
    reason: str | None = None  # stable code when not sendable
    detail: str = ""  # human sentence for the UI


@dataclass
class FilterReport:
    total: int = 0
    sendable: int = 0
    blocked: int = 0
    by_reason: dict = field(default_factory=dict)  # reason -> count
    blocked_phones: list = field(default_factory=list)  # first N for preview


def _national10(raw: str) -> str | None:
    """Best-effort national 10-digit form for pattern checks (no validity)."""
    digits = re.sub(r"\D", "", clean_phone_number(str(raw or "")))
    if digits.startswith("234"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 and digits.isdigit() else None


def _is_hard_fake(national10: str) -> bool:
    """Numbers that are fake with near-certainty (hard block).

    Only all-same-digit (08000000000) and exact full sequences. Repeated-digit
    \"golden\" numbers (08031111111) are REAL paid-for lines in Nigeria, so
    anything less certain than this is a soft flag, never a block.
    """
    if not national10:
        return False
    if len(set(national10)) == 1:
        return True
    # X followed by nine zeros: 08000000000. Nobody owns this number.
    if re.fullmatch(r"\d0{9}", national10):
        return True
    return bool(_SEQUENTIAL_FULL.match(national10))


def looks_like_test_data(raw: str | None) -> str | None:
    """Soft flag: does this look like sample/test data? Returns reason/None.

    Used by dry-run previews and explicit list-clean actions only — never by
    the send path itself.
    """
    n10 = _national10(raw or "")
    if not n10:
        return None
    if n10 in _EXAMPLE_LOCAL:
        return "test_data_suspected"
    # Ascending/descending runs of 8+: 8123456789, 9876543210… Shorter runs
    # (3456789) occur in real allocations, so the bar is deliberately high.
    for i in range(len(n10) - 7):
        window = n10[i:i + 8]
        if all(int(window[j + 1]) - int(window[j]) == 1 for j in range(7)):
            return "test_data_suspected"
        if all(int(window[j]) - int(window[j + 1]) == 1 for j in range(7)):
            return "test_data_suspected"
    # 8+ repeated digits in a row. Golden lines with 6-7 repeats are common
    # and real, so only extreme repetition is flagged.
    if re.search(r"(\d)\1{7}", n10):
        return "test_data_suspected"
    return None


def classify_number(raw: str | None) -> FilterResult:
    """Classify one raw phone string. Pure function — no DB needed.

    HARD verdict: only blocks numbers that structurally cannot receive an SMS
    (or are fake with near-certainty). Test-data lookalikes are NOT blocked
    here — see looks_like_test_data() for the soft flag used by previews and
    explicit list-clean actions.
    """
    if not raw or not str(raw).strip():
        return FilterResult(False, None, "empty_number", "No phone number on record.")
    cleaned = clean_phone_number(str(raw))
    # Short codes first: they are deliberately short (3-8 digits).
    if cleaned.isdigit() and 3 <= len(cleaned) <= 8:
        return FilterResult(False, None, "short_code",
                             f"'{raw}' looks like a short code, not a mobile number.")
    if not cleaned or len(re.sub(r"\D", "", cleaned)) < 7:
        return FilterResult(False, None, "invalid_format",
                             f"'{raw}' is too short to be a phone number.")
    normalized = normalize_nigerian_number(raw)
    if not normalized:
        # Even unparseable input can be an obvious fake (08888888888 fails
        # phonenumbers validity) — label it as a test number, not garbage.
        n10 = _national10(raw)
        if n10 and _is_hard_fake(n10):
            return FilterResult(False, None, "test_number",
                                 f"'{raw}' looks like a test/fake number.")
        # Distinguish "valid but foreign" from garbage — foreign numbers fail
        # on a Nigerian SIM, so both are blocked, with different reasons.
        digits = re.sub(r"\D", "", cleaned)
        if cleaned.startswith("+") and 8 <= len(digits) <= 15:
            return FilterResult(False, None, "foreign_number",
                                 f"'{raw}' is not a Nigerian number — the SIM cannot deliver to it.")
        if cleaned.isdigit() and 3 <= len(cleaned) <= 8:
            return FilterResult(False, None, "short_code",
                                 f"'{raw}' looks like a short code, not a mobile number.")
        return FilterResult(False, None, "invalid_format",
                             f"'{raw}' is not a valid Nigerian mobile number.")
    # Valid but not Nigerian (e.g. +1…): the phonenumbers fallback accepts any
    # valid international number, so catch non-+234 explicitly.
    if not normalized.startswith("+234"):
        return FilterResult(False, normalized, "foreign_number",
                             f"'{raw}' is not a Nigerian number — the SIM cannot deliver to it.")
    # Hard-fake check BEFORE the prefix check: 08000000000 must read as a
    # test number, not a landline, so the UI explains it correctly.
    national10 = normalized.replace("+234", "")
    if _is_hard_fake(national10):
        return FilterResult(False, normalized, "test_number",
                             f"'{raw}' looks like a test/fake number.")
    if not is_nigerian_mobile(raw):
        return FilterResult(False, normalized, "not_mobile",
                             f"'{raw}' is not a Nigerian mobile prefix (landline/special range).")
    # Prefix sanity against the known mobile ranges.
    digits = "0" + national10
    if not any(digits.startswith(p) for p in NG_MOBILE_PREFIXES):
        return FilterResult(False, normalized, "unknown_prefix",
                             f"'{raw}' is not in a known Nigerian mobile prefix range.")
    return FilterResult(True, normalized, None, "")


def filter_numbers(phones: list[str], preview_limit: int = 50,
                   *, strict_patterns: bool = True) -> FilterReport:
    """Classify many numbers at once (dry-run / preview, no DB writes).

    Previews default to ``strict_patterns=True`` so test-data lookalikes are
    surfaced; the live send path uses the hard verdict only.
    """
    report = FilterReport(total=len(phones))
    for p in phones:
        r = classify_number(p)
        soft = looks_like_test_data(p) if (r.sendable and strict_patterns) else None
        if r.sendable and not soft:
            report.sendable += 1
        else:
            reason = r.reason if not r.sendable else soft
            detail = r.detail if not r.sendable else f"'{p}' looks like sample/test data."
            report.blocked += 1
            report.by_reason[reason or "unknown"] = report.by_reason.get(reason or "unknown", 0) + 1
            if len(report.blocked_phones) < preview_limit:
                report.blocked_phones.append({"phone": p, "reason": reason, "detail": detail})
    return report


async def contact_sendable(db, contact, *, strict_patterns: bool = False) -> FilterResult:
    """Full check for a Contact row: number validity + opt-out/suppression.

    ``strict_patterns`` is for previews and explicit clean actions — the live
    send path always uses the hard verdict so test-data lookalikes that are
    real subscribers still receive their SMS.
    """
    from sqlalchemy import select

    from app.models.suppression import SuppressionEntry

    if getattr(contact, "is_opted_out", False):
        return FilterResult(False, None, "opted_out", "Contact opted out (STOP).")
    if getattr(contact, "is_undeliverable", False):
        return FilterResult(False, None, "undeliverable",
                             getattr(contact, "undeliverable_reason", "") or "Previously marked undeliverable.")
    r = classify_number(contact.phone_number or "")
    if not r.sendable:
        return r
    row = (await db.execute(select(SuppressionEntry).where(
        SuppressionEntry.phone_number == contact.phone_number))).scalar_one_or_none()
    if row is not None:
        return FilterResult(False, r.normalized, "suppressed", "Number is on the suppression list.")
    if strict_patterns:
        soft = looks_like_test_data(contact.phone_number or "")
        if soft:
            return FilterResult(False, r.normalized, soft,
                                 f"'{contact.phone_number}' looks like sample/test data.")
    return r


async def scan_contacts(db, contacts: list, *, quarantine: bool = True,
                       strict_patterns: bool = True) -> dict:
    """Classify contacts; optionally quarantine the bad ones in-place.

    Returns counts + per-reason breakdown. Quarantine = mark undeliverable so
    no future send path touches them (and no carrier charge is incurred).
    Explicit clean actions use strict patterns (test-data lookalikes included).
    """
    from app.services.list_hygiene import mark_undeliverable

    counts: dict[str, int] = {}
    quarantined = 0
    sendable = 0
    for c in contacts:
        r = await contact_sendable(db, c, strict_patterns=strict_patterns)
        if r.sendable:
            sendable += 1
            continue
        reason = r.reason or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
        # Opt-outs/suppressed are already skipped — don't double-flag them.
        if quarantine and reason not in ("opted_out", "suppressed", "undeliverable"):
            if not c.is_undeliverable:
                await mark_undeliverable(c, reason)
                quarantined += 1
    await db.flush()
    return {"scanned": len(contacts), "sendable": sendable,
            "blocked": len(contacts) - sendable, "by_reason": counts,
            "quarantined": quarantined}
