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
