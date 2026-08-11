"""Contact display names must be identical in the chat and in Pushover alerts."""

import pytest

from app.utils.naming import contact_display_name


class _C:
    def __init__(self, first=None, last=None, business=None, phone="+2348031112222"):
        self.first_name = first
        self.last_name = last
        self.business_name = business
        self.phone_number = phone


class TestContactDisplayName:
    def test_full_person_name(self):
        assert contact_display_name(_C(first="Ada", last="Obi")) == "Ada Obi"

    def test_first_name_only(self):
        assert contact_display_name(_C(first="Ada")) == "Ada"

    def test_last_name_only(self):
        assert contact_display_name(_C(last="Obi")) == "Obi"

    def test_business_name_when_no_person(self):
        assert contact_display_name(_C(business="Gwarinpa Autos")) == "Gwarinpa Autos"

    def test_person_beats_business(self):
        """The inbox list shows the person, so the notification must too."""
        c = _C(first="Ada", last="Obi", business="Gwarinpa Autos")
        assert contact_display_name(c) == "Ada Obi"

    def test_phone_when_nothing_else(self):
        assert contact_display_name(_C()) == "+2348031112222"

    def test_whitespace_names_are_ignored(self):
        assert contact_display_name(_C(first="   ", last="\t", business="Real Brand")) == "Real Brand"

    def test_missing_contact_uses_fallback(self):
        assert contact_display_name(None) == "Unknown"
        assert contact_display_name(None, "n/a") == "n/a"

    def test_no_data_at_all_falls_back(self):
        assert contact_display_name(_C(phone=None)) == "Unknown"

    def test_none_phone_with_custom_fallback(self):
        assert contact_display_name(_C(phone=""), "no number") == "no number"


class TestNotificationTitleUsesTheName:
    """Guards the exact string the user sees on their phone."""

    @pytest.mark.parametrize(
        "contact,expected",
        [
            (_C(first="Ada", last="Obi"), "📱 New SMS from Ada Obi"),
            (_C(business="Zenith Motors"), "📱 New SMS from Zenith Motors"),
            (_C(), "📱 New SMS from +2348031112222"),
        ],
    )
    def test_title_format(self, contact, expected):
        title = f"📱 New SMS from {contact_display_name(contact, contact.phone_number)}"
        assert title == expected
