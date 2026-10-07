"""Email enrichment: cleaning, diagnosis, provider verdicts, and the safety rule.

The rule these tests exist to protect:

    A **guessed** address is stored but NEVER marked verified, and an address a
    provider could not confirm is never promoted to the contact's primary
    address in a way that hides its uncertainty. Sending to guesses is what
    quietly wrecks sender reputation, so the distinction has to be enforced in
    code, not left to a comment.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.contact import Contact
from app.services import email_enrichment as ee


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestDiagnose:
    def test_clean_address_is_deliverable_shaped(self, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        result = ee.diagnose("ada.obi@acmefoods.com")
        assert result.valid_syntax is True
        assert result.domain == "acmefoods.com"
        assert result.suggestion is None
        assert result.deliverable_hint is True

    def test_junk_is_rejected(self):
        assert ee.diagnose("not an email").valid_syntax is False
        assert ee.diagnose("").valid_syntax is False
        assert ee.diagnose(None).valid_syntax is False

    def test_typo_domain_is_suggested(self, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: False)
        result = ee.diagnose("ada@gmial.com")
        assert result.suggestion == "ada@gmail.com"
        assert any("typo" in problem for problem in result.problems)

    def test_role_and_free_mail_are_flagged(self, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        assert ee.diagnose("info@acmefoods.com").is_role is True
        assert ee.diagnose("ada@gmail.com").is_free_mail is True
        assert ee.diagnose("ada.obi@acmefoods.com").is_role is False

    def test_disposable_domain_flagged(self, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        result = ee.diagnose("ada@mailinator.com")
        assert result.is_disposable is True
        assert any("disposable" in problem for problem in result.problems)

    def test_placeholder_flagged(self):
        assert ee.diagnose("test@example.com").is_placeholder is True
        assert ee.diagnose("ada@acmefoods.com").is_placeholder is False

    def test_missing_mx_marks_not_deliverable(self, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: False)
        result = ee.diagnose("ada@dead-domain.zz")
        assert result.mx_found is False
        assert result.deliverable_hint is False


class TestInference:
    def test_candidates_are_most_likely_first(self):
        candidates = ee.infer_candidates("Ada", "Obi", "acmefoods.com")
        assert candidates[0] == "ada.obi@acmefoods.com"
        assert "adaobi@acmefoods.com" in candidates
        assert all(c.endswith("@acmefoods.com") for c in candidates)

    def test_punctuation_in_a_name_is_stripped(self):
        candidates = ee.infer_candidates("O'Brien", "Nwosu-Ade", "x.com")
        assert candidates[0] == "obrien.nwosuade@x.com"

    def test_no_domain_means_no_candidates(self):
        assert ee.infer_candidates("Ada", "Obi", "") == []
        assert ee.infer_candidates("Ada", "Obi", "notadomain") == []

    def test_no_name_means_no_candidates(self):
        assert ee.infer_candidates("", "", "x.com") == []

    def test_domain_from_website(self):
        assert ee.domain_from_website("https://acmefoods.com/contact") == "acmefoods.com"
        assert ee.domain_from_website("www.acmefoods.com") == "acmefoods.com"
        assert ee.domain_from_website("acmefoods.com") == "acmefoods.com"
        assert ee.domain_from_website("") == ""


class TestExtraAddresses:
    def test_finds_addresses_in_custom_fields(self):
        contact = Contact(
            phone_number="+2348031234567",
            custom_fields='{"accounts_email": "billing@acme.com", "notes": "call Tuesday"}',
        )
        extras = ee.collect_extra_emails(contact)
        assert extras == ["billing@acme.com"]

    def test_notes_without_addresses_are_ignored(self):
        contact = Contact(
            phone_number="+2348031234567",
            custom_fields='{"notes": "prefers mornings", "pain_point": "slow replies"}',
        )
        assert ee.collect_extra_emails(contact) == []

    def test_placeholder_addresses_are_not_collected(self):
        contact = Contact(
            phone_number="+2348031234567",
            custom_fields='{"cc": "test@example.com"}',
        )
        assert ee.collect_extra_emails(contact) == []


class TestEnrichContact:
    @pytest.mark.asyncio
    async def test_existing_address_is_cleaned_on_the_confirm_path(self, db, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        contact = Contact(phone_number="+2348031234567", email="ADA@AcmeFoods.com")
        db.add(contact)
        await db.flush()

        await ee.enrich_contact(db, contact, use_providers=False)
        assert contact.email == "ada@acmefoods.com"
        assert contact.email_lower == "ada@acmefoods.com"

    @pytest.mark.asyncio
    async def test_provider_can_verify_an_existing_address(self, db, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)

        async def fake_verify(address):
            return ee.ProviderAnswer(
                address=address, verdict=ee.VERDICT_DELIVERABLE,
                confidence=95, provider="zerobounce",
            )

        monkeypatch.setattr(ee, "verify_address", fake_verify)
        contact = Contact(phone_number="+2348031234567", email="ada@acme.com")
        db.add(contact)
        await db.flush()

        result = await ee.enrich_contact(db, contact, use_providers=True)
        assert result.action == "verified"
        assert contact.email_verified is True
        assert contact.email_verified_at is not None
        assert contact.email_confidence == 95

    @pytest.mark.asyncio
    async def test_undeliverable_verdict_quarantines_the_address(self, db, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)

        async def fake_verify(address):
            return ee.ProviderAnswer(
                address=address, verdict=ee.VERDICT_UNDELIVERABLE, provider="zerobounce",
            )

        monkeypatch.setattr(ee, "verify_address", fake_verify)
        contact = Contact(phone_number="+2348031234567", email="dead@acme.com")
        db.add(contact)
        await db.flush()

        await ee.enrich_contact(db, contact, use_providers=True)
        assert contact.is_email_undeliverable is True
        assert contact.email_status == "invalid"
        assert contact.email_verified is False

    @pytest.mark.asyncio
    async def test_a_provider_error_does_not_mark_the_address_bad(self, db, monkeypatch):
        """Quota exhausted / key missing must not look like a bad address."""
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)

        async def failing_verify(address):
            return ee.ProviderAnswer(provider="", error="zerobounce: rate/credit limit reached")

        monkeypatch.setattr(ee, "verify_address", failing_verify)
        contact = Contact(phone_number="+2348031234567", email="ada@acme.com")
        db.add(contact)
        await db.flush()

        result = await ee.enrich_contact(db, contact, use_providers=True)
        assert result.action == "failed"
        assert contact.email_verified is False
        assert contact.is_email_undeliverable is False
        assert "credit limit" in (contact.email_enrichment_note or "")

    @pytest.mark.asyncio
    async def test_phone_only_contact_with_website_can_be_found(self, db, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)

        async def fake_find(domain, first_name="", last_name=""):
            return ee.ProviderAnswer(
                address="ada.obi@acmefoods.com", verdict=ee.VERDICT_DELIVERABLE,
                confidence=90, provider="hunter",
            )

        monkeypatch.setattr(ee, "find_address", fake_find)
        contact = Contact(
            phone_number="+2348031234567", first_name="Ada", last_name="Obi",
            website="https://acmefoods.com",
        )
        db.add(contact)
        await db.flush()

        result = await ee.enrich_contact(db, contact, use_providers=True)
        assert result.action == "found"
        assert contact.email == "ada.obi@acmefoods.com"
        assert contact.email_verified is True
        assert contact.email_source == "hunter"

    @pytest.mark.asyncio
    async def test_phone_only_contact_without_a_website_is_left_alone(self, db, monkeypatch):
        """No domain to look up: skip rather than fabricate."""
        contact = Contact(phone_number="+2348031234567", first_name="Ada")
        db.add(contact)
        await db.flush()

        result = await ee.enrich_contact(db, contact, use_providers=True)
        assert result.action == "skipped"
        assert contact.email is None

    @pytest.mark.asyncio
    async def test_inferred_address_is_stored_unverified(self, db, monkeypatch):
        """The safety rule: a guess is never presented as a verified address."""
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        contact = Contact(
            phone_number="+2348031234567", first_name="Ada", last_name="Obi",
            website="acmefoods.com",
        )
        db.add(contact)
        await db.flush()

        result = await ee.enrich_contact(
            db, contact, use_providers=False, allow_inferred=True
        )
        assert result.action == "inferred"
        assert contact.email == "ada.obi@acmefoods.com"
        assert contact.email_source == "inferred"
        assert contact.email_verified is False, "a guess must never be verified"
        assert contact.email_confidence == 30
        assert "guess" in (contact.email_enrichment_note or "").lower()

    @pytest.mark.asyncio
    async def test_inference_is_off_unless_asked_for(self, db, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        contact = Contact(
            phone_number="+2348031234567", first_name="Ada", last_name="Obi",
            website="acmefoods.com",
        )
        db.add(contact)
        await db.flush()

        result = await ee.enrich_contact(db, contact, use_providers=False)
        assert result.action == "skipped"
        assert contact.email is None, "no guess is made by default"

    @pytest.mark.asyncio
    async def test_second_address_on_a_contact_is_kept_as_an_alias(self, db, monkeypatch):
        """An email-only contact's extra addresses survive as reply aliases."""
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        contact = Contact(
            phone_number="+2348031234567",
            email="ada@acme.com",
            custom_fields='{"accounts_email": "billing@acme.com"}',
        )
        db.add(contact)
        await db.flush()

        await ee.enrich_contact(db, contact, use_providers=False)

        from app.services.email_service import contact_email_aliases

        aliases = await contact_email_aliases(db, contact.id)
        assert "billing@acme.com" in aliases

    @pytest.mark.asyncio
    async def test_enrichment_does_not_overwrite_a_human_supplied_source(
        self, db, monkeypatch
    ):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)

        async def fake_verify(address):
            return ee.ProviderAnswer(
                address=address, verdict=ee.VERDICT_DELIVERABLE, provider="zerobounce",
            )

        monkeypatch.setattr(ee, "verify_address", fake_verify)
        contact = Contact(
            phone_number="+2348031234567", email="ada@acme.com", email_source="csv",
        )
        db.add(contact)
        await db.flush()

        await ee.enrich_contact(db, contact, use_providers=True)
        assert contact.email_source == "csv"


class TestEnrichBatch:
    @pytest.mark.asyncio
    async def test_counters_and_per_contact_items(self, db, monkeypatch):
        monkeypatch.setattr(ee, "mx_records_exist", lambda domain: True)
        db.add(Contact(phone_number="+2348031111111", email="a@acme.com"))
        db.add(Contact(phone_number="+2348032222222", email="b@acme.com"))
        await db.flush()
        contacts = list((await db.execute(select(Contact))).scalars().all())

        report = await ee.enrich_contacts(db, contacts, use_providers=False)
        assert report["processed"] == 2
        assert report["with_email"] == 2
        assert len(report["items"]) == 2
        assert report["success"] is True

    @pytest.mark.asyncio
    async def test_disabled_enrichment_skips_everything(self, db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "EMAIL_ENRICHMENT_ENABLED", False)
        contact = Contact(phone_number="+2348031234567", email="a@acme.com")
        db.add(contact)
        await db.flush()

        report = await ee.enrich_contacts(db, [contact])
        assert report["skipped"] == 1
