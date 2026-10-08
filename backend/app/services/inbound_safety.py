"""Privacy-first screening for clearly sensitive inbound SMS.

The detector is intentionally conservative: ordinary replies remain in the
inbox, while recognizable OTP/PIN and payment-account material is suppressed
before it can create a contact, conversation, message, webhook payload copy,
or log line. The caller may retain only the coarse reason and provider IDs.
"""

from __future__ import annotations

import re


_OTP_CONTEXT = re.compile(
    r"\b(?:otp|one[ -]?time (?:password|passcode|code)|verification code|"
    r"security code|passcode|pin)\b",
    re.IGNORECASE,
)
_CODE_WITH_CONTEXT = re.compile(
    r"\b(?:otp|one[ -]?time (?:password|passcode|code)|verification code|"
    r"security code|passcode|pin)\b[^\d]{0,24}\d{4,8}\b|"
    r"\b\d{4,8}\b[^\w]{0,24}\b(?:otp|one[ -]?time (?:password|passcode|code)|"
    r"verification code|security code|passcode|pin)\b",
    re.IGNORECASE,
)
_ACCOUNT_NUMBER = re.compile(
    r"\b(?:bank\s+)?account\s*(?:number|no\.?|#)\s*[:=-]?\s*\d(?:[ -]?\d){7,19}\b",
    re.IGNORECASE,
)
_CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_CARD_CONTEXT = re.compile(r"\b(?:credit|debit|payment|card|cvv|cvc|expiry|expiration)\b", re.I)


def _passes_luhn(number: str) -> bool:
    digits = [int(char) for char in number if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def sensitive_inbound_reason(text: str | None) -> str | None:
    """Return a non-sensitive category for high-confidence OTP/payment text."""
    value = (text or "").strip()
    if not value:
        return None
    if _CODE_WITH_CONTEXT.search(value):
        return "one_time_code"
    if _ACCOUNT_NUMBER.search(value):
        return "bank_account_number"
    for match in _CARD_NUMBER.finditer(value):
        if _passes_luhn(match.group(0)) or _CARD_CONTEXT.search(value):
            return "payment_card_number"
    # A lone explicit CVV/CVC plus a 3–4 digit value is sensitive even when no
    # full card number was included in the same text.
    if re.search(r"\b(?:cvv|cvc|card security code)\b[^\d]{0,16}\d{3,4}\b", value, re.I):
        return "payment_security_code"
    # Mentioning a code/PIN without digits is not enough to suppress a reply;
    # only redact when the message looks like a credential disclosure.
    if _OTP_CONTEXT.search(value) and re.search(r"\b(?:is|:|=)\s*\d{4,8}\b", value, re.I):
        return "one_time_code"
    return None
