"""
tests/integration/test_ranker_cache_invalidation.py
Integration tests for SimilarRankerCache.invalidate() choke-point hooks（57b-T4）

Verifies that each DB write mutation triggers cache invalidation so the next
SimilarRankerCache.get() returns a freshly-built instance (identity check).
"""
from __future__ import annotations

import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import init_db, Video, VideoRepository
from core.similar.ranker_cache import SimilarRankerCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_video(idx: int, number: str | None = None) -> Video:
    """Build a minimal Video for upsert; path must be unique."""
    return Video(
        path=f"file:///fake/cache_test_{idx:04d}.mp4",
        number=number or f"CACHE-{idx:04d}",
        title=f"Cache Test Video {idx:04d}",
        maker="MakerX",
        series=None,
        actresses=[],
        tags=["高畫質", "單體作品", f"tag{idx}"],
        release_date="2024-01-01",
        duration=90,
        cover_path="",
        mtime=float(idx),
    )


def _make_corrupted_video(idx: int) -> Video:
    """Build a Video with corrupted number (digit_prefix rule) for fix_numbers_apply tests."""
    return Video(
        path=f"file:///fake/fix_num_{idx:04d}.mp4",
        number=f"7IPZ-{idx:03d}",
        title=f"Fix Num Video {idx:04d}",
        maker="MakerY",
        series=None,
        actresses=[],
        tags=["高畫質"],
        release_date="2024-01-01",
        duration=60,
        cover_path="",
        mtime=float(idx),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure cache is clean before and after each test."""
    SimilarRankerCache._instance = None
    yield
    SimilarRankerCache._instance = None


@pytest.fixture
def repo_and_cache(tmp_path, monkeypatch):
    """建立測試 DB + 初始化 warm cache。"""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)

    # Seed at least one video so the ranker corpus is non-trivial
    seed = _make_video(0)
    repo.upsert(seed)

    # Patch SimilarRankerCache to build corpus from our test DB
    monkeypatch.setattr(
        "core.similar.ranker_cache.VideoRepository",
        lambda: VideoRepository(db_path),
    )

    # Warm the cache (reset_cache fixture already set _instance = None)
    first = SimilarRankerCache.get()
    assert first is not None

    yield repo, first, db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInvalidateAfterUpsert:
    """T4-1: upsert() invalidates cache."""

    def test_invalidate_after_upsert(self, repo_and_cache):
        repo, first, db_path = repo_and_cache

        new_video = _make_video(1)
        repo.upsert(new_video)

        second = SimilarRankerCache.get()
        assert second is not first, "upsert() must invalidate cache so get() rebuilds"


class TestInvalidateAfterUpsertBatch:
    """T4-2: upsert_batch() invalidates cache."""

    def test_invalidate_after_upsert_batch(self, repo_and_cache):
        repo, first, db_path = repo_and_cache

        batch = [_make_video(i) for i in range(10, 13)]
        repo.upsert_batch(batch)

        second = SimilarRankerCache.get()
        assert second is not first, "upsert_batch() must invalidate cache so get() rebuilds"

    def test_empty_batch_does_not_invalidate(self, repo_and_cache):
        """Early-return path: empty list → no write → cache unchanged."""
        repo, first, db_path = repo_and_cache

        repo.upsert_batch([])

        # Cache should still be the same instance (no invalidation on no-op)
        second = SimilarRankerCache.get()
        assert second is first, "upsert_batch([]) must NOT invalidate cache (early return)"


class TestInvalidateAfterDeleteByPaths:
    """T4-3: delete_by_paths() invalidates cache."""

    def test_invalidate_after_delete_by_paths(self, repo_and_cache):
        repo, first, db_path = repo_and_cache

        # The seed video at idx=0 exists
        seed_path = "file:///fake/cache_test_0000.mp4"
        repo.delete_by_paths([seed_path])

        second = SimilarRankerCache.get()
        assert second is not first, "delete_by_paths() must invalidate cache so get() rebuilds"

    def test_empty_paths_does_not_invalidate(self, repo_and_cache):
        """Early-return path: empty list → no write → cache unchanged."""
        repo, first, db_path = repo_and_cache

        repo.delete_by_paths([])

        second = SimilarRankerCache.get()
        assert second is first, "delete_by_paths([]) must NOT invalidate cache (early return)"


class TestInvalidateAfterClearAll:
    """T4-4: clear_all() invalidates cache and corpus becomes empty."""

    def test_invalidate_after_clear_all(self, repo_and_cache):
        repo, first, db_path = repo_and_cache

        repo.clear_all()

        new_instance = SimilarRankerCache.get()
        assert new_instance is not first, "clear_all() must invalidate cache so get() rebuilds"
        assert len(new_instance._corpus) == 0, "corpus must be empty after clear_all()"


class TestInvalidateAfterFixNumbersApply:
    """T4-5: POST /api/collection/fix-numbers/apply invalidates cache."""

    def test_invalidate_after_fix_numbers_apply(self, tmp_path, monkeypatch):
        db_path = tmp_path / "fix_test.db"
        init_db(db_path)
        repo = VideoRepository(db_path)

        # Insert a video with a corrupted number (digit_prefix rule: "7IPZ-154")
        bad_video = _make_corrupted_video(1)
        vid_id = repo.upsert(bad_video)

        # Patch get_db_path to use our test DB
        monkeypatch.setattr("web.routers.collection.get_db_path", lambda: db_path)
        # Patch SimilarRankerCache to use our test DB for rebuilds
        monkeypatch.setattr(
            "core.similar.ranker_cache.VideoRepository",
            lambda: VideoRepository(db_path),
        )

        # Warm the cache
        SimilarRankerCache._instance = None
        first = SimilarRankerCache.get()
        assert first is not None

        # Build minimal app with collection router
        app = FastAPI()
        from web.routers.collection import router as collection_router
        app.include_router(collection_router)
        client = TestClient(app)

        resp = client.post(
            "/api/collection/fix-numbers/apply",
            json={"ids": [vid_id]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("updated", 0) >= 1, f"Expected at least 1 updated, got {data}"

        second = SimilarRankerCache.get()
        assert second is not first, (
            "fix_numbers_apply must invalidate cache so get() rebuilds"
        )


class TestInvalidateExceptionDoesNotFailWrite:
    """T4-6: SimilarRankerCache.invalidate() raising must not break upsert()."""

    def test_invalidate_exception_does_not_fail_write(self, tmp_path):
        db_path = tmp_path / "exc_test.db"
        init_db(db_path)
        repo = VideoRepository(db_path)

        # Seed a video first
        seed = _make_video(0)
        repo.upsert(seed)

        new_video = _make_video(99)

        with patch(
            "core.similar.ranker_cache.SimilarRankerCache.invalidate",
            side_effect=RuntimeError("simulated invalidate failure"),
        ):
            # upsert must succeed even when invalidate raises
            vid_id = repo.upsert(new_video)

        assert vid_id > 0, "upsert() must succeed even when SimilarRankerCache.invalidate raises"

        # Verify the row was actually written to DB
        fetched = repo.get_by_path(new_video.path)
        assert fetched is not None, "Video must be persisted in DB despite cache error"
        assert fetched.number == new_video.number
