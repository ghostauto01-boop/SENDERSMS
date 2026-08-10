"""
Tests for phone number normalization and validation.
"""

import pytest
from app.utils.phone import (
    normalize_nigerian_number,
    clean_phone_number,
    detect_opt_out_keyword,
    count_sms_segments,
    is_nigerian_mobile,
)


class TestPhoneNormalization:
    def test_clean_phone_number(self):
        assert clean_phone_number("0801 234 5678") == "08012345678"
        assert clean_phone_number("080-123-456-78") == "08012345678"
        assert clean_phone_number("(0801) 234-5678") == "08012345678"
        assert clean_phone_number("+234 801 234 5678") == "+2348012345678"
        assert clean_phone_number("") == ""

    def test_normalize_leading_zero(self):
        """08012345678 → +2348012345678"""
        result = normalize_nigerian_number("08012345678")
        assert result == "+2348012345678"

    def test_normalize_no_zero(self):
        """8012345678 → +2348012345678"""
        result = normalize_nigerian_number("8012345678")
        assert result == "+2348012345678"

    def test_normalize_country_code(self):
        """2348012345678 → +2348012345678"""
        result = normalize_nigerian_number("2348012345678")
        assert result == "+2348012345678"

    def test_normalize_e164(self):
        """+2348012345678 → +2348012345678"""
        result = normalize_nigerian_number("+2348012345678")
        assert result == "+2348012345678"

    def test_normalize_with_spaces(self):
        result = normalize_nigerian_number("0801 234 5678")
        assert result == "+2348012345678"

    def test_invalid_number_returns_none(self):
        assert normalize_nigerian_number("123") is None
        assert normalize_nigerian_number("abc") is None
        assert normalize_nigerian_number("") is None

    def test_is_nigerian_mobile(self):
        assert is_nigerian_mobile("08034567890") is True
        assert is_nigerian_mobile("+2348034567890") is True
        assert is_nigerian_mobile("08021234567") is True
        assert is_nigerian_mobile("12345") is False


class TestOptOutDetection:
    def test_stop_keyword(self):
        assert detect_opt_out_keyword("STOP") == "STOP"
        assert detect_opt_out_keyword("stop") == "STOP"
        assert detect_opt_out_keyword("Stop please") == "STOP"

    def test_unsubscribe_keyword(self):
        assert detect_opt_out_keyword("UNSUBSCRIBE") == "UNSUBSCRIBE"
        assert detect_opt_out_keyword("unsubscribe me") == "UNSUBSCRIBE"

    def test_cancel_keyword(self):
        assert detect_opt_out_keyword("CANCEL") == "CANCEL"

    def test_remove_keyword(self):
        assert detect_opt_out_keyword("REMOVE") == "REMOVE"

    def test_opt_out_keyword(self):
        assert detect_opt_out_keyword("OPT OUT") == "OPT OUT"

    def test_non_opt_out(self):
        assert detect_opt_out_keyword("Hello") is None
        assert detect_opt_out_keyword("Thanks for the info") is None


class TestSMSSegments:
    def test_single_segment(self):
        char_count, segments = count_sms_segments("Hello")
        assert segments == 1
        assert char_count == 5

    def test_160_chars_one_segment(self):
        msg = "a" * 160
        char_count, segments = count_sms_segments(msg)
        assert segments == 1
        assert char_count == 160

    def test_161_chars_two_segments(self):
        msg = "a" * 161
        char_count, segments = count_sms_segments(msg)
        assert segments == 2

    def test_306_chars_two_segments(self):
        msg = "a" * 306
        char_count, segments = count_sms_segments(msg)
        assert segments == 2
