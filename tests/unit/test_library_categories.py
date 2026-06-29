from pathlib import Path

from core.library_categories import (
    category_root_for,
    dedupe_scan_directories,
    matching_gallery_root,
)


def test_category_root_for_parent_root_uses_chinese_folder(tmp_path):
    root = tmp_path / "3.14"

    assert category_root_for(root, "western", None) == root / "欧美"
    assert category_root_for(root, "jav", None) == root / "日韩"


def test_category_root_for_category_root_does_not_double_nest(tmp_path):
    root = tmp_path / "3.14" / "欧美"

    assert category_root_for(root, "western", None) == tmp_path / "3.14" / "欧美"


def test_matching_gallery_root_returns_parent_when_source_is_in_category(tmp_path):
    root = tmp_path / "3.14"
    source = root / "欧美" / "Bangbus" / "a.mp4"

    assert matching_gallery_root(source, [root], None) == root


def test_dedupe_scan_directories_folds_category_to_parent(tmp_path):
    root = tmp_path / "3.14"

    result = dedupe_scan_directories([str(root / "欧美"), str(root / "日韩")], None)

    assert result == [str(root)]


def test_dedupe_scan_directories_removes_child_when_parent_exists(tmp_path):
    root = tmp_path / "3.14"

    result = dedupe_scan_directories([str(root), str(root / "欧美")], None)

    assert result == [str(root)]
