"""Email address normalization + the shared "is this contact usable?" rules.

WHY THIS MODULE EXISTS
----------------------
A contact can be reached on SMS, on email, or on both. Before this module the
importer treated the phone number as mandatory, so a perfectly good
email-only row (a restaurant that publishes ``hello@`` but no mobile) was
rejected as invalid — and the phone column alone decided whether two rows were
the same person.

The rules here are deliberately in ONE place so import, enrichment and the
send paths can never disagree about what counts as a duplicate:

* :func:`normalize_email` — lower-case/trim, reject anything that is not an
  address. Same rule the email service uses, minus the provider specifics.
* :func:`contact_key` — the dedupe identity of a row: the normalized phone if
  there is one, otherwise the lower-cased email. Two rows for the same person
  therefore merge instead of splitting into two contacts.
* :func:`contact_is_usable` — a contact is worth keeping when it has at least
  one channel. This is what makes phone-only and email-only imports both valid.
"""

from __future__ import annotations

import re
from typing import Optional

from app.utils.phone import clean_phone_number, normalize_nigerian_number

# Deliberately permissive at the syntax level: a single ``@``, no whitespace,
# a dotted domain. Deep validation is the enrichment stage's job (MX lookup or
# a provider call), not this parser's — rejecting a real address because it
# looks unusual is worse than carrying it and letting verification say so.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

#: Addresses that are syntactically fine but obviously not a person's mailbox.
PLACEHOLDER_LOCAL_PARTS = {
    "example", "test", "sample", "noreply", "no-reply", "donotreply",
    "yourname", "youremail", "email", "someone", "user", "name",
}

#: Pseudo-domains used in documentation and test fixtures.
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "test.com", "test.local",
    "localhost", "email.com", "domain.com", "yourdomain.com", "sentry.io",
    "example.co", "mailinator.com",
}

#: Shared inboxes. Real addresses, but they reach a desk rather than a person,
#: so CRM flows that expect a human reply should be able to deprioritise them.
ROLE_LOCAL_PARTS = {
    "info", "sales", "support", "admin", "contact", "hello", "hi", "help",
    "enquiries", "enquiry", "inquiries", "inquiry", "office", "mail", "team",
    "marketing", "billing", "accounts", "accounting", "hr", "jobs", "careers",
    "orders", "bookings", "reservations", "reservation", "customercare",
    "customerservice", "service", "general", "reception", "frontdesk",
    "webmaster", "postmaster", "noreply", "no-reply", "donotreply",
}


def normalize_email(raw: Optional[str]) -> Optional[str]:
    """Lower-case/trim an address, or ``None`` when it is unusable.

    Unicode zero-width characters and surrounding punctuation (``<``, ``>``,
    quotes, trailing commas from CSV cells) are stripped first: pasted
    addresses routinely arrive as ``<Ada@Example.com>,`` and every one of those
    characters used to make the whole address look invalid.
    """
    if raw is None:
        return None
    value = str(raw)
    value = value.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    # Strip repeatedly: "<hello@x.com>," needs the quotes AND the punctuation
    # removed, and a single pass leaves the trailing ">" behind because the
    # final comma had to go first.
    junk = " \t\r\n<>\"'.,;:()[]"
    previous = None
    while previous != value:
        previous = value
        value = value.strip(junk)
    value = value.lower()
    if not value or " " in value:
        return None
    if not _EMAIL_RE.match(value):
        return None
    return value


def email_local_part(address: Optional[str]) -> str:
    """The part before the ``@`` (empty string when there is no address)."""
    value = normalize_email(address)
    if not value:
        return ""
    return value.split("@", 1)[0]


def email_domain(address: Optional[str]) -> str:
    """The part after the ``@`` (empty string when there is no address)."""
    value = normalize_email(address)
    if not value or "@" not in value:
        return ""
    return value.split("@", 1)[1]


def is_role_address(address: Optional[str]) -> bool:
    """True for shared inboxes such as ``info@`` / ``sales@``."""
    local = email_local_part(address)
    if not local:
        return False
    # sales.lagos@ / info.ng@ are the same desk as sales@ / info@.
    return local.split("+", 1)[0].split(".", 1)[0] in ROLE_LOCAL_PARTS


def is_placeholder_email(address: Optional[str]) -> bool:
    """True for obvious filler such as ``test@example.com`` or ``name@email.com``."""
    value = normalize_email(address)
    if not value:
        return False
    local, domain = value.split("@", 1)
    if domain in PLACEHOLDER_DOMAINS:
        return True
    if local in PLACEHOLDER_LOCAL_PARTS and domain in PLACEHOLDER_DOMAINS:
        return True
    return local in {"example", "yourname", "youremail", "noreply", "no-reply"}


def clean_phone(raw: Optional[str]) -> Optional[str]:
    """E.164 Nigerian mobile, or ``None`` when there is no usable number.

    A blank/whitespace/placeholder cell means "this contact has no phone", not
    "invalid row" — the caller decides whether that is acceptable, which is the
    whole point of :func:`contact_is_usable`.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Excel exports filler cells as "-", "N/A", "none", "0"…
    if text.lower() in {"-", "n/a", "na", "none", "null", "nil", "0", "x"}:
        return None
    if not clean_phone_number(text):
        return None
    return normalize_nigerian_number(text)


def contact_is_usable(phone: Optional[str], email: Optional[str]) -> bool:
    """A contact is real when it has a reachable channel: phone **or** email.

    Phone-only rows and email-only rows are both valid. A row with neither is
    the only thing an import should ever report as invalid.
    """
    return bool(clean_phone(phone) or normalize_email(email))


def contact_key(phone: Optional[str], email: Optional[str]) -> Optional[str]:
    """Dedupe identity for a contact row: ``p:+234…`` or ``e:ada@example.com``.

    Phone wins when both are present, so a CSV that lists the same person once
    with a number and once with an address still merge on the number rather
    than creating a second record. Returns ``None`` for a row with neither
    channel (which the importer reports as invalid instead of importing).
    """
    normalized_phone = clean_phone(phone)
    if normalized_phone:
        return f"p:{normalized_phone}"
    normalized_email = normalize_email(email)
    if normalized_email:
        return f"e:{normalized_email}"
    return None


def merge_contact_fields(existing: dict, incoming: dict) -> dict:
    """Merge an incoming row into an existing record without losing data.

    Used when an import (or CSV re-import) matches a contact that already
    exists. Blank incoming values never overwrite a populated field — a
    phone-only re-import must not wipe the address already on file, and an
    email-only import must not blank the phone.
    """
    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, "", []):
            continue
        if merged.get(key) in (None, "", []):
            merged[key] = value
        elif key == "email" and merged.get(key) != value:
            # Two addresses for one contact: keep the existing primary and
            # park the newcomer as a secondary so nothing is lost. Handled by
            # the caller (which can write an alias row); the primary stays put.
            merged.setdefault("_additional_emails", [])
            merged["_additional_emails"] = list(merged["_additional_emails"]) + [
                v for v in [value] if v != merged[key]
            ]
    return merged
