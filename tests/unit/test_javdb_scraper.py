"""
test_javdb_scraper.py - JavDB 爬蟲單元測試（TASK-73c-T3）

測試策略：
- 全 mock，不連網，不需 curl_cffi
- patch scraper._get_html 回傳 HTML fixture 或 inline HTML
- rate_limit 也 mock 掉（避免 sleep）

fixture 實際解析值（73b T2 落檔）：
  search fixture: SONE-103 → /v/Ww9zN8
  detail fixture:
    cover src = https://c0.jdbstatic.com/covers/ww/Ww9zN8.jpg（非 ps.jpg）
    女優: つばさ舞  (sibling classes: ['symbol', 'female'])
    男優: 結城結弦  (sibling classes: ['symbol', 'male'])  → 應被過濾
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ============================================================
# Fixture HTML 載入（file-based fixtures）
# ============================================================

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "scrapers"

SEARCH_HTML = (_FIXTURE_DIR / "javdb_SONE-103_search.html").read_text(encoding="utf-8")
DETAIL_HTML = (_FIXTURE_DIR / "javdb_SONE-103.html").read_text(encoding="utf-8")

# ============================================================
# Inline HTML — no-match search（用一個不匹配的番號結果頁）
# ============================================================

NO_MATCH_SEARCH_HTML = """\
<html><body>
<div class="movie-list">
  <div class="item">
    <div class="video-title"><strong>FONE-103</strong></div>
    <a href="/v/qbgGP"></a>
  </div>
</div>
</body></html>
"""

# ============================================================
# Inline HTML — cover 含 ps.jpg（觸發 ps→pl 升級）
# ============================================================

PS_COVER_SEARCH_HTML = """\
<html><body>
<div class="movie-list">
  <div class="item">
    <div class="video-title"><strong>SONE-103</strong></div>
    <a href="/v/Ww9zN8"></a>
  </div>
</div>
</body></html>
"""

PS_COVER_DETAIL_HTML = """\
<html><body>
<div class="video-cover">
  <img src="https://pics.example.com/covers/ps.jpg">
</div>
<div class="panel-block">
  <strong>演員:</strong>
  <div class="value">
    <a href="/actors/1">テスト女優</a>
    <span class="symbol female">♀</span>
  </div>
</div>
</body></html>
"""


# ============================================================
# Helper
# ============================================================

def run_search(scraper, search_html: str, detail_html: str, number: str = "SONE-103"):
    """
    patch _get_html: first call returns search_html, second returns detail_html.
    """
    with patch.object(
        scraper,
        "_get_html",
        side_effect=[search_html, detail_html],
    ):
        return scraper.search(number)


# ============================================================
# pytest Fixture
# ============================================================

@pytest.fixture
def scraper():
    from core.scrapers.javdb import JavDBScraper
    with patch("core.scrapers.javdb.rate_limit"):
        s = JavDBScraper()
        yield s


# ============================================================
# Tests
# ============================================================

class TestJavdbNumberMatch:
    """番號比對：match / no-match"""

    def test_match_returns_video_with_number(self, scraper):
        """search+detail fixture → 非 None，video.number == 'SONE-103'"""
        video = run_search(scraper, SEARCH_HTML, DETAIL_HTML)
        assert video is not None
        assert video.number == "SONE-103"

    def test_no_match_returns_none(self, scraper):
        """搜尋結果僅 FONE-103（不匹配） → 回 None（比對迴圈走完無 match）"""
        video = run_search(scraper, NO_MATCH_SEARCH_HTML, DETAIL_HTML)
        assert video is None


class TestJavdbGenderFilter:
    """性別過濾：男優 sibling class 包含 'male' 且不含 'female' → 排除"""

    def test_actress_only_female(self, scraper):
        """
        fixture 演員 panel 含：
          つばさ舞  sibling=['symbol','female'] → 保留
          結城結弦  sibling=['symbol','male']   → 過濾
        → video.actresses 只有 つばさ舞
        """
        video = run_search(scraper, SEARCH_HTML, DETAIL_HTML)
        assert video is not None
        actress_names = [a.name for a in video.actresses]
        assert "つばさ舞" in actress_names
        assert "結城結弦" not in actress_names


class TestJavdbCoverUpgrade:
    """ps.jpg → pl.jpg 升級"""

    def test_ps_cover_upgraded_to_pl(self, scraper):
        """inline detail HTML cover src 含 ps.jpg → video.cover_url 含 pl.jpg"""
        video = run_search(scraper, PS_COVER_SEARCH_HTML, PS_COVER_DETAIL_HTML)
        assert video is not None
        assert "pl.jpg" in video.cover_url
        assert "ps.jpg" not in video.cover_url


class TestJavdbValidateGuard:
    """validate_number 失敗 → ValueError"""

    def test_invalid_number_raises(self, scraper):
        """'invalid!!!' → ValueError（不進網路）"""
        with pytest.raises(ValueError):
            scraper.search("invalid!!!")


class TestJavdbGetHtmlNone:
    """_get_html 回 None → search 回 None（空 HTML guard）"""

    def test_get_html_none_returns_none(self, scraper):
        """_get_html side_effect=[None] → search 短路回 None"""
        with patch.object(scraper, "_get_html", side_effect=[None]):
            result = scraper.search("SONE-103")
        assert result is None


class TestJavdbTags:
    """tags 解析：從 fixture SONE-103 detail 頁驗證 video.tags"""

    def test_javdb_tags_from_fixture(self, scraper):
        """
        用 SEARCH_HTML + DETAIL_HTML（javdb_SONE-103.html）餵 run_search，
        驗證 video.tags 為非空 list[str] 且含穩定錨點 tag「戲劇」。

        fixture 行 373–375 含 7 個 tag：戲劇、乳交、巨乳、單體作品、苗條、按摩、妹妹
        只斷言型別 + 非空 + 一個穩定 tag，不斷言完整 list 或順序。
        """
        video = run_search(scraper, SEARCH_HTML, DETAIL_HTML)
        assert video is not None

        # 層 1：型別 + 非空
        assert isinstance(video.tags, list) and len(video.tags) > 0

        # 層 2：每個元素都是 str
        assert all(isinstance(t, str) for t in video.tags)

        # 層 3：含穩定錨點 tag
        assert "戲劇" in video.tags
