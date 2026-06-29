"""Safe physical deletion helpers for Showcase videos."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.config import load_config
from core.database import VideoRepository
from core.duplicate_delete import DuplicateDeleteError, move_files_to_recycle_bin
from core.logger import get_logger
from core.path_utils import normalize_path, to_file_uri, uri_to_fs_path
from core.thumbnail_cache import invalidate as invalidate_thumb
from core.video_extensions import get_video_extensions

logger = get_logger(__name__)


class ShowcaseDeleteError(RuntimeError):
    """Safe, user-correctable Showcase physical-delete failure."""


SIDECAR_EXTENSIONS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx",
}


def _resolve_file(path_value: str) -> Path:
    if not path_value or "\x00" in path_value:
        raise ShowcaseDeleteError("invalid_path")
    try:
        path = Path(uri_to_fs_path(path_value)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ShowcaseDeleteError("invalid_path") from exc
    if not path.exists():
        raise ShowcaseDeleteError("path_not_found")
    if not path.is_file():
        raise ShowcaseDeleteError("path_not_file")
    return path


def _gallery_roots(config: dict[str, Any]) -> list[Path]:
    gallery = config.get("gallery", {}) if isinstance(config, dict) else {}
    roots: list[Path] = []
    for raw in gallery.get("directories", []) or []:
        try:
            roots.append(Path(normalize_path(str(raw))).expanduser().resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            continue
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _ensure_in_gallery(path: Path, config: dict[str, Any]) -> None:
    roots = _gallery_roots(config)
    if not roots:
        raise ShowcaseDeleteError("gallery_not_configured")
    if not any(_is_under(path, root) for root in roots):
        raise ShowcaseDeleteError("path_outside_gallery")


def _folder_files(folder: Path) -> list[Path]:
    try:
        return sorted((item for item in folder.iterdir() if item.is_file()), key=lambda p: p.name.lower())
    except OSError as exc:
        raise ShowcaseDeleteError("folder_unreadable") from exc


def _canonical_uri(path: Path, config: dict[str, Any]) -> str:
    mappings = (config.get("gallery", {}) or {}).get("path_mappings", {}) or {}
    return to_file_uri(str(path), mappings)


def _db_path_candidates(path_value: str, video: Path, config: dict[str, Any]) -> list[str]:
    candidates = [path_value, str(video), _canonical_uri(video, config), to_file_uri(str(video))]
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            result.append(candidate)
            seen.add(candidate)
    return result


def preview_showcase_folder_delete(
    path_value: str,
    *,
    config: dict[str, Any] | None = None,
    repo: VideoRepository | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    video = _resolve_file(path_value)
    _ensure_in_gallery(video, selected_config)
    video_exts = {ext.lower() for ext in get_video_extensions(selected_config)}
    if video.suffix.lower() not in video_exts:
        raise ShowcaseDeleteError("path_not_video")

    folder = video.parent
    files = _folder_files(folder)
    videos = [item for item in files if item.suffix.lower() in video_exts]
    if len(videos) != 1 or videos[0].resolve(strict=False) != video:
        raise ShowcaseDeleteError("folder_contains_other_videos")

    selected_repo = repo or VideoRepository()
    db_video = selected_repo.get_by_path(path_value)
    if db_video is None:
        for candidate in _db_path_candidates(path_value, video, selected_config):
            db_video = selected_repo.get_by_path(candidate)
            if db_video is not None:
                break
    target_number = (db_video.number if db_video else "") or ""
    folder_db_rows = [
        row for row in selected_repo.get_all()
        if _same_parent(row.path, folder)
    ]
    other_numbers = sorted({
        (row.number or "")
        for row in folder_db_rows
        if target_number and row.number and row.number != target_number
    })
    if other_numbers:
        raise ShowcaseDeleteError("folder_contains_other_numbers")

    allowed_exts = video_exts | SIDECAR_EXTENSIONS
    unexpected = [item for item in files if item.suffix.lower() not in allowed_exts]
    if unexpected:
        raise ShowcaseDeleteError("folder_contains_unknown_files")

    return {
        "folder": str(folder),
        "path": str(video),
        "path_uri": _canonical_uri(video, selected_config),
        "number": target_number,
        "file_count": len(files),
        "total_bytes": sum(item.stat().st_size for item in files),
        "db_rows": len(folder_db_rows) or (1 if db_video else 0),
        "blocked": False,
        "files": [
            {
                "path": str(item),
                "name": item.name,
                "size": item.stat().st_size,
                "kind": "video" if item == video else "sidecar",
            }
            for item in files
        ],
    }


def apply_showcase_folder_delete(
    path_value: str,
    *,
    confirm: bool,
    config: dict[str, Any] | None = None,
    repo: VideoRepository | None = None,
    recycle_func=None,
) -> dict[str, Any]:
    if not confirm:
        raise ShowcaseDeleteError("confirmation_required")
    selected_config = config or load_config()
    selected_repo = repo or VideoRepository()
    preview = preview_showcase_folder_delete(path_value, config=selected_config, repo=selected_repo)
    folder = Path(preview["folder"])
    selected_recycle = recycle_func or move_files_to_recycle_bin
    try:
        selected_recycle([folder])
    except DuplicateDeleteError as exc:
        raise ShowcaseDeleteError(str(exc) or "recycle_bin_failed") from exc
    except Exception as exc:
        raise ShowcaseDeleteError("recycle_bin_failed") from exc

    delete_candidates: list[str] = []
    for file_item in preview["files"]:
        if file_item.get("kind") != "video":
            continue
        file_path = Path(file_item["path"])
        delete_candidates.extend(_db_path_candidates(path_value, file_path, selected_config))
    deleted = selected_repo.delete_by_paths(delete_candidates)
    try:
        invalidate_thumb(preview["path_uri"])
    except Exception:
        logger.exception("showcase physical delete thumbnail invalidate failed: %s", preview["path_uri"])
    return {
        "success": True,
        "moved_to_recycle_bin": 1,
        "deleted_db_rows": deleted,
        "warnings": [],
        "folder": preview["folder"],
        "files": preview["files"],
    }


def _same_parent(uri_or_path: str, folder: Path) -> bool:
    try:
        path = Path(uri_to_fs_path(uri_or_path)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return os.path.normcase(str(path.parent)) == os.path.normcase(str(folder))
