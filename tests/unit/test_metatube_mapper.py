"""test_metatube_mapper.py - metatube mapper + clean_metatube_summary 邊界測試"""
import pytest
from core.scrapers.models import Actress


# ============ Fixture ============

def _full_info() -> dict:
    """模仿 FANZA POC 完整 22 欄回傳"""
    return {
        "id": "abc123",
        "number": "SONE-205",
        "title": "テストタイトル",
        "summary": "これはテスト用の簡介です。",
        "provider": "FANZA",
        "homepage": "https://fanza.com/sone-205",
        "director": "山田太郎",
        "actors": ["女優A", "女優B"],
        "thumb_url": "https://img.fanza.com/thumb.jpg",
        "big_thumb_url": "https://img.fanza.com/big_thumb.jpg",
        "cover_url": "https://img.fanza.com/cover.jpg",
        "big_cover_url": "",
        "preview_video_url": "",
        "preview_video_hls_url": "",
        "preview_images": ["https://img.fanza.com/s1.jpg", "https://img.fanza.com/s2.jpg"],
        "maker": "S1 NO.1 STYLE",
        "label": "S1",
        "series": "テストシリーズ",
        "genres": ["巨乳", "美少女"],
        "score": 8.5,
        "runtime": 120,
        "release_date": "2024-01-15T00:00:00Z",
    }


# ============ 主映射測試 ============

def test_map_full_info_basic_fields():
    """完整 dict → Video 基本欄 1:1"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    video = map_movie_info(info)

    assert video.number == "SONE-205"
    assert video.title == "テストタイトル"
    assert video.maker == "S1 NO.1 STYLE"
    assert video.director == "山田太郎"
    assert video.label == "S1"
    assert video.series == "テストシリーズ"


def test_map_full_info_actresses():
    """actors list → actresses list[Actress]"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.actresses == [Actress(name="女優A"), Actress(name="女優B")]


def test_map_full_info_date():
    """release_date T 格式 → date YYYY-MM-DD"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.date == "2024-01-15"


def test_map_full_info_tags_detail_url_sample_images():
    """genres→tags, homepage→detail_url, preview_images→sample_images"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.tags == ["巨乳", "美少女"]
    assert video.detail_url == "https://fanza.com/sone-205"
    assert video.sample_images == ["https://img.fanza.com/s1.jpg", "https://img.fanza.com/s2.jpg"]


def test_map_full_info_cover_url():
    """cover_url 1:1"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.cover_url == "https://img.fanza.com/cover.jpg"


def test_map_full_info_rating_nonzero():
    """score 非零 → rating passthrough"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.rating == 8.5


def test_map_full_info_duration_nonzero():
    """runtime 非零 → duration passthrough"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.duration == 120


def test_map_full_info_source():
    """provider → source = "metatube:FANZA" """
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.source == "metatube:FANZA"


def test_map_full_info_summary():
    """summary 非 FC2 → clean passthrough"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert video.summary == "これはテスト用の簡介です。"


def test_map_full_info_us7_summary_not_in_legacy_dict():
    """US7 硬契約：summary 永不入 to_legacy_dict()"""
    from core.metatube.mapper import map_movie_info
    video = map_movie_info(_full_info())
    assert "summary" not in video.to_legacy_dict()


def test_map_empty_actors():
    """actors=[] → actresses=[], 不炸"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["actors"] = []
    video = map_movie_info(info)
    assert video.actresses == []


def test_map_actors_with_empty_string():
    """actors 含空字串 → 過濾，不觸發 Actress min_length=1 ValidationError"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["actors"] = ["Alice", "", "Bob"]
    video = map_movie_info(info)
    assert video.actresses == [Actress(name="Alice"), Actress(name="Bob")]


def test_map_score_zero_to_none():
    """score=0.0 → rating is None"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["score"] = 0.0
    video = map_movie_info(info)
    assert video.rating is None


def test_map_runtime_zero_to_none():
    """runtime=0 → duration is None（CD-63a-9 plan 拍板）"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["runtime"] = 0
    video = map_movie_info(info)
    assert video.duration is None


def test_map_release_date_no_t():
    """release_date 無 T → date 原樣（split("T")[0] 安全）"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["release_date"] = "2024-01-15"
    video = map_movie_info(info)
    assert video.date == "2024-01-15"


def test_map_release_date_empty():
    """release_date="" → date="" 不炸"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["release_date"] = ""
    video = map_movie_info(info)
    assert video.date == ""


def test_map_release_date_null():
    """release_date=None（JSON null）→ date="" 不炸（`or ""` 攔 None，不走 None.split）"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["release_date"] = None
    video = map_movie_info(info)
    assert video.date == ""


def test_map_missing_optional_keys():
    """缺 series/label/director/preview_images/genres → 預設值，不炸"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    for k in ("series", "label", "director", "preview_images", "genres"):
        info.pop(k, None)
    video = map_movie_info(info)
    assert video.series == ""
    assert video.label == ""
    assert video.director == ""
    assert video.sample_images == []
    assert video.tags == []


def test_map_actors_none():
    """actors=None → actresses=[], 不炸"""
    from core.metatube.mapper import map_movie_info
    info = _full_info()
    info["actors"] = None
    video = map_movie_info(info)
    assert video.actresses == []


# ============ clean_metatube_summary 邊界測試 ============

def test_clean_fc2_base64_truncated():
    """FC2 含 ≥40 字元 base64 blob → 截斷至 blob 前"""
    from core.metatube.mapper import clean_metatube_summary
    blob = "A" * 45
    raw = f"正常文字{blob}後面"
    result = clean_metatube_summary("FC2", raw)
    assert "A" * 45 not in result
    assert "正常文字" in result


def test_clean_fc2_base64_all_noise():
    """FC2 全是 base64 → 截斷後空 → ''"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "A" * 45
    result = clean_metatube_summary("FC2", raw)
    assert result == ""


def test_clean_fc2_script_tag():
    """FC2 含 <script → 截斷至 <script 前"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "正常文字<script>alert(1)</script>"
    result = clean_metatube_summary("FC2", raw)
    assert result == "正常文字"


def test_clean_fc2_function_marker():
    """FC2 含 function( → 截斷"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "簡介 function(a,b){}"
    result = clean_metatube_summary("FC2", raw)
    assert result == "簡介"


def test_clean_fc2_double_brace():
    """FC2 含 {{ → 截斷"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "內容 {{template}}"
    result = clean_metatube_summary("FC2", raw)
    assert result == "內容"


def test_clean_fc2_overlength():
    """FC2 超長 >500 字（無雜訊）→ 限 500"""
    from core.metatube.mapper import clean_metatube_summary
    # 使用 CJK 字元避免觸發 base64-like regex
    raw = "あ" * 1000
    result = clean_metatube_summary("FC2", raw)
    assert len(result) == 500


def test_clean_fc2_empty():
    """FC2 空字串 → ''"""
    from core.metatube.mapper import clean_metatube_summary
    result = clean_metatube_summary("FC2", "")
    assert result == ""


def test_clean_fc2_no_noise():
    """FC2 無雜訊 → passthrough"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "正常日文簡介"
    result = clean_metatube_summary("FC2", raw)
    assert result == "正常日文簡介"


def test_clean_non_fc2_normal():
    """非 FC2 正常 passthrough"""
    from core.metatube.mapper import clean_metatube_summary
    result = clean_metatube_summary("JavBus", "正常簡介")
    assert result == "正常簡介"


def test_clean_non_fc2_overlength():
    """非 FC2 超長 → 限 500（unicode-safe）"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "あ" * 600
    result = clean_metatube_summary("HEYZO", raw)
    assert len(result) == 500


def test_clean_non_fc2_empty():
    """非 FC2 空 → ''"""
    from core.metatube.mapper import clean_metatube_summary
    result = clean_metatube_summary("DUGA", "")
    assert result == ""


def test_clean_fc2hub_is_fc2_family():
    """fc2hub 是 FC2 系，走 FC2 清理路徑"""
    from core.metatube.mapper import clean_metatube_summary
    raw = "簡介<script>noise</script>"
    result = clean_metatube_summary("fc2hub", raw)
    assert result == "簡介"


def test_clean_fc2ppvdb_is_fc2_family():
    """FC2PPVDB 是 FC2 系，base64 截斷"""
    from core.metatube.mapper import clean_metatube_summary
    blob = "B" * 50 + "=="
    raw = f"FC2PPVDB簡介{blob}"
    result = clean_metatube_summary("FC2PPVDB", raw)
    assert "B" * 50 not in result
    assert "FC2PPVDB簡介" in result
