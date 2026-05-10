"""
tests/integration/test_similar_api.py
integration tests for GET /api/similar-covers/* endpoints（57b-T2）

Strategy:
- TestClient + 獨立 mini-app（只掛 similar router，不用 web.app 避免 clip router 衝突）
- in-memory SQLite via tmp_path（不依賴真 DB）
- patch VideoRepository to use tmp_path DB
- patch SimilarRankerCache.get() to return a real ranker built from test corpus
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import init_db, Video, VideoRepository
from core.path_utils import to_file_uri
from core.similar.ranker import SimilarRanker
from core.similar.ranker_cache import SimilarRankerCache
from web.routers.similar import router as similar_router


def _make_test_app() -> FastAPI:
    """建立只掛 similar_router 的最小 FastAPI app，用於 integration test。"""
    app = FastAPI()
    app.include_router(similar_router)
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_RESULT_KEYS = frozenset({
    "video_id",
    "number",
    "title",
    "cover_path",
    "cover_url",
    "cosine_score",
    "penalty_applied",
    "actresses",
})

_EXPECTED_QUERY_VIDEO_KEYS = frozenset({
    "video_id",
    "number",
    "title",
    "cover_url",
})

_EXPECTED_TOP_KEYS = frozenset({
    "video_id",
    "model_id",
    "query_video",
    "results",
})


def _make_video(
    idx: int,
    number: str | None = None,
    tags: list[str] | None = None,
    maker: str = "MakerA",
    series: str | None = None,
    actresses: list[str] | None = None,
    release_date: str = "2023-01-01",
    duration: int = 90,
    cover_path: str = "",
) -> Video:
    """Build a Video for upsert; path must be unique."""
    return Video(
        path=to_file_uri(f"/fake/video_{idx:03d}.mp4"),
        number=number or f"ABC-{idx:03d}",
        title=f"Test Video {idx:03d}",
        maker=maker,
        series=series,
        actresses=actresses or [],
        tags=tags or ["高畫質", "單體作品", f"tag{idx}"],
        release_date=release_date,
        duration=duration,
        cover_path=cover_path,
        mtime=float(idx),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_with_corpus(tmp_path):
    """建立 13 部影片的測試 DB（1 target + 12 candidates）。
    回傳 (db_path, target_id, target_number)。
    """
    db_path = tmp_path / "test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)

    # target video
    target = _make_video(
        idx=1,
        number="TEST-001",
        tags=["高畫質", "單體作品", "美乳", "巨乳"],
        maker="MakerA",
        actresses=[],
    )
    target_id = repo.upsert(target)

    # 12 candidate videos — 共用部分 tag 讓 ranker 可以找到它們
    candidates = [
        _make_video(
            idx=i,
            number=f"TEST-{i:03d}",
            tags=["高畫質", "單體作品", "美乳"],
            maker="MakerA",
        )
        for i in range(2, 14)
    ]
    repo.upsert_batch(candidates)

    return db_path, target_id, "TEST-001"


@pytest.fixture
def client_with_corpus(db_with_corpus, monkeypatch):
    """TestClient with patched VideoRepository + SimilarRankerCache."""
    db_path, target_id, target_number = db_with_corpus

    # Patch VideoRepository to use test DB（router 裡 VideoRepository() 無參數呼叫）
    real_repo_cls = VideoRepository

    class _PatchedRepo(real_repo_cls):
        def __init__(self, db_path_arg=None):
            super().__init__(db_path)

    monkeypatch.setattr("web.routers.similar.VideoRepository", _PatchedRepo)

    # Build a real ranker from test corpus and patch cache
    repo = VideoRepository(db_path)
    corpus = repo.get_all()
    ranker = SimilarRanker(corpus)
    monkeypatch.setattr(SimilarRankerCache, "_instance", ranker)

    yield TestClient(_make_test_app()), target_id, target_number

    # cleanup: 清 singleton 避免汙染其他測試
    SimilarRankerCache._instance = None


@pytest.fixture
def db_single_video(tmp_path):
    """只有 target 一部影片的 DB（corpus size = 1）。"""
    db_path = tmp_path / "single.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    vid = _make_video(idx=1, number="SOLO-001")
    vid_id = repo.upsert(vid)
    return db_path, vid_id, "SOLO-001"


@pytest.fixture
def client_single_video(db_single_video, monkeypatch):
    """TestClient with only 1 video in corpus."""
    db_path, vid_id, number = db_single_video

    real_repo_cls = VideoRepository

    class _PatchedRepo(real_repo_cls):
        def __init__(self, db_path_arg=None):
            super().__init__(db_path)

    monkeypatch.setattr("web.routers.similar.VideoRepository", _PatchedRepo)

    repo = VideoRepository(db_path)
    corpus = repo.get_all()
    ranker = SimilarRanker(corpus)
    monkeypatch.setattr(SimilarRankerCache, "_instance", ranker)

    yield TestClient(_make_test_app()), vid_id, number

    SimilarRankerCache._instance = None


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestSimilarCoversAPI:

    # --- T1: by-number 200 + 12 結果 ---

    def test_by_number_200_12_results(self, client_with_corpus):
        client, target_id, target_number = client_with_corpus
        resp = client.get(f"/api/similar-covers/by-number/{target_number}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 12

    # --- T2: by-id 200 + 12 結果 ---

    def test_by_id_200_12_results(self, client_with_corpus):
        client, target_id, target_number = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 12

    # --- T3: by-number 404 ---

    def test_by_number_404(self, client_with_corpus):
        client, _, _ = client_with_corpus
        resp = client.get("/api/similar-covers/by-number/NONEXISTENT-999")
        assert resp.status_code == 404

    # --- T4: by-id 404 ---

    def test_by_id_404(self, client_with_corpus):
        client, _, _ = client_with_corpus
        resp = client.get("/api/similar-covers/99999")
        assert resp.status_code == 404

    # --- T5: response shape 含全部 v0.8.6 key ---

    def test_response_shape_top_level_keys(self, client_with_corpus):
        client, target_id, _ = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        data = resp.json()
        assert set(data.keys()) >= _EXPECTED_TOP_KEYS, (
            f"缺少 top-level key：{_EXPECTED_TOP_KEYS - set(data.keys())}"
        )

    def test_response_shape_query_video_keys(self, client_with_corpus):
        client, target_id, _ = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        qv = resp.json()["query_video"]
        assert set(qv.keys()) >= _EXPECTED_QUERY_VIDEO_KEYS, (
            f"query_video 缺少 key：{_EXPECTED_QUERY_VIDEO_KEYS - set(qv.keys())}"
        )

    def test_response_shape_result_keys(self, client_with_corpus):
        client, target_id, _ = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        results = resp.json()["results"]
        assert len(results) > 0, "results 為空，無法驗 key set"
        for r in results:
            missing = _EXPECTED_RESULT_KEYS - set(r.keys())
            assert not missing, f"result item 缺少 key：{missing}，item={r}"

    # --- T6: model_id == "rule-based:v1" ---

    def test_model_id_is_rule_based_v1(self, client_with_corpus):
        client, target_id, _ = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        assert resp.json()["model_id"] == "rule-based:v1"

    # --- T7: penalty_applied is False for all results ---

    def test_penalty_applied_is_false(self, client_with_corpus):
        client, target_id, _ = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        for r in resp.json()["results"]:
            assert r["penalty_applied"] is False, (
                f"penalty_applied should be False, got {r['penalty_applied']}"
            )

    # --- T8: corpus 只有 target 一部 → results: [] (200 不報錯) ---

    def test_single_video_corpus_returns_empty_results(self, client_single_video):
        client, vid_id, _ = client_single_video
        resp = client.get(f"/api/similar-covers/{vid_id}")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    # --- T9: target 全 hot tag + corpus ≥ 12 部 → results 仍 12 部 (Tier 3/4 fallback) ---

    def test_hot_tag_target_still_returns_12_results(self, tmp_path, monkeypatch):
        """target 的 tags 全為 hot tag（IDF=0），Tier 1/2 retrieve 無結果，
        靠 Tier 3（同 prefix random）或 Tier 4（全庫 random）兜底仍能回 12 部。
        """
        db_path = tmp_path / "hot_tag.db"
        init_db(db_path)
        repo = VideoRepository(db_path)

        # 超高頻 tag → 全庫都有 → IDF = 0 → "hot"
        hot_tags = ["高畫質"]  # 全部影片都有此 tag → IDF 會是 0

        # target（prefix = TEST）
        target = _make_video(idx=1, number="TEST-001", tags=hot_tags)
        repo.upsert(target)

        # 12 candidates 全用同 prefix TEST，讓 Tier 3 可以接住
        candidates = [
            _make_video(idx=i, number=f"TEST-{i:03d}", tags=hot_tags)
            for i in range(2, 14)
        ]
        repo.upsert_batch(candidates)

        real_repo_cls = VideoRepository

        class _PatchedRepo(real_repo_cls):
            def __init__(self, _=None):
                super().__init__(db_path)

        monkeypatch.setattr("web.routers.similar.VideoRepository", _PatchedRepo)

        corpus = repo.get_all()
        ranker = SimilarRanker(corpus)
        monkeypatch.setattr(SimilarRankerCache, "_instance", ranker)

        client = TestClient(_make_test_app())
        try:
            target_row = repo.get_by_number("TEST-001")
            resp = client.get(f"/api/similar-covers/{target_row.id}")
            assert resp.status_code == 200
            assert len(resp.json()["results"]) == 12
        finally:
            SimilarRankerCache._instance = None

    # --- T10: limit query parameter 生效 ---

    def test_limit_parameter(self, client_with_corpus):
        client, target_id, _ = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 5

    def test_limit_by_number(self, client_with_corpus):
        client, _, target_number = client_with_corpus
        resp = client.get(f"/api/similar-covers/by-number/{target_number}?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 3

    # --- T11: target 不在 results 中 ---

    def test_target_not_in_results(self, client_with_corpus):
        client, target_id, target_number = client_with_corpus
        resp = client.get(f"/api/similar-covers/{target_id}")
        data = resp.json()
        result_ids = {r["video_id"] for r in data["results"]}
        assert target_id not in result_ids, (
            f"target video_id {target_id} should not appear in results"
        )

        result_numbers = {r["number"] for r in data["results"]}
        assert target_number not in result_numbers, (
            f"target number {target_number!r} should not appear in results"
        )


# ---------------------------------------------------------------------------
# Codex P1 — legacy DB startup path regression test
# ---------------------------------------------------------------------------

class TestLegacySchemaStartupPath:
    """CD-57b-8：init_db() 必須在 SimilarRankerCache 首次 get() 前執行。

    覆蓋 Codex finding P1：若 legacy v0.8.6 DB 仍含 clip_embedding / clip_model_id 欄位，
    Video.from_row cls(**data) 會因未知 keyword 拋 TypeError → 500。
    init_db() 在 lifespan startup 執行 DROP COLUMN migration 後此問題消失。
    """

    def test_init_db_drops_clip_columns_before_similar_query(self, tmp_path, monkeypatch):
        """legacy DB 啟動後，similar endpoint 不因 clip 欄位拋 500。"""
        import sqlite3

        db_path = tmp_path / "legacy_v086.db"

        # 建立模擬 v0.8.6 schema（含 clip_embedding / clip_model_id）
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                number TEXT,
                title TEXT,
                original_title TEXT,
                actresses TEXT DEFAULT '[]',
                maker TEXT DEFAULT '',
                director TEXT DEFAULT '',
                series TEXT DEFAULT '',
                label TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                sample_images TEXT DEFAULT '',
                user_tags TEXT DEFAULT '[]',
                duration INTEGER DEFAULT 0,
                size_bytes INTEGER,
                cover_path TEXT DEFAULT '',
                release_date TEXT DEFAULT '',
                mtime REAL DEFAULT 0.0,
                nfo_mtime REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                clip_embedding BLOB DEFAULT NULL,
                clip_model_id TEXT DEFAULT NULL
            )
        """)
        # 插入一筆帶 clip_embedding 的 legacy row
        conn.execute(
            "INSERT INTO videos (path, number, title, actresses, tags) VALUES (?,?,?,?,?)",
            (to_file_uri("/legacy/test.mp4"), "LEGACY-001", "Legacy Test", "[]", '["巨乳"]'),
        )
        conn.commit()
        conn.close()

        # 執行 init_db（模擬 app startup），應 DROP clip 欄位
        init_db(db_path)

        # 驗欄位已 DROP
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
        conn.close()
        assert "clip_embedding" not in cols
        assert "clip_model_id" not in cols

        # 驗 similar endpoint 不 500：patch VideoRepository + cache，用修正後 DB 建 ranker
        real_repo_cls = VideoRepository

        class _PatchedRepo(real_repo_cls):
            def __init__(self, _=None):
                super().__init__(db_path)

        monkeypatch.setattr("web.routers.similar.VideoRepository", _PatchedRepo)

        repo = VideoRepository(db_path)
        corpus = repo.get_all()  # 不應 TypeError
        ranker = SimilarRanker(corpus)
        monkeypatch.setattr(SimilarRankerCache, "_instance", ranker)

        client = TestClient(_make_test_app())
        try:
            resp = client.get("/api/similar-covers/by-number/LEGACY-001")
            # 有 1 部影片，corpus = [target]，results = []，200 不 500
            assert resp.status_code == 200
            assert resp.json()["results"] == []
        finally:
            SimilarRankerCache._instance = None
