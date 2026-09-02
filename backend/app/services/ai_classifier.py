"""Keyless, offline AI reply intelligence.

The app needed "AI knowledge" that understands whether an inbound reply is
positive or negative -- e.g. "are you [business name]?" answered with "yes"
(keep going) versus "wrong number" (remove the contact and stop messaging) --
without requiring an API key or any network call.

This module implements that as a deterministic NLP engine: a weighted lexicon
for sentiment plus a set of intent matchers, both tuned for short SMS replies
and Nigerian English / Pidgin ("yes o", "no wahala", "abeg remove me", ...).

It is deliberately self-contained and never touches the network, so it works
on the free tier, offline, and with zero configuration. Every result carries a
confidence value and the raw signals behind it, so the automation engine can
set its own thresholds.
"""

from __future__ import annotations

import re
from typing import Optional

# --------------------------------------------------------------------------
# Sentiment lexicon. Entries are (phrase, weight). Matching is word-boundary
# aware for single words and substring for multi-word phrases.
# --------------------------------------------------------------------------

POSITIVE_WORDS = {
    "yes": 2.0, "yeah": 2.0, "yep": 2.0, "yup": 2.0, "ya": 1.5, "yay": 2.0,
    "sure": 1.5, "ok": 1.0, "okay": 1.0, "okey": 1.0, "k": 0.6, "kk": 1.0,
    "correct": 2.0, "right": 1.0, "confirmed": 2.0, "confirm": 1.0,
    "interested": 2.0, "love": 1.5, "like": 0.8, "great": 1.5, "good": 1.0,
    "nice": 1.0, "awesome": 1.5, "perfect": 1.5, "fine": 1.0, "cool": 1.2,
    "yes please": 2.5, "that's us": 2.5, "thats us": 2.5, "this is us": 2.5,
    "we are": 1.0, "it is": 0.6, "yes o": 2.5, "yes oo": 2.5, "na we": 2.0,
    "na us": 2.0, "yes we are": 3.0, "we are the": 2.0,
    "interested o": 3.0, "am interested": 3.0, "i'm interested": 3.0,
    "i am interested": 3.0, "i dey interested": 3.0, "i dey": 1.0,
    "send": 0.5, "yes send": 2.5, "go ahead": 1.5, "continue": 1.0,
    "will do": 1.0, "sounds good": 2.0, "no problem": 1.0, "no wahala": 1.5,
    "no issues": 1.0, "alright": 1.0, "all good": 1.5, "all set": 1.0,
    "thanks": 0.5, "thank you": 0.5, "appreciate": 0.8,
    "available": 1.0, "open": 0.6,
}

NEGATIVE_WORDS = {
    "no": 1.5, "nope": 2.0, "nah": 2.0, "nope": 2.0, "not": 1.0, "never": 1.2,
    "wrong": 2.0, "wrong number": 3.0, "mistake": 1.5, "who is this": 2.0,
    "who's this": 2.0, "who are you": 2.0, "unknown": 1.0, "stranger": 1.0,
    "don't know you": 2.5, "dont know you": 2.5, "don't know this": 2.0,
    "not interested": 3.0, "not intrested": 3.0, "no interest": 3.0,
    "not me": 2.5, "not us": 2.5, "not ours": 2.5, "that's not": 2.0,
    "thats not": 2.0, "we are not": 2.5, "we're not": 2.5, "this is not": 2.5,
    "this isnt": 2.5, "is not": 2.0, "isn't us": 2.5, "isnt us": 2.5,
    "not the": 2.0, "not this": 2.0, "different": 1.0, "someone else": 2.5,
    "another": 0.8, "other": 0.8, "remove": 2.0, "delete": 1.5, "stop": 2.5,
    "unsubscribe": 2.5, "cancel": 2.0, "opt out": 3.0, "don't text": 3.0,
    "dont text": 3.0, "stop texting": 3.0, "stop messaging": 3.0,
    "leave me": 2.5, "leave me alone": 3.0, "abeg": 0.5, "abeg no": 2.0,
    "abeg stop": 3.0, "abeg remove": 3.0, "wahala": 1.5, "annoying": 2.0,
    "annoyed": 2.0, "angry": 1.5, "bad": 1.0, "terrible": 1.5, "hate": 1.5,
    "not now": 2.0, "later": 0.4, "no longer": 2.0, "no thanks": 2.0,
    "no thank you": 2.0, "not really": 1.5, "nothing": 0.5, "never mind": 1.5,
    "nevermind": 1.5, "leave": 1.2, "quit": 1.2,
}

# Phrase patterns that decide an intent. Ordered: first match wins.
INTENT_PATTERNS: list[tuple[str, str, "re.Pattern[str]"]] = []


def _register(intent: str, *patterns: str) -> None:
    for p in patterns:
        flags = re.IGNORECASE
        INTENT_PATTERNS.append((intent, p, re.compile(p, flags)))


def _init_intents() -> None:
    if INTENT_PATTERNS:
        return
    # Wrong number / not who we think — checked before the generic opt-out so
    # "wrong number, remove me" is recognised as a wrong number first.
    _register("wrong_number",
              r"\bwrong number\b", r"\bwrong no\b", r"\bwho is this\b",
              r"\bwho's this\b", r"\bwho are you\b", r"\bwho be this\b",
              r"\bdont know you\b", r"\bdon't know you\b", r"\bi don't know you\b",
              r"\bnever heard\b", r"\bnot me\b", r"\bnot us\b", r"\bnot ours\b",
              r"\byou have the wrong\b", r"\bwrong person\b")
    # Opt-out / do-not-contact
    _register("opt_out",
              r"\bstop\b", r"\bunsubscribe\b", r"\bcancel\b",
              r"\bopt[\s-]?out\b", r"\bremove me\b", r"\bdelete me\b",
              r"\bdont text\b", r"\bdon't text\b", r"\bstop texting\b",
              r"\bstop messaging\b", r"\bleave me alone\b", r"\babeg stop\b",
              r"\babeg remove\b")
    # Explicit not-interested
    _register("not_interested",
              r"\bnot interested\b", r"\bnot intrested\b", r"\bno interest\b",
              r"\bnot now\b", r"\bno thanks\b", r"\bno thank you\b",
              r"\bam not interested\b", r"\bi'm not interested\b",
              r"\bnot interested o\b")
    # Explicit interested
    _register("interested",
              r"\binterested\b", r"\bam interested\b", r"\bi'm interested\b",
              r"\bi am interested\b", r"\bi dey interested\b", r"\byes please\b",
              r"\bgo ahead\b", r"\bsounds good\b", r"\bsend it\b", r"\bsend me\b")
    # Business confirmation question: "is this X?", "are you the restaurant?"
    _register("confirm_business_question",
              r"\bis this\b", r"\bare you\b", r"\bu be\b", r"\buna be\b",
              r"\bis that\b", r"\bis your\b", r"\bis this the\b",
              r"\bam i speaking\b", r"\bam i talking\b", r"\bis this really\b")
    # Pricing / hours questions
    _register("pricing_question",
              r"\bhow much\b", r"\bprice\b", r"\bprices\b", r"\bpricing\b",
              r"\bcost\b", r"\bfee\b", r"\bfees\b", r"\brate card\b", r"\bcharges\b")
    _register("hours_question",
              r"\bopen\b", r"\bclose\b", r"\bclosing\b", r"\bopening\b",
              r"\bhours\b", r"\bwhat time\b", r"\bwhen do you\b", r"\btoday\b",
              r"\btomorrow\b")
    _register("greeting", r"^\s*(hi|hello|hey|hallo|good\s*(morning|afternoon|evening|day))\b")
    _register("thanks", r"\bthanks\b", r"\bthank you\b", r"\bappreciate\b",
              r"\bgod bless\b", r"\bwell done\b")


_init_intents()

# Confirmation-answer tokens: a reply to "is this X?" that is a bare yes/no.
_POSITIVE_CONFIRM = re.compile(
    r"^(yes|yeah|yep|yup|ya|correct|right|confirmed|true|yes o|yes oo|na we|na us|it is|we are|yes we are|that's us|thats us|this is us|yes please|ok|okay|sure)\b",
    re.IGNORECASE,
)
_NEGATIVE_CONFIRM = re.compile(
    r"^(no|nope|nah|wrong|not me|not us|not ours|not this|no o|no oo|false|no it|it's not|its not|we are not|we're not|this is not|is not|not the|no it's not)\b",
    re.IGNORECASE,
)
# A leading negation turns a would-be positive answer negative.
_LEADING_NEGATION = re.compile(
    r"^\s*(no|not|nah|nope|never|wrong|don't|dont|doesn't|doesnt|isn't|isnt|aren't|arent)\b",
    re.IGNORECASE,
)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def analyze_sentiment(text: str) -> dict:
    """Score a message. Returns (score, positive_hits, negative_hits)."""
    lower = (text or "").lower()
    score = 0.0
    pos_hits: list[str] = []
    neg_hits: list[str] = []

    # Multi-word phrases first (substring match on the lowercased text).
    for phrase, weight in sorted(POSITIVE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if " " in phrase and phrase in lower:
            score += weight
            pos_hits.append(phrase)
    for phrase, weight in sorted(NEGATIVE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if " " in phrase and phrase in lower:
            score -= weight
            neg_hits.append(phrase)

    # Single-word terms, word-boundary aware.
    tokens = set(_tokenize(lower))
    for phrase, weight in POSITIVE_WORDS.items():
        if " " not in phrase and phrase in tokens:
            score += weight
            pos_hits.append(phrase)
    for phrase, weight in NEGATIVE_WORDS.items():
        if " " not in phrase and phrase in tokens:
            score -= weight
            neg_hits.append(phrase)

    # Leading negation flips short confirmations ("no, not interested").
    if _LEADING_NEGATION.match(lower.strip()):
        score -= 1.0

    return {"score": round(score, 2), "positive": pos_hits, "negative": neg_hits}


def _confidence(score: float) -> float:
    """Map a raw score onto a 0..1 confidence that mirrors magnitude."""
    magnitude = abs(score)
    return round(min(0.99, 0.5 + magnitude * 0.14), 2)


def detect_intent(text: str) -> tuple[str, Optional[str]]:
    """Return (intent, matched_pattern) for the first matching pattern."""
    for intent, pattern, rx in INTENT_PATTERNS:
        if rx.search(text or ""):
            return intent, pattern
    return "general", None


def classify(text: str, contact: Optional[object] = None) -> dict:
    """Classify a reply into sentiment + intent + labels + confidence.

    ``contact`` is optional and only used to derive the business name for the
    business-confirmation question signal.
    """
    message = (text or "").strip()
    sentiment = analyze_sentiment(message)
    intent, matched = detect_intent(message)

    is_business_question = intent == "confirm_business_question"

    # Is this an answer (yes/no) to a business-confirmation question?
    confirm_yes = bool(_POSITIVE_CONFIRM.match(message))
    confirm_no = bool(_NEGATIVE_CONFIRM.match(message))

    # Derive the final sentiment label.
    score = sentiment["score"]
    if intent == "opt_out" or intent == "wrong_number":
        label = "negative"
    elif intent == "not_interested":
        label = "negative"
    elif intent in ("interested", "thanks"):
        label = "positive"
    elif confirm_no:
        label = "negative"
    elif confirm_yes:
        label = "positive"
    elif score >= 1.0:
        label = "positive"
    elif score <= -1.0:
        label = "negative"
    else:
        label = "neutral"

    # Labels consumed by the automation engine.
    labels: list[str] = []
    if confirm_yes:
        labels.append("yes")
    if confirm_no:
        labels.append("no")
    labels.append(label)
    if intent != "general":
        labels.append(intent)
    if is_business_question:
        labels.append("business_question")

    business_name = ""
    if contact is not None:
        business_name = (getattr(contact, "business_name", None) or "").strip()

    return {
        "sentiment": label,
        "score": score,
        "confidence": _confidence(score),
        "intent": intent,
        "matched_pattern": matched,
        "is_business_question": is_business_question,
        "confirm_yes": confirm_yes,
        "confirm_no": confirm_no,
        "positive_hits": sentiment["positive"][:10],
        "negative_hits": sentiment["negative"][:10],
        "labels": labels,
        "business_name": business_name,
        "suggested_action": _suggest(label, intent, confirm_yes, confirm_no),
    }


def _suggest(label: str, intent: str, confirm_yes: bool, confirm_no: bool) -> str:
    """A human-readable suggestion for the UI / automation templates."""
    if intent == "opt_out":
        return "opt_out"
    if intent == "wrong_number":
        return "remove_contact"
    if intent == "not_interested" or confirm_no or label == "negative":
        return "stop_and_remove"
    if intent == "interested" or confirm_yes or label == "positive":
        return "continue_or_send_next"
    if intent == "pricing_question":
        return "send_pricing"
    if intent == "hours_question":
        return "send_hours"
    return "none"
