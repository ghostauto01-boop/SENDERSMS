"""
Contact display names.

One rule, used everywhere a human sees a contact: the inbox list, the
conversation header, Pushover notifications and follow-up reminders. These
used to be three slightly different expressions, so a Pushover alert could
say "Ada" while the chat said "Ada Obi", or show a bare phone number for a
contact the chat labelled by business name.

Preference order: person's name, then business/brand, then phone number. A
person's name wins because that is what the inbox list shows, and the two
must agree.
"""

from typing import Optional, Protocol


class _NameLike(Protocol):
    first_name: Optional[str]
    last_name: Optional[str]
    business_name: Optional[str]
    phone_number: Optional[str]


def contact_display_name(contact: Optional[_NameLike], fallback: str = "Unknown") -> str:
    """Best human-readable label for a contact.

    Falls back through person name -> business name -> phone number, and
    tolerates a missing contact so callers do not each repeat a None check.
    Whitespace-only names are treated as absent.
    """
    if contact is None:
        return fallback

    first = (getattr(contact, "first_name", None) or "").strip()
    last = (getattr(contact, "last_name", None) or "").strip()
    person = f"{first} {last}".strip()
    if person:
        return person

    business = (getattr(contact, "business_name", None) or "").strip()
    if business:
        return business

    phone = (getattr(contact, "phone_number", None) or "").strip()
    return phone or fallback
