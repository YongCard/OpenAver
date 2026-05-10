import random

import pytest

from core.database import Video
from core.similar.ranker import SimilarRanker, extract_prefix


def _v(
    *,
    id: int | None = None,
    tags: list[str] | None = None,
    actresses: list[str] | None = None,
    series: str | None = None,
    maker: str = "",
    duration: int | None = None,
    release_date: str = "",
    number: str | None = None,
) -> Video:
    return Video(
        id=id,
        number=number,
        tags=tags or [],
        actresses=actresses or [],
        series=series,
        maker=maker,
        duration=duration,
        release_date=release_date,
    )


# ------------------------------------------------------------------
# extract_prefix — CD-57a-5 6 case
# ------------------------------------------------------------------

@pytest.mark.parametrize("number,expected", [
    ("SONE-205", "SONE"),
    ("FC2-PPV-12345", "FC2-PPV"),
    ("HEYZO-1234", "HEYZO"),
    ("Caribbean-123456-789", "CARIBBEAN"),
    ("1pondo-123456_001", None),
    ("", None),
    (None, None),
    ("12345", None),
    ("---", None),
])
def test_extract_prefix_cases(number, expected):
    assert extract_prefix(number) == expected


# ------------------------------------------------------------------
# Tier 1 happy path：≥2 共同 useful tag → MMR
# ------------------------------------------------------------------

def test_tier1_happy_path_returns_le_top_k():
    # padding 30 部讓 rare tag IDF > 0
    padding = [_v(id=1000 + i, tags=[f"pad_{i}"]) for i in range(30)]
    target = _v(id=1, tags=["rareA", "rareB", "rareC"])
    cands = [
        _v(id=10 + i, tags=["rareA", "rareB", f"u_{i}"])
        for i in range(5)
    ]
    r = SimilarRanker(padding + [target] + cands)
    out = r.rank(target, top_k=12)
    assert len(out) <= 12
    # 5 部 ≥2 共同 useful tag → 全進 Tier 1
    assert all(c.id is not None and 10 <= c.id < 15 for c in out[:5])


# ------------------------------------------------------------------
# Tier 2 補位：所有 corpus 與 target 只有 ≥1 共同 useful tag
# ------------------------------------------------------------------

def test_tier2_fills_when_only_one_shared_useful_tag():
    # padding 大量 → rare tag IDF > 0
    padding = [_v(id=2000 + i, tags=[f"pad_{i}"]) for i in range(30)]
    # target 帶 rareA + rareB；cands 只有 rareA + 自己 unique → 共 1 shared
    target = _v(id=1, tags=["rareA", "rareB"])
    cands = [
        _v(id=20 + i, tags=["rareA", f"u_{i}"])
        for i in range(8)
    ]
    r = SimilarRanker(padding + [target] + cands)
    out = r.rank(target, top_k=12)
    # Tier 1 池為空（≥2 過濾），靠 Tier 2 補 8 部 cands；剩 4 部由 Tier 4 補 padding
    cand_ids = {20 + i for i in range(8)}
    in_cand = [c for c in out if c.id in cand_ids]
    # 8 cands 必須全部進前 8 名（Tier 2 排序在 Tier 4 之前）
    assert len(in_cand) == 8
    front_ids = {c.id for c in out[:8]}
    assert front_ids == cand_ids
    assert len(out) == 12


# ------------------------------------------------------------------
# Tier 3 prefix 補位：target 0 useful tag → Tier 1/2 跳過
# ------------------------------------------------------------------

def test_tier3_prefix_fallback_when_no_useful_tag():
    # 12 部都用 unique rare tag，target 完全 0 共同 tag，但有 prefix
    padding = [_v(id=3000 + i, tags=[f"pad_{i}"]) for i in range(20)]
    target = _v(id=1, number="ABC-001", tags=["alone_tag"])
    same_prefix = [
        _v(id=30 + i, number=f"ABC-{i:03d}", tags=[f"x_{i}"])
        for i in range(2, 8)
    ]
    different = [
        _v(id=40 + i, number=f"XYZ-{i:03d}", tags=[f"y_{i}"])
        for i in range(5)
    ]
    r = SimilarRanker(padding + [target] + same_prefix + different)
    random.seed(42)  # 測試本地 seed，T3 內部不 seed
    out = r.rank(target, top_k=12)
    # 6 部 ABC prefix 全進 Tier 3
    abc_count = sum(1 for c in out if c.number and c.number.startswith("ABC"))
    assert abc_count == 6
    # 剩餘 6 部由 Tier 4 補（XYZ 5 部 + 部分 padding）
    assert len(out) == 12


# ------------------------------------------------------------------
# Tier 4 兜底：target prefix=None + 0 useful tag → Tier 4 random
# ------------------------------------------------------------------

def test_tier4_fallback_when_no_prefix_and_no_useful_tag():
    padding = [_v(id=4000 + i, tags=[f"pad_{i}"]) for i in range(20)]
    target = _v(id=1, number=None, tags=["alone_tag"])
    others = [_v(id=400 + i, tags=[f"u_{i}"]) for i in range(15)]
    r = SimilarRanker(padding + [target] + others)
    out = r.rank(target, top_k=12)
    assert len(out) == 12
    # 全部來自 corpus（不會包含 target 自己）
    assert target not in out


# ------------------------------------------------------------------
# 嚴格 1 → 2 → 3 → 4 順序
# ------------------------------------------------------------------

def test_strict_tier_order_1_2_3_4():
    # Tier 1 候選 3 部、Tier 2 候選 4 部、Tier 3 候選 5 部，target prefix=ABC
    padding = [_v(id=5000 + i, tags=[f"pad_{i}"]) for i in range(30)]
    target = _v(id=1, number="ABC-001", tags=["rareA", "rareB", "rareC"])
    tier1 = [
        _v(id=100 + i, number=f"OTH-{i:03d}", tags=["rareA", "rareB", f"unique{i}"])
        for i in range(3)
    ]
    tier2 = [
        _v(id=200 + i, number=f"OTHER-{i:03d}", tags=["rareA", f"u2_{i}"])
        for i in range(4)
    ]
    tier3 = [
        _v(id=300 + i, number=f"ABC-{i + 100:03d}", tags=[f"u3_{i}"])
        for i in range(5)
    ]
    r = SimilarRanker(padding + [target] + tier1 + tier2 + tier3)
    out = r.rank(target, top_k=12)

    # 前 3 部必須來自 tier1 ids
    tier1_ids = {100, 101, 102}
    tier2_ids = {200, 201, 202, 203}
    tier3_ids = {300, 301, 302, 303, 304}
    assert {c.id for c in out[:3]} == tier1_ids
    # 接下來 4 部必須是 tier2
    assert {c.id for c in out[3:7]} == tier2_ids
    # 接下來 5 部必須是 tier3
    assert {c.id for c in out[7:12]} == tier3_ids


# ------------------------------------------------------------------
# MMR diversity：corpus 含 5 部同片商連續 → top-12 不全是同片商前 5
# ------------------------------------------------------------------

def test_mmr_diversity_maker_concentration():
    # padding 60 部讓 rareA/rareB df/N < 0.25（target+12 = 13/73 ≈ 0.178）
    padding = [_v(id=6000 + i, tags=[f"pad_{i}"]) for i in range(60)]
    target = _v(id=1, tags=["rareA", "rareB"], maker="MK0", actresses=["actor_t"])
    same_maker = [
        _v(id=60 + i, tags=["rareA", "rareB", f"u_{i}"], maker="MK_SAME", actresses=[f"a_{i}"])
        for i in range(5)
    ]
    diverse = [
        _v(id=70 + i, tags=["rareA", "rareB", f"u_{i}"], maker=f"MK_{i}", actresses=[f"d_{i}"])
        for i in range(7)
    ]
    r = SimilarRanker(padding + [target] + same_maker + diverse)
    out = r.rank(target, top_k=12)
    # 全 12 部都進 Tier 1
    assert len(out) == 12
    same_count = sum(1 for c in out if c.maker == "MK_SAME")
    # MMR 減號：penalty 後同片商不會集中前 5（雖在 12 名內仍被全選，但 _sim 扣分使其排序靠後）
    assert same_count == 5
    # diversity 應分散：5 MK_SAME + 7 MK_i = 8 distinct makers
    makers = [c.maker for c in out]
    assert len(set(makers)) >= 6


# ------------------------------------------------------------------
# corpus 同女優連 3 部 → 結果多樣化
# ------------------------------------------------------------------

def test_mmr_diversity_actress_concentration():
    padding = [_v(id=7000 + i, tags=[f"pad_{i}"]) for i in range(60)]
    target = _v(id=1, tags=["rareA", "rareB"], actresses=["actor_t"])
    # 3 部同女優 actress_X，全有高 rel
    same_actress = [
        _v(id=80 + i, tags=["rareA", "rareB", f"u_{i}"], actresses=["actress_X"])
        for i in range(3)
    ]
    diverse = [
        _v(id=90 + i, tags=["rareA", "rareB", f"u_{i}"], actresses=[f"a_div_{i}"])
        for i in range(7)
    ]
    r = SimilarRanker(padding + [target] + same_actress + diverse)
    out = r.rank(target, top_k=12)
    actress_set = {tuple(c.actresses) for c in out}
    # MMR sim 扣分 → 不會 3 部同女優都聚前
    assert len(actress_set) >= 5


# ------------------------------------------------------------------
# P1-B regression：hot-only overlap（IDF=0） → 不走 Tier 1/2
# ------------------------------------------------------------------

def test_regression_p1b_hot_only_overlap_falls_to_tier3_or_4():
    # corpus 30 部都含「common」tag（df/N=30/30=1.0 > 0.25 → IDF=0 hot）
    # 各自 unique rare tag → IDF > 0
    corpus = [
        _v(id=100 + i, number=f"ABC-{i:03d}", tags=["common", f"unique_{i}"])
        for i in range(30)
    ]
    # target 只有 hot tag「common」
    target = _v(id=1, number="ABC-999", tags=["common"])
    r = SimilarRanker(corpus + [target])
    # target_useful 應為空（common IDF=0）
    target_useful = r._useful_set(target)
    assert target_useful == set()
    out = r.rank(target, top_k=12)
    assert len(out) == 12
    # 因 useful=∅，Tier 1/2 都跳過 → Tier 3 prefix=ABC 補滿
    assert all(c.number and c.number.startswith("ABC") for c in out)


# ------------------------------------------------------------------
# P1-A regression：MMR 符號減號（寫成 + 此 test 必 fail）
# ------------------------------------------------------------------

def test_regression_p1a_mmr_minus_sign_diversity():
    padding = [_v(id=8000 + i, tags=[f"pad_{i}"]) for i in range(60)]
    target = _v(id=1, tags=["rareA", "rareB"], actresses=["actor_t"], maker="MK_TARGET")
    # 5 部同女優同片商高 rel → 互相 sim=1.0
    cluster = [
        _v(id=200 + i, tags=["rareA", "rareB", f"c_{i}"], actresses=["Actress_X"], maker="Maker_Y")
        for i in range(5)
    ]
    # 7 部 diverse
    diverse = [
        _v(id=210 + i, tags=["rareA", "rareB", f"d_{i}"], actresses=[f"a_{i}"], maker=f"MK_{i}")
        for i in range(7)
    ]
    r = SimilarRanker(padding + [target] + cluster + diverse)
    out = r.rank(target, top_k=12)
    # 12 部全進（5 + 7）；關鍵是「順序」不同
    assert len(out) == 12
    # 寫成 + 會把 5 部 Actress_X 全排前 → top-5 都是 Actress_X
    # 寫成 - 會穿插 → top-5 中 Actress_X 應 < 5
    top5_actress_x = sum(1 for c in out[:5] if c.actresses == ["Actress_X"])
    assert top5_actress_x < 5
    # top-5 中 Maker_Y 也應 < 5
    top5_maker_y = sum(1 for c in out[:5] if c.maker == "Maker_Y")
    assert top5_maker_y < 5
    # 整體 top-12 不同女優數應分散
    distinct_actresses = {tuple(c.actresses) for c in out}
    assert len(distinct_actresses) >= 6


# ------------------------------------------------------------------
# P2-A regression：target 不同物件實例同 number 必排除（id=None 兩邊都走 num fallback）
# ------------------------------------------------------------------

def test_regression_p2a_same_number_different_object_excluded():
    # 加 padding 避免 hot 化
    padding = [_v(id=9000 + i, tags=[f"pad_{i}"]) for i in range(30)]
    # corpus video A: id=None, number="ABP-001"
    a = _v(id=None, number="ABP-001", tags=["巨乳"])
    # 加幾部其他可被選的 corpus
    others = [_v(id=500 + i, tags=[f"o_{i}"]) for i in range(5)]
    # target B: 不同 Python object，但 id=None + number="ABP-001" 相同
    b = _v(id=None, number="ABP-001", tags=["巨乳"])
    r = SimilarRanker(padding + [a] + others)
    # 確認 stable_key 撞
    assert SimilarRanker._stable_key(a) == ('num', 'ABP-001')
    assert SimilarRanker._stable_key(b) == ('num', 'ABP-001')
    assert SimilarRanker._stable_key(a) == SimilarRanker._stable_key(b)
    # 確認 a 與 b 是不同 object
    assert a is not b
    out = r.rank(b, top_k=12)
    # a 不應出現於結果
    assert a not in out
    # 但 number 相同的 a 也透過 stable_key 被排除
    assert all(c.number != "ABP-001" or c is not a for c in out)


# ------------------------------------------------------------------
# rank() 邊界穩定性 4-in-1
# ------------------------------------------------------------------

def test_rank_boundary_empty_corpus():
    target = _v(id=1, tags=["rareA"])
    r = SimilarRanker([])
    assert r.rank(target, top_k=12) == []


def test_rank_boundary_corpus_only_target():
    target = _v(id=1, tags=["rareA"])
    r = SimilarRanker([target])
    assert r.rank(target, top_k=12) == []


def test_rank_boundary_corpus_5_videos_target_in_corpus():
    videos = [_v(id=10 + i, tags=[f"u_{i}"]) for i in range(5)]
    target = videos[0]
    r = SimilarRanker(videos)
    out = r.rank(target, top_k=12)
    # 5 - 1 (target) = 4
    assert len(out) == 4
    assert target not in out


def test_rank_boundary_corpus_12_videos_target_in_corpus():
    videos = [_v(id=10 + i, tags=[f"u_{i}"]) for i in range(12)]
    target = videos[0]
    r = SimilarRanker(videos)
    out = r.rank(target, top_k=12)
    # 12 - 1 = 11
    assert len(out) == 11
    assert target not in out


# ------------------------------------------------------------------
# _stable_key 三段 fallback 直接 unit test
# ------------------------------------------------------------------

def test_stable_key_id_path():
    v = _v(id=42, number="ABC-001")
    assert SimilarRanker._stable_key(v) == ('id', 42)


def test_stable_key_number_fallback():
    v = _v(id=None, number="ABC-001")
    assert SimilarRanker._stable_key(v) == ('num', 'ABC-001')


def test_stable_key_object_id_fallback():
    v = _v(id=None, number=None)
    key = SimilarRanker._stable_key(v)
    assert key[0] == 'mem'
    assert key[1] == id(v)


# ------------------------------------------------------------------
# 永遠 ≤ top_k
# ------------------------------------------------------------------

def test_rank_never_exceeds_top_k():
    padding = [_v(id=1000 + i, tags=[f"pad_{i}"]) for i in range(50)]
    target = _v(id=1, tags=["rareA", "rareB"], number="ABC-001")
    cands = [_v(id=10 + i, number=f"ABC-{i:03d}", tags=["rareA", "rareB", f"u_{i}"]) for i in range(50)]
    r = SimilarRanker(padding + [target] + cands)
    out = r.rank(target, top_k=12)
    assert len(out) == 12


# ------------------------------------------------------------------
# Tier 3 prefix 不命中 → fall to Tier 4
# ------------------------------------------------------------------

def test_tier3_no_prefix_match_falls_to_tier4():
    padding = [_v(id=2000 + i, tags=[f"pad_{i}"]) for i in range(20)]
    target = _v(id=1, number="UNIQUE-001", tags=["alone"])
    # corpus 沒人是 UNIQUE prefix
    others = [_v(id=200 + i, number=f"OTHER-{i:03d}", tags=[f"u_{i}"]) for i in range(15)]
    r = SimilarRanker(padding + [target] + others)
    out = r.rank(target, top_k=12)
    assert len(out) == 12
    # 全部走 Tier 4 random（包含 padding 與 others）


# ------------------------------------------------------------------
# Tier 2 公式：maker bonus + actress penalty
# ------------------------------------------------------------------

def test_tier2_maker_bonus_outranks_pure_shared():
    padding = [_v(id=3000 + i, tags=[f"pad_{i}"]) for i in range(30)]
    target = _v(id=1, tags=["rareA", "rareB"], maker="MK_T", actresses=["actor_t"])
    # cand_a：1 shared tag + same maker → 0.3 + 0.5 = 0.8
    cand_a = _v(id=10, tags=["rareA", "u_a"], maker="MK_T", actresses=["other_a"])
    # cand_b：1 shared tag + diff maker → 0.3
    cand_b = _v(id=11, tags=["rareA", "u_b"], maker="MK_X", actresses=["other_b"])
    r = SimilarRanker(padding + [target, cand_a, cand_b])
    out = r.rank(target, top_k=12)
    # 兩部都 1 shared → Tier 1 跳過（≥2 過濾）→ Tier 2 補；cand_a 因 maker bonus 排前
    assert out[0].id == 10
    assert out[1].id == 11


# ------------------------------------------------------------------
# Tier 1 contract regression
# ------------------------------------------------------------------

def test_regression_codex_p1_duplicate_target_does_not_consume_tier1_slot():
    # 57b 場景：corpus 從 DB build，rank() 收到的 target 是另一個 fetched Video
    # 同 stable_key 但不同 Python object → `is` 排不掉
    # 修前：target 副本占 Tier 1 slot → 從 Tier 2 補位 → 違反「Tier 1 嚴格 before Tier 2」契約
    padding = [_v(id=4000 + i, tags=[f"pad_{i}"]) for i in range(30)]
    target_in_corpus = _v(id=1, tags=["rareA", "rareB", "rareC"], maker="MK_T")
    # 3 部 strong Tier 1（≥2 shared，diff maker）
    strong_cands = [
        _v(id=10 + i, tags=["rareA", "rareB", f"u_{i}"], maker="MK_X")
        for i in range(3)
    ]
    # 1 部 Tier 2 only with maker bonus（修前會擠進 top_k=3）
    tier2_with_bonus = _v(id=20, tags=["rareA", "u_t2"], maker="MK_T")

    r = SimilarRanker(padding + [target_in_corpus] + strong_cands + [tier2_with_bonus])

    # 另一個 Python object，同 id（DB row 重抓場景）
    target_query = _v(id=1, tags=["rareA", "rareB", "rareC"], maker="MK_T")
    assert target_query is not target_in_corpus

    out = r.rank(target_query, top_k=3)
    strong_ids = {10, 11, 12}
    assert all(c.id in strong_ids for c in out), (
        f"Tier 1 slot consumed by target duplicate; got {[c.id for c in out]}"
    )
    # target 副本不應出現在結果
    assert all(c.id != 1 for c in out)
