"""Deterministic, local answers for common workspace-count questions.

These answers query the app database directly rather than paying for a model to
choose a counting tool and then summarize its result. The LLM remains the route
for follow-up analysis and everything not matched with high confidence.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


_COUNT_QUESTION = re.compile(r"\b(how many|number of|count of|total number of)\b", re.I)
_CONTACT_WORD = re.compile(r"\b(contacts?|people|recipients?|customers?|clients?|leads?|subscribers?)\b", re.I)
_OUTGOING_WORD = re.compile(
    r"\b(send|sends|sent|sending|message|messages|messaged|text|texts|texted|email|emailed|reach|reached|contacted)\b",
    re.I,
)
_INCOMING_PHRASE = re.compile(
    r"\b(?:messaged|texted|emailed|contacted|replied to)\s+(?:me|us)\b|"
    r"\b(?:who|people|contacts?)\s+(?:sent|messaged|texted|emailed)\s+(?:me|us)\b",
    re.I,
)


def _message_audience_intent(text: str) -> bool:
    query = (text or "").strip()
    lower = query.lower()
    if not _COUNT_QUESTION.search(query) or not _CONTACT_WORD.search(query):
        return False
    if (
        not _OUTGOING_WORD.search(query)
        or _INCOMING_PHRASE.search(query)
        or re.search(r"\b(opted? out|unsubscribed|unsubscribes)\b", lower)
    ):
        return False

    # Be conservative: only skip the model when the wording clearly asks about
    # the operator's outbound activity or a passive outbound-send count.
    first_person_send = re.search(
        r"\b(?:did|have|has|do|can)\s+(?:i|we)\s+(?:send|sent|message|messaged|text|texted|email|emailed|reach|contact|contacted)\b",
        lower,
    )
    owned_activity = re.search(
        r"\b(?:my|our|the app|this workspace)\s+(?:outbound\s+)?(?:messages?|sends?|campaigns?)\b",
        lower,
    )
    passive_send = re.search(
        r"\b(?:were|have been|has been|got|received)\s+(?:successfully\s+)?(?:sent|messaged|texted|emailed|reached)\b",
        lower,
    )
    direct_send_to = re.search(r"\b(?:sent|messaged|texted|emailed)\b.{0,35}\bto\b", lower)
    return bool(first_person_send or owned_activity or passive_send or direct_send_to)


def _scope(text: str) -> Optional[tuple[Optional[str], Optional[int], str]]:
    lower = (text or "").lower()
    asks_sms = bool(re.search(r"\b(sms|texts?|phone messages?)\b", lower))
    asks_email = bool(re.search(r"\bemails?\b", lower))
    if asks_sms and not asks_email:
        channel = "sms"
        channel_label = "SMS"
    elif asks_email and not asks_sms:
        channel = "email"
        channel_label = "email"
    else:
        channel = None
        channel_label = "SMS and email"

    period = re.search(r"\b(?:last|past|previous)\s+(\d{1,4})\s+(day|week|month|year)s?\b", lower)
    if period:
        amount = int(period.group(1))
        unit = period.group(2)
        factor = {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        days = max(1, min(amount * factor, 3650))
        period_label = f"the last {days} day{'s' if days != 1 else ''}"
    else:
        if re.search(
            r"\b(today|yesterday|this (?:week|month|year|quarter)|(?:last|previous|past) (?:week|month|year|quarter)|since|before|after|between)\b",
            lower,
        ):
            # Do not quietly turn a calendar period or date range into an all-time count.
            return None
        days = None
        period_label = "all time"
    return channel, days, f"{channel_label} · {period_label}"


async def _fast_message_audience_answer(db: AsyncSession, text: str) -> Optional[str]:
    from app.services.message_metrics import message_audience_counts

    scope = _scope(text)
    if scope is None:
        return None
    channel, days, scope_label = scope
    stats = await message_audience_counts(db, channel=channel, days=days)
    contacts = stats["contacts_sent"]
    messages = stats["messages_sent"]
    if contacts == 0:
        return f"I found no successfully sent or delivered {scope_label.split(' · ')[0]} messages in {scope_label.split(' · ')[1]}."

    if channel:
        channel_stats = stats["by_channel"][channel]
        return (
            f"You've successfully sent {channel_stats['messages_sent']:,} {channel.upper() if channel == 'sms' else 'email'} "
            f"messages to {contacts:,} unique contacts ({scope_label.split(' · ')[1]}). "
            "Only messages marked sent or delivered are counted; queued and failed attempts are excluded."
        )

    sms = stats["by_channel"]["sms"]["contacts_sent"]
    email = stats["by_channel"]["email"]["contacts_sent"]
    return (
        f"You've successfully sent {messages:,} messages to {contacts:,} unique contacts across SMS and email "
        f"({scope_label.split(' · ')[1]}): {sms:,} contacts by SMS and {email:,} by email. "
        "A contact reached on both channels is counted once in the total. Only messages marked sent or delivered count."
    )


async def _fast_contact_count_answer(db: AsyncSession, text: str) -> Optional[str]:
    lower = (text or "").lower()
    if not _COUNT_QUESTION.search(text or "") or not _CONTACT_WORD.search(text or ""):
        return None

    opted_question = bool(re.search(r"\b(opted? out|unsubscribed|unsubscribes)\b", lower))
    outgoing_activity = re.search(
        r"\b(send|sends|sent|sending|message|messages|messaged|text|texts|texted|emailed|reach|reached|contacted)\b|"
        r"\b(?:did|have|has|do|can)\s+(?:i|we)\s+email\b",
        lower,
    )
    email_opt_out = re.search(
        r"\b(?:opted? out of|unsubscribed from)\s+email\b|\bemail\s+(?:opted? out|unsubscribed)\b",
        lower,
    )
    if outgoing_activity or (re.search(r"\bemail\b", lower) and not (opted_question and email_opt_out)):
        return None

    workspace_question = bool(
        re.search(r"\b(do i have|in (?:my|the) app|in my workspace|total contacts|all contacts|my contacts)\b", lower)
    )
    asks_for_both = bool(
        re.search(r"\b(and|plus|also)\b.{0,60}\b(opted? out|unsubscribed|unsubscribes)\b", lower)
    )
    if opted_question and workspace_question and not asks_for_both:
        workspace_question = False
    specific_scope = bool(
        re.search(r"\b(lists?|audiences?|segments?|tags?|city|state|country|campaign|from|within|near)\b", lower)
        or re.search(
            r"\bin\s+(?!my\s+app\b|the\s+app\b|my\s+workspace\b|this\s+workspace\b)[a-z]",
            lower,
        )
    )
    if specific_scope or (not opted_question and not workspace_question):
        return None
    if not opted_question and re.search(r"\b(sms|email|phone|reachable|subscribed|active)\b", lower):
        return None

    from app.models.contact import Contact

    total = int((await db.execute(select(func.count(Contact.id)))).scalar() or 0)
    sms_opted_out = int(
        (await db.execute(select(func.count(Contact.id)).where(Contact.is_opted_out.is_(True)))).scalar() or 0
    )
    email_opted_out = int(
        (await db.execute(select(func.count(Contact.id)).where(Contact.is_email_opted_out.is_(True)))).scalar() or 0
    )
    if opted_question and not workspace_question:
        if re.search(r"\b(email|unsubscribed)\b", lower) and not re.search(r"\bsms\b", lower):
            return f"{email_opted_out:,} contacts are opted out of email."
        if re.search(r"\bsms\b", lower) and not re.search(r"\bemail\b", lower):
            return f"{sms_opted_out:,} contacts are opted out of SMS."
        return f"{sms_opted_out:,} contacts have opted out of SMS; {email_opted_out:,} have opted out of email."

    return (
        f"Your workspace has {total:,} contacts. {sms_opted_out:,} have opted out of SMS and "
        f"{email_opted_out:,} have opted out of email."
    )


async def fast_answer(db: AsyncSession, text: str) -> Optional[str]:
    """Return a local answer only when the question has a clear, exact intent."""
    if _message_audience_intent(text):
        return await _fast_message_audience_answer(db, text)
    return await _fast_contact_count_answer(db, text)
