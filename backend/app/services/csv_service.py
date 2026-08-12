"""
CSV Import Service.
Handles CSV parsing, validation, and import of contacts.
"""

import csv
import io
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.utils.phone import normalize_nigerian_number

logger = logging.getLogger(__name__)

# Supported CSV columns
SUPPORTED_COLUMNS = [
    "first_name", "last_name", "business_name", "phone", "phone_number",
    "email", "city", "state", "website", "industry", "source",
]

COLUMN_MAP = {
    "phone": "phone_number",
    "phone_number": "phone_number",
    "first_name": "first_name",
    "last_name": "last_name",
    "business_name": "business_name",
    "email": "email",
    "city": "city",
    "state": "state",
    "website": "website",
    "industry": "industry",
    "source": "source",
}

# Header aliases -> Contact field. Matching is case-insensitive and tolerant of
# the spellings real restaurant/CRM exports use (e.g. "Phone Number", "Mobile",
# "Restaurant", "First Name"). Anything not listed is ignored.
HEADER_ALIASES = {
    "phone_number": ("phone", "phone_number", "phonenumber", "phone number", "phone no",
                     "phone_no", "mobile", "mobile number", "mobile_number", "mobile no",
                     "mobile_no", "tel", "telephone", "telephone number", "contact",
                     "contact number", "number"),
    "first_name": ("first_name", "firstname", "first name", "fname", "given name",
                   "given_name", "name", "full name", "fullname", "customer name",
                   "customer_name", "contact name", "contact_name", "contact person",
                   "contact_person", "client name", "client_name"),
    "last_name": ("last_name", "lastname", "last name", "lname", "surname", "family name",
                  "family_name"),
    "business_name": ("business_name", "businessname", "business name", "business", "company",
                      "brand", "organization", "organisation", "restaurant", "restaurant name",
                      "shop", "store"),
    "email": ("email", "e-mail", "mail", "email address", "email_address"),
    "city": ("city", "town"),
    "state": ("state", "region", "province"),
    "website": ("website", "web", "url", "site", "web site"),
    "industry": ("industry", "sector", "category"),
    "source": ("source", "channel", "origin"),
}


def detect_column_mapping(headers: list[str]) -> dict[str, str]:
    """Auto-detect a CSV header -> Contact field mapping.

    ``headers`` are the raw header strings (any casing). The returned mapping is
    keyed by the *normalized* (lowercased, stripped) header so it lines up with
    the normalized row keys produced during import.
    """
    mapping: dict[str, str] = {}
    for header in headers:
        key = str(header).strip().lower()
        for field, aliases in HEADER_ALIASES.items():
            if key in aliases:
                mapping[key] = field
                break
    return mapping


class CSVImportResult:
    """Result of a CSV import operation."""

    def __init__(self):
        self.imported: int = 0
        self.skipped: int = 0
        self.invalid: int = 0
        self.duplicates: int = 0
        self.total_rows: int = 0
        self.errors: list[dict] = []
        self.imported_contact_ids: list[int] = []


class CSVImportService:
    """Service for importing contacts from CSV."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def preview_csv(self, content: bytes, max_rows: int = 20) -> dict:
        """Preview CSV content: return headers and sample rows."""
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        if not reader.fieldnames:
            return {"error": "No headers found in CSV", "headers": [], "rows": [], "total_rows": 0}

        headers = [h.strip() for h in reader.fieldnames]
        rows = []
        row_count = 0

        for row in reader:
            row_count += 1
            if len(rows) < max_rows:
                rows.append({k.strip(): v for k, v in row.items()})

        return {
            "headers": headers,
            "rows": rows,
            "total_rows": row_count,
        }

    async def validate_and_import(
        self,
        content: bytes,
        column_mapping: dict[str, str],
        list_id: Optional[int] = None,
        skip_duplicates: bool = True,
    ) -> CSVImportResult:
        """
        Validate and import contacts from CSV.
        column_mapping maps CSV column names to Contact field names.
        """
        result = CSVImportResult()
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))

        if not reader.fieldnames:
            result.errors.append({"row": 0, "error": "No headers found"})
            return result

        # Normalize mapping keys so they match the normalized row keys below.
        # The client may send original-case header names; we compare case-blind.
        mapping = {str(k).strip().lower(): v for k, v in (column_mapping or {}).items()}

        # Resolve the target list once (and only once) so we can attach members.
        contact_list = None
        if list_id is not None:
            contact_list = (
                await self.db.execute(select(ContactList).where(ContactList.id == list_id))
            ).scalar_one_or_none()

        seen_phones = set()
        row_num = 0
        added_to_list = 0

        for row in reader:
            row_num += 1
            result.total_rows += 1

            # Lowercase row keys: csv.DictReader preserves the original header
            # casing, so a "Phone Number" column never matched a mapping keyed
            # as "phone number" -- every row came back "No phone number".
            row_lower = {str(k).strip().lower(): v for k, v in row.items()}

            # Build contact data from column mapping
            contact_data = {}
            for csv_col, contact_field in mapping.items():
                if contact_field in COLUMN_MAP.values():
                    value = (row_lower.get(csv_col) or "").strip()
                    if value:
                        contact_data[contact_field] = value

            # Validate phone number
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

            # Check duplicates
            if skip_duplicates:
                if normalized in seen_phones:
                    result.duplicates += 1
                    continue

                # Check database
                existing = await self.db.execute(
                    select(Contact).where(Contact.phone_number == normalized)
                )
                if existing.scalar_one_or_none():
                    result.duplicates += 1
                    continue

            seen_phones.add(normalized)
            contact_data["phone_number"] = normalized
            contact_data.setdefault("country", "Nigeria")

            # Create contact
            contact = Contact(**contact_data)
            self.db.add(contact)
            await self.db.flush()
            result.imported += 1
            result.imported_contact_ids.append(contact.id)

            # Attach the contact to the chosen list, if one was requested.
            if contact_list is not None:
                member = ContactListMember(list_id=contact_list.id, contact_id=contact.id)
                self.db.add(member)
                added_to_list += 1

        if contact_list is not None and added_to_list:
            contact_list.contact_count = (contact_list.contact_count or 0) + added_to_list

        return result
