"""Pre-send number filter: bad numbers must never reach the gateway."""

import pytest

from app.services.number_filter import (
    classify_number,
    filter_numbers,
    looks_like_test_data,
)


class TestClassifyNumber:
    def test_valid_nigerian_mobiles_pass(self):
        for raw in ["08031234567", "8031234567", "+2348031234567", "2348031234567",
                    "09012345678", "07051234567", "08101234567",
                    # Canonical example numbers used across the suite must SEND:
                    # pattern suspicion never hard-blocks a live send.
                    "+2348012345678", "08012345678", "08123456789"]:
            r = classify_number(raw)
            assert r.sendable, f"{raw}: {r.reason} {r.detail}"
            assert r.normalized and r.normalized.startswith("+234")

    def test_golden_numbers_send(self):
        # Repeated-digit "golden" lines are real paid-for numbers in Nigeria.
        assert classify_number("08031111111").sendable
        assert classify_number("08033333333").sendable

    def test_empty_and_short_blocked(self):
        assert classify_number("").reason == "empty_number"
        assert classify_number(None).reason == "empty_number"
        assert classify_number("12").reason == "invalid_format"

    def test_garbage_blocked(self):
        r = classify_number("not-a-number")
        assert not r.sendable
        assert r.reason in ("invalid_format", "not_mobile")

    def test_foreign_numbers_blocked(self):
        r = classify_number("+14155551212")
        assert not r.sendable
        assert r.reason == "foreign_number"

    def test_short_codes_blocked(self):
        r = classify_number("32122")
        assert not r.sendable
        assert r.reason == "short_code"

    def test_hard_fake_numbers_blocked(self):
        for raw in ["08000000000", "07777777777"]:
            r = classify_number(raw)
            assert not r.sendable, f"{raw} should be blocked, got sendable"
            assert r.reason == "test_number", f"{raw}: {r.reason}"
        # Out-of-range prefixes are blocked too (different reason, same wall).
        assert not classify_number("08999999999").sendable

    def test_landline_prefix_blocked(self):
        # 01 = Lagos landline area code, not a mobile prefix.
        r = classify_number("014567890")
        assert not r.sendable


class TestSoftFlags:
    def test_looks_like_test_data(self):
        assert looks_like_test_data("08123456789") == "test_data_suspected"
        assert looks_like_test_data("08012345678") == "test_data_suspected"
        assert looks_like_test_data("08987654321") == "test_data_suspected"
        assert looks_like_test_data("08031234567") is None
        assert looks_like_test_data("08031112222") is None
        # Golden lines are real — 7 repeats must NOT be flagged.
        assert looks_like_test_data("08031111111") is None
        assert looks_like_test_data("+2348034567890") is None

    def test_preview_is_strict_by_default(self):
        rep = filter_numbers(["08031234567", "08123456789"])
        assert rep.sendable == 1
        assert rep.blocked == 1
        assert rep.by_reason.get("test_data_suspected") == 1

    def test_preview_relaxed_mode(self):
        rep = filter_numbers(["08031234567", "08123456789"], strict_patterns=False)
        assert rep.sendable == 2


class TestFilterNumbers:
    def test_report_counts(self):
        rep = filter_numbers(["08031234567", "08000000000", "+14155551212", "32122", ""],
                             strict_patterns=False)
        assert rep.total == 5
        assert rep.sendable == 1
        assert rep.blocked == 4
        assert rep.by_reason.get("test_number") == 1
        assert rep.by_reason.get("foreign_number") == 1
        assert rep.by_reason.get("short_code") == 1
        assert rep.by_reason.get("empty_number") == 1
        assert len(rep.blocked_phones) == 4


class TestListHygieneIntegration:
    def test_classify_number_delegates(self):
        from app.services.list_hygiene import classify_number as legacy

        ok, _ = legacy("08031234567")
        assert ok is True
        ok, _ = legacy("+2348012345678")
        assert ok is True
        ok, reason = legacy("08000000000")
        assert ok is False
        assert reason == "test_number"
        ok, reason = legacy("+14155551212")
        assert ok is False
        assert reason == "foreign_number"


class TestQuotaSplit:
    def test_quota_counts_exact(self):
        from app.services.ads_service import quota_counts

        class C:
            def __init__(self, q):
                self.send_quota = q

        # Capped creatives take exactly their quota, in order.
        assert quota_counts(100, [C(30), C(20)]) == [30, 20]
        # Uncapped splits the remainder equally.
        assert quota_counts(100, [C(30), C(None)]) == [30, 70]
        assert quota_counts(100, [C(30), C(None), C(None)]) == [30, 35, 35]
        # Quotas beyond the pool leave contacts unassigned (never over-send).
        assert quota_counts(10, [C(30), C(20)]) == [10, 0]
        # All uncapped = even split.
        assert quota_counts(10, [C(None), C(0)]) == [5, 5]
        assert quota_counts(0, [C(5)]) == [0]
