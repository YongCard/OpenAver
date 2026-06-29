from core.scrapers.kingdom import KingDomScraper, is_kingdom_number


SEARCH_HTML = """
<html><body>
  <a href="https://kingdom.vc/products/detail/1111">older</a>
  <a href="/products/detail/2222">target</a>
  <a href="/products/detail/2222">duplicate</a>
</body></html>
"""

DETAIL_OTHER_HTML = """
<html><head><meta property="og:title" content="別作品/尾崎ヒカル"></head>
<body>
  <h2 class="detail-title">別作品/尾崎ヒカル</h2>
  <span class="product-code-default">KIDM-1173</span>
</body></html>
"""

DETAIL_TARGET_HTML = """
<html>
<head>
  <meta property="og:title" content="甘い恋心/尾崎ヒカル" />
  <meta property="og:image" content="https://kingdom.vc//html/upload/save_image/1174.jpg" />
  <meta property="og:description" content="そそられるボディ。" />
</head>
<body>
  <h2 class="detail-title">甘い恋心/尾崎ヒカル</h2>
  <div class="item_visual">
    <img src="/html/upload/save_image/1174.jpg">
    <img src="/html/upload/save_image/kidm1174.jpg">
  </div>
  <div class="detail-profile__meta__desc"><p>そそられるボディ。</p></div>
  <div class="table table-product">
    <div class="tr"><div class="th">発売日</div><div class="td">2025/11/07</div></div>
    <div class="tr"><div class="th">商品番号</div><div class="td"><span class="product-code-default">KIDM-1174</span></div></div>
    <div class="tr"><div class="th">関連カテゴリ</div><div class="td">
      <a>メディア</a><a>レーベル</a><a>Queen</a><a>DVD</a>
    </div></div>
    <div class="tr"><div class="th">女優名</div><div class="td"><a>尾崎ヒカル</a></div></div>
  </div>
</body>
</html>
"""

DETAIL_MINIMAL_HTML = """
<html><body>
  <h2 class="detail-title">最小作品</h2>
  <span class="product-code-default">KIDM-9999</span>
</body></html>
"""


def test_is_kingdom_number_prefix_gate():
    assert is_kingdom_number("KIDM-1174") is True
    assert is_kingdom_number("kidm1174") is True
    assert is_kingdom_number("SONE-205") is False


def test_extract_detail_links_deduplicates_relative_and_absolute_urls():
    scraper = KingDomScraper()

    links = scraper._extract_detail_links(SEARCH_HTML)

    assert links == [
        "https://kingdom.vc/products/detail/1111",
        "https://kingdom.vc/products/detail/2222",
    ]


def test_parse_detail_extracts_video_fields():
    scraper = KingDomScraper()

    video = scraper._parse_detail(
        DETAIL_TARGET_HTML,
        "https://kingdom.vc/products/detail/2222",
        requested_number="KIDM-1174",
    )

    assert video is not None
    assert video.number == "KIDM-1174"
    assert video.title == "甘い恋心/尾崎ヒカル"
    assert [a.name for a in video.actresses] == ["尾崎ヒカル"]
    assert video.date == "2025-11-07"
    assert video.maker == "King Dom"
    assert video.label == "Queen"
    assert video.cover_url == "https://kingdom.vc//html/upload/save_image/1174.jpg"
    assert video.detail_url == "https://kingdom.vc/products/detail/2222"
    assert video.summary == "そそられるボディ。"
    assert "Queen" in video.tags
    assert video.source == "kingdom"


def test_parse_detail_rejects_non_matching_number():
    scraper = KingDomScraper()

    assert scraper._parse_detail(DETAIL_OTHER_HTML, "https://kingdom.vc/products/detail/1111", requested_number="KIDM-1174") is None


def test_parse_detail_missing_optional_fields_does_not_crash():
    scraper = KingDomScraper()

    video = scraper._parse_detail(
        DETAIL_MINIMAL_HTML,
        "https://kingdom.vc/products/detail/9999",
        requested_number="KIDM-9999",
    )

    assert video is not None
    assert video.number == "KIDM-9999"
    assert video.title == "最小作品"
    assert video.date == ""
    assert video.actresses == []
    assert video.cover_url == ""


def test_search_walks_detail_links_until_exact_number(monkeypatch):
    scraper = KingDomScraper()
    pages = {
        "https://kingdom.vc/products/list?name=KIDM-1174": SEARCH_HTML,
        "https://kingdom.vc/products/detail/1111": DETAIL_OTHER_HTML,
        "https://kingdom.vc/products/detail/2222": DETAIL_TARGET_HTML,
    }
    monkeypatch.setattr(scraper, "_get_html", lambda url: pages.get(url))
    monkeypatch.setattr("core.scrapers.kingdom.rate_limit", lambda *_args, **_kwargs: None)

    video = scraper.search("KIDM-1174")

    assert video is not None
    assert video.number == "KIDM-1174"
    assert video.source == "kingdom"


def test_search_returns_none_for_non_kingdom_prefix(monkeypatch):
    scraper = KingDomScraper()
    called = False

    def fake_get_html(_url):
        nonlocal called
        called = True
        return SEARCH_HTML

    monkeypatch.setattr(scraper, "_get_html", fake_get_html)

    assert scraper.search("SONE-205") is None
    assert called is False
