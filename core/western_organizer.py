"""Preview/apply organizer for Stash-backed western scenes."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import load_config
from core.database import VideoRepository
from core.db_inflow import try_inflow_upsert
from core.empty_folders import configured_gallery_roots
from core.library_migration import SIDECAR_EXTENSIONS
from core.library_categories import category_root_for, matching_gallery_root
from core.organizer import sanitize_filename, truncate_to_chars
from core.path_utils import to_file_uri, uri_to_fs_path

RUN_FOLDER = ".openaver-migration"
DEFAULT_APPLY_BATCH_SIZE = 20
DEFAULT_WESTERN_PROFILE = {
    "create_folder": True,
    "folder_format": "{studio}/{year}/{date} {title}",
    "filename_format": "[{date}] {title} - {performers}{suffix}",
    "external_manager": "jellyfin",
}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}
MOJIBAKE_DIR_REPLACEMENTS = {
    "Å·ÃÀ": "欧美",
    "Êý¾Ý¿â": "数据库",
}


class WesternOrganizerError(RuntimeError):
    """Safe, user-correctable western organizer failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _parent_writable_without_creating(path: Path) -> bool:
    """Best-effort preflight that does not create target folders or files."""
    parent = path.parent
    while not _path_exists(parent) and parent != parent.parent:
        parent = parent.parent
    if not _path_exists(parent) or not _path_is_dir(parent):
        return False
    return os.access(str(parent), os.W_OK)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise WesternOrganizerError("manifest_not_found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WesternOrganizerError("invalid_manifest") from exc


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, RuntimeError):
        return False


def _path_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, RuntimeError):
        return False


def _path_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, RuntimeError):
        return False


def _path_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _existing_path(path: Path) -> Path | None:
    resolved = _safe_resolve(path)
    return resolved if _path_exists(resolved) else None


def _mojibake_corrected_path(path: Path) -> Path | None:
    raw = str(path)
    fixed = raw
    for bad, good in MOJIBAKE_DIR_REPLACEMENTS.items():
        fixed = fixed.replace(bad, good)
    if fixed == raw:
        return None
    return _safe_resolve(Path(fixed))


def _candidate_from_mojibake(path: Path) -> Path | None:
    fixed = _mojibake_corrected_path(path)
    return _existing_path(fixed) if fixed else None


def _unc_roots_from_nas(config: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    try:
        from core.nas import build_unc_path
    except Exception:
        return roots
    for share in (config.get("nas", {}) or {}).get("shares", []) or []:
        if not isinstance(share, dict) or share.get("enabled") is False:
            continue
        unc = build_unc_path(
            str(share.get("host", "")),
            str(share.get("share", "")),
            str(share.get("subpath", "")),
        )
        if unc:
            roots.append(_safe_resolve(Path(unc)))
    return roots


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        resolved = _safe_resolve(path)
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _effective_roots(config: dict[str, Any], roots: list[Path]) -> list[Path]:
    nas_roots = _unc_roots_from_nas(config)
    corrected_roots = [
        corrected
        for root in [*roots, *nas_roots]
        if (corrected := _mojibake_corrected_path(root)) is not None
    ]
    western_roots = [category_root_for(root, "western", config) for root in [*corrected_roots, *nas_roots, *roots]]
    return _dedupe_paths([*western_roots, *corrected_roots, *nas_roots, *roots])


def _resolve_video_source(video: Any, config: dict[str, Any], roots: list[Path]) -> Path:
    """Resolve DB URI to a real source path, with NAS/mojibake fallbacks."""
    try:
        original = _safe_resolve(Path(uri_to_fs_path(video.path)))
    except Exception:
        original = _safe_resolve(Path(str(video.path)))

    for candidate in (_candidate_from_mojibake(original), original):
        if candidate and _path_exists(candidate):
            return candidate

    filename = original.name
    search_roots = _effective_roots(config, roots)
    seen: set[str] = set()
    for root in search_roots:
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        candidate = _existing_path(root / filename)
        if candidate is not None:
            return candidate

    return original


def _roots(config: dict[str, Any]) -> list[Path]:
    roots = configured_gallery_roots(config)
    if not roots:
        raise WesternOrganizerError("gallery_not_configured")
    return roots


def _profile(config: dict[str, Any]) -> dict[str, Any]:
    profile = (
        config.get("scraper_profiles", {})
        .get("western", {})
    )
    return {**DEFAULT_WESTERN_PROFILE, **(profile if isinstance(profile, dict) else {})}


def _format_value(template: str, data: dict[str, str]) -> str:
    value = str(template or "")
    for key, replacement in data.items():
        value = value.replace("{" + key + "}", replacement)
    value = re.sub(r"\{[^{}]+\}", "", value)
    return value


def _date_parts(date: str) -> tuple[str, str, str]:
    match = re.match(r"^(\d{4})(?:[-./](\d{2})(?:[-./](\d{2}))?)?", date or "")
    if not match:
        return "", "", ""
    return match.group(1) or "", match.group(2) or "", match.group(3) or ""


def _data_for_video(video: Any) -> dict[str, str]:
    date = (video.release_date or "").strip()
    year, month, day = _date_parts(date)
    performers = ", ".join(video.actresses or [])
    title = (video.title or video.original_title or video.number or "Untitled").strip()
    studio = (video.maker or "Unknown Studio").strip()
    return {
        "num": video.number or "",
        "scene_id": video.number or "",
        "title": title,
        "studio": studio,
        "maker": studio,
        "performers": performers,
        "actors": performers,
        "actor": (video.actresses or [""])[0] if video.actresses else "",
        "date": date,
        "year": year,
        "month": month,
        "day": day,
        "suffix": "",
    }


def _safe_component(value: str, fallback: str = "Unknown") -> str:
    clean = sanitize_filename(value or "").strip(". ")
    return clean or fallback


def _target_for_video(video: Any, source: Path, root: Path, profile: dict[str, Any]) -> Path:
    data = _data_for_video(video)
    filename_template = profile.get("filename_format") or DEFAULT_WESTERN_PROFILE["filename_format"]
    folder_template = profile.get("folder_format") or DEFAULT_WESTERN_PROFILE["folder_format"]
    stem = _safe_component(_format_value(filename_template, data), source.stem)
    stem = truncate_to_chars(stem, 180)

    if not profile.get("create_folder", True):
        return root / f"{stem}{source.suffix.lower()}"

    parts = []
    for raw_part in re.split(r"[\\/]+", folder_template):
        formatted = _safe_component(_format_value(raw_part, data))
        parts.append(truncate_to_chars(formatted, 120))
    return root.joinpath(*parts, f"{stem}{source.suffix.lower()}")


def _same_stem_related(video: Path) -> list[Path]:
    stems = {
        video.stem.casefold(),
        f"{video.stem}-poster".casefold(),
        f"{video.stem}-fanart".casefold(),
        f"{video.stem}-cover".casefold(),
        f"{video.stem}-thumb".casefold(),
    }
    exts = set(SIDECAR_EXTENSIONS) | SUBTITLE_EXTENSIONS
    try:
        children = list(video.parent.iterdir())
    except OSError:
        return []
    related = []
    for child in children:
        if child == video or not _path_is_file(child):
            continue
        if child.suffix.lower() not in exts:
            continue
        if child.stem.casefold() in stems:
            related.append(_safe_resolve(child))
    return sorted(related, key=lambda item: str(item).casefold())


def _related_target(sidecar: Path, source: Path, target_video: Path) -> Path:
    old_stem = source.stem
    new_stem = target_video.stem
    if sidecar.stem.casefold() == old_stem.casefold():
        return target_video.with_suffix(sidecar.suffix)
    if sidecar.name.casefold().startswith(old_stem.casefold()):
        return target_video.with_name(new_stem + sidecar.name[len(old_stem):])
    return target_video.parent / sidecar.name


def _path_mappings(config: dict[str, Any] | None = None) -> dict[str, str] | None:
    selected = config or load_config()
    mappings = (selected.get("gallery", {}) or {}).get("path_mappings") or {}
    return mappings or None


def _sync_moved_sidecar_db(target: Path, config: dict[str, Any] | None = None) -> None:
    repo = VideoRepository()
    mappings = _path_mappings(config)
    target_uri = to_file_uri(str(target), mappings)
    video = repo.get_by_path(target_uri)
    if not video:
        return
    nfo = target.with_suffix(".nfo")
    cover = target.with_suffix(".jpg")
    video.nfo_mtime = _path_mtime(nfo) if _path_exists(nfo) else 0.0
    if _path_exists(cover):
        video.cover_path = to_file_uri(str(cover), mappings)
    repo.upsert(video)


def _western_videos(repo: VideoRepository, roots: list[Path], config: dict[str, Any]) -> list[Any]:
    allowed_roots = _effective_roots(config, roots)
    root_strings = [os.path.normcase(str(_safe_resolve(root))) for root in allowed_roots]
    videos = []
    for video in repo.get_all():
        number = (video.number or "").upper()
        if not number.startswith("WEST-"):
            continue
        fs_path = _resolve_video_source(video, config, roots)
        if any(_is_under(fs_path, Path(root)) or os.path.normcase(str(fs_path)).startswith(root) for root in root_strings):
            videos.append(video)
    return videos


def _entry_metadata(video: Any) -> dict[str, Any]:
    return {
        "number": video.number or "",
        "title": video.title or "",
        "original_title": video.original_title or "",
        "actors": video.actresses or [],
        "tags": video.tags or [],
        "date": video.release_date or "",
        "maker": video.maker or "",
        "director": video.director or "",
        "series": video.series or "",
        "label": video.label or "",
        "duration": video.duration,
    }


def preview_western_organize(
    *,
    selected_paths: list[str] | None = None,
    config: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    roots = _roots(selected_config)
    repo = VideoRepository()
    profile = _profile(selected_config)
    selected = {str(item) for item in (selected_paths or []) if item}
    selected_fs = set()
    for item in selected:
        try:
            selected_fs.add(os.path.normcase(str(_safe_resolve(Path(uri_to_fs_path(item))))))
        except Exception:
            selected_fs.add(os.path.normcase(str(_safe_resolve(Path(item)))))

    selected_run_id = run_id or f"western-organizer-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    organizer_roots = _effective_roots(selected_config, roots)
    reserved: set[str] = set()
    entries = []
    for video in _western_videos(repo, roots, selected_config):
        source = _resolve_video_source(video, selected_config, roots)
        if selected_fs and os.path.normcase(str(source)) not in selected_fs and video.path not in selected:
            continue
        base_root = matching_gallery_root(source, organizer_roots, selected_config) or organizer_roots[0]
        root = category_root_for(base_root, "western", selected_config)
        status = "planned"
        reason = ""
        target = _target_for_video(video, source, root, profile)
        if not _path_exists(source):
            status = "skipped"
            reason = "source_missing"
        elif target == source:
            status = "skipped"
            reason = "already_in_place"
        else:
            key = os.path.normcase(str(target))
            if _path_exists(target) or key in reserved:
                status = "conflict"
                reason = "target_exists"
            reserved.add(key)
            if not _parent_writable_without_creating(target):
                status = "skipped"
                reason = "target_unwritable"

        related = []
        if status == "planned":
            for sidecar in _same_stem_related(source):
                side_target = _related_target(sidecar, source, target)
                if _path_exists(side_target) or os.path.normcase(str(side_target)) in reserved:
                    status = "conflict"
                    reason = "target_exists"
                    break
                reserved.add(os.path.normcase(str(side_target)))
                related.append({
                    "source": str(sidecar),
                    "target": str(side_target),
                    "kind": "subtitle" if sidecar.suffix.lower() in SUBTITLE_EXTENSIONS else "sidecar",
                    "size": _path_size(sidecar),
                })

        entries.append({
            "id": uuid.uuid4().hex,
            "status": status,
            "reason": reason,
            "root": str(root),
            "source": str(source),
            "target": str(target),
            "number": video.number or "",
            "title": video.title or "",
            "metadata": _entry_metadata(video),
            "sidecars": related if status == "planned" else [],
            "size": _path_size(source) if _path_exists(source) else 0,
        })

    manifest_root = Path(entries[0]["root"]) if entries else organizer_roots[0]
    run_root = manifest_root / RUN_FOLDER / selected_run_id
    manifest = {
        "run_id": selected_run_id,
        "created_at": _utc_now(),
        "entries": entries,
        "journal": str(run_root / "western_organizer_journal.json"),
    }
    manifest_path = run_root / "western_organizer_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "success": True,
        "run_id": selected_run_id,
        "manifest": str(manifest_path),
        "profile": profile,
        "summary": {
            "total": len(entries),
            "planned_count": sum(1 for item in entries if item["status"] == "planned"),
            "conflict_count": sum(1 for item in entries if item["status"] == "conflict"),
            "skipped_count": sum(1 for item in entries if item["status"] == "skipped"),
        },
        "entries": entries,
    }


def _move_no_overwrite(source: Path, target: Path) -> None:
    if _path_exists(target):
        raise WesternOrganizerError("target_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def apply_western_manifest(
    manifest: str | Path,
    *,
    confirm: bool,
    batch_size: int = DEFAULT_APPLY_BATCH_SIZE,
) -> dict[str, Any]:
    if not confirm:
        raise WesternOrganizerError("confirmation_required")
    manifest_path = Path(manifest)
    data = _read_json(manifest_path)
    journal_path = Path(data.get("journal") or manifest_path.with_name("western_organizer_journal.json"))
    journal = _read_json(journal_path) if journal_path.exists() else {"operations": []}
    operations = journal.setdefault("operations", [])
    limit = max(1, int(batch_size or DEFAULT_APPLY_BATCH_SIZE))
    selected = [entry for entry in data.get("entries", []) if entry.get("status") == "planned"][:limit]
    moved = 0
    skipped = []
    for entry in selected:
        source = Path(entry["source"])
        target = Path(entry["target"])
        if not _path_exists(source):
            entry["status"] = "skipped"
            entry["reason"] = "source_missing"
            skipped.append({"id": entry["id"], "reason": "source_missing"})
            continue
        if _path_exists(target):
            entry["status"] = "conflict"
            entry["reason"] = "target_exists"
            skipped.append({"id": entry["id"], "reason": "target_exists"})
            continue
        moved_for_entry = []
        try:
            for sidecar in entry.get("sidecars", []):
                side_source = Path(sidecar["source"])
                side_target = Path(sidecar["target"])
                if _path_exists(side_source):
                    _move_no_overwrite(side_source, side_target)
                    moved_for_entry.append((side_source, side_target, sidecar.get("kind") or "sidecar"))
            _move_no_overwrite(source, target)
            moved_for_entry.append((source, target, "video"))
        except Exception as exc:
            for original, moved_path, _kind in reversed(moved_for_entry):
                if _path_exists(moved_path) and not _path_exists(original):
                    shutil.move(str(moved_path), str(original))
            entry["status"] = "skipped"
            entry["reason"] = str(exc) or exc.__class__.__name__
            skipped.append({"id": entry["id"], "reason": entry["reason"]})
            continue
        for original, moved_path, kind in moved_for_entry:
            operations.append({
                "entry_id": entry["id"],
                "kind": kind,
                "source": str(original),
                "target": str(moved_path),
                "timestamp": _utc_now(),
            })
        entry["status"] = "moved"
        entry["moved_at"] = _utc_now()
        entry["db_sync"] = try_inflow_upsert(
            str(target),
            old_file_path=str(source),
            scraped_metadata=entry.get("metadata") or None,
        )
        _sync_moved_sidecar_db(target)
        moved += 1
    _write_json(manifest_path, data)
    _write_json(journal_path, journal)
    return {
        "success": True,
        "manifest": str(manifest_path),
        "journal": str(journal_path),
        "moved_entries": moved,
        "skipped": skipped,
        "remaining": sum(1 for entry in data.get("entries", []) if entry.get("status") == "planned"),
        "entries": data.get("entries", []),
    }
