"""
tests/unit/test_ranker_cache.py
SimilarRankerCache singleton — 全 mock，不碰真 DB
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────
# Fixture：每個 test 前後 reset class state，避免互相污染
# ──────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def reset_cache():
    """每個 test 前後都 reset SimilarRankerCache state，避免 test 間互相污染"""
    from core.similar.ranker_cache import SimilarRankerCache
    SimilarRankerCache._instance = None
    yield
    SimilarRankerCache._instance = None


# ──────────────────────────────────────────────────────────
# T1：首次 get() 建立 SimilarRanker instance
# ──────────────────────────────────────────────────────────
def test_get_builds_on_first_call():
    from core.similar.ranker_cache import SimilarRankerCache
    from core.similar.ranker import SimilarRanker

    mock_video = MagicMock()
    with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls:
        mock_repo_cls.return_value.get_all.return_value = [mock_video]
        result = SimilarRankerCache.get()

    assert result is not None
    assert isinstance(result, SimilarRanker)


# ──────────────────────────────────────────────────────────
# T2：兩次 get() 回傳同一物件（identity）
# ──────────────────────────────────────────────────────────
def test_get_returns_same_instance():
    from core.similar.ranker_cache import SimilarRankerCache

    mock_video = MagicMock()
    with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls:
        mock_repo_cls.return_value.get_all.return_value = [mock_video]
        a = SimilarRankerCache.get()
        b = SimilarRankerCache.get()

    assert a is b


# ──────────────────────────────────────────────────────────
# T3：invalidate() 後 get() 回傳新 instance
# ──────────────────────────────────────────────────────────
def test_invalidate_clears_cache():
    from core.similar.ranker_cache import SimilarRankerCache

    mock_video = MagicMock()
    with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls:
        mock_repo_cls.return_value.get_all.return_value = [mock_video]
        a = SimilarRankerCache.get()
        SimilarRankerCache.invalidate()
        b = SimilarRankerCache.get()

    assert a is not b


# ──────────────────────────────────────────────────────────
# T4：從未 get() 直接 invalidate() — no-op，不拋例外
# ──────────────────────────────────────────────────────────
def test_invalidate_on_empty_cache():
    from core.similar.ranker_cache import SimilarRankerCache

    # _instance 已在 fixture 設為 None，直接 invalidate 不能拋
    SimilarRankerCache.invalidate()  # should not raise


# ──────────────────────────────────────────────────────────
# T5：多執行緒同時 get() — SimilarRanker 只 build 一次
# ──────────────────────────────────────────────────────────
def test_thread_safety_single_build():
    from core.similar.ranker_cache import SimilarRankerCache

    build_count = [0]
    mock_video = MagicMock()

    # 我們需要在 patch SimilarRanker 之前先保存原來的，但這裡直接計次即可
    with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls, \
         patch("core.similar.ranker_cache.SimilarRanker") as mock_ranker_cls:
        mock_repo_cls.return_value.get_all.return_value = [mock_video]

        def counting_init(corpus):
            build_count[0] += 1
            instance = MagicMock()
            return instance

        mock_ranker_cls.side_effect = counting_init

        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()  # 所有 thread 同時衝
            results.append(SimilarRankerCache.get())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert build_count[0] == 1, f"Expected 1 build, got {build_count[0]}"
    assert len(results) == 8, f"Expected 8 results, got {len(results)}"
    assert len({id(r) for r in results}) == 1, "All threads should share the same instance"


# ──────────────────────────────────────────────────────────
# T6：同 thread 內 get → invalidate → get，驗不死鎖（RLock）
# ──────────────────────────────────────────────────────────
def test_thread_safety_reentrant():
    from core.similar.ranker_cache import SimilarRankerCache

    done = threading.Event()
    errors = []

    mock_video = MagicMock()

    def run():
        try:
            with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls:
                mock_repo_cls.return_value.get_all.return_value = [mock_video]
                SimilarRankerCache.get()
                SimilarRankerCache.invalidate()
                SimilarRankerCache.get()
            done.set()
        except Exception as e:
            errors.append(e)
            done.set()

    t = threading.Thread(target=run)
    t.start()
    completed = done.wait(timeout=1.0)

    assert completed, "Deadlock detected: operation did not complete within 1s"
    assert not errors, f"Unexpected exception: {errors[0]}"


# ──────────────────────────────────────────────────────────
# T7：import 時不觸發 DB（lazy）
# ──────────────────────────────────────────────────────────
def test_lazy_import_no_db_on_import():
    import importlib
    import core.similar.ranker_cache

    # reload() 在同一個 module object 上 re-exec，會替換其中的 class 屬性。
    # 儲存原始 class 並在 finally 中還原，避免 class identity 跨測試污染。
    original_class = core.similar.ranker_cache.SimilarRankerCache
    try:
        with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls:
            importlib.reload(core.similar.ranker_cache)

            # import/reload 後不應呼叫 get_all
            assert mock_repo_cls.return_value.get_all.call_count == 0
    finally:
        core.similar.ranker_cache.SimilarRankerCache = original_class


# ──────────────────────────────────────────────────────────
# T8：空 corpus get() 正常不拋例外
# ──────────────────────────────────────────────────────────
def test_get_empty_corpus():
    from core.similar.ranker_cache import SimilarRankerCache
    from core.similar.ranker import SimilarRanker

    with patch("core.similar.ranker_cache.VideoRepository") as mock_repo_cls:
        mock_repo_cls.return_value.get_all.return_value = []
        result = SimilarRankerCache.get()

    assert result is not None
    assert isinstance(result, SimilarRanker)
