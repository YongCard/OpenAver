"""
test_api_collection_analysis.py — Integration tests for:
  GET  /api/collection/analysis
  POST /api/collection/analysis/groups

Uses FastAPI TestClient + tmp_path real SQLite DB.
"""

import sqlite3
import pytest
from fastapi.testclient import TestClient

from core.database import init_db
from core.path_utils import to_file_uri


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """空 videos 表（只建 schema）"""
    db_path = tmp_path / "test_analysis.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def client(tmp_db, monkeypatch):
    """TestClient，monkeypatch get_db_path → tmp_db"""
    monkeypatch.setattr("web.routers.collection.get_db_path", lambda: tmp_db)
    from web.app import app
    return TestClient(app)


def _insert_videos(db_path, rows):
    """Helper: 批次插入測試影片資料"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany(
        """INSERT INTO videos
           (path, number, title, actresses, maker, tags, director, label,
            original_title, cover_path, release_date, nfo_mtime)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


# ── I1: analysis happy path ───────────────────────────────────────────────────

class TestAnalysisHappyPath:
    """I1: 插入 5 筆影片，驗證 analysis 端點正確計算各統計數值"""

    def test_analysis_returns_correct_counts(self, tmp_db, monkeypatch):
        # 插入 5 筆（含各種缺失情況）
        rows = [
            # path, number, title, actresses, maker, tags, director, label, original_title, cover_path, release_date, nfo_mtime
            (to_file_uri("/test/SONE-205.mp4"), "SONE-205", "Title 1",
             '["明日花キララ"]', "Sony", '["巨乳","ハイビジョン"]',
             "Dir A", "Label A", "原題 1", "/covers/SONE-205.jpg", "2024-01-01", 1000.0),
            (to_file_uri("/test/ABW-001.mp4"), "ABW-001", "Title 2",
             '["葵つかさ"]', "ABC", '["女教師"]',
             "Dir B", "Label B", "原題 2", "/covers/ABW-001.jpg", "2024-02-01", 2000.0),
            # nfo_mtime=0 → missing_nfo
            (to_file_uri("/test/IPZ-154.mp4"), "IPZ-154", "Title 3",
             '["あい"]', "Idea", '["単体"]',
             "Dir C", "Label C", "原題 3", "/covers/IPZ-154.jpg", "2024-03-01", 0.0),
            # director='' → missing_fields.director
            (to_file_uri("/test/MIDE-001.mp4"), "MIDE-001", "Title 4",
             '["女優D"]', "Mide", '["スレンダー"]',
             "", "Label D", "原題 4", "/covers/MIDE-001.jpg", "2024-04-01", 3000.0),
            # actresses='[]' → empty_array_fields.actresses
            (to_file_uri("/test/PRED-001.mp4"), "PRED-001", "Title 5",
             "[]", "Pred", '["美乳"]',
             "Dir E", "Label E", "原題 5", "/covers/PRED-001.jpg", "2024-05-01", 4000.0),
        ]
        _insert_videos(tmp_db, rows)

        monkeypatch.setattr("web.routers.collection.get_db_path", lambda: tmp_db)
        from web.app import app
        test_client = TestClient(app)

        resp = test_client.get("/api/collection/analysis")
        assert resp.status_code == 200
        data = resp.json()

        # 基本計數
        assert data["total_videos"] == 5

        # nfo_status
        assert data["nfo_status"]["missing_nfo"] >= 1   # IPZ-154 (nfo_mtime=0)
        assert data["nfo_status"]["has_nfo"] >= 2

        # missing_fields
        assert data["missing_fields"]["director"] >= 1  # MIDE-001 director=''

        # empty_array_fields
        assert data["empty_array_fields"]["actresses"] >= 1  # PRED-001 actresses='[]'

        # available_groups 含 5 個 group 名稱
        expected_groups = {
            "no_nfo", "corrupted_numbers", "japanese_tags",
            "missing_core", "missing_secondary"
        }
        assert set(data["available_groups"]) == expected_groups

        # 結構完整性
        assert "missing_fields" in data
        assert "empty_array_fields" in data
        assert "corrupted_numbers" in data
        assert "japanese_tags" in data
        assert "nfo_status" in data


# ── I2: analysis 空 DB ────────────────────────────────────────────────────────

class TestAnalysisEmptyDb:
    """I2: 空 videos 表，所有計數應為 0，available_groups 仍有 5 個"""

    def test_analysis_empty_db(self, client):
        resp = client.get("/api/collection/analysis")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_videos"] == 0

        for field in ["title", "actresses", "maker", "tags",
                      "release_date", "cover_path", "director",
                      "label", "original_title"]:
            assert data["missing_fields"][field] == 0, \
                f"missing_fields.{field} 應為 0，實際為 {data['missing_fields'][field]}"

        assert data["empty_array_fields"]["actresses"] == 0
        assert data["empty_array_fields"]["tags"] == 0
        assert data["corrupted_numbers"]["total"] == 0
        assert data["japanese_tags"]["total"] == 0
        assert data["nfo_status"]["has_nfo"] == 0
        assert data["nfo_status"]["missing_nfo"] == 0

        assert len(data["available_groups"]) == 5


# ── I3: groups no_nfo happy path ──────────────────────────────────────────────

class TestGroupsNoNfo:
    """I3: no_nfo group 應回傳 nfo_mtime=0 且符合 is_number_format() 的影片"""

    def test_groups_no_nfo_happy_path(self, tmp_db, monkeypatch):
        rows = [
            # 3 筆 nfo_mtime=0，number 符合 is_number_format()
            (to_file_uri("/test/SONE-205.mp4"), "SONE-205", "Title 1",
             '["明日花"]', "Sony", '[]', "Dir", "L", "T1", None, "2024-01-01", 0.0),
            (to_file_uri("/test/ABW-001.mp4"), "ABW-001", "Title 2",
             '["葵"]', "ABC", '[]', "Dir", "L", "T2", None, "2024-02-01", 0.0),
            (to_file_uri("/test/IPZ-154.mp4"), "IPZ-154", "Title 3",
             '["あ"]', "Idea", '[]', "Dir", "L", "T3", None, "2024-03-01", 0.0),
            # 1 筆正常（有 nfo）→ 不應出現在結果
            (to_file_uri("/test/MIDE-001.mp4"), "MIDE-001", "Title 4",
             '["女優D"]', "Mide", '[]', "Dir", "L", "T4", None, "2024-04-01", 9999.0),
        ]
        _insert_videos(tmp_db, rows)

        monkeypatch.setattr("web.routers.collection.get_db_path", lambda: tmp_db)
        from web.app import app
        test_client = TestClient(app)

        resp = test_client.post(
            "/api/collection/analysis/groups",
            json={"group": "no_nfo", "limit": 50, "exclude_western": False},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 3
        assert len(data["items"]) == 3

        # 每筆含必填欄位
        for item in data["items"]:
            assert "id" in item
            assert "number" in item
            assert "file_path" in item
            assert "title" in item
            assert "maker" in item

        # 正常影片不應出現
        returned_numbers = {item["number"] for item in data["items"]}
        assert "MIDE-001" not in returned_numbers

        # response 結構
        assert data["group"] == "no_nfo"
        assert data["limit"] == 50
        assert data["exclude_western"] is False

    def test_groups_total_reflects_real_count_not_limit(self, tmp_db, monkeypatch):
        """total 應為符合條件的真實總數，不受 limit 限制"""
        rows = [
            (to_file_uri("/test/A-%03d.mp4" % i), "SONE-%03d" % i, "Title %d" % i,
             '[]', "Sony", '[]', "", "", "", None, "", 0.0)
            for i in range(1, 11)  # 10 筆 no_nfo
        ]
        _insert_videos(tmp_db, rows)

        monkeypatch.setattr("web.routers.collection.get_db_path", lambda: tmp_db)
        from web.app import app
        test_client = TestClient(app)

        resp = test_client.post(
            "/api/collection/analysis/groups",
            json={"group": "no_nfo", "limit": 3, "exclude_western": False},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 10   # 真實總數
        assert len(data["items"]) == 3  # 只回傳 limit 筆


# ── I4: groups 無效 group → 422 ───────────────────────────────────────────────

class TestGroupsInvalidGroup:
    """I4: 無效 group 名稱應回傳 HTTP 422"""

    def test_groups_invalid_group_returns_422(self, client):
        resp = client.post(
            "/api/collection/analysis/groups",
            json={"group": "evil_group", "limit": 50},
        )
        assert resp.status_code == 422
