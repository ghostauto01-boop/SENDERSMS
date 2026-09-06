"""Contact variable registry: discovery, short codes and render maps.

Three jobs:

1. **Discover.** Scan every contact's ``custom_fields`` JSON (plus the standard
   Contact columns) and make sure the registry has a row for each variable that
   has ever been imported. This is what makes "import a CSV with a States
   column, then see States on the Variables page" work with no extra step.
2. **Resolve.** Build the alias map ``{shortcode -> field_key}`` and the
   fallback map used by the renderer, so a message written with
   ``{{Pain Point}}`` picks up ``pain_point`` off the contact.
3. **Report.** Count how many contacts actually hold a value for each variable
   so the page can warn "only 12 of 400 contacts have this".
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.variable import ContactVariable
from app.utils.templating import normalize_key

logger = logging.getLogger(__name__)

#: Standard variables that always exist because they are Contact columns.
#: (field_key, label, description)
STANDARD_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("first_name", "First Name", "Contact's first name. Falls back to the business name, then 'there'."),
    ("last_name", "Last Name", "Contact's surname."),
    ("full_name", "Full Name", "First and last name joined."),
    ("business_name", "Business Name", "Company / brand name."),
    ("phone_number", "Phone Number", "Normalized phone number."),
    ("email", "Email", "Email address."),
    ("city", "City", "City or town."),
    ("state", "State", "State, region or province."),
    ("country", "Country", "Country."),
    ("website", "Website", "Website URL."),
    ("industry", "Industry", "Industry / sector / niche."),
)

#: Never offer these as message variables.
_RESERVED = {"custom_fields", "notes", "id"}


def humanize(field_key: str) -> str:
    """``pain_point`` -> ``Pain Point`` for a first-guess label."""
    return " ".join(part.capitalize() for part in field_key.split("_") if part) or field_key


def _custom_values(contact: Contact) -> dict[str, str]:
    raw = contact.custom_fields
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or isinstance(value, (dict, list)):
            continue
        text = "" if value is None else str(value).strip()
        norm = normalize_key(key)
        if norm and norm not in _RESERVED:
            out[norm] = text
    return out


async def ensure_standard_variables(db: AsyncSession) -> int:
    """Create the registry rows for the built-in Contact columns."""
    existing = {
        row for row in (await db.execute(select(ContactVariable.field_key))).scalars().all()
    }
    created = 0
    for field_key, label, description in STANDARD_VARIABLES:
        if field_key in existing:
            continue
        db.add(
            ContactVariable(
                field_key=field_key,
                label=label,
                shortcode=field_key,
                description=description,
                source="standard",
            )
        )
        created += 1
    if created:
        await db.flush()
    return created


async def sync_variables_from_contacts(db: AsyncSession, *, limit: int | None = None) -> dict:
    """Discover every imported variable and refresh usage statistics.

    Safe to run repeatedly: existing rows keep the operator's chosen short code
    and fallback, only the counters and sample value are refreshed.
    """
    await ensure_standard_variables(db)

    rows = (await db.execute(select(ContactVariable))).scalars().all()
    by_key: dict[str, ContactVariable] = {row.field_key: row for row in rows}
    taken_shortcodes = {row.shortcode for row in rows}

    counts: dict[str, int] = {}
    samples: dict[str, str] = {}

    query = select(Contact)
    if limit:
        query = query.limit(limit)
    contacts = (await db.execute(query)).scalars().all()

    for contact in contacts:
        for field_key, _label, _desc in STANDARD_VARIABLES:
            value = getattr(contact, field_key, None)
            if field_key == "full_name":
                value = " ".join(
                    p for p in (contact.first_name, contact.last_name) if p
                )
            text = "" if value is None else str(value).strip()
            if text:
                counts[field_key] = counts.get(field_key, 0) + 1
                samples.setdefault(field_key, text)

        for field_key, text in _custom_values(contact).items():
            if text:
                counts[field_key] = counts.get(field_key, 0) + 1
                samples.setdefault(field_key, text)
            else:
                counts.setdefault(field_key, 0)

    discovered = 0
    for field_key in counts:
        variable = by_key.get(field_key)
        if variable is None:
            # A brand-new imported column. Default its short code to the field
            # key, de-duplicating if the operator already used that word.
            shortcode = field_key
            suffix = 2
            while shortcode in taken_shortcodes:
                shortcode = f"{field_key}_{suffix}"
                suffix += 1
            taken_shortcodes.add(shortcode)
            variable = ContactVariable(
                field_key=field_key,
                label=humanize(field_key),
                shortcode=shortcode,
                source="imported",
            )
            db.add(variable)
            by_key[field_key] = variable
            discovered += 1
        variable.contact_count = counts.get(field_key, 0)
        if samples.get(field_key):
            variable.sample_value = samples[field_key][:500]

    await db.flush()
    return {
        "discovered": discovered,
        "total": len(by_key),
        "contacts_scanned": len(contacts),
    }


async def variable_maps(db: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(aliases, fallbacks)`` for the renderer.

    ``aliases`` maps every recognized short-code spelling onto the field key
    that holds the value. Both the short code and the field key are included,
    so a message keeps working whether the operator typed the custom short code
    or the raw column name.
    """
    rows = (await db.execute(select(ContactVariable))).scalars().all()
    aliases: dict[str, str] = {}
    fallbacks: dict[str, str] = {}
    for row in rows:
        if not row.is_active:
            continue
        field_key = normalize_key(row.field_key)
        aliases[normalize_key(row.shortcode)] = field_key
        aliases.setdefault(field_key, field_key)
        aliases.setdefault(normalize_key(row.label), field_key)
        if row.fallback_text:
            fallbacks[field_key] = row.fallback_text
    return aliases, fallbacks


async def render_for_contact(db: AsyncSession, body: str, contact) -> str:
    """Render a message for a contact using the registry's short codes."""
    from app.utils.templating import render_template

    aliases, fallbacks = await variable_maps(db)
    return render_template(body, contact, aliases=aliases, fallbacks=fallbacks)


def find_placeholders(body: str) -> list[str]:
    """Every short code used in a message body, normalized and de-duplicated."""
    from app.utils.templating import _PLACEHOLDER

    seen: list[str] = []
    for match in _PLACEHOLDER.finditer(body or ""):
        key = normalize_key(match.group(1))
        if key and key not in seen:
            seen.append(key)
    return seen


async def analyze_message(db: AsyncSession, body: str, contact=None) -> dict:
    """Explain what will happen to each short code in ``body``.

    Powers the composer warning: which variables resolve, which are unknown
    (and will be removed), and which are known but empty for this contact.
    """
    from app.utils.templating import build_context

    aliases, fallbacks = await variable_maps(db)
    context = build_context(contact) if contact is not None else {}

    resolved, empty, unknown = [], [], []
    for key in find_placeholders(body):
        field_key = aliases.get(key)
        if field_key is None:
            unknown.append(key)
            continue
        value = context.get(field_key, "")
        if value:
            resolved.append({"shortcode": key, "field_key": field_key, "value": value})
        elif fallbacks.get(field_key):
            resolved.append(
                {"shortcode": key, "field_key": field_key, "value": fallbacks[field_key]}
            )
        else:
            empty.append({"shortcode": key, "field_key": field_key})

    from app.utils.templating import render_template

    return {
        "preview": render_template(body, contact, aliases=aliases, fallbacks=fallbacks),
        "resolved": resolved,
        "empty": empty,
        "unknown": unknown,
        "will_remove": [item["shortcode"] for item in empty] + unknown,
    }
