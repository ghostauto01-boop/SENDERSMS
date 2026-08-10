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

        headers = [h.strip().lower() for h in reader.fieldnames]
        rows = []
        row_count = 0

        for row in reader:
            row_count += 1
            if len(rows) < max_rows:
                rows.append({k.strip().lower(): v for k, v in row.items()})

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

        seen_phones = set()
        row_num = 0

        for row in reader:
            row_num += 1
            result.total_rows += 1

            # Build contact data from column mapping
            contact_data = {}
            for csv_col, contact_field in column_mapping.items():
                if contact_field in COLUMN_MAP.values():
                    value = row.get(csv_col, "").strip()
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
            contact_data["country"] = contact_data.get("country", "Nigeria")

            # Create contact
            contact = Contact(**contact_data)
            self.db.add(contact)
            await self.db.flush()
            result.imported += 1
            result.imported_contact_ids.append(contact.id)

        return result
