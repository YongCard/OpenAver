import math
import re
from collections import defaultdict

from core.database import Video
from core.similar.canonicalize import canonicalize
from core.similar.cast_bucket import cast_bucket
from core.similar.idf import build_idf, idf_jaccard, IDF_HOT_THRESHOLD  # noqa: F401


# spec-57 §2.4 helper：release_date 解析失敗 → 0.0；公式 exp(-0.5*(diff/sigma)^2)
def gaussian_year_proximity(cand: Video, target: Video, sigma: float = 4) -> float:
    cy = _extract_year(cand.release_date)
    ty = _extract_year(target.release_date)
    if cy is None or ty is None:
        return 0.0
    diff = cy - ty
    return math.exp(-0.5 * (diff / sigma) ** 2)


# spec-57 §2.4 三桶（≤20 / 20-60 / 60+）；任一邊 None/0（無資訊）→ False
def same_duration_bucket(cand: Video, target: Video) -> bool:
    cd = cand.duration
    td = target.duration
    if not cd or not td:
        return False
    return _bucket(cd) == _bucket(td)


def _extract_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    m = re.match(r"^(\d{4})", release_date)
    return int(m.group(1)) if m else None


def _bucket(minutes: int) -> int:
    if minutes <= 20:
        return 0
    if minutes <= 60:
        return 1
    return 2


class SimilarRanker:
    def __init__(self, corpus: list[Video]) -> None:
        self._corpus: list[Video] = corpus
        # CD-57a-3：建構期預先 canonicalize，rank() / _retrieve() 不重做
        self._canon_tags: list[list[str]] = [canonicalize(v.tags) for v in corpus]
        # CD-57a-9：IDF 只看 v.tags（_canon_tags），不含 user_tags
        self._idf_table: dict[str, float] = build_idf(self._canon_tags)
        self._inverted_index: dict[str, list[int]] = {}
        for i, tags in enumerate(self._canon_tags):
            # set() 去 per-video 重複；canonicalize 已去重，這裡是 belt-and-suspenders
            for t in set(tags):
                # 嚴格 > 0：hot tag (IDF=0) 與 OOV 都不入索引
                if self._idf_table.get(t, 0.0) > 0:
                    self._inverted_index.setdefault(t, []).append(i)

    def _retrieve(
        self,
        target_tags: list[str],
        exclude: Video | None = None,
        top_n: int = 100,
    ) -> list[Video]:
        useful = [t for t in target_tags if self._idf_table.get(t, 0.0) > 0]
        if not useful:
            return []
        scores: dict[int, float] = defaultdict(float)
        for t in useful:
            idf = self._idf_table[t]
            for i in self._inverted_index.get(t, []):
                scores[i] += idf
        # 用 object identity 排除 target 自身（id=None / number 重複場景皆穩）
        if exclude is not None:
            filtered = [(i, s) for i, s in scores.items() if self._corpus[i] is not exclude]
        else:
            filtered = list(scores.items())
        filtered.sort(key=lambda kv: kv[1], reverse=True)
        return [self._corpus[i] for i, _ in filtered[:top_n]]

    # spec-57 §2.4：base + series/maker/year/duration/cast bonus + actress penalty（同系列例外）；不 clamp
    def _score(self, target: Video, cand: Video) -> float:
        target_canon = set(canonicalize(target.tags))
        cand_canon = set(canonicalize(cand.tags))
        rel = idf_jaccard(target_canon, cand_canon, self._idf_table)

        if cand.series and cand.series == target.series:
            rel += 0.30
        if cand.maker and cand.maker == target.maker:
            rel += 0.20
        rel += 0.15 * gaussian_year_proximity(cand, target, sigma=4)
        if same_duration_bucket(cand, target):
            rel += 0.10

        tgt_b = cast_bucket(target.actresses)
        cnd_b = cast_bucket(cand.actresses)
        if tgt_b == cnd_b and tgt_b in ("duo", "multi"):
            rel += 0.20

        if set(target.actresses) & set(cand.actresses):
            if cand.series and cand.series == target.series:
                rel -= 0.15
            else:
                rel -= 0.50

        return rel
