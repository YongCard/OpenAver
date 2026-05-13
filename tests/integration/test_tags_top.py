"""
test_tags_top.py — GET /api/tags/top 整合測試

策略：TDD-lite（TestClient + tmp_path 真實 SQLite DB，不 mock SQL）
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from core.database import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """建立臨時測試資料庫，插入含日中英混合 tag 的測試資料"""
    db_path = tmp_path / "test_tags_top.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # 插入 9 筆影片，各種 tags 組合
    # tag 出現次數：
    #   巨乳: 5, 高畫質: 3, DMM獨家: 2, 女教師: 2,
    #   Big Tits: 1, 単体作品: 1, 痴女: 1 (噪音 tag)
    #   空字串: 應被過濾
    videos = [
        ("file:///1.mp4", "AAA-001", '["巨乳","高畫質","DMM獨家"]'),
        ("file:///2.mp4", "AAA-002", '["巨乳","高畫質","女教師"]'),
        ("file:///3.mp4", "AAA-003", '["巨乳","高畫質","DMM獨家"]'),
        ("file:///4.mp4", "AAA-004", '["巨乳","女教師"]'),
        ("file:///5.mp4", "AAA-005", '["巨乳","Big Tits"]'),
        ("file:///6.mp4", "AAA-006", '["単体作品"]'),
        ("file:///7.mp4", "AAA-007", '["痴女"]'),
        ("file:///8.mp4", "AAA-008", '[""]'),        # 空字串 tag → 應過濾
        ("file:///9.mp4", "AAA-009", "[]"),          # 空 tags
    ]
    conn.executemany(
        "INSERT INTO videos (path, number, tags) VALUES (?, ?, ?)",
        videos,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path):
    """空資料庫（無影片）"""
    db_path = tmp_path / "test_tags_empty.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def client(tmp_db, monkeypatch):
    """TestClient，monkeypatch get_db_path 指向 tmp DB"""
    monkeypatch.setattr("web.routers.tags.get_db_path", lambda: tmp_db)
    from web.app import app
    return TestClient(app)


@pytest.fixture
def empty_client(empty_db, monkeypatch):
    """TestClient，指向空 DB"""
    monkeypatch.setattr("web.routers.tags.get_db_path", lambda: empty_db)
    from web.app import app
    return TestClient(app)


# ── Schema Check ──────────────────────────────────────────────────────────────

class TestResponseSchema:
    def test_required_fields_present(self, client):
        """response 必有 success / items / total_unique_tags / applied_min_count"""
        resp = client.get("/api/tags/top")
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "items" in data
        assert "total_unique_tags" in data
        assert "applied_min_count" in data

    def test_success_true(self, client):
        resp = client.get("/api/tags/top")
        assert resp.json()["success"] is True

    def test_items_shape(self, client):
        """items 每筆含 tag: string, count: integer"""
        resp = client.get("/api/tags/top")
        items = resp.json()["items"]
        assert len(items) > 0
        for item in items:
            assert "tag" in item
            assert "count" in item
            assert isinstance(item["tag"], str)
            assert isinstance(item["count"], int)

    def test_items_sorted_desc(self, client):
        """items 按 count 降序排列"""
        resp = client.get("/api/tags/top")
        counts = [item["count"] for item in resp.json()["items"]]
        assert counts == sorted(counts, reverse=True)


# ── Happy Path ────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_default_min_count_filters_noise(self, client):
        """預設 min_count=2：Big Tits/単体作品/痴女（各1次）不在結果中"""
        resp = client.get("/api/tags/top")
        tags = [item["tag"] for item in resp.json()["items"]]
        assert "Big Tits" not in tags
        assert "単体作品" not in tags
        assert "痴女" not in tags

    def test_default_includes_frequent_tags(self, client):
        """預設 min_count=2：巨乳/高畫質/DMM獨家/女教師 都應在結果中"""
        resp = client.get("/api/tags/top")
        tags = [item["tag"] for item in resp.json()["items"]]
        assert "巨乳" in tags
        assert "高畫質" in tags
        assert "DMM獨家" in tags
        assert "女教師" in tags

    def test_top_tag_correct_count(self, client):
        """巨乳出現 5 次，應為最高頻 tag"""
        resp = client.get("/api/tags/top")
        items = resp.json()["items"]
        assert items[0]["tag"] == "巨乳"
        assert items[0]["count"] == 5

    def test_applied_min_count_reflected(self, client):
        """applied_min_count 回傳實際使用值"""
        resp = client.get("/api/tags/top?min_count=3")
        assert resp.json()["applied_min_count"] == 3


# ── min_count 邊界 ─────────────────────────────────────────────────────────────

class TestMinCount:
    def test_min_count_1_shows_all_tags(self, client):
        """min_count=1 opt-in：全部 tag 含噪音都出現"""
        resp = client.get("/api/tags/top?min_count=1")
        tags = [item["tag"] for item in resp.json()["items"]]
        assert "Big Tits" in tags
        assert "単体作品" in tags
        assert "痴女" in tags

    def test_min_count_2_filters_one_count(self, client):
        """min_count=2：只出現 1 次的 tag 被過濾"""
        resp = client.get("/api/tags/top?min_count=2")
        items = resp.json()["items"]
        for item in items:
            assert item["count"] >= 2

    def test_min_count_0_returns_422(self, client):
        """min_count=0 → HTTP 422（ge=1）"""
        resp = client.get("/api/tags/top?min_count=0")
        assert resp.status_code == 422

    def test_total_unique_tags_not_affected_by_min_count(self, client):
        """total_unique_tags 不受 min_count 影響"""
        resp2 = client.get("/api/tags/top?min_count=2")
        resp1 = client.get("/api/tags/top?min_count=1")
        assert resp2.json()["total_unique_tags"] == resp1.json()["total_unique_tags"]

    def test_total_unique_tags_correct_count(self, client):
        """total_unique_tags 應等於所有非空 tag 的 unique 數（巨乳/高畫質/DMM獨家/女教師/Big Tits/単体作品/痴女 = 7）"""
        resp = client.get("/api/tags/top")
        # 7 個 unique 非空 tag（空字串和 [] 被過濾）
        assert resp.json()["total_unique_tags"] == 7


# ── limit 邊界 ─────────────────────────────────────────────────────────────────

class TestLimit:
    def test_limit_1_returns_only_top(self, client):
        """limit=1 → 只回傳 count 最高的 1 個 tag"""
        resp = client.get("/api/tags/top?limit=1&min_count=1")
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["tag"] == "巨乳"

    def test_limit_3_happy_path(self, client):
        """limit=3 → 前 3 名按 count desc"""
        resp = client.get("/api/tags/top?limit=3&min_count=1")
        items = resp.json()["items"]
        assert len(items) == 3
        counts = [item["count"] for item in items]
        assert counts == sorted(counts, reverse=True)

    def test_limit_500_max_allowed(self, client):
        """limit=500 → FastAPI 允許，正常回傳"""
        resp = client.get("/api/tags/top?limit=500")
        assert resp.status_code == 200

    def test_limit_501_returns_422(self, client):
        """limit=501 → HTTP 422"""
        resp = client.get("/api/tags/top?limit=501")
        assert resp.status_code == 422

    def test_corpus_less_than_limit(self, client):
        """corpus < limit 時回傳全部（不報錯）"""
        resp = client.get("/api/tags/top?limit=100&min_count=1")
        assert resp.status_code == 200
        # 7 個 unique 非空 tag，全部回傳
        assert len(resp.json()["items"]) == 7


# ── 空 corpus ─────────────────────────────────────────────────────────────────

class TestEmptyCorpus:
    def test_empty_db_returns_empty_items(self, empty_client):
        """空 videos table → items=[], total_unique_tags=0"""
        resp = empty_client.get("/api/tags/top")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total_unique_tags"] == 0
        assert data["applied_min_count"] == 2

    def test_empty_db_success_true(self, empty_client):
        """空 corpus 回傳 success=True（不是 500）"""
        resp = empty_client.get("/api/tags/top")
        assert resp.json()["success"] is True


# ── 全噪音 corpus ─────────────────────────────────────────────────────────────

class TestAllNoisyCorpus:
    @pytest.fixture
    def noisy_db(self, tmp_path):
        """所有 tag 出現次數 = 1"""
        db_path = tmp_path / "test_noisy.db"
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.executemany(
            "INSERT INTO videos (path, number, tags) VALUES (?, ?, ?)",
            [
                ("file:///a.mp4", "NNN-001", '["tagA"]'),
                ("file:///b.mp4", "NNN-002", '["tagB"]'),
                ("file:///c.mp4", "NNN-003", '["tagC"]'),
            ],
        )
        conn.commit()
        conn.close()
        return db_path

    def test_all_noise_items_empty(self, noisy_db, monkeypatch):
        """min_count=2 時全噪音 → items=[]，total_unique_tags 仍正確"""
        monkeypatch.setattr("web.routers.tags.get_db_path", lambda: noisy_db)
        from web.app import app
        client = TestClient(app)
        resp = client.get("/api/tags/top?min_count=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total_unique_tags"] == 3  # 3 個 unique tag，不受 min_count 影響

    def test_all_noise_min_count_1_shows_all(self, noisy_db, monkeypatch):
        """min_count=1 opt-in → 全部出現"""
        monkeypatch.setattr("web.routers.tags.get_db_path", lambda: noisy_db)
        from web.app import app
        client = TestClient(app)
        resp = client.get("/api/tags/top?min_count=1")
        data = resp.json()
        assert len(data["items"]) == 3


# ── 空字串 tag 過濾 ────────────────────────────────────────────────────────────

class TestEmptyStringFilter:
    def test_empty_string_tag_excluded(self, client):
        """tag="" 應被 WHERE je.value != '' 過濾"""
        resp = client.get("/api/tags/top?min_count=1")
        tags = [item["tag"] for item in resp.json()["items"]]
        assert "" not in tags


# ── First-run（Codex P2-1）──────────────────────────────────────────────────────

class TestFirstRunNoDb:
    """Regression: AI agent 在用戶第一次跑 scan/search 前直接呼叫 /api/tags/top，
    DB 檔尚未建立 → 端點應 init_db 自救，回 success=True + 空 corpus，不噴 500。"""

    def test_first_run_db_missing_returns_empty_success(self, tmp_path, monkeypatch):
        """DB 檔不存在 → init_db 自動建表 → 回 success=True + items=[]"""
        missing_db = tmp_path / "nonexistent.db"
        assert not missing_db.exists()

        monkeypatch.setattr("web.routers.tags.get_db_path", lambda: missing_db)
        from web.app import app
        client = TestClient(app)
        resp = client.get("/api/tags/top")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["items"] == []
        assert data["total_unique_tags"] == 0
        # 端點呼叫後 DB 檔應已被建立（init_db 副作用）
        assert missing_db.exists()
