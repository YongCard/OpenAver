"""Deterministic unit tests for the 8-source health-canary decision-core.

Zero network, fully synthetic inputs. Carries spec §US1 acceptance
("verify the three-state decision logic with controllable mocks, not real
network"). This file is intentionally NOT marked @pytest.mark.smoke — it must
run in CI fast-run.
"""

import types
from pathlib import Path

import pytest

from tests.smoke._canary_core import classify_one, numbers_match, quorum_verdict


def _video(number="SONE-205", title="Some Title", cover_url="http://x/c.jpg"):
    """Duck-typed fake video object with .number/.title/.cover_url."""
    return types.SimpleNamespace(number=number, title=title, cover_url=cover_url)


# ---------------------------------------------------------------------------
# classify_one — 6-state table
# ---------------------------------------------------------------------------

class TestClassifyOne:
    def test_healthy_video_passes(self):
        # Row 2: Video + canonical match + title + cover -> pass
        v = _video(number="SONE-205")
        assert classify_one(v, True, "SONE-205", "javbus") == "pass"

    def test_video_number_mismatch_fails(self):
        # Row 3: returned Video but wrong number -> fail (not skip)
        v = _video(number="SONE-206")
        assert classify_one(v, True, "SONE-205", "dmm") == "fail"

    def test_video_empty_title_fails(self):
        # Row 3: returned Video but empty title -> fail
        v = _video(number="SONE-205", title="")
        assert classify_one(v, True, "SONE-205", "javbus") == "fail"

    def test_video_empty_cover_fails(self):
        # Row 3: returned Video but empty cover_url -> fail
        v = _video(number="SONE-205", cover_url="")
        assert classify_one(v, True, "SONE-205", "javbus") == "fail"

    def test_none_group_a_probe_true_fails(self):
        # Row 4: None + Group A + probe reachable -> fail (parser broken)
        assert classify_one(None, True, "SONE-205", "javbus") == "fail"
        assert classify_one(None, True, "051119-917", "avsox") == "fail"  # avsox GROUP_A row 4

    def test_none_group_a_probe_false_skips(self):
        # Row 5: None + Group A + probe unreachable -> skip
        assert classify_one(None, False, "SONE-205", "javbus") == "skip"
        assert classify_one(None, False, "051119-917", "avsox") == "skip"  # avsox GROUP_A row 5

    def test_none_group_b_skips(self):
        # Row 6: None + Group B (no probe) -> skip
        assert classify_one(None, None, "SSNI-001", "javdb") == "skip"
        assert classify_one(None, None, "FC2-PPV-1723984", "fc2") == "skip"
        # avsox is now GROUP_A — removed from this test

    def test_none_group_b_skips_even_if_probe_passed(self):
        # Group B membership dominates: probe_reachable ignored for B sources.
        assert classify_one(None, True, "SSNI-001", "javdb") == "skip"

    def test_avsox_group_a_no_probe_degrades_to_skip(self):
        # Row 6 fallback: avsox GROUP_A but probe=None (no probe signal) -> skip
        # This is the no-probe degradation safety net (not a GROUP_B test).
        assert classify_one(None, None, "051119-917", "avsox") == "skip"

    def test_timeout_error_skips(self):
        # Row 1: TimeoutError signal -> skip (pure disconnect, not parser broken)
        assert classify_one(TimeoutError("timed out"), True, "SONE-205", "javbus") == "skip"

    def test_timeout_error_skips_group_b(self):
        assert classify_one(TimeoutError(), None, "SSNI-001", "javdb") == "skip"


# ---------------------------------------------------------------------------
# numbers_match — canonical per source-class
# ---------------------------------------------------------------------------

class TestNumbersMatchYukoCensored:
    @pytest.mark.parametrize("source", ["javbus", "jav321", "javdb", "dmm"])
    def test_censored_match_variants(self, source):
        assert numbers_match("SONE-205", "sone205", source) is True
        assert numbers_match("SONE-205", "SONE205", source) is True
        assert numbers_match("sone205", "SONE-205", source) is True

    @pytest.mark.parametrize("source", ["javbus", "jav321", "javdb", "dmm"])
    def test_censored_tokyo_hot_single_letter(self, source):
        assert numbers_match("N0762", "n0762", source) is True

    @pytest.mark.parametrize("source", ["javbus", "jav321", "javdb", "dmm"])
    def test_censored_non_match(self, source):
        assert numbers_match("SONE-205", "SONE-206", source) is False
        assert numbers_match("SONE-205", "SSNI-001", source) is False


class TestNumbersMatchFc2:
    def test_fc2_match_variants(self):
        assert numbers_match("FC2-1723984", "FC2-PPV-1723984", "fc2") is True
        assert numbers_match("FC2-1723984", "1723984", "fc2") is True
        assert numbers_match("FC2-PPV-1723984", "FC2-1723984", "fc2") is True
        assert numbers_match("1723984", "FC2-PPV-1723984", "fc2") is True

    def test_fc2_non_match(self):
        assert numbers_match("FC2-1723984", "FC2-PPV-1723985", "fc2") is False


class TestNumbersMatchHeyzo:
    def test_heyzo_match_leading_zero(self):
        assert numbers_match("HEYZO-0783", "HEYZO-783", "heyzo") is True
        assert numbers_match("HEYZO-783", "HEYZO-0783", "heyzo") is True

    def test_heyzo_non_match(self):
        assert numbers_match("HEYZO-0783", "HEYZO-0784", "heyzo") is False


class TestNumbersMatchD2pass:
    def test_d2pass_exact_match(self):
        assert numbers_match("120415_201", "120415_201", "d2pass") is True

    def test_d2pass_separator_significant(self):
        # underscore vs hyphen is semantically significant -> must NOT match
        assert numbers_match("120415_201", "120415-201", "d2pass") is False

    def test_d2pass_different_date(self):
        assert numbers_match("120415_201", "120416_201", "d2pass") is False


class TestNumbersMatchAvsox:
    def test_avsox_exact_match(self):
        assert numbers_match("051119-917", "051119-917", "avsox") is True

    def test_avsox_case_insensitive(self):
        assert numbers_match("abc-123", "ABC-123", "avsox") is True


class TestNumbersMatchGuards:
    def test_none_inputs_return_false(self):
        assert numbers_match(None, "SONE-205", "javbus") is False
        assert numbers_match("SONE-205", None, "javbus") is False
        assert numbers_match(None, None, "javbus") is False

    def test_empty_inputs_return_false(self):
        assert numbers_match("", "SONE-205", "javbus") is False
        assert numbers_match("SONE-205", "", "javbus") is False


# ---------------------------------------------------------------------------
# quorum_verdict
# ---------------------------------------------------------------------------

class TestQuorumVerdict:
    def test_any_pass_is_green(self):
        verdict, reason = quorum_verdict(["pass", "skip"])
        assert verdict == "green"
        assert isinstance(reason, str) and reason

    def test_pass_wins_over_fail(self):
        verdict, reason = quorum_verdict(["pass", "fail"])
        assert verdict == "green"
        assert isinstance(reason, str) and reason

    def test_fail_no_pass_is_red(self):
        verdict, reason = quorum_verdict(["skip", "fail"])
        assert verdict == "red"
        assert isinstance(reason, str) and reason

    def test_all_skip_is_skip(self):
        verdict, reason = quorum_verdict(["skip", "skip"])
        assert verdict == "skip"
        assert isinstance(reason, str) and reason


# ---------------------------------------------------------------------------
# Purity guard — static string check on the source file
# ---------------------------------------------------------------------------

def test_canary_core_is_pure():
    """_canary_core.py must not import requests or any core.scrapers module."""
    src = Path(__file__).parent.parent / "smoke" / "_canary_core.py"
    text = src.read_text(encoding="utf-8")
    assert "import requests" not in text
    assert "from core.scrapers" not in text
    assert "import core.scrapers" not in text
