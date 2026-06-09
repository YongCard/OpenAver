"""
tests/unit/test_javlibrary_cf_flow.py — TASK-70-T6
=================================================
Unit tests for CF sentinel re-raise in core/scraper.py explicit-source loop.

Patch strategy:
  core.scrapers.javlibrary.get_cf_transport (consumer namespace)
  → JavLibraryScraper.search() raises real exceptions
  → search_jav(source='javlibrary') must bubble them (not continue → None)
"""
import pytest
from unittest.mock import MagicMock, patch

from core.cf_transport import CfChallengeRequired, CfTransportUnavailable


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_mock_transport(*, fetch_side_effect=None, is_ready=False):
    t = MagicMock()
    if fetch_side_effect is not None:
        t.fetch.side_effect = fetch_side_effect
    t.is_ready.return_value = is_ready
    return t


# ── core/scraper.py explicit-source loop: CF sentinel re-raise ───────────────

class TestSearchJavCfSentinelReRaise:
    """
    測試 A：core/scraper.py explicit 迴圈對 JavLibrary CF 例外的 re-raise 行為。

    在 explicit 迴圈 except Exception 內，CfChallengeRequired /
    CfTransportUnavailable 必須 re-raise（bubble 給 router），不能被 continue 吞掉。
    """

    def test_search_jav_javlibrary_bubbles_cf_challenge_required(self, monkeypatch):
        """JavLibraryScraper.search() 拋 CfChallengeRequired → search_jav bubble（非回 None）"""
        mock_transport = _make_mock_transport(
            fetch_side_effect=CfChallengeRequired("CF challenge detected")
        )
        with patch(
            "core.scrapers.javlibrary.get_cf_transport",
            return_value=mock_transport,
        ):
            from core.scraper import search_jav
            with pytest.raises(CfChallengeRequired):
                search_jav("SONE-205", source="javlibrary")

    def test_search_jav_javlibrary_bubbles_cf_transport_unavailable(self, monkeypatch):
        """JavLibraryScraper.search() 拋 CfTransportUnavailable → search_jav bubble（非回 None）"""
        with patch(
            "core.scrapers.javlibrary.get_cf_transport",
            return_value=None,   # None → scraper 第一行 raise CfTransportUnavailable
        ):
            from core.scraper import search_jav
            with pytest.raises(CfTransportUnavailable):
                search_jav("SONE-205", source="javlibrary")

    def test_search_jav_javlibrary_other_exception_continues_returns_none(self, monkeypatch):
        """JavLibraryScraper.search() 拋普通 RuntimeError → search_jav 回 None（continue 不 bubble）"""
        mock_transport = _make_mock_transport(
            fetch_side_effect=RuntimeError("network error")
        )
        with patch(
            "core.scrapers.javlibrary.get_cf_transport",
            return_value=mock_transport,
        ):
            from core.scraper import search_jav
            result = search_jav("SONE-205", source="javlibrary")
            assert result is None, "普通例外應被 continue 吞掉，回 None"

    def test_search_jav_other_source_exception_still_continues(self, monkeypatch):
        """其他 builtin scraper（jav321）拋普通 Exception → continue 行為，不受 CF sentinel 影響。

        JAV321Scraper 是非 JavLibrary 的一般 builtin scraper，CF sentinel re-raise 邏輯
        只針對 CfChallengeRequired / CfTransportUnavailable；普通 RuntimeError 仍被 continue 吞掉。
        patch core.scrapers.jav321.JAV321Scraper 讓其 search() 拋 RuntimeError，
        斷言 search_jav(source='jav321') 回 None（continue 行為不變，不受 CF sentinel 影響）。
        """
        with patch(
            "core.scrapers.jav321.JAV321Scraper.search",
            side_effect=RuntimeError("network timeout"),
        ):
            from core.scraper import search_jav
            result = search_jav("SONE-205", source="jav321")
            assert result is None, "非 CF 例外應被 continue 吞掉，回 None"
