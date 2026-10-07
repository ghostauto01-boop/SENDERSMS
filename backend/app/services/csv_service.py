"""CSV contact import with automatic standard and custom-field mapping."""

import csv
import io
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact, ContactTag, Tag
from app.models.contact_list import ContactList, ContactListMember
from app.utils.contact_identity import (
    clean_phone,
    contact_key,
    normalize_email,
)

logger = logging.getLogger(__name__)

# Fields that can safely be populated directly on Contact during an import.
CONTACT_FIELDS = {
    "first_name", "last_name", "business_name", "phone_number", "email",
    "city", "state", "country", "website", "industry", "source",
    "lead_status", "notes",
}

# Special target: a "tags" CSV column (or the per-import tag box) attaches
# one-or-more tags to every imported contact, comma or semicolon separated.
TAGS_FIELD = "tags"


def extract_emails(raw: str | None, limit: int = 5) -> list[str]:
    """Every usable address inside one cell, best-first, deduplicated.

    A single ``email`` cell routinely holds more than one address — Excel and
    Google Contacts export ``Ada <ada@x.com>``, and outreach lists arrive as
    ``ada@x.com; billing@x.com``. Splitting the cell keeps the second address
    (parked on the contact for enrichment) instead of storing a malformed blob
    that no send path could ever use.
    """
    if not raw:
        return []
    found: list[str] = []
    for token in re.split(r"[;,\s]+", str(raw)):
        address = normalize_email(token)
        if address and address not in found:
            found.append(address)
        if len(found) >= limit:
            break
    if not found:
        # Fall back to a straight normalisation so a single unpunctuated
        # address ("hello@x.com") still comes through.
        single = normalize_email(raw)
        if single:
            found.append(single)
    return found


def split_tags(raw: str) -> list[str]:
    """Split a tags value into clean, deduplicated, trimmed tag names."""
    if not raw:
        return []
    parts = re.split(r"[;,|]", str(raw))
    seen: list[str] = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in seen and len(tag) <= 100:
            seen.append(tag)
    return seen

# Header aliases -> Contact field. Everything not recognized here is still
# imported automatically into Contact.custom_fields instead of being dropped.
HEADER_ALIASES = {
    "phone_number": ("phone", "phone_number", "phonenumber", "phone number", "phone no",
                     "phone_no", "mobile", "mobile number", "mobile_number", "mobile no",
                     "mobile_no", "tel", "telephone", "telephone number", "contact",
                     "contact number", "number", "cell", "cell phone", "sms number"),
    "first_name": ("first_name", "firstname", "first name", "fname", "given name",
                   "given_name", "name", "full name", "fullname", "customer name",
                   "customer_name", "contact name", "contact_name", "contact person",
                   "contact_person", "client name", "client_name"),
    "last_name": ("last_name", "lastname", "last name", "lname", "surname", "family name",
                  "family_name"),
    "business_name": ("business_name", "businessname", "business name", "business", "company",
                      "company name", "brand", "brand name", "brand_name", "organization",
                      "organisation", "restaurant", "restaurant name", "shop", "store"),
    "email": ("email", "e-mail", "mail", "email address", "email_address",
              "email id", "emailid", "email 1", "primary email", "contact email",
              "business email", "work email", "e mail address", "mail id"),
    "city": ("city", "town"),
    "state": ("state", "region", "province"),
    "country": ("country", "nation"),
    "website": ("website", "web", "url", "site", "web site"),
    "industry": ("industry", "sector", "category"),
    "source": ("source", "channel", "origin"),
    "lead_status": ("lead status", "lead_status", "status", "pipeline status"),
    "notes": ("notes", "note", "comments", "comment"),
    "tags": ("tags", "tag", "label", "labels", "groups", "group"),
}


def custom_field_key(header: str) -> str:
    """Turn an arbitrary CSV heading into a template-safe identifier.

    For example ``Pain Point`` becomes ``pain_point``, usable as
    ``{{pain_point}}``. Unicode headings are retained where possible, while the
    ASCII identifier rule used by templates is respected.
    """
    value = unicodedata.normalize("NFKD", str(header)).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    if not value:
        value = "custom_field"
    if value[0].isdigit():
        value = f"field_{value}"
    return value


def detect_column_mapping(headers: list[str]) -> dict[str, str]:
    """Map every CSV header to a Contact field or ``custom:<key>``.

    Keys in the returned mapping are normalized raw headers because import rows
    are matched case-insensitively. Unknown columns are never silently lost.
    Duplicate custom identifiers receive a stable numeric suffix.
    """
    mapping: dict[str, str] = {}
    used_custom: set[str] = set()
    for header in headers:
        raw_key = str(header).strip().lower()
        if not raw_key:
            continue
        target = None
        for field, aliases in HEADER_ALIASES.items():
            if raw_key in aliases:
                target = field
                break
        if target is None:
            base = custom_field_key(str(header))
            key = base
            suffix = 2
            while key in used_custom:
                key = f"{base}_{suffix}"
                suffix += 1
            used_custom.add(key)
            target = f"custom:{key}"
        mapping[raw_key] = target
    return mapping


class CSVImportResult:
    def __init__(self):
        self.imported = 0
        self.skipped = 0
        self.invalid = 0
        self.duplicates = 0
        #: Rows that matched an existing contact and improved it instead of
        #: being thrown away — the "perfect merge" count. Kept separate from
        #: ``duplicates`` so the UI can say "3 contacts updated" rather than
        #: reporting a merge as a rejected duplicate.
        self.merged = 0
        #: Subset of ``merged`` that matched on name alone (no shared phone or
        #: address). Surfaced separately so an unexpected merge is visible.
        self.merged_by_name = 0
        #: How many imported/merged rows ended up with each channel. This is
        #: what tells the operator "412 of your 500 rows are email-only, so
        #: they will never receive an SMS" before they start a campaign.
        self.with_phone = 0
        self.with_email = 0
        self.email_only = 0
        self.phone_only = 0
        self.both = 0
        self.total_rows = 0
        self.errors: list[dict] = []
        self.imported_contact_ids: list[int] = []

    def note_channels(self, phone: str | None, email: str | None) -> None:
        """Record which channels a row produced."""
        has_phone = bool(phone)
        has_email = bool(email)
        if has_phone:
            self.with_phone += 1
        if has_email:
            self.with_email += 1
        if has_phone and has_email:
            self.both += 1
        elif has_email:
            self.email_only += 1
        elif has_phone:
            self.phone_only += 1

    def as_dict(self) -> dict:
        return {
            "imported": self.imported,
            "merged": self.merged,
            "merged_by_name": self.merged_by_name,
            "skipped": self.skipped,
            "invalid": self.invalid,
            "duplicates": self.duplicates,
            "total_rows": self.total_rows,
            "with_phone": self.with_phone,
            "with_email": self.with_email,
            "email_only": self.email_only,
            "phone_only": self.phone_only,
            "both": self.both,
            "errors": self.errors,
            "imported_ids": self.imported_contact_ids,
        }


class CSVImportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _find_existing(
        self,
        phone: str | None,
        email: str | None,
        contact_data: dict | None = None,
        *,
        allow_name_match: bool = True,
    ) -> tuple[Contact | None, str]:
        """The contact this row belongs to, and how it was matched.

        Checked in order of certainty:

        1. **Phone** — a number is the strongest identifier; if it matches, it
           is the same subscriber.
        2. **Email** — likewise for the email side.
        3. **Name** — the cross-file case that has nothing else in common. A
           phone-only file and an email-only file for the same people share no
           phone and no address, so without this the operator ends up with two
           half-empty rows per person ("3 contacts imported" for 3 people who
           are already in the CRM).

        Step 3 is deliberately timid, because merging two different people is
        hard to notice and harder to undo. It only fires when the name matches
        exactly, on a full name (first **and** last, or a business name), and
        exactly ONE existing contact matches, and that contact is missing the
        thing this row supplies. Anything ambiguous imports as a new contact
        instead of silently absorbing somebody else's record.
        """
        if phone:
            found = (
                await self.db.execute(select(Contact).where(Contact.phone_number == phone))
            ).scalar_one_or_none()
            if found is not None:
                return found, "phone"

        if email:
            query = select(Contact).where(func.lower(Contact.email) == email)
            found = (await self.db.execute(query.limit(1))).scalars().first()
            if found is not None:
                return found, "email"
            # An address parked as a secondary/alias still identifies the
            # contact — otherwise a re-import creates a duplicate of somebody
            # whose reply alias we just learned.
            try:
                from app.models.email_inbox import EmailContactAddress

                alias = (
                    await self.db.execute(
                        select(Contact)
                        .join(EmailContactAddress, EmailContactAddress.contact_id == Contact.id)
                        .where(func.lower(EmailContactAddress.email_address) == email)
                        .limit(1)
                    )
                ).scalars().first()
                if alias is not None:
                    return alias, "email_alias"
            except Exception:
                pass

        if not allow_name_match or not contact_data:
            return None, ""

        first = (contact_data.get("first_name") or "").strip()
        last = (contact_data.get("last_name") or "").strip()
        business = (contact_data.get("business_name") or "").strip()

        candidates: list[Contact] = []
        if first and last:
            if phone:
                # This row has a number the CRM does not know about yet, so we
                # are filling in an email-only record.
                query = select(Contact).where(
                    func.lower(Contact.first_name) == first.lower(),
                    func.lower(Contact.last_name) == last.lower(),
                    Contact.email.isnot(None),
                    or_(Contact.phone_number.is_(None), Contact.phone_number == ""),
                )
            else:
                # Email-only row filling in the address of a phone-only record.
                query = select(Contact).where(
                    func.lower(Contact.first_name) == first.lower(),
                    func.lower(Contact.last_name) == last.lower(),
                    or_(Contact.email.is_(None), Contact.email == ""),
                )
            candidates = list((await self.db.execute(query.limit(2))).scalars().all())
        elif business:
            if phone:
                query = select(Contact).where(
                    func.lower(Contact.business_name) == business.lower(),
                    Contact.email.isnot(None),
                    or_(Contact.phone_number.is_(None), Contact.phone_number == ""),
                )
            else:
                query = select(Contact).where(
                    func.lower(Contact.business_name) == business.lower(),
                    or_(Contact.email.is_(None), Contact.email == ""),
                )
            candidates = list((await self.db.execute(query.limit(2))).scalars().all())

        if len(candidates) == 1:
            return candidates[0], "name"
        return None, ""

    async def _merge_into(
        self,
        contact: Contact,
        contact_data: dict,
        custom_data: dict[str, str],
        phone: str | None,
        email: str | None,
    ) -> bool:
        """Fold a re-imported row into an existing contact. True if it changed.

        Deliberately additive: a blank cell never clears a populated field, so
        importing a file that only lists numbers cannot wipe the addresses
        already on file (which is what "fix typos in a column and re-upload"
        would otherwise do).
        """
        changed = False

        def _fill(attr: str, value) -> None:
            nonlocal changed
            if value in (None, "", []):
                return
            if getattr(contact, attr, None) in (None, "", []):
                setattr(contact, attr, value)
                changed = True

        # A newly supplied email on a phone-only contact — the headline case.
        if email and email != normalize_email(contact.email):
            if not contact.email:
                contact.email = email
                contact.email_lower = email
                contact.email_source = contact.email_source or "csv"
                changed = True
            else:
                # Already has a different primary: keep it, remember the new
                # one as a secondary so a reply from it is still recognised.
                from app.services.email_enrichment import store_secondary_email

                if await store_secondary_email(self.db, contact, email):
                    changed = True
        # And the reverse: a number supplied for an email-only contact.
        if phone and not contact.phone_number:
            contact.phone_number = phone
            changed = True

        for attr, value in contact_data.items():
            if attr in ("phone_number", "email", "email_lower"):
                continue
            _fill(attr, value)

        merged_custom: dict = {}
        try:
            parsed = json.loads(contact.custom_fields or "{}")
            merged_custom = parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            merged_custom = {}
        for key, value in (custom_data or {}).items():
            if value in (None, "") or merged_custom.get(key):
                continue
            merged_custom[key] = value
            changed = True
        if merged_custom:
            contact.custom_fields = json.dumps(merged_custom, ensure_ascii=False)

        if changed:
            contact.updated_at = datetime.now(timezone.utc)
            await self.db.flush()
        return changed

    async def preview_csv(self, content: bytes, max_rows: int = 20) -> dict:
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return {"error": "No headers found in CSV", "headers": [], "rows": [], "total_rows": 0}
        headers = [h.strip() for h in reader.fieldnames]
        mapping = detect_column_mapping(headers)
        rows, row_count = [], 0
        # Channel tallies for the WHOLE file, so the preview can say "this file
        # is 90% email-only, SMS campaigns will not reach it" before an import
        # is started rather than after.
        channels = {"phone": 0, "email": 0, "both": 0, "email_only": 0, "phone_only": 0, "neither": 0}
        for row in reader:
            row_count += 1
            clean_row = {(k or "").strip(): v for k, v in row.items()}
            if len(rows) < max_rows:
                rows.append(clean_row)
            lower = {k.lower(): (v or "") for k, v in clean_row.items()}
            has_phone = False
            has_email = False
            for header, target in mapping.items():
                value = lower.get(header, "")
                if not value:
                    continue
                if target == "phone_number" and clean_phone(value):
                    has_phone = True
                elif target == "email" and normalize_email(value):
                    has_email = True
            if has_phone:
                channels["phone"] += 1
            if has_email:
                channels["email"] += 1
            if has_phone and has_email:
                channels["both"] += 1
            elif has_email:
                channels["email_only"] += 1
            elif has_phone:
                channels["phone_only"] += 1
            else:
                channels["neither"] += 1
        return {"headers": headers, "rows": rows, "total_rows": row_count,
                "column_mapping": mapping, "channels": channels,
                "has_phone_column": "phone_number" in mapping.values(),
                "has_email_column": "email" in mapping.values()}

    async def validate_and_import(
        self,
        content: bytes,
        column_mapping: dict[str, str],
        list_id: Optional[int] = None,
        skip_duplicates: bool = True,
        tags: Optional[list[str]] = None,
    ) -> CSVImportResult:
        result = CSVImportResult()
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            result.errors.append({"row": 0, "error": "No headers found"})
            return result

        mapping = {str(k).strip().lower(): str(v).strip() for k, v in (column_mapping or {}).items()}
        contact_list = None
        if list_id is not None:
            contact_list = (await self.db.execute(
                select(ContactList).where(ContactList.id == list_id)
            )).scalar_one_or_none()

        # Tags applied to every imported contact (the import modal's tag box).
        global_tags = split_tags(", ".join(tags or [])) if tags else []

        # Find-or-create tag rows once per import, keyed by lowercased name.
        existing_tags = (await self.db.execute(select(Tag))).scalars().all()
        tag_cache: dict[str, Tag] = {t.name.lower(): t for t in existing_tags}

        async def _ensure_tag(name: str) -> Tag:
            key = name.lower()
            if key in tag_cache:
                return tag_cache[key]
            tag = Tag(name=name)
            self.db.add(tag)
            await self.db.flush()
            tag_cache[key] = tag
            return tag

        # Dedupe keys already seen in THIS file: "p:+234…" for phones and
        # "e:ada@example.com" for email-only contacts, so a mixed file with a
        # phone-only and an email-only entry for the same row set still merges.
        seen_keys: set[str] = set()
        added_to_list = 0
        for row_num, row in enumerate(reader, start=1):
            result.total_rows += 1
            row_lower = {str(k or "").strip().lower(): v for k, v in row.items()}
            contact_data: dict = {}
            custom_data: dict[str, str] = {}
            row_tags: list[str] = []

            for csv_col, target in mapping.items():
                value = (row_lower.get(csv_col) or "").strip()
                if not value or not target or target == "ignore":
                    continue
                if target in CONTACT_FIELDS:
                    if target == "email":
                        # An address cell can hold "Ada <ada@x.com>; billing@x.com".
                        # The first usable one is the primary; the rest are kept
                        # as custom fields so enrichment can merge them as
                        # secondary addresses instead of losing them.
                        addresses = extract_emails(value)
                        if addresses:
                            contact_data["email"] = addresses[0]
                            if len(addresses) > 1:
                                custom_data["additional_emails"] = ", ".join(addresses[1:])
                    else:
                        contact_data[target] = value
                elif target == TAGS_FIELD:
                    row_tags.extend(split_tags(value))
                elif target.startswith("custom:"):
                    key = custom_field_key(target.split(":", 1)[1])
                    # A direct Contact field always wins if a custom heading
                    # happens to normalize to the same name.
                    if key not in CONTACT_FIELDS:
                        custom_data[key] = value

            # --- Identity: a contact needs a phone OR an email ---------------
            # Not both. Many businesses publish only an address (a restaurant
            # with a reservation inbox and no mobile) and many leads arrive
            # with only a number, so requiring a phone silently threw away
            # every email-only row in the file.
            normalized = clean_phone(contact_data.get("phone_number"))
            email = normalize_email(contact_data.get("email"))
            raw_phone = (contact_data.get("phone_number") or "").strip()

            if raw_phone and not normalized:
                # A phone cell that is present but unusable. That is only fatal
                # when there is no address to fall back on; otherwise the row
                # is imported as an email contact and the bad number is
                # reported so it can be fixed at source.
                result.errors.append({
                    "row": row_num,
                    "error": f"Phone number skipped (not a valid Nigerian mobile): {raw_phone}",
                    "level": "warning",
                })
            if not normalized and not email:
                result.invalid += 1
                result.errors.append({
                    "row": row_num,
                    "error": "No phone number and no email address"
                    + (f" (phone '{raw_phone}' is not valid)" if raw_phone else ""),
                })
                continue

            key = contact_key(normalized, email)
            if key is None:  # pragma: no cover - guarded by the check above
                result.invalid += 1
                result.errors.append({"row": row_num, "error": "Row has no usable contact detail"})
                continue

            if skip_duplicates:
                if key in seen_keys:
                    result.duplicates += 1
                    continue
                existing_contact, matched_by = await self._find_existing(
                    normalized, email, contact_data
                )
                if existing_contact is not None:
                    if matched_by == "name":
                        # Say so out loud: a name match is the one merge the
                        # operator may want to double-check.
                        result.merged_by_name += 1
                        result.errors.append({
                            "row": row_num,
                            "level": "info",
                            "error": (
                                "Merged into existing contact #"
                                f"{existing_contact.id} by name match"
                            ),
                        })
                    # MERGE, do not discard. The row's new facts are folded into
                    # the record that is already there (an email-only re-import
                    # fills in the address of a phone-only contact, and vice
                    # versa) so a second file cannot split one person in two.
                    merged = await self._merge_into(
                        existing_contact, contact_data, custom_data, normalized, email
                    )
                    result.merged += 1 if merged else 0
                    result.duplicates += 0 if merged else 1
                    result.note_channels(
                        existing_contact.phone_number, existing_contact.email
                    )
                    # A merged row still belongs in the destination list — the
                    # operator asked for "these people in this list", and an
                    # existing contact is one of those people.
                    if contact_list is not None and existing_contact.id is not None:
                        already = (
                            await self.db.execute(
                                select(ContactListMember.id).where(
                                    ContactListMember.list_id == contact_list.id,
                                    ContactListMember.contact_id == existing_contact.id,
                                )
                            )
                        ).scalar_one_or_none()
                        if already is None:
                            self.db.add(
                                ContactListMember(
                                    list_id=contact_list.id,
                                    contact_id=existing_contact.id,
                                )
                            )
                            added_to_list += 1
                    # Tags from this file are additive too.
                    for tag_name in [*global_tags, *row_tags]:
                        tag = await _ensure_tag(tag_name)
                        link = (
                            await self.db.execute(
                                select(ContactTag.id).where(
                                    ContactTag.contact_id == existing_contact.id,
                                    ContactTag.tag_id == tag.id,
                                )
                            )
                        ).scalar_one_or_none()
                        if link is None:
                            self.db.add(
                                ContactTag(contact_id=existing_contact.id, tag_id=tag.id)
                            )
                    continue

            seen_keys.add(key)
            if normalized:
                contact_data["phone_number"] = normalized
            else:
                contact_data.pop("phone_number", None)
            if email:
                contact_data["email"] = email
            contact_data.setdefault("country", "Nigeria")
            contact_data.setdefault("lead_status", "new")
            if contact_data.get("email"):
                contact_data["email_lower"] = contact_data["email"]
                # Source is "csv": a human supplied it. Enrichment must never
                # overwrite that provenance when it verifies the address later.
                contact_data.setdefault("email_source", "csv")
            if custom_data:
                contact_data["custom_fields"] = json.dumps(custom_data, ensure_ascii=False)

            contact = Contact(**contact_data)
            self.db.add(contact)
            await self.db.flush()
            result.imported += 1
            result.imported_contact_ids.append(contact.id)
            result.note_channels(contact.phone_number, contact.email)

            # Attach tags (per-row column values + the import-wide tag box).
            applied: set[str] = set()
            for tag_name in [*global_tags, *row_tags]:
                if tag_name.lower() in applied:
                    continue
                applied.add(tag_name.lower())
                tag = await _ensure_tag(tag_name)
                self.db.add(ContactTag(contact_id=contact.id, tag_id=tag.id))

            if contact_list is not None:
                self.db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
                added_to_list += 1

        if contact_list is not None and added_to_list:
            contact_list.contact_count = (contact_list.contact_count or 0) + added_to_list
        return result
