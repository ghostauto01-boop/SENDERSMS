"""
Message personalization.

One renderer, used by every path that turns a template into an SMS: campaign
sending, direct send, the SMSService helper and the preview endpoint. These
used to be four separate chains of ``str.replace`` calls that disagreed with
each other, which produced three customer-visible bugs:

  * A contact with no first name got "Hi , this is ..." because the placeholder
    was replaced with an empty string and the punctuation was left behind.
  * ``send.py`` and ``SMSService`` only substituted four of the eight fields
    the template editor offers, so real messages went out reading
    "Visit {{website}} for {{industry}} deals".
  * The preview endpoint understood a different set of placeholders from the
    senders, so preview was not a reliable picture of what would be sent.

Behaviour:
  * ``{{field}}`` and ``{{ field }}`` (any inner whitespace) both resolve.
  * Matching is case-insensitive: ``{{First_Name}}`` works.
  * ``{{field|fallback text}}`` uses the fallback when the field is empty.
  * ``{{first_name}}`` with no explicit fallback degrades to the business name
    and then to "there", so a greeting never collapses into "Hi ,".
  * Unknown placeholders resolve to empty rather than being sent as literal
    braces. Leaking template syntax to a customer is worse than a gap.
  * Whatever the substitutions leave behind is tidied: doubled spaces collapse
    and orphaned space-before-punctuation is repaired.
  * Values from a contact's ``custom_fields`` JSON are available by key, so
    imported columns can be used in templates.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

# Placeholder with an optional "|fallback" section. The field name is
# restricted to identifier characters so stray braces in ordinary prose are
# left untouched.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|([^}]*))?\}\}")

# Same shape with single braces, applied only to field names we can resolve.
_SINGLE_PLACEHOLDER = re.compile(r"\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|([^}]*))?\}")

# Fields lifted straight off the Contact model.
_CONTACT_FIELDS = (
    "first_name",
    "last_name",
    "business_name",
    "phone_number",
    "email",
    "city",
    "state",
    "country",
    "website",
    "industry",
)

#: Greeting fallback used when a contact has no name at all.
GENERIC_NAME = "there"


def _clean(value: Any) -> str:
    """Normalize a field value to a trimmed string ("" for None/blank)."""
    if value is None:
        return ""
    return str(value).strip()


def build_context(contact: Optional[object]) -> dict[str, str]:
    """Collect every placeholder value available for a contact.

    Custom fields are merged in first so that a real column always wins over
    an imported key of the same name.
    """
    context: dict[str, str] = {}
    if contact is None:
        return context

    raw_custom = getattr(contact, "custom_fields", None)
    if raw_custom:
        try:
            parsed = json.loads(raw_custom) if isinstance(raw_custom, str) else raw_custom
            if isinstance(parsed, Mapping):
                for key, value in parsed.items():
                    if isinstance(key, str) and not isinstance(value, (dict, list)):
                        context[key.strip().lower()] = _clean(value)
        except (ValueError, TypeError):
            # A malformed custom_fields blob must never block a send.
            pass

    for field in _CONTACT_FIELDS:
        context[field] = _clean(getattr(contact, field, None))

    # Convenience aliases people reach for in templates.
    full_name = " ".join(p for p in (context.get("first_name"), context.get("last_name")) if p)
    context["full_name"] = full_name
    context["name"] = full_name or context.get("business_name", "")

    return context


def _resolve(field: str, explicit_fallback: Optional[str], context: Mapping[str, str]) -> str:
    value = context.get(field, "")
    if value:
        return value

    if explicit_fallback is not None:
        return explicit_fallback.strip()

    # A greeting is the one place an empty value reads as broken rather than
    # merely terse, so give the name fields a sensible human default.
    if field in ("first_name", "name", "full_name"):
        return context.get("business_name", "") or GENERIC_NAME

    return ""


def tidy(text: str) -> str:
    """Repair the artifacts an empty substitution leaves behind."""
    # "Hi , this" -> "Hi, this" ; "about ." -> "about."
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)
    # Collapse runs of spaces/tabs created by a removed placeholder.
    text = re.sub(r"[ \t]{2,}", " ", text)
    # Tidy spaces hugging a newline, without discarding the newline itself.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return text.strip()


def render_template(body: Optional[str], contact: Optional[object] = None, **overrides: Any) -> str:
    """Render ``body`` for ``contact``.

    ``overrides`` supply or replace individual placeholder values, which is how
    the preview endpoint injects its sample data through the same code path the
    senders use.
    """
    if not body:
        return ""

    context = build_context(contact)
    for key, value in overrides.items():
        if value is not None:
            context[key.strip().lower()] = _clean(value)

    def _sub(match: re.Match) -> str:
        return _resolve(match.group(1).lower(), match.group(2), context)

    rendered = _PLACEHOLDER.sub(_sub, body)

    # Tolerate single braces around a KNOWN field name. The editor inserts
    # {{first_name}}, but people type {first_name} from memory and the result
    # was the literal text "{first_name}" arriving on a customer's phone.
    # Restricting this to names we can actually resolve keeps ordinary prose
    # containing braces untouched.
    def _sub_single(match: re.Match) -> str:
        field = match.group(1).lower()
        if field not in context and field not in ("first_name", "name", "full_name"):
            return match.group(0)
        return _resolve(field, match.group(2), context)

    rendered = _SINGLE_PLACEHOLDER.sub(_sub_single, rendered)

    return tidy(rendered)
