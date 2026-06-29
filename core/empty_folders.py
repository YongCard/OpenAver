"""Safe empty-folder preview and cleanup helpers for personal library tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import load_config
from core.path_utils import normalize_path, uri_to_fs_path

PROTECTED_EMPTY_FOLDER_NAMES = {
    "#待整理",
    "#待人工整理",
    ".openaver-migration",
    "未整理",
}


class EmptyFolderError(RuntimeError):
    """Safe, user-correctable empty-folder cleanup failure."""


def configured_gallery_roots(config: dict[str, Any] | None = None) -> list[Path]:
    selected = config or load_config()
    gallery = selected.get("gallery", {}) if isinstance(selected, dict) else {}
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in gallery.get("directories", []) or []:
        try:
            path = Path(normalize_path(str(raw))).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        roots.append(path)
        seen.add(key)
    return roots


def _resolve_path_value(path_value: str | Path) -> Path:
    raw = str(path_value)
    if not raw or "\x00" in raw:
        raise EmptyFolderError("invalid_path")
    try:
        fs_path = uri_to_fs_path(raw)
        return Path(fs_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise EmptyFolderError("invalid_path") from exc


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _matching_root(path: Path, roots: list[Path]) -> Path | None:
    matches = [root for root in roots if path == root or _is_under(path, root)]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.parts))


def _is_protected(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in PROTECTED_EMPTY_FOLDER_NAMES for part in parts)


def _is_effectively_empty_dir(
    path: Path,
    root: Path,
    *,
    ignored_paths: set[Path] | None = None,
) -> bool:
    if path == root or _is_protected(path, root) or not path.exists() or not path.is_dir():
        return False
    ignored = ignored_paths or set()
    try:
        for child in path.iterdir():
            resolved = child.resolve(strict=False)
            if resolved in ignored:
                continue
            return False
    except OSError as exc:
        raise EmptyFolderError("scan_failed") from exc
    return True


def _is_cleanable_empty_tree(path: Path, root: Path) -> bool:
    cleanable, candidates, _skipped = _scan_empty_tree(path, root)
    return cleanable and candidates == [path]


def collect_empty_ancestors_after_removal(
    start_dir: Path,
    *,
    roots: list[Path],
    removed_paths: list[Path] | None = None,
) -> list[Path]:
    """Collect the highest empty ancestor made cleanable by removing files."""
    directory = start_dir.resolve(strict=False)
    root = _matching_root(directory, roots)
    if root is None:
        raise EmptyFolderError("path_outside_gallery")

    ignored = {path.resolve(strict=False) for path in removed_paths or []}
    highest: Path | None = None
    current = directory
    while current != root:
        if _is_effectively_empty_dir(current, root, ignored_paths=ignored):
            highest = current
            ignored.add(current)
            current = current.parent
            continue
        break
    return [highest] if highest is not None else []


def _scan_empty_tree(path: Path, root: Path) -> tuple[bool, list[Path], int]:
    """Return (cleanable, highest_candidates, skipped_protected_count)."""
    if path == root:
        candidates: list[Path] = []
        skipped = 0
        try:
            children = list(path.iterdir()) if path.exists() else []
        except OSError as exc:
            raise EmptyFolderError("scan_failed") from exc
        for child in children:
            if child.is_dir():
                _cleanable, child_candidates, child_skipped = _scan_empty_tree(child, root)
                candidates.extend(child_candidates)
                skipped += child_skipped
        return False, candidates, skipped

    if _is_protected(path, root):
        return False, [], 1

    has_blocker = False
    candidates: list[Path] = []
    skipped = 0
    try:
        children = list(path.iterdir())
    except OSError as exc:
        raise EmptyFolderError("scan_failed") from exc
    for child in children:
        if child.is_dir():
            cleanable, child_candidates, child_skipped = _scan_empty_tree(child, root)
            skipped += child_skipped
            if cleanable:
                continue
            if child_candidates:
                candidates.extend(child_candidates)
            else:
                has_blocker = True
        else:
            has_blocker = True

    if not has_blocker:
        return True, [path], skipped
    return False, candidates, skipped


def preview_empty_folders(
    *,
    config: dict[str, Any] | None = None,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    roots = configured_gallery_roots(config)
    if not roots:
        raise EmptyFolderError("gallery_not_configured")

    candidates: list[Path] = []
    skipped_protected = 0
    if paths is None:
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            _cleanable, root_candidates, skipped = _scan_empty_tree(root, root)
            candidates.extend(root_candidates)
            skipped_protected += skipped
    else:
        for raw in paths:
            path = _resolve_path_value(raw)
            root = _matching_root(path, roots)
            if root is None:
                raise EmptyFolderError("path_outside_gallery")
            if _is_protected(path, root):
                raise EmptyFolderError("protected_folder")
            if not _is_cleanable_empty_tree(path, root):
                raise EmptyFolderError("folder_not_empty")
            candidates.append(path)

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: str(item).casefold()):
        key = str(candidate).casefold()
        if key in seen:
            continue
        root = _matching_root(candidate, roots)
        if root is None or candidate == root or _is_protected(candidate, root):
            continue
        deduped.append(candidate)
        seen.add(key)

    folders = [
        {
            "path": str(path),
            "root": str(_matching_root(path, roots) or ""),
            "name": path.name,
        }
        for path in deduped
    ]
    return {
        "folder_count": len(folders),
        "folders": folders,
        "skipped_protected_count": skipped_protected,
        "protected_names": sorted(PROTECTED_EMPTY_FOLDER_NAMES),
    }


def apply_empty_folders(
    *,
    confirm: bool,
    config: dict[str, Any] | None = None,
    paths: list[str] | None = None,
    recycle_func=None,
) -> dict[str, Any]:
    if not confirm:
        raise EmptyFolderError("confirmation_required")
    preview = preview_empty_folders(config=config, paths=paths)
    folders = [Path(item["path"]) for item in preview["folders"]]
    if folders:
        selected_recycle = recycle_func
        if selected_recycle is None:
            from core.duplicate_delete import move_files_to_recycle_bin
            selected_recycle = move_files_to_recycle_bin
        selected_recycle(folders)
    return {
        "success": True,
        "removed_empty_folders": preview["folders"],
        "removed_empty_folder_count": len(folders),
        "skipped_protected_count": preview["skipped_protected_count"],
        "warnings": [],
    }
