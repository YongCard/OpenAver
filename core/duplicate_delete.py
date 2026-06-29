"""Safe duplicate-video deletion helpers for the personal duplicate detector."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any

from core.config import load_config
from core.database import VideoRepository
from core.empty_folders import (
    EmptyFolderError,
    collect_empty_ancestors_after_removal,
    configured_gallery_roots,
)
from core.logger import get_logger
from core.path_utils import normalize_path, to_file_uri, uri_to_fs_path
from core.thumbnail_cache import invalidate as invalidate_thumb
from core.video_extensions import get_video_extensions

logger = get_logger(__name__)

SIDECAR_EXTENSIONS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx",
}
STEM_SIDECAR_SUFFIXES = (
    "-poster", "-fanart", "-cover", "-thumb", "-landscape", "-clearlogo", "-banner",
)


class DuplicateDeleteError(RuntimeError):
    """Safe, user-correctable duplicate-delete failure."""


def _resolve_existing_file(path_value: str) -> Path:
    if not path_value or "\x00" in path_value:
        raise DuplicateDeleteError("invalid_path")
    try:
        path = Path(uri_to_fs_path(path_value)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise DuplicateDeleteError("invalid_path") from exc
    if not path.exists():
        raise DuplicateDeleteError("path_not_found")
    if not path.is_file():
        raise DuplicateDeleteError("path_not_file")
    return path


def _configured_gallery_roots(config: dict[str, Any] | None = None) -> list[Path]:
    selected = config or load_config()
    gallery = selected.get("gallery", {}) if isinstance(selected, dict) else {}
    roots: list[Path] = []
    for raw in gallery.get("directories", []) or []:
        try:
            path = Path(normalize_path(str(raw))).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        roots.append(path)
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _ensure_in_gallery(path: Path, *, config: dict[str, Any] | None = None) -> None:
    roots = _configured_gallery_roots(config)
    if not roots:
        raise DuplicateDeleteError("gallery_not_configured")
    if not any(_is_under(path, root) for root in roots):
        raise DuplicateDeleteError("path_outside_gallery")


def _canonical_video_uri(path: Path, *, config: dict[str, Any] | None = None) -> str:
    selected = config or load_config()
    gallery = selected.get("gallery", {}) if isinstance(selected, dict) else {}
    mappings = gallery.get("path_mappings", {}) or {}
    return to_file_uri(str(path), mappings)


def _db_path_candidates(
    path_value: str,
    video: Path,
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Return exact path variants that may have been stored in the DB.

    OpenAver normally stores ``file:///`` URIs, but older personal workflows and
    migration helpers may leave plain Windows paths in the database.  Deleting
    the physical file should therefore clean up either representation, without
    guessing beyond the same resolved file.
    """
    candidates = [
        path_value,
        str(video),
        _canonical_video_uri(video, config=config),
        to_file_uri(str(video)),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        deduped.append(candidate)
        seen.add(candidate)
    return deduped


def _sidecars_for_video(video: Path) -> list[Path]:
    prefix = video.stem.lower()
    matches = []
    for item in video.parent.iterdir():
        if not item.is_file() or item == video:
            continue
        if item.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        lowered_stem = item.stem.lower()
        if lowered_stem == prefix or any(lowered_stem == f"{prefix}{suffix}" for suffix in STEM_SIDECAR_SUFFIXES):
            matches.append(item)
    return sorted(matches)


def preview_duplicate_delete(
    path_value: str,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the video and same-stem sidecars that would be moved to trash."""
    video = _resolve_existing_file(path_value)
    _ensure_in_gallery(video, config=config)
    extensions = {ext.lower() for ext in get_video_extensions(config or load_config())}
    if video.suffix.lower() not in extensions:
        raise DuplicateDeleteError("path_not_video")
    files = [video, *_sidecars_for_video(video)]
    roots = configured_gallery_roots(config)
    empty_candidates = collect_empty_ancestors_after_removal(
        video.parent,
        roots=roots,
        removed_paths=files,
    )
    total_bytes = sum(item.stat().st_size for item in files)
    return {
        "path": str(video),
        "path_uri": _canonical_video_uri(video, config=config),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "empty_folder_candidates": [
            {"path": str(path), "root": str(next((root for root in roots if path.is_relative_to(root)), ""))}
            for path in empty_candidates
        ],
        "empty_folder_candidate_count": len(empty_candidates),
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


def move_files_to_recycle_bin(paths: list[Path]) -> None:
    """Move files to the Windows recycle bin. Never falls back to permanent delete."""
    if sys.platform != "win32":
        raise DuplicateDeleteError("recycle_bin_unavailable")
    if not paths:
        return
    # SHFileOperationW expects a double-NUL-terminated multi-string.
    encoded = "\0".join(str(path) for path in paths) + "\0\0"
    shell32 = ctypes.windll.shell32
    fof_allow_undo = 0x0040
    fof_no_confirmation = 0x0010
    fof_silent = 0x0004
    fo_delete = 3

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_bool),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    operation = SHFILEOPSTRUCTW(
        None,
        fo_delete,
        encoded,
        None,
        fof_allow_undo | fof_no_confirmation | fof_silent,
        False,
        None,
        None,
    )
    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise DuplicateDeleteError("recycle_bin_failed")


def apply_duplicate_delete(
    path_value: str,
    *,
    confirm: bool,
    config: dict[str, Any] | None = None,
    repo: VideoRepository | None = None,
) -> dict[str, Any]:
    """Move a duplicate video and same-stem sidecars to trash, then remove its DB row."""
    if not confirm:
        raise DuplicateDeleteError("confirmation_required")
    preview = preview_duplicate_delete(path_value, config=config)
    files = [Path(item["path"]) for item in preview["files"]]
    video_uri = preview["path_uri"]
    selected_repo = repo or VideoRepository()
    warnings = []
    removed_empty_folders = []
    move_files_to_recycle_bin(files)
    empty_folders = [Path(item["path"]) for item in preview.get("empty_folder_candidates", [])]
    if empty_folders:
        try:
            move_files_to_recycle_bin(empty_folders)
            removed_empty_folders = preview.get("empty_folder_candidates", [])
        except (DuplicateDeleteError, EmptyFolderError) as exc:
            warnings.append({
                "code": str(exc) or exc.__class__.__name__,
                "message": "empty_folder_recycle_failed",
                "folders": preview.get("empty_folder_candidates", []),
            })
    deleted = selected_repo.delete_by_paths(
        _db_path_candidates(path_value, files[0], config=config)
    )
    try:
        invalidate_thumb(video_uri)
    except Exception:
        logger.exception("duplicate delete thumbnail invalidate failed: %s", video_uri)
    return {
        "success": True,
        "deleted_db_rows": deleted,
        "moved_to_recycle_bin": len(files),
        "removed_empty_folders": removed_empty_folders,
        "removed_empty_folder_count": len(removed_empty_folders),
        "warnings": warnings,
        "files": preview["files"],
    }
