import json
from pathlib import Path

import pytest

from core.database import Video, VideoRepository, init_db
from core.path_utils import to_file_uri
from core.title_placeholder import (
    TitlePlaceholderError,
    apply_title_placeholder_manifest,
    preview_title_placeholders,
    rollback_title_placeholder_manifest,
)


def _config(root):
    return {
        "gallery": {
            "directories": [str(root)],
            "path_mappings": {},
            "extensions": [".mp4", ".avi"],
        }
    }


def _repo(tmp_path):
    db_path = tmp_path / "openaver.db"
    init_db(db_path)
    return VideoRepository(db_path)


def test_filename_placeholder_is_detected(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "KIDM-451"
    folder.mkdir(parents=True)
    video = folder / "[KIDM-451] 标题未定.mp4"
    video.write_bytes(b"video")

    result = preview_title_placeholders(config=_config(root), repo=_repo(tmp_path))

    assert result["summary"]["candidate_count"] == 1
    assert result["entries"][0]["source"] == str(video)
    assert result["entries"][0]["target"].endswith(str(Path("#待整理") / "[KIDM-451] 标题未定.mp4"))


def test_nfo_placeholder_is_detected(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    video = folder / "[ABC-123] Real title.mp4"
    video.write_bytes(b"video")
    video.with_suffix(".nfo").write_text("<movie><title>标题未定</title></movie>", encoding="utf-8")

    result = preview_title_placeholders(config=_config(root), repo=_repo(tmp_path))

    assert result["summary"]["candidate_count"] == 1
    assert "nfo_title_placeholder" in result["entries"][0]["reason"]


def test_db_blank_or_number_title_is_detected(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    video = folder / "[ABC-123] Something.mp4"
    video.write_bytes(b"video")
    repo = _repo(tmp_path)
    repo.upsert(Video(path=to_file_uri(str(video)), number="ABC-123", title="ABC-123"))

    result = preview_title_placeholders(config=_config(root), repo=repo)

    assert result["summary"]["candidate_count"] == 1
    assert "db_title_placeholder" in result["entries"][0]["reason"]


def test_protected_dirs_are_skipped(tmp_path):
    root = tmp_path / "library"
    for protected in ("#待整理", "#待人工整理", ".openaver-migration", "未整理"):
        folder = root / protected
        folder.mkdir(parents=True)
        (folder / "[KIDM-451] 标题未定.mp4").write_bytes(b"video")

    result = preview_title_placeholders(config=_config(root), repo=_repo(tmp_path))

    assert result["summary"]["candidate_count"] == 0


def test_apply_moves_video_sidecars_and_updates_db_path(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "KIDM-451"
    folder.mkdir(parents=True)
    video = folder / "[KIDM-451] 标题未定.mp4"
    nfo = folder / "[KIDM-451] 标题未定.nfo"
    poster = folder / "[KIDM-451] 标题未定-poster.jpg"
    subtitle = folder / "[KIDM-451] 标题未定.srt"
    video.write_bytes(b"video")
    nfo.write_text("<movie><title>标题未定</title></movie>", encoding="utf-8")
    poster.write_bytes(b"poster")
    subtitle.write_text("subtitle", encoding="utf-8")
    repo = _repo(tmp_path)
    repo.upsert(Video(path=to_file_uri(str(video)), number="KIDM-451", title="标题未定"))

    preview = preview_title_placeholders(config=_config(root), repo=repo)
    result = apply_title_placeholder_manifest(
        preview["manifest"],
        confirm=True,
        repo=repo,
        cleanup_empty_folders=False,
    )

    target = root / "#待整理" / video.name
    assert result["moved_entries"] == 1
    assert result["updated_db_rows"] == 1
    assert target.exists()
    assert (target.parent / nfo.name).exists()
    assert (target.parent / poster.name).exists()
    assert (target.parent / subtitle.name).exists()
    assert repo.get_by_path(to_file_uri(str(target))) is not None
    assert repo.get_by_path(to_file_uri(str(video))) is None


def test_target_conflict_gets_safe_suffix(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "KIDM-451"
    manual = root / "#待整理"
    folder.mkdir(parents=True)
    manual.mkdir(parents=True)
    video = folder / "[KIDM-451] 标题未定.mp4"
    video.write_bytes(b"video")
    (manual / video.name).write_bytes(b"existing")

    result = preview_title_placeholders(config=_config(root), repo=_repo(tmp_path))

    assert result["entries"][0]["target"].endswith("[KIDM-451] 标题未定__2.mp4")


def test_apply_requires_confirm(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "KIDM-451"
    folder.mkdir(parents=True)
    (folder / "[KIDM-451] 标题未定.mp4").write_bytes(b"video")
    preview = preview_title_placeholders(config=_config(root), repo=_repo(tmp_path))

    with pytest.raises(TitlePlaceholderError, match="confirmation_required"):
        apply_title_placeholder_manifest(preview["manifest"], confirm=False)


def test_apply_respects_twenty_item_batch_limit(tmp_path):
    root = tmp_path / "library"
    repo = _repo(tmp_path)
    for index in range(21):
        folder = root / "Actor" / f"ABC-{index:03d}"
        folder.mkdir(parents=True)
        (folder / f"[ABC-{index:03d}] 标题未定.mp4").write_bytes(b"video")

    preview = preview_title_placeholders(config=_config(root), repo=repo)
    result = apply_title_placeholder_manifest(
        preview["manifest"],
        confirm=True,
        batch_size=21,
        repo=repo,
        cleanup_empty_folders=False,
    )

    assert result["moved_entries"] == 20
    assert result["remaining"] == 1


def test_rollback_restores_video_and_sidecar(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "KIDM-451"
    folder.mkdir(parents=True)
    video = folder / "[KIDM-451] 标题未定.mp4"
    nfo = folder / "[KIDM-451] 标题未定.nfo"
    video.write_bytes(b"video")
    nfo.write_text("<movie><title>标题未定</title></movie>", encoding="utf-8")
    repo = _repo(tmp_path)
    repo.upsert(Video(path=to_file_uri(str(video)), number="KIDM-451", title="标题未定"))
    preview = preview_title_placeholders(config=_config(root), repo=repo)
    apply_title_placeholder_manifest(
        preview["manifest"],
        confirm=True,
        repo=repo,
        cleanup_empty_folders=False,
    )

    result = rollback_title_placeholder_manifest(preview["manifest"], confirm=True, batch_size=20, repo=repo)

    assert result["rolled_back_operations"] == 2
    assert video.exists()
    assert nfo.exists()
    assert repo.get_by_path(to_file_uri(str(video))) is not None


def test_preview_manifest_is_fixed_json(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "KIDM-451"
    folder.mkdir(parents=True)
    (folder / "[KIDM-451] 标题未定.mp4").write_bytes(b"video")

    preview = preview_title_placeholders(config=_config(root), repo=_repo(tmp_path))
    manifest = json.loads(Path(preview["manifest"]).read_text(encoding="utf-8"))

    assert manifest["manual_folder"] == "#待整理"
    assert manifest["entries"][0]["status"] == "planned"
