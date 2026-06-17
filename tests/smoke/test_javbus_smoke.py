"""
test_javbus_smoke.py - JavBus 爬蟲真實連線 Smoke Tests

Phase 35 Task 8a: 驗證重寫後 JavBusScraper 所有新欄位

執行方式：
    pytest tests/smoke/test_javbus_smoke.py -v -m smoke

注意：
- 只用於本地手動測試，不進 CI（避免被 ban）
- 無法連線時自動 skip，不算失敗
"""

import pytest

from core.scrapers import JavBusScraper
from core.scrapers.models import Video

pytestmark = pytest.mark.smoke

# ========== 測試番號 ==========

# 多語言測試用番號
NUMBER_MULTILANG = "SNOS-143"


# ========== 模糊搜尋測試 ==========

class TestJavBusSmokeKeyword:
    """模糊搜尋：search_by_keyword + get_ids_from_search"""

    @pytest.fixture
    def scraper(self):
        return JavBusScraper(lang="zh-tw")

    def test_search_by_keyword_returns_videos(self, scraper):
        """search_by_keyword 回傳 Video 列表"""
        results = scraper.search_by_keyword("三上悠亞", limit=5)

        if not results:
            pytest.skip("search_by_keyword 無法連線或回傳空列表（可能被網站封鎖）")

        assert isinstance(results, list)
        assert len(results) <= 5, f"超過 limit=5，實際: {len(results)}"

        for v in results:
            assert isinstance(v, Video), f"結果包含非 Video 物件: {type(v)}"
            assert v.number, "Video.number 為空"
            assert v.source == "javbus", f"source 不符: {v.source!r}"

    def test_search_by_keyword_video_has_fields(self, scraper):
        """search_by_keyword 回傳的 Video 包含基本欄位"""
        results = scraper.search_by_keyword("SONE", limit=3)

        if not results:
            pytest.skip("search_by_keyword 無法連線或回傳空列表（可能被網站封鎖）")

        first = results[0]
        assert isinstance(first.title, str) and len(first.title) > 0, \
            "第一筆結果 title 為空"
        assert first.cover_url.startswith("http"), \
            f"第一筆結果 cover_url 格式錯誤: {first.cover_url!r}"

    def test_get_ids_from_search_returns_list(self, scraper):
        """get_ids_from_search 回傳番號字串列表"""
        ids = scraper.get_ids_from_search("SONE")

        if not ids:
            pytest.skip("get_ids_from_search 無法連線或回傳空列表（可能被網站封鎖）")

        assert isinstance(ids, list), f"回傳型別應為 list，實際: {type(ids)}"
        assert all(isinstance(i, str) for i in ids), "ids 包含非 str 元素"
        assert all(len(i) > 0 for i in ids), "ids 包含空字串"


# ========== 多語言測試 ==========

class TestJavBusSmokeMultilang:
    """同一番號 zh-tw vs ja tags 應因語言而異"""

    def test_zh_tw_vs_ja_tags_differ(self):
        """zh-tw 和 ja 的 tags 文字內容應不同（不同語言）"""
        scraper_tw = JavBusScraper(lang="zh-tw")
        scraper_ja = JavBusScraper(lang="ja")

        try:
            video_tw = scraper_tw.search(NUMBER_MULTILANG)
            video_ja = scraper_ja.search(NUMBER_MULTILANG)
        except Exception as e:
            pytest.skip(f"JavBus 連線問題: {e}")

        if video_tw is None or video_ja is None:
            pytest.skip(
                "JavBus 無法連線（zh-tw 或 ja 任一失敗），跳過多語言測試"
            )

        assert isinstance(video_tw.tags, list) and len(video_tw.tags) > 0, \
            "zh-tw tags 為空列表"
        assert isinstance(video_ja.tags, list) and len(video_ja.tags) > 0, \
            "ja tags 為空列表"

        # Tags 文字內容應不同（不同語言的翻譯）
        assert video_tw.tags != video_ja.tags, \
            f"zh-tw 和 ja 的 tags 應因語言而異\nzh-tw: {video_tw.tags}\nja: {video_ja.tags}"

    def test_zh_tw_vs_ja_number_consistent(self):
        """不同語言搜尋同一番號，number 應一致"""
        scraper_tw = JavBusScraper(lang="zh-tw")
        scraper_ja = JavBusScraper(lang="ja")

        try:
            video_tw = scraper_tw.search(NUMBER_MULTILANG)
            video_ja = scraper_ja.search(NUMBER_MULTILANG)
        except Exception as e:
            pytest.skip(f"JavBus 連線問題: {e}")

        if video_tw is None or video_ja is None:
            pytest.skip(
                "JavBus 無法連線（zh-tw 或 ja 任一失敗），跳過多語言一致性測試"
            )

        assert video_tw.number == video_ja.number == NUMBER_MULTILANG, \
            f"番號不一致: tw={video_tw.number!r}, ja={video_ja.number!r}"
        assert video_tw.source == video_ja.source == "javbus"
