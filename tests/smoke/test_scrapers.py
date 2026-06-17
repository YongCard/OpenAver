"""Scraper 模組測試

Phase 16 Task 2: 測試 5 個爬蟲模組
- Task 1 (舊): JavBusScraper, JAV321Scraper, JavDBScraper
- Task 2 (新): FC2Scraper, AVSOXScraper

Note: TestJavDBScraper (test_cover_from_javdb) 已在 TASK-73e-T3 退役。
等效離線替代品：tests/unit/test_javdb_scraper.py::TestJavdbCoverUpgrade
"""
import pytest
from core.scrapers import (
    JAV321Scraper,
    Video,
)

pytestmark = pytest.mark.smoke

# ========== Task 1 爬蟲測試 ==========

class TestJAV321Scraper:
    """JAV321 爬蟲測試"""

    @pytest.fixture
    def scraper(self):
        return JAV321Scraper()

    def test_search_by_keyword(self, scraper):
        """測試：關鍵字搜尋"""
        results = scraper.search_by_keyword("天使もえ", limit=5)

        assert isinstance(results, list)
        if results:
            assert len(results) <= 5
            for video in results:
                assert isinstance(video, Video)
                assert isinstance(video.title, str) and len(video.title) > 0
                assert video.number is not None and len(video.number) > 0


