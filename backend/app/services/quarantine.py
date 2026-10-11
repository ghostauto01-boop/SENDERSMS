"""Quarantine the pattern-generated addresses: tag them, never delete them.

An address the enrichment pipeline GUESSED (``first.last@domain``, ``info@domain``; ``email_source =
"inferred"``) was never shown to exist. The send gate already holds such addresses back (``inferred_unverified``);
this makes it visible and filterable by giving each one the tags ``pattern_generated`` and ``sendable:false``.
A dry run (the default) counts and changes nothing; applying it is idempotent; no contact is ever removed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact, ContactTag, Tag

GENERATED_TAG = "pattern_generated"
NOT_SENDABLE_TAG = "sendable:false"
TAGS = (GENERATED_TAG, NOT_SENDABLE_TAG)

#: Local parts that name a role, not a person. Reported (not tagged): a role address someone typed or
#: imported is a real address; it is only a guess when the pipeline invented it.
ROLE_LOCALS = frozenset({
    "info", "sales", "contact", "hello", "hi", "admin", "support", "office", "enquiries", "enquiry", "mail",
    "team", "hr", "accounts", "accounting", "billing", "marketing", "service", "customerservice", "help",
    "careers", "jobs", "press", "media", "webmaster", "postmaster", "noreply", "no-reply", "reception",
})


async def _tag(db: AsyncSession, name: str) -> Tag:
    tag = (await db.execute(select(Tag).where(Tag.name == name))).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name)
        db.add(tag)
        await db.flush()
    return tag


async def quarantine_generated(db: AsyncSession, *, dry_run: bool = True) -> dict:
    guessed = (
        await db.execute(
            select(Contact.id, Contact.email).where(
                Contact.email_source == "inferred", Contact.email.isnot(None)
            ).order_by(Contact.id)
        )
    ).all()
    ids = [row.id for row in guessed]

    have: dict[str, set[int]] = {name: set() for name in TAGS}
    if ids:
        rows = await db.execute(
            select(ContactTag.contact_id, Tag.name)
            .join(Tag, ContactTag.tag_id == Tag.id)
            .where(Tag.name.in_(TAGS), ContactTag.contact_id.in_(ids))
        )
        for contact_id, name in rows.all():
            have[name].add(contact_id)

    fully_tagged = have[GENERATED_TAG] & have[NOT_SENDABLE_TAG]
    needs = [cid for cid in ids if cid not in fully_tagged]
    tagged = 0
    if needs and not dry_run:
        for name in TAGS:
            tag = await _tag(db, name)
            for cid in needs:
                if cid not in have[name]:
                    db.add(ContactTag(contact_id=cid, tag_id=tag.id))
        await db.flush()
        tagged = len(needs)

    unverified = (
        await db.execute(
            select(Contact.email).where(Contact.email.isnot(None), Contact.email_verified.is_(False))
        )
    ).scalars().all()
    role_unverified = sum(1 for e in unverified if (e or "").split("@")[0].strip().lower() in ROLE_LOCALS)
    return {
        "dry_run": dry_run,
        "matched": len(ids),
        "tagged": tagged,
        "already_tagged": len(fully_tagged),
        "tags": list(TAGS),
        "role_mailboxes_unverified": role_unverified,
        "examples": [row.email for row in guessed[:5]],
        "note": (
            "Dry run: nothing was changed. Repeat with dry_run=false to tag them."
            if dry_run else
            "Tagged; no contact was deleted. They stay unsendable until a verifier confirms them AND the "
            "guessed-address rule is lifted (EMAIL_SEND_INFERRED)."
        ),
    }
