import pytest

from core.database import Video, VideoRepository, init_db
from core.duplicate_delete import (
    DuplicateDeleteError,
    apply_duplicate_delete,
    preview_duplicate_delete,
)
from core.path_utils import to_file_uri


def _config(root):
    return {
        "gallery": {"directories": [str(root)], "path_mappings": {}},
        "scraper": {},
    }


def test_preview_collects_video_and_same_stem_sidecars(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    video = root / "ABC-123.mp4"
    video.write_bytes(b"video")
    nfo = root / "ABC-123.nfo"
    nfo.write_bytes(b"nfo")
    poster = root / "ABC-123-poster.jpg"
    poster.write_bytes(b"poster")
    other = root / "ABC-123-CD2.nfo"
    other.write_bytes(b"other")

    result = preview_duplicate_delete(str(video), config=_config(root))

    names = [item["name"] for item in result["files"]]
    assert set(names) == {"ABC-123.mp4", "ABC-123.nfo", "ABC-123-poster.jpg"}
    assert "ABC-123-CD2.nfo" not in names
    assert result["file_count"] == 3


def test_preview_reports_empty_folder_candidate_after_file_removal(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    video = folder / "ABC-123.mp4"
    video.write_bytes(b"video")
    nfo = folder / "ABC-123.nfo"
    nfo.write_bytes(b"nfo")

    result = preview_duplicate_delete(str(video), config=_config(root))

    assert result["empty_folder_candidate_count"] == 1
    assert result["empty_folder_candidates"][0]["path"] == str(root / "Actor")


def test_preview_rejects_path_outside_gallery(tmp_path):
    root = tmp_path / "library"
    outside = tmp_path / "outside.mp4"
    root.mkdir()
    outside.write_bytes(b"video")

    with pytest.raises(DuplicateDeleteError, match="path_outside_gallery"):
        preview_duplicate_delete(str(outside), config=_config(root))


def test_apply_requires_confirmation(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    video = root / "ABC-123.mp4"
    video.write_bytes(b"video")

    with pytest.raises(DuplicateDeleteError, match="confirmation_required"):
        apply_duplicate_delete(str(video), confirm=False, config=_config(root))


def test_apply_moves_to_recycle_before_db_delete(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    video = root / "ABC-123.mp4"
    video.write_bytes(b"video")
    nfo = root / "ABC-123.nfo"
    nfo.write_bytes(b"nfo")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    uri = to_file_uri(str(video))
    repo.upsert(Video(path=uri, number="ABC-123"))
    moved = []

    def fake_recycle(paths):
        moved.extend(paths)

    monkeypatch.setattr("core.duplicate_delete.move_files_to_recycle_bin", fake_recycle)

    result = apply_duplicate_delete(str(video), confirm=True, config=_config(root), repo=repo)

    assert [path.name for path in moved] == ["ABC-123.mp4", "ABC-123.nfo"]
    assert result["deleted_db_rows"] == 1
    assert result["removed_empty_folder_count"] == 0
    assert repo.get_by_path(uri) is None


def test_apply_cleans_empty_parent_folder_chain(tmp_path, monkeypatch):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    video = folder / "ABC-123.mp4"
    video.write_bytes(b"video")
    moved = []

    monkeypatch.setattr("core.duplicate_delete.move_files_to_recycle_bin", lambda paths: moved.extend(paths))

    result = apply_duplicate_delete(str(video), confirm=True, config=_config(root))

    assert [path.name for path in moved] == ["ABC-123.mp4", "Actor"]
    assert result["removed_empty_folder_count"] == 1
    assert result["removed_empty_folders"][0]["path"] == str(root / "Actor")


def test_apply_keeps_success_when_empty_folder_recycle_fails(tmp_path, monkeypatch):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    video = folder / "ABC-123.mp4"
    video.write_bytes(b"video")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    uri = to_file_uri(str(video))
    repo.upsert(Video(path=uri, number="ABC-123"))
    calls = []

    def fake_recycle(paths):
        calls.append([path.name for path in paths])
        if paths and paths[0].is_dir():
            raise DuplicateDeleteError("recycle_bin_failed")

    monkeypatch.setattr("core.duplicate_delete.move_files_to_recycle_bin", fake_recycle)

    result = apply_duplicate_delete(str(video), confirm=True, config=_config(root), repo=repo)

    assert calls == [["ABC-123.mp4"], ["Actor"]]
    assert result["deleted_db_rows"] == 1
    assert result["removed_empty_folder_count"] == 0
    assert result["warnings"][0]["message"] == "empty_folder_recycle_failed"


def test_apply_deletes_plain_windows_path_db_row(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    video = root / "ABC-123.mp4"
    video.write_bytes(b"video")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    raw_path = str(video)
    repo.upsert(Video(path=raw_path, number="ABC-123"))

    monkeypatch.setattr("core.duplicate_delete.move_files_to_recycle_bin", lambda _paths: None)

    result = apply_duplicate_delete(raw_path, confirm=True, config=_config(root), repo=repo)

    assert result["deleted_db_rows"] == 1
    assert repo.get_by_path(raw_path) is None


def test_apply_does_not_delete_db_when_recycle_fails(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    video = root / "ABC-123.mp4"
    video.write_bytes(b"video")
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    repo = VideoRepository(db_path)
    uri = to_file_uri(str(video))
    repo.upsert(Video(path=uri, number="ABC-123"))

    def fail_recycle(_paths):
        raise DuplicateDeleteError("recycle_bin_failed")

    monkeypatch.setattr("core.duplicate_delete.move_files_to_recycle_bin", fail_recycle)

    with pytest.raises(DuplicateDeleteError, match="recycle_bin_failed"):
        apply_duplicate_delete(str(video), confirm=True, config=_config(root), repo=repo)
    assert repo.get_by_path(uri) is not None
