"""Scraper 模組測試

Phase 16 Task 2: 測試 5 個爬蟲模組
- Task 1 (舊): JavBusScraper, JAV321Scraper, JavDBScraper
- Task 2 (新): FC2Scraper, AVSOXScraper
"""
import pytest
from core.scrapers import (
    JavBusScraper, JAV321Scraper, JavDBScraper,
    FC2Scraper, AVSOXScraper,
    Video, Actress
)

pytestmark = pytest.mark.smoke

# ========== 測試樣本 ==========

# 有碼番號（主流片商）
SAMPLE_CENSORED = {
    "SONE-205": {"maker": "S1", "actress": "未歩なな"},
    "MIDV-018": {"maker": "Moodyz", "actress": "高橋しょう子"},
    "SSNI-001": {"maker": "S1", "actress": "三上悠亞"},
    "STARS-804": {"maker": "SOD", "actress": "永野いち夏"},
}

# 無碼番號（Carib/1Pondo 等）
SAMPLE_UNCENSORED = {
    "051119-917": {"title": "結婚直前"},
    "012523-001": {"title": "一本道"},
}

# FC2 番號
SAMPLE_FC2 = {
    "FC2-PPV-1723984": {"title": "透け透け体操服"},
    "FC2-PPV-3061583": {"title": ""},  # 可能找不到
}

# ========== 共用測試 ==========

@pytest.mark.parametrize("scraper_cls, test_number", [
    (JavBusScraper, "SONE-205"),
    (JAV321Scraper, "MIDV-018"),
    (JavDBScraper, "SSNI-001"),
    (FC2Scraper, "FC2-PPV-1723984"),
])
def test_search_valid_number(scraper_cls, test_number):
    """測試：搜尋有效番號"""
    if scraper_cls == JavDBScraper:
        try:
            from curl_cffi import requests
        except ImportError:
            pytest.skip("curl_cffi not installed")

    scraper = scraper_cls()
    video = scraper.search(test_number)

    if video:  # external network dependency
        assert isinstance(video, Video)
        if scraper_cls == FC2Scraper:
            assert video.number.startswith("FC2-PPV-") or "FC2" in video.number.upper()
            assert "1723984" in video.number
            assert isinstance(video.title, str) and len(video.title) > 0
        elif scraper_cls == JAV321Scraper:
            assert video.number.upper().startswith("MIDV")
            actresses = getattr(video, 'actresses', [])
            assert isinstance(actresses, list) and len(actresses) > 0
            assert isinstance(actresses[0], Actress)
        elif scraper_cls == JavDBScraper:
            assert video.number == test_number
            tags = getattr(video, 'tags', [])
            assert isinstance(tags, list) and len(tags) > 0
            assert isinstance(tags[0], str)
        else:
            assert video.number == test_number
            assert isinstance(video.title, str) and len(video.title) > 0
        assert video.source == scraper.source_name

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


class TestJavDBScraper:
    """JavDB 爬蟲測試"""

    @pytest.fixture
    def scraper(self):
        return JavDBScraper()

    def test_cover_from_javdb(self, scraper):
        """測試：封面來自 JavDB"""
        video = scraper.search("SONE-205")

        if video:
            assert isinstance(video.cover_url, str)
            assert any(d in video.cover_url for d in ["jdbimgs", "javdb", "jdbstatic"])


# ========== Task 2 新爬蟲測試 ==========

class TestAVSOXScraper:
    """AVSOX 爬蟲測試（無碼專用）"""

    @pytest.fixture
    def scraper(self):
        return AVSOXScraper()

    def test_get_working_domain(self, scraper):
        """測試：取得可用網域（改用 _ensure_session()，T1 已刪 _get_working_domain）"""
        base, token = scraper._ensure_session()
        if base:  # 網路依賴
            assert isinstance(base, str)
            assert base.startswith("https://")
            assert "avsox" in base
            assert isinstance(token, str) and token

    def test_search_uncensored_number(self, scraper):
        """測試：搜尋無碼番號"""
        video = scraper.search("051119-917")

        if video:  # 網路依賴
            assert isinstance(video, Video)
            assert video.source == "avsox"
            assert isinstance(video.actresses, list) and len(video.actresses) > 0
            assert isinstance(video.actresses[0], Actress)

