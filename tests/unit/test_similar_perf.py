import random
import time

import pytest

from core.database import Video
from core.similar.ranker import SimilarRanker


TAG_POOL = [
    "巨乳", "美乳", "貧乳", "爆乳", "黑髮", "金髮", "短髮", "長髮", "馬尾", "雙馬尾",
    "OL", "制服", "女僕", "護士", "教師", "學生", "人妻", "素人", "女大生", "空姐",
    "美腿", "絲襪", "高跟", "緊身褲", "短裙", "比基尼", "泳裝", "和服", "旗袍", "睡衣",
    "戶外", "車內", "辦公室", "教室", "浴室", "廚房", "海邊", "溫泉", "飯店", "公園",
    "巨乳痴女", "誘惑", "口交", "潮吹", "顏射", "中出", "騎乘", "後背", "輪姦", "群交",
]

ACTRESS_POOL = [f"女優{i:03d}" for i in range(200)]
MAKER_POOL = [f"廠商{i:02d}" for i in range(30)]
SERIES_POOL = [f"系列{i:02d}" for i in range(50)]
NUMBER_PREFIXES = ["SSIS", "MIDV", "STARS", "ABF", "FSDSS", "JUL"]


def _build_corpus(n: int = 6000) -> list[Video]:
    rng = random.Random(42)
    corpus: list[Video] = []
    for i in range(n):
        tag_count = rng.randint(1, 5)
        tags = rng.sample(TAG_POOL, tag_count)
        actress_count = rng.randint(1, 2)
        actresses = rng.sample(ACTRESS_POOL, actress_count)
        maker = rng.choice(MAKER_POOL)
        series_choice = rng.choice(SERIES_POOL + [None] * 10)
        prefix = rng.choice(NUMBER_PREFIXES)
        number = f"{prefix}-{i:05d}"
        release_date = f"{rng.randint(2010, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        duration = rng.randint(15, 180)
        corpus.append(
            Video(
                id=i,
                number=number,
                tags=tags,
                actresses=actresses,
                maker=maker,
                series=series_choice,
                release_date=release_date,
                duration=duration,
            )
        )
    return corpus


def _build_target() -> Video:
    return Video(
        id=999_999,
        number="SSIS-99999",
        tags=["巨乳", "美乳", "OL", "制服", "辦公室"],
        actresses=["女優000", "女優001"],
        maker="廠商00",
        series="系列00",
        release_date="2020-06-15",
        duration=120,
    )


@pytest.mark.perf
def test_rank_under_50ms_6000_corpus():
    corpus = _build_corpus(6000)
    target = _build_target()
    ranker = SimilarRanker(corpus)

    warmup_result = ranker.rank(target, top_k=12)
    assert len(warmup_result) == 12, (
        f"target 應走 Tier 1 主路徑回滿 12 部，實際 {len(warmup_result)} — "
        "若 ranker 退到 fallback，benchmark 會假快"
    )

    elapsed_ms_list: list[float] = []
    for _ in range(3):
        t0 = time.perf_counter_ns()
        ranker.rank(target)
        elapsed_ms_list.append((time.perf_counter_ns() - t0) / 1_000_000)

    avg_ms = sum(elapsed_ms_list) / len(elapsed_ms_list)
    assert avg_ms < 50, (
        f"rank() avg {avg_ms:.2f}ms (runs: {elapsed_ms_list}) — budget 50ms"
    )
