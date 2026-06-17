"""
test_extra_paths_live.py - 桶 C：額外 live 路徑 Smoke Tests

TASK-73e-T5：將桶 C 9 個源函數精簡重組為 8 個 slim live 函數。
統一連線失敗策略為顯式 pytest.skip，確保無網路時全 skip 不報 P/F。

執行方式：
    pytest tests/smoke/test_extra_paths_live.py -v -m smoke

注意：
- 只用於本地手動測試，不進 CI（避免被 ban）
- 無法連線時自動 skip，不算失敗
"""

import pytest
from core.scraper import search_jav, search_actress, smart_search
from core.scrapers import JAV321Scraper, JavBusScraper
from core.scrapers.models import Video

pytestmark = pytest.mark.smoke

# ========== 多語言測試番號 ==========

NUMBER_MULTILANG = "SNOS-143"


# ========== 1. 舊 API fan-out + legacy dict 合約 ==========

def test_auto_fanout_and_legacy_dict():
    """search_jav auto 模式：多源 fan-out；result dict 含 actors list（to_legacy_dict 合約）

    源自：TestOldAPIConnectivity::test_auto_source_connectivity（test_scraper_live.py:24）
    """
    result = search_jav("MIDV-139", source="auto")
    if result is None:
        pytest.skip("所有爬蟲來源無法連線（可能被網站封鎖或網路問題）")

    assert result.get('number'), "無番號返回"
    assert result.get('title') not in (None, ""), \
        f"標題為空或 None，實際值: {result.get('title')!r}"
    # search_jav 透過 to_legacy_dict() 回傳，女優欄位名稱為 'actors'（字串列表）
    actors = result.get('actors', [])
    assert isinstance(actors, list), \
        f"'actors' 欄位應為 list，實際型別: {type(actors).__name__}"


# ========== 2. 女優搜尋 ==========

def test_actress_search():
    """search_actress 連通性：回傳 list 含至少 1 筆結果且有番號

    源自：TestActressSearch::test_actress_search_connectivity（test_scraper_live.py:45）
    """
    results = search_actress("三上悠亞", limit=5)
    if not results:
        pytest.skip("女優搜尋無法連線（可能被網站封鎖）")

    assert len(results) >= 1, "至少應返回 1 個結果"
    assert results[0].get('number'), "結果應包含番號"


# ========== 3. smart_search uncensored 路由 ==========

@pytest.mark.parametrize("number,desc", [
    ("FC2-PPV-2200414", "fc2"),
    ("010120-001", "1pondo"),
])
def test_smart_search_uncensored_mode(number, desc):
    """smart_search 對無碼番號觸發 uncensored 路由（liveness 確認）

    精簡自 TestSpecialNumbers::test_uncensored_smart_search × 5（test_scraper_live.py:70）
    → 保留 2 個代表性 case（FC2-PPV 前綴 + 純日期型），路由 deterministic 邏輯已由 unit test 覆蓋。
    只確認 _mode == 'uncensored'，不重測路由選源邏輯。
    """
    results = smart_search(number, limit=1)
    if not results:
        pytest.skip(f"{number} ({desc}) 無法搜尋到結果")
    assert results[0].get('_mode') == 'uncensored', \
        f"{number} ({desc}) 應為 uncensored 模式，實際: {results[0].get('_mode')}"


# ========== 4. JAV321 關鍵字搜尋 ==========

def test_jav321_keyword_search():
    """JAV321Scraper.search_by_keyword 回傳 list[Video]，各筆 title/number 非空

    源自：TestJAV321Scraper::test_search_by_keyword（test_scrapers.py:27）
    修正：原 if-pass-through 改為顯式 pytest.skip，確保連不上時報 S 而非空 pass。
    """
    scraper = JAV321Scraper()
    results = scraper.search_by_keyword("天使もえ", limit=5)

    assert isinstance(results, list)
    if not results:
        pytest.skip("JAV321 search_by_keyword 無法連線或回傳空列表（可能被網站封鎖）")

    assert len(results) <= 5
    for video in results:
        assert isinstance(video, Video)
        assert isinstance(video.title, str) and len(video.title) > 0
        assert video.number is not None and len(video.number) > 0


# ========== 5. JavBus 關鍵字搜尋（C-3b + C-3c 合併） ==========

def test_javbus_keyword_search():
    """JavBusScraper.search_by_keyword 回傳 Video list + 第一筆基本欄位驗證

    合併自：
    - TestJavBusSmokeKeyword::test_search_by_keyword_returns_videos（test_javbus_smoke.py:36）
    - TestJavBusSmokeKeyword::test_search_by_keyword_video_has_fields（test_javbus_smoke.py:51）
    一次呼叫 keyword="三上悠亞"，驗 Video list 結構（number + source）+ 第一筆 title/cover_url。
    """
    scraper = JavBusScraper(lang="zh-tw")
    results = scraper.search_by_keyword("三上悠亞", limit=5)

    if not results:
        pytest.skip("JavBus search_by_keyword 無法連線或回傳空列表（可能被網站封鎖）")

    assert isinstance(results, list)
    assert len(results) <= 5, f"超過 limit=5，實際: {len(results)}"

    for v in results:
        assert isinstance(v, Video), f"結果包含非 Video 物件: {type(v)}"
        assert v.number, "Video.number 為空"
        assert v.source == "javbus", f"source 不符: {v.source!r}"

    first = results[0]
    assert isinstance(first.title, str) and len(first.title) > 0, \
        "第一筆結果 title 為空"
    assert first.cover_url.startswith("http"), \
        f"第一筆結果 cover_url 格式錯誤: {first.cover_url!r}"


# ========== 6. JavBus get_ids_from_search ==========

def test_javbus_get_ids_from_search():
    """JavBusScraper.get_ids_from_search 回傳 list[str]，無空元素

    源自：TestJavBusSmokeKeyword::test_get_ids_from_search_returns_list（test_javbus_smoke.py:64）
    """
    scraper = JavBusScraper(lang="zh-tw")
    try:
        ids = scraper.get_ids_from_search("SONE")
    except Exception as e:
        pytest.skip(f"JavBus get_ids_from_search 連線問題: {e}")

    if not ids:
        pytest.skip("JavBus get_ids_from_search 無法連線或回傳空列表（可能被網站封鎖）")

    assert isinstance(ids, list), f"回傳型別應為 list，實際: {type(ids)}"
    assert all(isinstance(i, str) for i in ids), "ids 包含非 str 元素"
    assert all(len(i) > 0 for i in ids), "ids 包含空字串"


# ========== 7. JavBus 多語言 tags 差異 ==========

def test_javbus_multilang_tags_differ():
    """同一番號 zh-tw vs ja 的 tags 文字內容應不同（不同語言翻譯）

    源自：TestJavBusSmokeMultilang::test_zh_tw_vs_ja_tags_differ（test_javbus_smoke.py:81）
    """
    scraper_tw = JavBusScraper(lang="zh-tw")
    scraper_ja = JavBusScraper(lang="ja")

    try:
        video_tw = scraper_tw.search(NUMBER_MULTILANG)
        video_ja = scraper_ja.search(NUMBER_MULTILANG)
    except Exception as e:
        pytest.skip(f"JavBus 連線問題: {e}")

    if video_tw is None or video_ja is None:
        pytest.skip("JavBus 無法連線（zh-tw 或 ja 任一失敗），跳過多語言測試")

    assert isinstance(video_tw.tags, list) and len(video_tw.tags) > 0, \
        "zh-tw tags 為空列表"
    assert isinstance(video_ja.tags, list) and len(video_ja.tags) > 0, \
        "ja tags 為空列表"

    assert video_tw.tags != video_ja.tags, \
        f"zh-tw 和 ja 的 tags 應因語言而異\nzh-tw: {video_tw.tags}\nja: {video_ja.tags}"


# ========== 8. JavBus 多語言 number 一致性 ==========

def test_javbus_multilang_number_consistent():
    """不同語言搜尋同一番號，number 應一致且 source 均為 javbus

    源自：TestJavBusSmokeMultilang::test_zh_tw_vs_ja_number_consistent（test_javbus_smoke.py:106）
    """
    scraper_tw = JavBusScraper(lang="zh-tw")
    scraper_ja = JavBusScraper(lang="ja")

    try:
        video_tw = scraper_tw.search(NUMBER_MULTILANG)
        video_ja = scraper_ja.search(NUMBER_MULTILANG)
    except Exception as e:
        pytest.skip(f"JavBus 連線問題: {e}")

    if video_tw is None or video_ja is None:
        pytest.skip("JavBus 無法連線（zh-tw 或 ja 任一失敗），跳過多語言一致性測試")

    assert video_tw.number == video_ja.number == NUMBER_MULTILANG, \
        f"番號不一致: tw={video_tw.number!r}, ja={video_ja.number!r}"
    assert video_tw.source == video_ja.source == "javbus"
