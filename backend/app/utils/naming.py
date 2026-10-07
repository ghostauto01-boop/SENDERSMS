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

    Falls back through person name -> business name -> phone number -> email
    address, and tolerates a missing contact so callers do not each repeat a
    None check. Whitespace-only names are treated as absent, and the email step
    exists because an email-only contact has no phone to fall back to — without
    it the inbox and follow-up lists would label them "Unknown".
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
    if phone:
        return phone

    email = (getattr(contact, "email", None) or "").strip()
    return email or fallback
