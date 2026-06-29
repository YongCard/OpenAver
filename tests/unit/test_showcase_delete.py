from pathlib import Path

import pytest

from core.database import Video, VideoRepository, init_db
from core.path_utils import to_file_uri
from core.showcase_delete import (
    ShowcaseDeleteError,
    apply_showcase_folder_delete,
    preview_showcase_folder_delete,
)


def _config(root: Path) -> dict:
    return {"gallery": {"directories": [str(root)], "path_mappings": {}}, "scraper": {}}


def test_preview_allows_single_video_folder_with_sidecars(tmp_path):
    root = tmp_path / "library"
    folder = root / "ABC-001"
    folder.mkdir(parents=True)
    video = folder / "ABC-001.mp4"
    video.write_bytes(b"video")
    (folder / "ABC-001.nfo").write_text("nfo", encoding="utf-8")
    (folder / "ABC-001-poster.jpg").write_bytes(b"jpg")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    repo.upsert(Video(path=to_file_uri(str(video)), number="ABC-001"))

    result = preview_showcase_folder_delete(to_file_uri(str(video)), config=_config(root), repo=repo)

    assert result["folder"] == str(folder)
    assert result["file_count"] == 3
    assert result["db_rows"] == 1


def test_preview_blocks_folder_with_other_video(tmp_path):
    root = tmp_path / "library"
    folder = root / "ABC-001"
    folder.mkdir(parents=True)
    video = folder / "ABC-001.mp4"
    video.write_bytes(b"video")
    (folder / "ABC-002.mp4").write_bytes(b"other")

    db_path = tmp_path / "openaver.db"
    init_db(db_path)

    with pytest.raises(ShowcaseDeleteError, match="folder_contains_other_videos"):
        preview_showcase_folder_delete(str(video), config=_config(root), repo=VideoRepository(db_path))


def test_preview_blocks_outside_gallery(tmp_path):
    root = tmp_path / "library"
    outside = tmp_path / "outside"
    outside.mkdir()
    video = outside / "ABC-001.mp4"
    video.write_bytes(b"video")

    with pytest.raises(ShowcaseDeleteError, match="path_outside_gallery"):
        preview_showcase_folder_delete(str(video), config=_config(root))


def test_apply_recycles_folder_before_db_delete(tmp_path):
    root = tmp_path / "library"
    folder = root / "ABC-001"
    folder.mkdir(parents=True)
    video = folder / "ABC-001.mp4"
    video.write_bytes(b"video")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    uri = to_file_uri(str(video))
    repo.upsert(Video(path=uri, number="ABC-001"))
    moved = []

    result = apply_showcase_folder_delete(
        uri,
        confirm=True,
        config=_config(root),
        repo=repo,
        recycle_func=lambda paths: moved.extend(paths),
    )

    assert moved == [folder]
    assert result["deleted_db_rows"] == 1
    assert repo.get_by_path(uri) is None


def test_apply_does_not_delete_db_when_recycle_fails(tmp_path):
    root = tmp_path / "library"
    folder = root / "ABC-001"
    folder.mkdir(parents=True)
    video = folder / "ABC-001.mp4"
    video.write_bytes(b"video")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    uri = to_file_uri(str(video))
    repo.upsert(Video(path=uri, number="ABC-001"))

    def fail_recycle(_paths):
        raise RuntimeError("boom")

    with pytest.raises(ShowcaseDeleteError, match="recycle_bin_failed"):
        apply_showcase_folder_delete(
            uri,
            confirm=True,
            config=_config(root),
            repo=repo,
            recycle_func=fail_recycle,
        )
    assert repo.get_by_path(uri) is not None
