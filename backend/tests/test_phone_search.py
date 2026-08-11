"""Regression cover for searching contacts/inbox by phone number.

Bug: numbers are stored in E.164 (+2348031112222) but Nigerians type them in
national form (08031112222). The search did a literal `LIKE %08031112222%`
against the stored value, so the single most common lookup -- "find this
number" -- returned nothing, in both the Contacts page and the Inbox.
"""

import pytest

from app.utils.phone import phone_search_variants


class TestPhoneSearchVariants:
    def test_national_form_matches_stored_e164(self):
        """0803... must produce the 234803... spelling that is stored."""
        assert "2348031112222" in phone_search_variants("08031112222")

    def test_bare_national_number(self):
        v = phone_search_variants("8031112222")
        assert "2348031112222" in v
        assert "08031112222" in v

    def test_e164_input_matches_national_spelling(self):
        v = phone_search_variants("+2348031112222")
        assert "2348031112222" in v
        assert "08031112222" in v

    def test_partial_fragment_is_supported(self):
        """Users type a prefix and expect live filtering."""
        v = phone_search_variants("0803111")
        assert "234803111" in v
        assert "803111" in v

    def test_formatting_characters_are_ignored(self):
        for raw in ["0803-111-2222", "0803 111 2222", "(0803) 111 2222"]:
            assert "2348031112222" in phone_search_variants(raw), raw

    @pytest.mark.parametrize("term", ["", "   ", "Ada", "Zenith Motors", None])
    def test_non_numeric_yields_no_variants(self, term):
        """Name searches must not be turned into phone clauses."""
        assert phone_search_variants(term) == []

    def test_variants_are_unique(self):
        v = phone_search_variants("08031112222")
        assert len(v) == len(set(v))

    def test_all_spellings_agree_on_one_number(self):
        """Every way of typing the same number reaches the stored form."""
        stored = "+2348031112222"
        for typed in ["08031112222", "8031112222", "2348031112222", "+2348031112222"]:
            assert any(v in stored for v in phone_search_variants(typed)), typed
