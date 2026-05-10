import math

import pytest

from core.database import Video
from core.similar.ranker import (
    SimilarRanker,
    gaussian_year_proximity,
    same_duration_bucket,
)


def _v(
    *,
    tags: list[str] | None = None,
    actresses: list[str] | None = None,
    series: str | None = None,
    maker: str = "",
    duration: int | None = None,
    release_date: str = "",
    number: str | None = None,
) -> Video:
    return Video(
        number=number,
        tags=tags or [],
        actresses=actresses or [],
        series=series,
        maker=maker,
        duration=duration,
        release_date=release_date,
    )


def _padded_ranker(extra: list[Video], pad: int = 30) -> SimilarRanker:
    # padding 讓 rare tag IDF > 0 且 jaccard 可計算
    padding = [_v(tags=[f"pad_{i}"]) for i in range(pad)]
    return SimilarRanker(padding + extra)


# --- helper: gaussian_year_proximity ---

def test_year_proximity_diff_zero():
    a = _v(release_date="2020")
    b = _v(release_date="2020")
    assert gaussian_year_proximity(a, b, sigma=4) == pytest.approx(1.0, abs=1e-6)


def test_year_proximity_diff_four():
    a = _v(release_date="2024")
    b = _v(release_date="2020")
    assert gaussian_year_proximity(a, b, sigma=4) == pytest.approx(0.6065, abs=0.01)


def test_year_proximity_diff_twelve():
    a = _v(release_date="2032")
    b = _v(release_date="2020")
    assert gaussian_year_proximity(a, b, sigma=4) == pytest.approx(0.0111, abs=0.01)


def test_year_proximity_empty_string():
    assert gaussian_year_proximity(_v(release_date=""), _v(release_date="2020")) == 0.0


def test_year_proximity_na():
    assert gaussian_year_proximity(_v(release_date="N/A"), _v(release_date="2020")) == 0.0


def test_year_proximity_garbage():
    assert gaussian_year_proximity(_v(release_date="abc"), _v(release_date="2020")) == 0.0


def test_year_proximity_three_formats_all_parse():
    target = _v(release_date="2020")
    expected = math.exp(-0.5 * (0 / 4) ** 2)
    for fmt in ("2020", "2020-07", "2020-07-15"):
        assert gaussian_year_proximity(_v(release_date=fmt), target) == pytest.approx(expected, abs=1e-6)


# --- helper: same_duration_bucket ---

def test_duration_bucket_both_none_false():
    assert same_duration_bucket(_v(duration=None), _v(duration=None)) is False


def test_duration_bucket_both_zero_false():
    assert same_duration_bucket(_v(duration=0), _v(duration=0)) is False


def test_duration_bucket_one_none_one_value_false():
    assert same_duration_bucket(_v(duration=None), _v(duration=30)) is False
    assert same_duration_bucket(_v(duration=30), _v(duration=None)) is False


def test_duration_bucket_short_short_true():
    assert same_duration_bucket(_v(duration=15), _v(duration=18)) is True


def test_duration_bucket_mid_mid_true():
    assert same_duration_bucket(_v(duration=30), _v(duration=50)) is True


def test_duration_bucket_long_long_true():
    assert same_duration_bucket(_v(duration=80), _v(duration=120)) is True


def test_duration_bucket_short_mid_false():
    assert same_duration_bucket(_v(duration=15), _v(duration=30)) is False


def test_duration_bucket_mid_long_false_boundary():
    # 60 -> mid (≤60); 61 -> long
    assert same_duration_bucket(_v(duration=60), _v(duration=61)) is False


def test_duration_bucket_short_mid_boundary():
    # 20 -> short (≤20); 21 -> mid
    assert same_duration_bucket(_v(duration=20), _v(duration=21)) is False


# --- _score: base / series / maker ---

def test_score_series_bonus_diff_actress():
    target = _v(tags=["rareA"], series="SUPER", actresses=["alice"])
    cand = _v(tags=["rareA"], series="SUPER", actresses=["bob"])
    r = _padded_ranker([target, cand])
    base_only = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([target, base_only])
    bare = rb._score(target, base_only)
    boosted = r._score(target, cand)
    assert boosted == pytest.approx(bare + 0.30, abs=1e-6)


def test_score_series_target_none_no_bonus():
    target = _v(tags=["rareA"], series=None, actresses=["alice"])
    cand = _v(tags=["rareA"], series="SUPER", actresses=["bob"])
    r = _padded_ranker([target, cand])
    bare_cand = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([target, bare_cand])
    assert r._score(target, cand) == pytest.approx(rb._score(target, bare_cand), abs=1e-6)


def test_score_series_cand_none_no_bonus():
    target = _v(tags=["rareA"], series="SUPER", actresses=["alice"])
    cand = _v(tags=["rareA"], series=None, actresses=["bob"])
    r = _padded_ranker([target, cand])
    assert r._score(target, cand) == pytest.approx(r._score(target, _v(tags=["rareA"], actresses=["bob"])), abs=1e-6)


def test_score_maker_bonus():
    target = _v(tags=["rareA"], maker="MK1", actresses=["alice"])
    cand = _v(tags=["rareA"], maker="MK1", actresses=["bob"])
    r = _padded_ranker([target, cand])
    bare_cand = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([target, bare_cand])
    assert r._score(target, cand) == pytest.approx(rb._score(target, bare_cand) + 0.20, abs=1e-6)


def test_score_maker_both_empty_no_bonus():
    target = _v(tags=["rareA"], maker="", actresses=["alice"])
    cand = _v(tags=["rareA"], maker="", actresses=["bob"])
    r = _padded_ranker([target, cand])
    assert r._score(target, cand) == pytest.approx(r._score(target, _v(tags=["rareA"], actresses=["bob"])), abs=1e-6)


# --- _score: cast bucket ---

def test_score_cast_solo_solo_no_bonus():
    target = _v(tags=["rareA"], actresses=["alice"])
    cand = _v(tags=["rareA"], actresses=["bob"])
    r = _padded_ranker([target, cand])
    # solo-solo 不加成；同時無 actress overlap → 不扣
    bare_cand = _v(tags=["rareA"])
    rb = _padded_ranker([target, bare_cand])
    # cand 有 1 個女優而 bare 無 → cast bucket 仍是 solo vs none → 都不加成 → 應相等
    assert r._score(target, cand) == pytest.approx(rb._score(target, bare_cand), abs=1e-6)


def test_score_cast_duo_duo_bonus():
    target = _v(tags=["rareA"], actresses=["alice", "ann"])
    cand = _v(tags=["rareA"], actresses=["bob", "ben"])
    r = _padded_ranker([target, cand])
    # cast duo-duo +0.20，無 overlap 不扣
    target_zero = _v(tags=["rareA"], actresses=["alice", "ann"])
    cand_solo = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([target_zero, cand_solo])
    # 對照：duo vs solo 不加成
    assert r._score(target, cand) == pytest.approx(rb._score(target_zero, cand_solo) + 0.20, abs=1e-6)


def test_score_cast_multi_multi_bonus():
    target = _v(tags=["rareA"], actresses=["a", "b", "c"])
    cand = _v(tags=["rareA"], actresses=["x", "y", "z"])
    r = _padded_ranker([target, cand])
    cand_solo = _v(tags=["rareA"], actresses=["x"])
    rb = _padded_ranker([_v(tags=["rareA"], actresses=["a", "b", "c"]), cand_solo])
    assert r._score(target, cand) == pytest.approx(rb._score(_v(tags=["rareA"], actresses=["a", "b", "c"]), cand_solo) + 0.20, abs=1e-6)


def test_score_cast_solo_vs_duo_no_bonus():
    target = _v(tags=["rareA"], actresses=["alice"])
    cand = _v(tags=["rareA"], actresses=["bob", "ben"])
    r = _padded_ranker([target, cand])
    # 不同 bucket 不加成
    cand2 = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([target, cand2])
    assert r._score(target, cand) == pytest.approx(rb._score(target, cand2), abs=1e-6)


def test_score_cast_none_none_no_bonus():
    target = _v(tags=["rareA"], actresses=[])
    cand = _v(tags=["rareA"], actresses=[])
    r = _padded_ranker([target, cand])
    cand_solo = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([target, cand_solo])
    # none-none 不加；none-solo 也不加 → 兩者相等
    assert r._score(target, cand) == pytest.approx(rb._score(target, cand_solo), abs=1e-6)


# --- _score: actress penalty ---

def test_score_actress_overlap_diff_series_minus_half():
    target = _v(tags=["rareA"], actresses=["alice"], series="S1")
    cand = _v(tags=["rareA"], actresses=["alice"], series="S2")
    r = _padded_ranker([target, cand])
    # 對照：無 overlap 同 cand
    cand_no_overlap = _v(tags=["rareA"], actresses=["bob"], series="S2")
    rb = _padded_ranker([target, cand_no_overlap])
    assert r._score(target, cand) == pytest.approx(rb._score(target, cand_no_overlap) - 0.50, abs=1e-6)


def test_score_actress_overlap_no_series_minus_half():
    target = _v(tags=["rareA"], actresses=["alice"], series=None)
    cand = _v(tags=["rareA"], actresses=["alice"], series=None)
    r = _padded_ranker([target, cand])
    cand_no = _v(tags=["rareA"], actresses=["bob"], series=None)
    rb = _padded_ranker([target, cand_no])
    assert r._score(target, cand) == pytest.approx(rb._score(target, cand_no) - 0.50, abs=1e-6)


def test_score_actress_overlap_same_series_minus_015():
    target = _v(tags=["rareA"], actresses=["alice"], series="SUPER")
    cand = _v(tags=["rareA"], actresses=["alice"], series="SUPER")
    r = _padded_ranker([target, cand])
    # 對照：同系列 + 不同女優（+0.30 series, no penalty）
    cand_diff_actress = _v(tags=["rareA"], actresses=["bob"], series="SUPER")
    rb = _padded_ranker([target, cand_diff_actress])
    # cand: base + 0.30 (series) - 0.15 (penalty)
    # cand_diff_actress: base + 0.30 (series)
    assert r._score(target, cand) == pytest.approx(rb._score(target, cand_diff_actress) - 0.15, abs=1e-6)


# --- _score: not clamped ---

def test_score_can_be_negative():
    # 無 useful tag 共有 → base ≈ 0；女優同 + 不同 series → -0.50；無其他加成
    target = _v(tags=["xtag"], actresses=["alice"], series="S1")
    cand = _v(tags=["ytag"], actresses=["alice"], series="S2")
    r = _padded_ranker([target, cand], pad=30)
    score = r._score(target, cand)
    assert score < 0


def test_score_can_exceed_one():
    # base ≈ 1 (target == cand tags 完全相同)；+ series + maker + year(diff=0) + duration + cast duo
    target = _v(
        tags=["rareA", "rareB"],
        actresses=["alice", "ann"],
        series="SUPER",
        maker="MK1",
        duration=30,
        release_date="2020",
    )
    cand = _v(
        tags=["rareA", "rareB"],
        actresses=["bob", "ben"],
        series="SUPER",
        maker="MK1",
        duration=40,
        release_date="2020",
    )
    r = _padded_ranker([target, cand], pad=50)
    score = r._score(target, cand)
    # base(~1) + 0.30 + 0.20 + 0.15 + 0.10 + 0.20 ~= 1.95
    assert score > 1.0


# --- _score: full integration sanity ---

def test_score_full_signals_combined():
    target = _v(
        tags=["rareA"],
        actresses=["alice"],
        series="SUPER",
        maker="MK1",
        duration=30,
        release_date="2020",
    )
    cand = _v(
        tags=["rareA"],
        actresses=["bob"],
        series="SUPER",
        maker="MK1",
        duration=50,
        release_date="2020",
    )
    r = _padded_ranker([target, cand])
    bare = _v(tags=["rareA"], actresses=["bob"])
    rb = _padded_ranker([_v(tags=["rareA"], actresses=["alice"]), bare])
    base = rb._score(_v(tags=["rareA"], actresses=["alice"]), bare)
    # 預期： base + series(0.30) + maker(0.20) + year(0.15*1.0) + duration(0.10) ；solo-solo 不加；無 overlap 不扣
    assert r._score(target, cand) == pytest.approx(base + 0.30 + 0.20 + 0.15 + 0.10, abs=1e-6)
