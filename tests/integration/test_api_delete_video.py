"""
test_api_delete_video.py — DELETE /api/showcase/video 整合測試（71-T7）

核心安全契約：DELETE 只刪 DB row + 衍生縮圖 WebP，**絕不 unlink 影片檔或原始封面檔**。

測試用真 temp DB + 真 temp 影片檔 + 真 temp 封面檔 + 真 temp thumb dir，
DELETE 後明確斷言：
- DB row 消失（repo.get_by_path → None）
- 影片檔 & 封面檔仍在磁碟（os.path.exists True）—— 最重要的斷言
- 預先 generate 的 thumb webp 被 invalidate 砍掉
- 未知 path → {"deleted": 0} no-op，不拋、不影響其他 row
"""

import os
import pytest
from pathlib import Path
from PIL import Image
from core.database import init_db, VideoRepository, Video
from core.path_utils import to_file_uri
from core import thumbnail_cache


@pytest.fixture
def delete_setup(tmp_path):
    """真 temp DB + 真影片檔 + 真封面檔；thumb dir = db.parent/thumb（thumbnail_cache 推導規則）。

    回傳 dict：{db_path, vid_uri, vid_fs, cover_fs, vid2_uri}
    """
    video_dir = tmp_path / "videos"
    video_dir.mkdir()

    # 真實影片檔 + 封面檔（內容隨意，存在性才是重點）
    vid_fs = video_dir / "video1.mp4"
    vid_fs.write_bytes(b"\x00fake-mp4-bytes\x00")
    cover_fs = video_dir / "video1.jpg"
    # 真實可解碼 JPG（thumbnail generate 需要真圖；存在性才是核心斷言目標）
    Image.new("RGB", (200, 300), (180, 120, 90)).save(cover_fs, "JPEG")

    vid_uri = to_file_uri(str(vid_fs), {})
    cover_uri = to_file_uri(str(cover_fs), {})
    vid2_uri = to_file_uri(str(video_dir / "video2.mp4"), {})

    db_path = tmp_path / "showcase_test.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    repo.upsert_batch([
        Video(
            path=vid_uri,
            number="SONE-001",
            title="To Be Deleted",
            cover_path=cover_uri,
            size_bytes=12,
            mtime=1700000000.0,
        ),
        Video(
            path=vid2_uri,
            number="SONE-002",
            title="Bystander",
            size_bytes=0,
            mtime=0.0,
        ),
    ])

    return {
        "db_path": db_path,
        "vid_uri": vid_uri,
        "vid_fs": vid_fs,
        "cover_fs": cover_fs,
        "vid2_uri": vid2_uri,
    }


def _patch_db_path(mocker, db_path):
    """showcase endpoint 與 thumbnail_cache 都從 get_db_path 解析（後者推導 thumb dir）。"""
    mocker.patch("web.routers.showcase.get_db_path", return_value=db_path)
    mocker.patch("core.thumbnail_cache.get_db_path", return_value=db_path)


class TestDeleteVideoRemovesDbRow:
    def test_delete_removes_db_row(self, client, delete_setup, mocker):
        """DELETE 後 repo.get_by_path → None（DB row 消失）。"""
        _patch_db_path(mocker, delete_setup["db_path"])

        resp = client.delete(
            "/api/showcase/video", params={"path": delete_setup["vid_uri"]}
        )

        assert resp.status_code == 200
        assert resp.json() == {"deleted": 1}

        repo = VideoRepository(delete_setup["db_path"])
        assert repo.get_by_path(delete_setup["vid_uri"]) is None

    def test_delete_does_not_affect_other_rows(self, client, delete_setup, mocker):
        """只刪目標 row，其他 row 不受影響。"""
        _patch_db_path(mocker, delete_setup["db_path"])

        client.delete("/api/showcase/video", params={"path": delete_setup["vid_uri"]})

        repo = VideoRepository(delete_setup["db_path"])
        assert repo.get_by_path(delete_setup["vid2_uri"]) is not None


class TestDeleteVideoNeverUnlinksFiles:
    """【核心安全】DELETE 絕不刪磁碟上的影片檔或原始封面檔。"""

    def test_video_file_still_exists(self, client, delete_setup, mocker):
        _patch_db_path(mocker, delete_setup["db_path"])

        client.delete("/api/showcase/video", params={"path": delete_setup["vid_uri"]})

        assert os.path.exists(delete_setup["vid_fs"]), \
            "DELETE 不得 unlink 影片檔"

    def test_cover_file_still_exists(self, client, delete_setup, mocker):
        _patch_db_path(mocker, delete_setup["db_path"])

        client.delete("/api/showcase/video", params={"path": delete_setup["vid_uri"]})

        assert os.path.exists(delete_setup["cover_fs"]), \
            "DELETE 不得 unlink 原始封面檔"


class TestDeleteVideoInvalidatesThumb:
    def test_pregenerated_thumb_removed_after_delete(self, client, delete_setup, mocker):
        """預先 generate 的縮圖 WebP，DELETE 後被 invalidate 砍掉。"""
        _patch_db_path(mocker, delete_setup["db_path"])

        # 預先 generate 一個真 thumb（thumb dir = db.parent/thumb，已 patch get_db_path）
        thumb = thumbnail_cache.get_or_create(
            delete_setup["vid_uri"], str(delete_setup["cover_fs"])
        )
        assert thumb is not None and thumb.exists(), "前置：thumb 應已生成"

        client.delete("/api/showcase/video", params={"path": delete_setup["vid_uri"]})

        assert not thumb.exists(), "DELETE 後對應 thumb webp 應被 invalidate 砍掉"


class TestDeleteVideoUnknownPath:
    def test_unknown_path_is_noop(self, client, delete_setup, mocker):
        """未知 path → {"deleted": 0}，不拋、不影響其他 row。"""
        _patch_db_path(mocker, delete_setup["db_path"])

        unknown = to_file_uri(str(Path(delete_setup["vid_fs"]).parent / "ghost.mp4"), {})
        resp = client.delete("/api/showcase/video", params={"path": unknown})

        assert resp.status_code == 200
        assert resp.json() == {"deleted": 0}

        repo = VideoRepository(delete_setup["db_path"])
        assert repo.get_by_path(delete_setup["vid_uri"]) is not None
        assert repo.get_by_path(delete_setup["vid2_uri"]) is not None
