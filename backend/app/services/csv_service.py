"""CSV contact import with automatic standard and custom-field mapping."""

import csv
import io
import json
import logging
import re
import unicodedata
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.utils.phone import normalize_nigerian_number

logger = logging.getLogger(__name__)

# Fields that can safely be populated directly on Contact during an import.
CONTACT_FIELDS = {
    "first_name", "last_name", "business_name", "phone_number", "email",
    "city", "state", "country", "website", "industry", "source",
    "lead_status", "notes",
}

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
    "email": ("email", "e-mail", "mail", "email address", "email_address"),
    "city": ("city", "town"),
    "state": ("state", "region", "province"),
    "country": ("country", "nation"),
    "website": ("website", "web", "url", "site", "web site"),
    "industry": ("industry", "sector", "category"),
    "source": ("source", "channel", "origin"),
    "lead_status": ("lead status", "lead_status", "status", "pipeline status"),
    "notes": ("notes", "note", "comments", "comment"),
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
        self.total_rows = 0
        self.errors: list[dict] = []
        self.imported_contact_ids: list[int] = []


class CSVImportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def preview_csv(self, content: bytes, max_rows: int = 20) -> dict:
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return {"error": "No headers found in CSV", "headers": [], "rows": [], "total_rows": 0}
        headers = [h.strip() for h in reader.fieldnames]
        rows, row_count = [], 0
        for row in reader:
            row_count += 1
            if len(rows) < max_rows:
                rows.append({(k or "").strip(): v for k, v in row.items()})
        return {"headers": headers, "rows": rows, "total_rows": row_count,
                "column_mapping": detect_column_mapping(headers)}

    async def validate_and_import(
        self,
        content: bytes,
        column_mapping: dict[str, str],
        list_id: Optional[int] = None,
        skip_duplicates: bool = True,
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

        seen_phones: set[str] = set()
        added_to_list = 0
        for row_num, row in enumerate(reader, start=1):
            result.total_rows += 1
            row_lower = {str(k or "").strip().lower(): v for k, v in row.items()}
            contact_data: dict = {}
            custom_data: dict[str, str] = {}

            for csv_col, target in mapping.items():
                value = (row_lower.get(csv_col) or "").strip()
                if not value or not target or target == "ignore":
                    continue
                if target in CONTACT_FIELDS:
                    contact_data[target] = value
                elif target.startswith("custom:"):
                    key = custom_field_key(target.split(":", 1)[1])
                    # A direct Contact field always wins if a custom heading
                    # happens to normalize to the same name.
                    if key not in CONTACT_FIELDS:
                        custom_data[key] = value

            raw_phone = contact_data.get("phone_number", "")
            if not raw_phone:
                result.invalid += 1
                result.errors.append({"row": row_num, "error": "No phone number"})
                continue
            normalized = normalize_nigerian_number(raw_phone)
            if not normalized:
                result.invalid += 1
                result.errors.append({"row": row_num, "error": f"Invalid phone number: {raw_phone}"})
                continue

            if skip_duplicates:
                if normalized in seen_phones:
                    result.duplicates += 1
                    continue
                existing = await self.db.execute(select(Contact).where(Contact.phone_number == normalized))
                if existing.scalar_one_or_none():
                    result.duplicates += 1
                    continue

            seen_phones.add(normalized)
            contact_data["phone_number"] = normalized
            contact_data.setdefault("country", "Nigeria")
            contact_data.setdefault("lead_status", "new")
            if custom_data:
                contact_data["custom_fields"] = json.dumps(custom_data, ensure_ascii=False)

            contact = Contact(**contact_data)
            self.db.add(contact)
            await self.db.flush()
            result.imported += 1
            result.imported_contact_ids.append(contact.id)

            if contact_list is not None:
                self.db.add(ContactListMember(list_id=contact_list.id, contact_id=contact.id))
                added_to_list += 1

        if contact_list is not None and added_to_list:
            contact_list.contact_count = (contact_list.contact_count or 0) + added_to_list
        return result
