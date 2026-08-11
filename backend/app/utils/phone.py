"""
Nigerian phone number normalization and validation.
Uses the phonenumbers library for E.164 formatting.
"""

import logging
import re
from typing import Optional, Tuple

import phonenumbers
from phonenumbers import PhoneNumberFormat, NumberParseException

logger = logging.getLogger(__name__)

# Nigerian mobile number patterns
NIGERIA_COUNTRY_CODE = "234"

NG_MOBILE_PREFIXES = [
    "0701", "07020", "07025", "07026", "07027", "07028", "07029",
    "0703", "0704", "0705", "0706", "0707", "0708", "0709",
    "0802", "0803", "0804", "0805", "0806", "0807", "0808", "0809",
    "0810", "0811", "0812", "0813", "0814", "0815", "0816", "0817", "0818", "0819",
    "0901", "0902", "0903", "0904", "0905", "0906", "0907", "0908", "0909",
    "0911", "0912", "0913", "0915", "0916",
]


def clean_phone_number(raw: str) -> str:
    """Remove spaces, brackets, dashes, and unnecessary punctuation."""
    if not raw:
        return ""
    # Remove everything except digits and leading +
    cleaned = re.sub(r'[^\d+]', '', raw.strip())
    # If multiple + signs, keep only the first
    if cleaned.startswith('+'):
        cleaned = '+' + cleaned[1:].replace('+', '')
    else:
        cleaned = cleaned.replace('+', '')
    return cleaned


def normalize_nigerian_number(phone: str) -> Optional[str]:
    """
    Normalize a Nigerian phone number to E.164 format.

    Handles:
    - 08012345678
    - 8012345678
    - 2348012345678
    - +2348012345678

    Returns None if the number is not a valid Nigerian mobile number.
    """
    if not phone:
        return None

    cleaned = clean_phone_number(phone)

    if not cleaned:
        return None

    # Handle various formats
    if cleaned.startswith('+234'):
        e164 = cleaned
    elif cleaned.startswith('234'):
        e164 = '+' + cleaned
    elif cleaned.startswith('0'):
        e164 = '+234' + cleaned[1:]
    elif len(cleaned) == 10 and cleaned.startswith(('7', '8', '9')):
        e164 = '+234' + cleaned
    elif len(cleaned) == 11 and cleaned.startswith('0'):
        e164 = '+234' + cleaned[1:]
    else:
        # Try phonenumbers library
        try:
            parsed = phonenumbers.parse(cleaned, "NG")
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
        except NumberParseException:
            pass
        return None

    # Validate with phonenumbers
    try:
        parsed = phonenumbers.parse(e164)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    except NumberParseException:
        return None


def is_nigerian_mobile(phone: str) -> bool:
    """Check if a phone number is a valid Nigerian mobile number."""
    normalized = normalize_nigerian_number(phone)
    if not normalized:
        return False
    # Check against known mobile prefixes (strip +234 and check with leading 0)
    digits = normalized.replace('+234', '')
    for prefix in NG_MOBILE_PREFIXES:
        # Prefixes have leading 0 (e.g., "0801"), digits from normalization don't
        if digits.startswith(prefix[1:]):  # Remove leading 0 from prefix pattern
            return True
    return False


def format_for_display(phone: str) -> str:
    """Format a normalized E.164 number for display."""
    normalized = normalize_nigerian_number(phone)
    if not normalized:
        return phone
    # Display as 0801 234 5678
    digits = normalized.replace('+234', '')
    if len(digits) == 10:
        return f"0{digits[:4]} {digits[4:7]} {digits[7:]}"
    return normalized


def detect_opt_out_keyword(message: str) -> Optional[str]:
    """
    Detect opt-out keywords in an incoming message.
    Returns the keyword found, or None.
    """
    keywords = ["STOP", "UNSUBSCRIBE", "CANCEL", "REMOVE", "OPT OUT"]
    msg_upper = message.upper().strip()

    for keyword in keywords:
        # Match exact keyword or keyword at start of message
        if msg_upper == keyword or msg_upper.startswith(keyword + " "):
            return keyword
        # Also match common variants
        if re.search(r'\b' + re.escape(keyword) + r'\b', msg_upper):
            return keyword

    return None


def count_sms_segments(message: str) -> Tuple[int, int]:
    """
    Count SMS segments and characters.

    GSM-7: 160 chars per segment, 153 for multipart
    UCS-2: 70 chars per segment, 67 for multipart
    """
    # Check if message contains non-GSM characters
    gsm_chars = set(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    )
    is_gsm = all(c in gsm_chars for c in message)

    char_count = len(message)

    if is_gsm:
        if char_count <= 160:
            return char_count, 1
        return char_count, (char_count + 152) // 153
    else:
        if char_count <= 70:
            return char_count, 1
        return char_count, (char_count + 66) // 67


def normalize_inbound_sender(raw: str) -> Optional[str]:
    """Normalize the sender of an INBOUND message.

    ``normalize_nigerian_number`` returns None for anything that is not a valid
    Nigerian mobile number, which is correct when we choose who to send to, but
    wrong for inbound traffic: banks, delivery services and 2FA providers reply
    from international numbers, short codes (``32665``) and alphanumeric sender
    IDs (``MTN``, ``GTBank``). Dropping those made real replies disappear from
    the inbox, so here we degrade gracefully instead of returning None.

    Order of preference:
      1. valid Nigerian mobile   -> +234...
      2. any valid international -> E.164
      3. numeric short code      -> digits as-is
      4. alphanumeric sender ID  -> uppercased, trimmed to the column width
    """
    if not raw:
        return None

    raw = str(raw).strip()
    if not raw:
        return None

    ng = normalize_nigerian_number(raw)
    if ng:
        return ng

    cleaned = clean_phone_number(raw)

    # Any other valid international number, e.g. +1..., +44...
    if cleaned.startswith("+"):
        try:
            parsed = phonenumbers.parse(cleaned, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
        except NumberParseException:
            pass
        # Keep it anyway if it looks like a plausible E.164 number.
        if 8 <= len(cleaned) - 1 <= 15 and cleaned[1:].isdigit():
            return cleaned

    # Short codes: 3-8 digits, no country code.
    if cleaned.isdigit() and 3 <= len(cleaned) <= 8:
        return cleaned

    # Alphanumeric sender ID (GSM 03.38 allows up to 11 chars).
    alnum = re.sub(r"[^A-Za-z0-9 ._-]", "", raw).strip()
    if alnum:
        return alnum.upper()[:20]

    return None


def phone_search_variants(term: str) -> list:
    """Return the phone fragments to try when a user searches by number.

    Numbers are stored in E.164 (``+2348031112222``), but people type them the
    way they say them: ``08031112222``, or a fragment like ``0803111``. A plain
    ``LIKE %0803111%`` never matches the stored ``+234...`` form, so searching
    by phone silently returned nothing -- the single most common way to look
    someone up.

    Given a numeric term we return every reasonable stored spelling:
      ``08031112222`` -> also try ``8031112222`` / ``2348031112222``
      ``8031112222``  -> also try ``2348031112222``
      ``2348031112222`` / ``+234...`` -> also try the national ``0`` form

    Non-numeric input yields no variants (the caller still does its normal
    name/business search). Fragments are returned bare so the caller can wrap
    them in wildcards.
    """
    if not term:
        return []

    digits = re.sub(r"[^\d]", "", str(term))
    if not digits:
        return []

    variants = {digits}

    # Typed with the national trunk prefix: 0803... -> 803... / 234803...
    if digits.startswith("0") and len(digits) > 1:
        national = digits[1:]
        variants.add(national)
        variants.add(NIGERIA_COUNTRY_CODE + national)
    # Typed with the country code: 234803... -> 803... / 0803...
    elif digits.startswith(NIGERIA_COUNTRY_CODE) and len(digits) > 3:
        national = digits[len(NIGERIA_COUNTRY_CODE):]
        variants.add(national)
        variants.add("0" + national)
    # Typed bare: 803... -> 234803... / 0803...
    else:
        variants.add(NIGERIA_COUNTRY_CODE + digits)
        variants.add("0" + digits)

    return sorted(variants, key=len, reverse=True)
