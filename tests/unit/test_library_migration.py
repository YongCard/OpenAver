import json
import sqlite3
from pathlib import Path

import pytest

from core.library_migration import (
    MigrationConflictError,
    MigrationError,
    apply_manifest,
    inventory_library,
    plan_library,
    rollback_manifest,
    verify_manifest,
)


def _create_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE videos (
            path TEXT, number TEXT, title TEXT, original_title TEXT, actresses TEXT
        )
        """
    )
    connection.commit()
    connection.close()


def _write_nfo(path: Path, number: str, title: str, actor: str) -> None:
    path.write_text(
        f"<movie><title>{title}</title><id>{number}</id>"
        f"<actor><name>{actor}</name></actor></movie>",
        encoding="utf-8",
    )


def test_end_to_end_apply_verify_and_rollback_by_entry(tmp_path):
    root = tmp_path / "library"
    first_dir = root / "legacy" / "ABC-123"
    second_dir = root / "staging"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)

    first_video = first_dir / "ABC-123.mp4"
    first_video.write_bytes(b"first-video")
    _write_nfo(first_dir / "ABC-123.nfo", "ABC-123", "測試標題", "測試女優")
    (first_dir / "ABC-123-poster.jpg").write_bytes(b"poster")
    second_video = second_dir / "home-movie.mp4"
    second_video.write_bytes(b"manual-video")
    (second_dir / "home-movie.srt").write_text("subtitle", encoding="utf-8")

    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    database = tmp_path / "openaver.db"
    _create_db(database)

    inventory = inventory_library(
        str(root), "test-run", config_path=config, db_path=database,
    )
    result = plan_library(inventory["run_dir"], db_path=database)
    manifest_path = Path(result["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert result["automatic"] == 1
    assert result["manual_move_ready"] == 1
    assert result["conflicts"] == 0
    assert manifest["entries"][0]["target"].endswith(
        "測試女優\\ABC-123\\[ABC-123] 測試標題.mp4"
    )

    applied = apply_manifest(str(manifest_path), "test-run", batch_size=2)
    assert applied["moved_this_batch"] == 2
    assert verify_manifest(str(manifest_path))["success"] is True
    assert not first_video.exists()
    assert not second_video.exists()

    rolled_back = rollback_manifest(str(manifest_path), "test-run", batch_size=2)
    assert rolled_back["rolled_back_entries"] == 2
    assert rolled_back["rolled_back_operations"] == 5
    assert first_video.read_bytes() == b"first-video"
    assert second_video.read_bytes() == b"manual-video"
    assert verify_manifest(str(manifest_path))["success"] is True


def test_apply_rejects_changed_source_before_any_move(tmp_path):
    root = tmp_path / "library"
    video_dir = root / "Actor" / "ABC-123"
    video_dir.mkdir(parents=True)
    video = video_dir / "ABC-123.mp4"
    video.write_bytes(b"original")
    _write_nfo(video.with_suffix(".nfo"), "ABC-123", "Title", "Actor")
    database = tmp_path / "openaver.db"
    _create_db(database)

    inventory = inventory_library(str(root), "changed", db_path=database)
    manifest = plan_library(inventory["run_dir"], db_path=database)["manifest"]
    video.write_bytes(b"changed")

    with pytest.raises(MigrationConflictError, match="preflight_failed"):
        apply_manifest(manifest, "changed")
    assert video.exists()


def test_plan_sends_ambiguous_duplicate_to_review(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    (folder / "ABC-123-one.mp4").write_bytes(b"one")
    (folder / "ABC-123-two.mp4").write_bytes(b"two")
    database = tmp_path / "openaver.db"
    _create_db(database)

    inventory = inventory_library(str(root), "duplicate", db_path=database)
    result = plan_library(inventory["run_dir"], db_path=database)

    assert result["automatic"] == 0
    assert result["review"] == 2


def test_plan_rejects_tampered_inventory_path_outside_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    database = tmp_path / "openaver.db"
    _create_db(database)
    inventory = inventory_library(str(root), "tampered", db_path=database)
    inventory_path = Path(inventory["run_dir"]) / "inventory.json"
    data = json.loads(inventory_path.read_text(encoding="utf-8"))
    data["videos"] = [{"path": str(outside)}]
    inventory_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MigrationError, match="inventory_path_outside_root"):
        plan_library(inventory["run_dir"], db_path=database)


def test_inventory_skips_manual_review_folder_by_default(tmp_path):
    root = tmp_path / "library"
    regular_dir = root / "regular"
    manual_dir = root / "#待人工整理"
    legacy_manual_dir = root / "未整理"
    regular_dir.mkdir(parents=True)
    manual_dir.mkdir(parents=True)
    legacy_manual_dir.mkdir(parents=True)
    (regular_dir / "ABC-123.mp4").write_bytes(b"regular")
    (manual_dir / "manual.mp4").write_bytes(b"manual")
    (legacy_manual_dir / "legacy.mp4").write_bytes(b"legacy")
    database = tmp_path / "openaver.db"
    _create_db(database)

    inventory = inventory_library(str(root), "skip-manual", db_path=database)
    inventory_data = json.loads((Path(inventory["run_dir"]) / "inventory.json").read_text(encoding="utf-8"))

    assert inventory["video_count"] == 1
    assert inventory_data["manual_folder"] == "#待人工整理"
    assert inventory_data["include_manual"] is False
    assert inventory_data["videos"][0]["relative_path"] == str(Path("regular") / "ABC-123.mp4")


def test_inventory_can_include_manual_review_folder_for_reidentify(tmp_path):
    root = tmp_path / "library"
    manual_dir = root / "#待人工整理"
    manual_dir.mkdir(parents=True)
    (manual_dir / "SUN-20.avi").write_bytes(b"manual")
    database = tmp_path / "openaver.db"
    _create_db(database)

    inventory = inventory_library(
        str(root),
        "include-manual",
        include_manual=True,
        db_path=database,
    )
    inventory_data = json.loads((Path(inventory["run_dir"]) / "inventory.json").read_text(encoding="utf-8"))

    assert inventory["video_count"] == 1
    assert inventory_data["include_manual"] is True
    assert inventory_data["videos"][0]["relative_path"] == str(Path("#待人工整理") / "SUN-20.avi")
