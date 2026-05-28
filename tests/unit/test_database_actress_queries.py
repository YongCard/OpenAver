"""Tests for the json_each-based actress query methods in VideoRepository.

Covers exact-match semantics, empty-array / NULL guards, deduplication (F3),
and multi-video OR queries — all of which the old 4-LIKE-OR pattern failed.
"""
import sqlite3
import pytest
from pathlib import Path

from core.database import Video, VideoRepository, init_db
from core.path_utils import to_file_uri


# ── helpers ──────────────────────────────────────────────────────────────────

def _video(path_suffix: str, actresses: list, number: str = None) -> Video:
    """Build a minimal Video for upsert."""
    return Video(
        path=to_file_uri(f"/test/{path_suffix}.mp4"),
        number=number or path_suffix.upper(),
        title=path_suffix,
        actresses=actresses,
    )


# ── count_by_actress ──────────────────────────────────────────────────────────

def test_count_by_actress_exact_match_no_prefix_collision(temp_db):
    """'巨乳' must not count the video whose actress is '巨乳波多野'."""
    repo = VideoRepository(temp_db)
    repo.upsert(_video("v1", ["巨乳波多野"]))
    repo.upsert(_video("v2", ["巨乳"]))

    assert repo.count_by_actress("巨乳") == 1


def test_count_by_actress_empty_array_not_matched(temp_db):
    """A video with actresses=[] should not be counted for any name."""
    repo = VideoRepository(temp_db)
    repo.upsert(_video("v1", []))

    assert repo.count_by_actress("anything") == 0


def test_count_by_actress_returns_zero_for_unknown(temp_db):
    """Query for a non-existent actress returns 0."""
    repo = VideoRepository(temp_db)
    repo.upsert(_video("v1", ["Alice"]))

    assert repo.count_by_actress("Nobody") == 0


def test_count_by_actress_handles_null_actresses(temp_db):
    """A row with actresses=NULL must not crash; it is silently skipped."""
    # Bypass upsert (which serialises to '[]') and write NULL directly
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO videos (path, title, actresses) VALUES (?, ?, NULL)",
        (to_file_uri("/test/null_actress.mp4"), "null test"),
    )
    conn.commit()
    conn.close()

    repo = VideoRepository(temp_db)
    assert repo.count_by_actress("Alice") == 0  # must not raise


def test_count_by_actress_handles_malformed_json(temp_db):
    """A row with actresses='not-json' must not crash (json_valid guard)."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO videos (path, title, actresses) VALUES (?, ?, ?)",
        (to_file_uri("/test/bad_json.mp4"), "bad json", "not-json"),
    )
    conn.commit()
    conn.close()

    repo = VideoRepository(temp_db)
    assert repo.count_by_actress("Alice") == 0


# ── get_videos_by_actress ─────────────────────────────────────────────────────

def test_get_videos_by_actress_exact_match(temp_db):
    """'巨乳' should return only the video whose actress is exactly '巨乳'."""
    repo = VideoRepository(temp_db)
    repo.upsert(_video("v1", ["巨乳波多野"]))
    repo.upsert(_video("v2", ["巨乳"]))

    results = repo.get_videos_by_actress("巨乳")
    assert len(results) == 1
    assert results[0].actresses == ["巨乳"]


def test_get_videos_by_actress_handles_null_actresses(temp_db):
    """Rows with NULL actresses must not crash and must not appear in results."""
    conn = sqlite3.connect(str(temp_db))
    conn.execute(
        "INSERT INTO videos (path, title, actresses) VALUES (?, ?, NULL)",
        (to_file_uri("/test/null_actress2.mp4"), "null test 2"),
    )
    conn.commit()
    conn.close()

    repo = VideoRepository(temp_db)
    results = repo.get_videos_by_actress("Alice")
    assert results == []


# ── get_videos_by_actress_names ───────────────────────────────────────────────

def test_get_videos_by_actress_names_no_duplicate(temp_db):
    """F3: one video with actresses=['Alice','Alice-alias'], queried by both names → length 1."""
    repo = VideoRepository(temp_db)
    repo.upsert(_video("v1", ["Alice", "Alice-alias"]))

    results = repo.get_videos_by_actress_names(["Alice", "Alice-alias"])
    assert len(results) == 1


def test_get_videos_by_actress_names_empty_returns_empty(temp_db):
    """`get_videos_by_actress_names([])` must return [] without hitting the DB."""
    repo = VideoRepository(temp_db)
    repo.upsert(_video("v1", ["Alice"]))

    assert repo.get_videos_by_actress_names([]) == []


def test_get_videos_by_actress_names_returns_multiple_videos(temp_db):
    """Query matching 2 of 3 videos returns exactly those 2, ordered by id."""
    repo = VideoRepository(temp_db)
    v1 = repo.upsert(_video("v1", ["Alice"], number="V001"))
    v2 = repo.upsert(_video("v2", ["Bob"], number="V002"))
    _v3 = repo.upsert(_video("v3", ["Carol"], number="V003"))

    results = repo.get_videos_by_actress_names(["Alice", "Bob"])
    assert len(results) == 2
    paths = {r.path for r in results}
    assert to_file_uri("/test/v1.mp4") in paths
    assert to_file_uri("/test/v2.mp4") in paths
    # Carol's video must not appear
    assert to_file_uri("/test/v3.mp4") not in paths
