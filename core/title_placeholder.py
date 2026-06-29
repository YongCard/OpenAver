"""Move placeholder-title videos to the manual review folder."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import load_config
from core.database import VideoRepository
from core.empty_folders import configured_gallery_roots, preview_empty_folders
from core.empty_folders import apply_empty_folders
from core.library_migration import (
    MANUAL_REVIEW_FOLDER,
    PROTECTED_FOLDER_NAMES,
    LEGACY_MANUAL_FOLDER_NAMES,
    SIDECAR_EXTENSIONS,
)
from core.path_utils import to_file_uri, uri_to_fs_path
from core.video_extensions import get_video_extensions

PLACEHOLDER_PATTERNS = (
    "标题未定",
    "標題未定",
    "未知标题",
    "未知標題",
    "unknown title",
    "title unknown",
)


class TitlePlaceholderError(RuntimeError):
    """Safe, user-correctable title-placeholder isolation failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _is_in_protected_folder(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    protected = set(PROTECTED_FOLDER_NAMES) | set(LEGACY_MANUAL_FOLDER_NAMES)
    return any(part in protected for part in parts)


def _coerce_fs_path(path_value: str) -> Path:
    return Path(uri_to_fs_path(path_value)).expanduser().resolve(strict=False)


def _read_nfo_title(video: Path) -> tuple[str | None, str | None]:
    nfo = video.with_suffix(".nfo")
    if not nfo.exists():
        return None, None
    try:
        root = ET.parse(str(nfo)).getroot()
    except ET.ParseError:
        return None, None

    def text(tag: str) -> str:
        elem = root.find(tag)
        return (elem.text or "").strip() if elem is not None else ""

    return text("title"), text("originaltitle")


def _has_placeholder_text(value: str | None, number: str | None = None) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    lowered = text.casefold()
    if any(pattern.casefold() in lowered for pattern in PLACEHOLDER_PATTERNS):
        return True
    normalized = re.sub(r"[\s\[\]【】()（）_-]+", "", lowered)
    number_norm = re.sub(r"[\s\[\]【】()（）_-]+", "", (number or "").casefold())
    return bool(number_norm and normalized == number_norm)


def _reasons(video: Path, db_video: Any | None) -> list[str]:
    number = getattr(db_video, "number", None) if db_video is not None else _number_from_name(video.name)
    reasons = []
    if any(pattern in video.name for pattern in PLACEHOLDER_PATTERNS[:4]):
        reasons.append("filename_placeholder")
    nfo_title, nfo_original = _read_nfo_title(video)
    if nfo_title is not None and _has_placeholder_text(nfo_title, number):
        reasons.append("nfo_title_placeholder")
    if nfo_original and _has_placeholder_text(nfo_original, number):
        reasons.append("nfo_original_title_placeholder")
    if db_video is not None:
        if _has_placeholder_text(getattr(db_video, "title", ""), number):
            reasons.append("db_title_placeholder")
        original = getattr(db_video, "original_title", "")
        if original and _has_placeholder_text(original, number):
            reasons.append("db_original_title_placeholder")
    return sorted(set(reasons))


def _number_from_name(name: str) -> str:
    match = re.search(r"(?i)([A-Z]{2,10})[-_ ]?([A-Z0-9]{2,8})", name)
    if not match:
        return ""
    return f"{match.group(1).upper()}-{match.group(2).upper()}"


def _sidecars_for_video(video: Path) -> list[Path]:
    result = []
    stem = video.stem.casefold()
    suffix_stems = {stem, f"{stem}-poster", f"{stem}-fanart", f"{stem}-cover", f"{stem}-thumb"}
    try:
        children = list(video.parent.iterdir())
    except OSError:
        return result
    for child in children:
        if not child.is_file() or child == video:
            continue
        if child.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        if child.stem.casefold() in suffix_stems:
            result.append(child)
    return sorted(result)


def _unique_target(path: Path, reserved: set[str]) -> Path:
    candidate = path
    index = 2
    while candidate.exists() or os.path.normcase(str(candidate)) in reserved:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        index += 1
    reserved.add(os.path.normcase(str(candidate)))
    return candidate


def _db_index(repo: VideoRepository) -> dict[str, Any]:
    index = {}
    for video in repo.get_all():
        try:
            fs_path = str(_coerce_fs_path(video.path))
        except Exception:
            fs_path = video.path
        index[os.path.normcase(fs_path)] = video
    return index


def _path_mappings(config: dict[str, Any] | None) -> dict[str, str]:
    gallery = config.get("gallery", {}) if isinstance(config, dict) else {}
    mappings = gallery.get("path_mappings", {}) or {}
    return mappings if isinstance(mappings, dict) else {}


def _db_path_candidates(
    path_value: str,
    fs_path: Path,
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    candidates = [
        path_value,
        str(fs_path),
        to_file_uri(str(fs_path)),
        to_file_uri(str(fs_path), _path_mappings(config)),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        deduped.append(candidate)
        seen.add(candidate)
    return deduped


def _find_db_video(
    repo: VideoRepository,
    entry: dict[str, Any],
    source: Path,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[str | None, Any | None]:
    stored = entry.get("db_path") or ""
    candidates = _db_path_candidates(stored or entry.get("source", ""), source, config=config)
    for candidate in candidates:
        video = repo.get_by_path(candidate)
        if video is not None:
            return candidate, video
    return None, None


def _moved_cover_path(target_video: Path, moved_for_entry: list[tuple[Path, Path, str]]) -> str:
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    image_sidecars = [
        moved for _original, moved, kind in moved_for_entry
        if kind == "sidecar" and moved.suffix.lower() in image_suffixes
    ]
    same_stem = [path for path in image_sidecars if path.stem.casefold() == target_video.stem.casefold()]
    selected = same_stem[0] if same_stem else (image_sidecars[0] if image_sidecars else None)
    return str(selected) if selected is not None else ""


def _sync_db_path_after_move(
    repo: VideoRepository,
    entry: dict[str, Any],
    source: Path,
    target: Path,
    moved_for_entry: list[tuple[Path, Path, str]],
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    old_uri, db_video = _find_db_video(repo, entry, source, config=config)
    if db_video is None:
        return False

    new_uri = to_file_uri(str(target), _path_mappings(config))
    db_video.path = new_uri
    db_video.mtime = target.stat().st_mtime if target.exists() else entry.get("mtime", 0.0)
    db_video.size_bytes = target.stat().st_size if target.exists() else entry.get("size", 0)

    nfo_path = target.with_suffix(".nfo")
    db_video.nfo_mtime = nfo_path.stat().st_mtime if nfo_path.exists() else 0.0

    cover_path = _moved_cover_path(target, moved_for_entry)
    if cover_path:
        db_video.cover_path = to_file_uri(cover_path, _path_mappings(config))

    repo.repath(old_uri, new_uri, db_video)
    return True


def _sync_db_path_after_rollback(
    repo: VideoRepository,
    source: Path,
    target: Path,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    old_uri = to_file_uri(str(target), _path_mappings(config))
    db_video = repo.get_by_path(old_uri) or repo.get_by_path(str(target))
    if db_video is None:
        return False
    new_uri = to_file_uri(str(source), _path_mappings(config))
    db_video.path = new_uri
    db_video.mtime = source.stat().st_mtime if source.exists() else db_video.mtime
    db_video.size_bytes = source.stat().st_size if source.exists() else db_video.size_bytes
    nfo_path = source.with_suffix(".nfo")
    db_video.nfo_mtime = nfo_path.stat().st_mtime if nfo_path.exists() else 0.0
    old_cover = Path(uri_to_fs_path(db_video.cover_path)).resolve(strict=False) if db_video.cover_path else None
    if old_cover and old_cover.name:
        candidate = source.with_name(old_cover.name)
        if candidate.exists():
            db_video.cover_path = to_file_uri(str(candidate), _path_mappings(config))
    repo.repath(old_uri, new_uri, db_video)
    return True


def _video_files(roots: list[Path], config: dict[str, Any]) -> list[Path]:
    extensions = {ext.lower() for ext in get_video_extensions(config)}
    videos = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            if _is_in_protected_folder(path, root):
                continue
            videos.append(path.resolve(strict=False))
    return sorted(videos, key=lambda item: str(item).casefold())


def _manifest_path(run_dir: Path) -> Path:
    return run_dir / "title_placeholder_manifest.json"


def _load_manifest(manifest: str | Path) -> dict[str, Any]:
    path = Path(manifest)
    if not path.exists():
        raise TitlePlaceholderError("manifest_not_found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TitlePlaceholderError("invalid_manifest") from exc


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preview_title_placeholders(
    *,
    run_id: str | None = None,
    config: dict[str, Any] | None = None,
    repo: VideoRepository | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    roots = configured_gallery_roots(selected_config)
    if not roots:
        raise TitlePlaceholderError("gallery_not_configured")
    selected_repo = repo or VideoRepository()
    db = _db_index(selected_repo)
    selected_run_id = run_id or f"title-placeholder-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    primary_root = roots[0]
    run_dir = primary_root / ".openaver-migration" / selected_run_id

    entries = []
    reserved_targets: set[str] = set()
    for video in _video_files(roots, selected_config):
        root = _matching_root(video, roots)
        if root is None:
            continue
        db_key = os.path.normcase(str(video))
        db_video = db.get(db_key)
        reasons = _reasons(video, db_video)
        if not reasons:
            continue

        manual_dir = root / MANUAL_REVIEW_FOLDER
        target_video = _unique_target(manual_dir / video.name, reserved_targets)
        sidecars = []
        for source in _sidecars_for_video(video):
            target = _unique_target(target_video.with_name(source.name), reserved_targets)
            sidecars.append({
                "source": str(source),
                "target": str(target),
                "size": source.stat().st_size,
                "mtime": source.stat().st_mtime,
            })

        status = "planned"
        conflict_reason = ""
        if target_video.exists() and target_video.resolve(strict=False) != video:
            status = "conflict"
            conflict_reason = "target_exists"

        entries.append({
            "id": uuid.uuid4().hex,
            "status": status,
            "reason": ",".join(reasons),
            "conflict_reason": conflict_reason,
            "root": str(root),
            "source": str(video),
            "target": str(target_video),
            "db_path": getattr(db_video, "path", "") if db_video is not None else "",
            "number": getattr(db_video, "number", None) or _number_from_name(video.name),
            "title": getattr(db_video, "title", "") if db_video is not None else "",
            "size": video.stat().st_size,
            "mtime": video.stat().st_mtime,
            "sidecars": sidecars,
        })

    manifest = {
        "run_id": selected_run_id,
        "created_at": _utc_now(),
        "roots": [str(root) for root in roots],
        "manual_folder": MANUAL_REVIEW_FOLDER,
        "entries": entries,
        "journal": str(run_dir / "title_placeholder_journal.json"),
    }
    manifest_file = _manifest_path(run_dir)
    _write_json(manifest_file, manifest)
    return {
        "run_id": selected_run_id,
        "manifest": str(manifest_file),
        "summary": {
            "candidate_count": len(entries),
            "planned_count": sum(1 for entry in entries if entry["status"] == "planned"),
            "conflict_count": sum(1 for entry in entries if entry["status"] == "conflict"),
            "sidecar_count": sum(len(entry["sidecars"]) for entry in entries),
        },
        "entries": entries,
    }


def _journal_path(manifest: dict[str, Any], manifest_path: Path) -> Path:
    return Path(manifest.get("journal") or manifest_path.with_name("title_placeholder_journal.json"))


def _load_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"operations": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"operations": []}


def _move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def apply_title_placeholder_manifest(
    manifest: str | Path,
    *,
    confirm: bool,
    batch_size: int = 20,
    repo: VideoRepository | None = None,
    cleanup_empty_folders: bool = True,
) -> dict[str, Any]:
    if not confirm:
        raise TitlePlaceholderError("confirmation_required")
    manifest_path = Path(manifest)
    data = _load_manifest(manifest_path)
    selected_config = load_config()
    batch_size = max(1, min(20, int(batch_size or 20)))
    entries = [entry for entry in data.get("entries", []) if entry.get("status") == "planned"]
    selected = entries[:batch_size]
    selected_repo = repo or VideoRepository()
    journal_path = _journal_path(data, manifest_path)
    journal = _load_journal(journal_path)
    operations = journal.setdefault("operations", [])
    moved_entries = 0
    updated_db_rows = 0
    skipped = []
    warnings = []
    removed_empty_folders: list[dict[str, Any]] = []

    for entry in selected:
        source = Path(entry["source"])
        target = Path(entry["target"])
        if not source.exists():
            skipped.append({"id": entry["id"], "reason": "source_missing"})
            entry["status"] = "skipped"
            continue
        if target.exists():
            skipped.append({"id": entry["id"], "reason": "target_exists"})
            entry["status"] = "conflict"
            continue

        moved_for_entry = []
        try:
            for action in entry.get("sidecars", []):
                sidecar_source = Path(action["source"])
                sidecar_target = Path(action["target"])
                if sidecar_source.exists():
                    if sidecar_target.exists():
                        raise TitlePlaceholderError("sidecar_target_exists")
                    _move(sidecar_source, sidecar_target)
                    moved_for_entry.append((sidecar_source, sidecar_target, "sidecar"))
            _move(source, target)
            moved_for_entry.append((source, target, "video"))
        except Exception as exc:
            for original, moved, _kind in reversed(moved_for_entry):
                if moved.exists() and not original.exists():
                    shutil.move(str(moved), str(original))
            skipped.append({"id": entry["id"], "reason": str(exc) or exc.__class__.__name__})
            entry["status"] = "skipped"
            continue

        for original, moved, kind in moved_for_entry:
            operations.append({
                "entry_id": entry["id"],
                "kind": kind,
                "source": str(original),
                "target": str(moved),
                "timestamp": _utc_now(),
            })
        if _sync_db_path_after_move(
            selected_repo,
            entry,
            source,
            target,
            moved_for_entry,
            config=selected_config,
        ):
            updated_db_rows += 1
        entry["status"] = "moved"
        entry["moved_at"] = _utc_now()
        moved_entries += 1

    _write_json(manifest_path, data)
    _write_json(journal_path, journal)
    if moved_entries and cleanup_empty_folders:
        try:
            empty_preview = preview_old_empty_folders_for_title_manifest(manifest_path)
            paths = [folder["path"] for folder in empty_preview.get("folders", [])]
            if paths:
                cleanup = apply_empty_folders(
                    confirm=True,
                    paths=paths,
                    config={"gallery": {"directories": data.get("roots", [])}},
                )
                removed_empty_folders = cleanup.get("removed_empty_folders", [])
        except Exception as exc:
            warnings.append({
                "code": str(exc) or exc.__class__.__name__,
                "message": "empty_folder_cleanup_failed",
            })
    return {
        "success": True,
        "manifest": str(manifest_path),
        "moved_entries": moved_entries,
        "updated_db_rows": updated_db_rows,
        "skipped": skipped,
        "remaining": sum(1 for entry in data.get("entries", []) if entry.get("status") == "planned"),
        "journal": str(journal_path),
        "removed_empty_folders": removed_empty_folders,
        "removed_empty_folder_count": len(removed_empty_folders),
        "warnings": warnings,
    }


def rollback_title_placeholder_manifest(
    manifest: str | Path,
    *,
    confirm: bool,
    batch_size: int = 20,
    repo: VideoRepository | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise TitlePlaceholderError("confirmation_required")
    manifest_path = Path(manifest)
    data = _load_manifest(manifest_path)
    selected_config = load_config()
    selected_repo = repo or VideoRepository()
    journal_path = _journal_path(data, manifest_path)
    journal = _load_journal(journal_path)
    batch_size = max(1, min(20, int(batch_size or 20)))
    operations = journal.get("operations", [])
    selected = operations[-batch_size:]
    rolled_back = 0
    updated_db_rows = 0
    video_pairs: list[tuple[Path, Path]] = []
    for op in reversed(selected):
        source = Path(op["source"])
        target = Path(op["target"])
        if target.exists() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(source))
            rolled_back += 1
            if op.get("kind") == "video":
                video_pairs.append((source, target))
    for source, target in video_pairs:
        if _sync_db_path_after_rollback(
            selected_repo,
            source,
            target,
            config=selected_config,
        ):
            updated_db_rows += 1
    if selected:
        journal["operations"] = operations[:-len(selected)]
    _write_json(journal_path, journal)
    return {
        "success": True,
        "rolled_back_operations": rolled_back,
        "updated_db_rows": updated_db_rows,
        "remaining_operations": len(journal.get("operations", [])),
    }


def preview_old_empty_folders_for_title_manifest(manifest: str | Path) -> dict[str, Any]:
    data = _load_manifest(manifest)
    roots = [Path(root) for root in data.get("roots", [])]
    parent_paths = sorted({str(Path(entry["source"]).parent) for entry in data.get("entries", [])})
    if not roots or not parent_paths:
        return {"folder_count": 0, "folders": [], "skipped_protected_count": 0, "protected_names": []}
    return preview_empty_folders(paths=parent_paths, config={"gallery": {"directories": [str(root) for root in roots]}})
