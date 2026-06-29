import pytest

from core.empty_folders import (
    EmptyFolderError,
    apply_empty_folders,
    preview_empty_folders,
)


def _config(root):
    return {"gallery": {"directories": [str(root)], "path_mappings": {}}}


def test_preview_finds_highest_empty_folder_without_deleting_root(tmp_path):
    root = tmp_path / "library"
    empty_number = root / "Actor" / "ABC-123"
    empty_number.mkdir(parents=True)

    result = preview_empty_folders(config=_config(root))

    assert result["folder_count"] == 1
    assert result["folders"][0]["path"] == str(root / "Actor")


def test_preview_keeps_non_empty_folders(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    (folder / ".hidden").write_text("keep", encoding="utf-8")

    result = preview_empty_folders(config=_config(root))

    assert result["folder_count"] == 0


def test_preview_skips_protected_folders(tmp_path):
    root = tmp_path / "library"
    (root / "#待整理" / "empty").mkdir(parents=True)
    (root / "#待人工整理" / "empty").mkdir(parents=True)
    (root / ".openaver-migration" / "empty").mkdir(parents=True)
    (root / "未整理" / "empty").mkdir(parents=True)

    result = preview_empty_folders(config=_config(root))

    assert result["folder_count"] == 0
    assert result["skipped_protected_count"] == 4


def test_apply_requires_confirmation(tmp_path):
    root = tmp_path / "library"
    (root / "Actor").mkdir(parents=True)

    with pytest.raises(EmptyFolderError, match="confirmation_required"):
        apply_empty_folders(confirm=False, config=_config(root))


def test_apply_rechecks_and_recycles_empty_folder(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    moved = []

    result = apply_empty_folders(
        confirm=True,
        config=_config(root),
        recycle_func=lambda paths: moved.extend(paths),
    )

    assert result["removed_empty_folder_count"] == 1
    assert moved == [root / "Actor"]


def test_apply_accepts_empty_folder_tree_from_preview(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    moved = []

    result = apply_empty_folders(
        confirm=True,
        config=_config(root),
        paths=[str(root / "Actor")],
        recycle_func=lambda paths: moved.extend(paths),
    )

    assert result["removed_empty_folder_count"] == 1
    assert moved == [root / "Actor"]


def test_apply_rejects_folder_tree_with_file(tmp_path):
    root = tmp_path / "library"
    folder = root / "Actor" / "ABC-123"
    folder.mkdir(parents=True)
    (folder / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(EmptyFolderError, match="folder_not_empty"):
        apply_empty_folders(
            confirm=True,
            config=_config(root),
            paths=[str(root / "Actor")],
            recycle_func=lambda _paths: None,
        )


def test_apply_rejects_outside_gallery_path(tmp_path):
    root = tmp_path / "library"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    with pytest.raises(EmptyFolderError, match="path_outside_gallery"):
        apply_empty_folders(confirm=True, config=_config(root), paths=[str(outside)])
