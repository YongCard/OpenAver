"""Manifest-driven library migration service.

The service inventories an existing library, creates an immutable move plan,
applies at most twenty entries per request, verifies every recorded file, and
can roll back complete entries.  No operation overwrites an existing file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import get_db_path
from core.config import load_config
from core.logger import get_logger
from core.path_utils import normalize_path
from core.scrapers.utils import extract_number
from core.video_extensions import get_video_extensions

logger = get_logger(__name__)

SIDECAR_EXTENSIONS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx",
}
MANUAL_REVIEW_FOLDER = "#待人工整理"
PROTECTED_FOLDER_NAMES = {"#待人工整理", ".openaver-migration"}
LEGACY_MANUAL_FOLDER_NAMES = {"未整理"}
IGNORED_ACTOR_FOLDERS = {
    *PROTECTED_FOLDER_NAMES,
    *LEGACY_MANUAL_FOLDER_NAMES,
    "#整理完成",
}
INVALID_COMPONENT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PART_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(cd|dvd|disc|disk|part|pt)[-_.\s]*([1-9A-Z])(?=[-_.\s\[\]()]|$)"
)
VERSION_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(uc|uncensored|restored|vr|u|c)(?=[-_.\s\[\]()]|$)")


class MigrationError(RuntimeError):
    """A safe, user-correctable migration failure."""


class MigrationConflictError(MigrationError):
    """The immutable plan cannot be applied without manual review."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_path(value: str | Path, *, must_exist: bool = False, directory: bool = False) -> Path:
    """Normalize an untrusted filesystem path before use."""
    raw = str(value).strip()
    if not raw or "\x00" in raw or raw.lower().startswith("file:"):
        raise MigrationError("invalid_path")
    try:
        path = Path(normalize_path(raw)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise MigrationError("invalid_path") from exc
    if must_exist and not path.exists():
        raise MigrationError("path_not_found")
    if directory and path.exists() and not path.is_dir():
        raise MigrationError("path_not_directory")
    return path


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("invalid_json") from exc


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _quick_fingerprint(path: Path, sample_size: int = 1024 * 1024) -> str:
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(sample_size))
        if size > sample_size:
            handle.seek(max(0, size - sample_size))
            digest.update(handle.read(sample_size))
    return digest.hexdigest()


def _sanitize_component(value: str | None, fallback: str, limit: int) -> str:
    cleaned = INVALID_COMPONENT_CHARS.sub(" ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .") or fallback
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if cleaned.upper() in reserved:
        cleaned = f"_{cleaned}"
    return cleaned[:limit].rstrip(" .") or fallback


def _is_in_named_folder(path: Path, root: Path, folder_names: set[str]) -> bool:
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return False
    return any(part in folder_names for part in parts)


def _all_files(root: Path, extensions: set[str], *, include_manual: bool = False) -> list[Path]:
    migration_dir = root / ".openaver-migration"
    skipped_folders = PROTECTED_FOLDER_NAMES | LEGACY_MANUAL_FOLDER_NAMES
    if include_manual:
        skipped_folders = {".openaver-migration"}
    return sorted(
        path for path in root.rglob("*")
        if (
            path.is_file()
            and path.suffix.lower() in extensions
            and not _is_under(path, migration_dir)
            and not _is_in_named_folder(path, root, skipped_folders)
        )
    )


def _video_extensions() -> set[str]:
    return {extension.lower() for extension in get_video_extensions(load_config())}


def _parse_nfo(path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"number": None, "title": None, "actors": []}
    if path is None or not path.exists():
        return result
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return result
    for element in root.findall(".//uniqueid"):
        if (element.attrib.get("type") or "").lower() in {"num", "number", "id"}:
            if number := extract_number(element.text or ""):
                result["number"] = number
                break
    for tag in ("numid", "id", "number", "sorttitle", "title"):
        if result["number"]:
            break
        element = root.find(tag)
        number = extract_number(element.text or "") if element is not None else None
        if number:
            result["number"] = number
            break
    title = root.find("title")
    if title is not None and title.text:
        result["title"] = title.text.strip()
    result["actors"] = [
        name.text.strip()
        for actor in root.findall("actor")
        if (name := actor.find("name")) is not None and name.text and name.text.strip()
    ]
    return result


def _choose_nfo(video: Path, videos_in_dir: list[Path]) -> Path | None:
    same_stem = video.with_suffix(".nfo")
    if same_stem.exists():
        return same_stem
    nfos = sorted(video.parent.glob("*.nfo"))
    if len(nfos) == 1 and len(videos_in_dir) == 1:
        return nfos[0]
    folder_named = video.parent / f"{video.parent.name}.nfo"
    return folder_named if folder_named.exists() else None


def _load_db_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT path, number, title, original_title, actresses FROM videos"
        ).fetchall()
        return {os.path.normcase(os.path.abspath(row["path"])): dict(row) for row in rows}
    except sqlite3.Error as exc:
        raise MigrationError("database_read_failed") from exc
    finally:
        connection.close()


def _db_actors(row: dict[str, Any]) -> list[str]:
    raw = row.get("actresses")
    if not raw:
        return []
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
        return [str(value).strip() for value in values if str(value).strip()]
    except (json.JSONDecodeError, TypeError):
        return [value.strip() for value in str(raw).split(",") if value.strip()]


def _path_actor(video: Path, root: Path) -> str | None:
    for part in video.relative_to(root).parts[:-1]:
        if part not in IGNORED_ACTOR_FOLDERS and not part.startswith("#") and not extract_number(part):
            return part
    return None


def _strip_number(title: str | None, number: str) -> str:
    result = (title or "").strip()
    for variant in (number, number.replace("-", ""), number.replace("-", "_")):
        result = re.sub(
            rf"^\s*[\[(]?{re.escape(variant)}[\])]?\s*[-:：]?\s*", "", result, flags=re.IGNORECASE,
        )
    return result or number


def _detect_part(stem: str) -> str:
    matches = list(PART_RE.finditer(stem))
    if matches:
        match = matches[-1]
        prefix, value = match.groups()
        value = {"A": "1", "B": "2"}.get(value.upper(), value.upper())
        return f"-CD{value}" if prefix.lower() in {"cd", "dvd", "disc", "disk"} else f"-Part{value}"
    tail = re.search(r"(?i)(?:^|[-_.\s])([AB])$", stem)
    if tail:
        return f"-CD{1 if tail.group(1).upper() == 'A' else 2}"
    trailing = re.search(r"(?:^|[-_.\s])([1-9])$", stem)
    return f"-CD{trailing.group(1)}" if trailing else ""


def _detect_version(stem: str) -> str:
    matches = list(VERSION_RE.finditer(stem))
    if not matches:
        return ""
    token = matches[-1].group(1).upper()
    return {"UNCENSORED": "-U", "RESTORED": "-Restored"}.get(token, f"-{token}")


def _sidecars_for_video(video: Path, videos_in_dir: list[Path], number: str | None) -> list[Path]:
    matches: set[Path] = set()
    for path in video.parent.iterdir():
        if not path.is_file() or path.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        if path.stem.lower() == video.stem.lower() or path.name.lower().startswith(f"{video.stem.lower()}-"):
            matches.add(path)
    if len(videos_in_dir) == 1:
        matches.update(
            path for path in video.parent.iterdir()
            if path.is_file() and path.suffix.lower() in SIDECAR_EXTENSIONS
        )
    elif number and all(extract_number(item.stem) == number for item in videos_in_dir):
        if video == sorted(videos_in_dir)[0]:
            matches.update(
                path for path in video.parent.iterdir()
                if path.is_file() and path.suffix.lower() in SIDECAR_EXTENSIONS
            )
    return sorted(matches)


def _sidecar_target(source: Path, old_video: Path, new_video: Path) -> Path:
    if source.name.lower().startswith(old_video.stem.lower()):
        return new_video.parent / f"{new_video.stem}{source.name[len(old_video.stem):]}"
    return new_video.parent / source.name


def _validated_run_dir(value: str | Path) -> tuple[Path, dict[str, Any], Path]:
    run_dir = _resolve_path(value, must_exist=True, directory=True)
    inventory = _read_json(run_dir / "inventory.json")
    root = _resolve_path(inventory.get("root", ""), must_exist=True, directory=True)
    expected = root / ".openaver-migration" / str(inventory.get("run_id", ""))
    if run_dir != expected.resolve(strict=False):
        raise MigrationError("invalid_run_directory")
    return run_dir, inventory, root


def inventory_library(
    root_value: str,
    run_id: str | None = None,
    *,
    include_manual: bool = False,
    config_path: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    root = _resolve_path(root_value, must_exist=True, directory=True)
    selected_run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    if not RUN_ID_RE.fullmatch(selected_run_id):
        raise MigrationError("invalid_run_id")
    run_dir = root / ".openaver-migration" / selected_run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise MigrationConflictError("run_directory_not_empty")
    backup_root = run_dir / "backup"
    sidecar_backup = backup_root / "sidecars"
    sidecar_backup.mkdir(parents=True, exist_ok=True)

    videos = _all_files(root, _video_extensions(), include_manual=include_manual)
    sidecars = _all_files(root, SIDECAR_EXTENSIONS, include_manual=include_manual)
    video_items = []
    for path in videos:
        stat = path.stat()
        video_items.append({
            "path": str(path),
            "relative_path": str(path.relative_to(root)),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "quick_fingerprint": _quick_fingerprint(path),
        })
    sidecar_items = []
    for path in sidecars:
        relative = path.relative_to(root)
        backup = sidecar_backup / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        sidecar_items.append({
            "path": str(path),
            "relative_path": str(relative),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "backup_path": str(backup),
        })

    if config_path is None:
        from core.config import CONFIG_PATH

        config_path = CONFIG_PATH
    if config_path.exists():
        shutil.copy2(config_path, backup_root / "openaver-config.json")
    selected_db = db_path or get_db_path()
    if selected_db.exists():
        source = sqlite3.connect(str(selected_db))
        destination = sqlite3.connect(str(backup_root / "openaver.db"))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    baseline = {
        "video_count": len(video_items),
        "video_bytes": sum(item["size"] for item in video_items),
        "sidecar_count": len(sidecar_items),
    }
    inventory = {
        "schema_version": 1,
        "run_id": selected_run_id,
        "created_at": _utc_now(),
        "root": str(root),
        "include_manual": include_manual,
        "manual_folder": MANUAL_REVIEW_FOLDER,
        "baseline": baseline,
        "videos": video_items,
        "sidecars": sidecar_items,
    }
    _atomic_json(run_dir / "inventory.json", inventory)
    return {"run_id": selected_run_id, "run_dir": str(run_dir), **baseline}


def plan_library(
    run_dir_value: str,
    *,
    max_path: int = 240,
    unknown_actor: str = "未知女優",
    manual_folder: str = MANUAL_REVIEW_FOLDER,
    db_path: Path | None = None,
) -> dict[str, Any]:
    run_dir, inventory, root = _validated_run_dir(run_dir_value)
    journal_path = run_dir / "journal.json"
    if journal_path.exists() and _read_json(journal_path).get("operations"):
        raise MigrationConflictError("migration_already_started")
    if not 120 <= max_path <= 1024:
        raise MigrationError("invalid_max_path")
    unknown_actor = _sanitize_component(unknown_actor, "未知女優", 80)
    manual_folder = _sanitize_component(manual_folder, "#待人工整理", 80)
    rows = _load_db_rows(db_path or get_db_path())
    videos = [_resolve_path(item["path"], must_exist=True) for item in inventory["videos"]]
    if any(not _is_under(video, root) for video in videos):
        raise MigrationError("inventory_path_outside_root")
    by_dir: dict[Path, list[Path]] = defaultdict(list)
    for video in videos:
        by_dir[video.parent].append(video)

    metadata: list[dict[str, Any]] = []
    for video in videos:
        row = rows.get(os.path.normcase(os.path.abspath(video))) or {}
        nfo_path = _choose_nfo(video, by_dir[video.parent])
        nfo = _parse_nfo(nfo_path)
        number = extract_number(video.parent.name) or nfo["number"] or row.get("number") or extract_number(video.stem)
        actors = nfo["actors"] or _db_actors(row)
        metadata.append({
            "video": video,
            "row": row,
            "nfo_path": nfo_path,
            "number": number,
            "actor": actors[0] if actors else _path_actor(video, root),
            "title": nfo["title"] or row.get("title") or row.get("original_title"),
        })

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in metadata:
        if item["number"]:
            groups[(item["number"], os.path.normcase(str(item["video"].parent)))].append(item)

    entries: list[dict[str, Any]] = []
    manual_entries: list[dict[str, Any]] = []
    claimed_sidecars: set[str] = set()
    for item in metadata:
        video = item["video"]
        stat = video.stat()
        base = {
            "id": str(uuid.uuid4()),
            "source": str(video),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "quick_fingerprint": _quick_fingerprint(video),
        }
        number = item["number"]
        if not number:
            target = root / manual_folder / video.name
            actions = _build_sidecar_actions(video, target, by_dir[video.parent], None, claimed_sidecars)
            manual_entries.append({
                **base,
                "target": str(target),
                "status": "planned_manual_move",
                "reason": "number_not_recognized",
                "sidecars": actions,
            })
            continue

        group = sorted(groups[(number, os.path.normcase(str(video.parent)))], key=lambda value: str(value["video"]))
        part = _detect_part(video.stem)
        if len(group) > 1 and not part:
            manual_entries.append({
                **base,
                "target": None,
                "number": number,
                "status": "review",
                "reason": "ambiguous_duplicate_number",
                "sidecars": [],
            })
            continue

        actor = _sanitize_component(item["actor"], unknown_actor, 80)
        title = _sanitize_component(_strip_number(item["title"], number), number, 95)
        version = _detect_version(video.stem)
        target_dir = root / actor / _sanitize_component(number, number, 40)
        stem = _sanitize_component(f"[{number}] {title}{version}{part}", number, 150)
        target = target_dir / f"{stem}{video.suffix.lower()}"
        if len(str(target)) >= max_path:
            overflow = len(str(target)) - max_path + 1
            title = _sanitize_component(title, number, max(20, 95 - overflow))
            stem = _sanitize_component(f"[{number}] {title}{version}{part}", number, 150)
            target = target_dir / f"{stem}{video.suffix.lower()}"
        if not _is_under(target, root):
            raise MigrationError("target_outside_root")
        actions = _build_sidecar_actions(video, target, by_dir[video.parent], number, claimed_sidecars)
        source_kind = "nfo" if item["nfo_path"] else ("openaver_db" if item["row"] else "path")
        entries.append({
            **base,
            "target": str(target),
            "number": number,
            "title": title,
            "actor": actor,
            "metadata_source": source_kind,
            "part_suffix": part,
            "version_suffix": version,
            "sidecars": actions,
            "status": "planned",
        })

    conflicts = _mark_conflicts(entries, manual_entries, max_path)
    summary = {
        "automatic": len(entries),
        "ready": sum(entry["status"] == "planned" for entry in entries),
        "conflicts": len(conflicts),
        "manual_total": len(manual_entries),
        "manual_move_ready": sum(entry["status"] == "planned_manual_move" for entry in manual_entries),
        "review": sum(entry["status"] == "review" for entry in manual_entries),
    }
    manifest = {
        "schema_version": 1,
        "run_id": inventory["run_id"],
        "created_at": _utc_now(),
        "root": str(root),
        "inventory_path": str(run_dir / "inventory.json"),
        "baseline": inventory["baseline"],
        "template": r"{actor}\{number}\[{number}] {title}{version}{part}{ext}",
        "entries": entries,
        "manual_entries": manual_entries,
        "conflicts": conflicts,
        "summary": summary,
    }
    manifest_path = run_dir / "manifest.json"
    _atomic_json(manifest_path, manifest)
    _write_preview(run_dir / "preview.csv", entries, manual_entries)
    return {"manifest": str(manifest_path), **summary}


def _build_sidecar_actions(
    video: Path,
    target: Path,
    videos_in_dir: list[Path],
    number: str | None,
    claimed: set[str],
) -> list[dict[str, Any]]:
    actions = []
    for source in _sidecars_for_video(video, videos_in_dir, number):
        key = os.path.normcase(str(source.resolve()))
        if key in claimed:
            continue
        claimed.add(key)
        destination = _sidecar_target(source, video, target)
        actions.append({
            "source": str(source),
            "target": str(destination),
            "size": source.stat().st_size,
            "sha256": _sha256(source),
        })
    return actions


def _mark_conflicts(
    entries: list[dict[str, Any]], manual_entries: list[dict[str, Any]], max_path: int,
) -> list[dict[str, Any]]:
    movable = [*entries, *(entry for entry in manual_entries if entry.get("target"))]
    video_counts = Counter(os.path.normcase(entry["target"]) for entry in movable)
    sidecar_counts = Counter(
        os.path.normcase(action["target"]) for entry in movable for action in entry.get("sidecars", [])
    )
    conflicts = []
    for entry in movable:
        reasons = []
        source, target = Path(entry["source"]), Path(entry["target"])
        if video_counts[os.path.normcase(entry["target"])] > 1:
            reasons.append("duplicate_target_in_manifest")
        if target.exists() and source.resolve() != target.resolve():
            reasons.append("target_already_exists")
        if len(str(target)) >= max_path:
            reasons.append("target_path_too_long")
        for action in entry.get("sidecars", []):
            destination = Path(action["target"])
            if sidecar_counts[os.path.normcase(action["target"])] > 1:
                reasons.append(f"duplicate_sidecar_target:{destination.name}")
            if destination.exists() and Path(action["source"]).resolve() != destination.resolve():
                reasons.append(f"sidecar_target_exists:{destination.name}")
        if reasons:
            entry["status"] = "conflict" if entry in entries else "review"
            entry["conflicts"] = reasons
            conflicts.append({"source": entry["source"], "target": entry["target"], "reasons": reasons})
    return conflicts


def _write_preview(path: Path, entries: list[dict[str, Any]], manual_entries: list[dict[str, Any]]) -> None:
    fields = ["status", "number", "actor", "title", "part_suffix", "source", "target", "reason"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for entry in [*entries, *manual_entries]:
            writer.writerow({field: entry.get(field, "") for field in fields})


def _load_manifest(value: str | Path) -> tuple[Path, dict[str, Any], Path, Path]:
    manifest_path = _resolve_path(value, must_exist=True)
    manifest = _read_json(manifest_path)
    root = _resolve_path(manifest.get("root", ""), must_exist=True, directory=True)
    run_dir = manifest_path.parent
    expected = root / ".openaver-migration" / str(manifest.get("run_id", "")) / "manifest.json"
    if manifest_path != expected.resolve(strict=False):
        raise MigrationError("invalid_manifest_path")
    return manifest_path, manifest, root, run_dir


def _load_journal(run_dir: Path, run_id: str) -> dict[str, Any]:
    path = run_dir / "journal.json"
    return _read_json(path) if path.exists() else {
        "schema_version": 1, "run_id": run_id, "started_at": _utc_now(), "operations": [],
    }


def _save_journal(run_dir: Path, journal: dict[str, Any]) -> None:
    _atomic_json(run_dir / "journal.json", journal)


def _preflight(entry: dict[str, Any], root: Path) -> list[str]:
    errors = []
    source = _resolve_path(entry["source"])
    target = _resolve_path(entry["target"])
    if not source.exists():
        errors.append("source_missing")
    elif source.stat().st_size != entry["size"]:
        errors.append("source_size_changed")
    elif source.stat().st_mtime_ns != entry["mtime_ns"]:
        errors.append("source_mtime_changed")
    elif _quick_fingerprint(source) != entry["quick_fingerprint"]:
        errors.append("source_fingerprint_changed")
    if not _is_under(source, root) or not _is_under(target, root):
        errors.append("path_outside_root")
    if target.exists() and source.resolve() != target.resolve():
        errors.append("target_exists")
    for action in entry.get("sidecars", []):
        sidecar_source = _resolve_path(action["source"])
        sidecar_target = _resolve_path(action["target"])
        if not _is_under(sidecar_source, root) or not _is_under(sidecar_target, root):
            errors.append("sidecar_path_outside_root")
        elif not sidecar_source.exists():
            errors.append(f"sidecar_source_missing:{sidecar_source.name}")
        elif sidecar_source.stat().st_size != action["size"] or _sha256(sidecar_source) != action["sha256"]:
            errors.append(f"sidecar_source_changed:{sidecar_source.name}")
        if sidecar_source.exists() and sidecar_target.exists() and sidecar_source.resolve() != sidecar_target.resolve():
            errors.append(f"sidecar_target_exists:{sidecar_target.name}")
    return errors


def _move_no_overwrite(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        return
    if target.exists():
        raise MigrationConflictError("target_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _execute_entry(entry: dict[str, Any], journal: dict[str, Any], run_dir: Path) -> None:
    completed: list[tuple[Path, Path]] = []
    try:
        for action in entry.get("sidecars", []):
            source, target = Path(action["source"]), Path(action["target"])
            if source.exists() and source.resolve() != target.resolve():
                _move_no_overwrite(source, target)
                completed.append((source, target))
                journal["operations"].append({
                    "entry_id": entry["id"], "kind": "sidecar", "source": str(source),
                    "target": str(target), "at": _utc_now(),
                })
                _save_journal(run_dir, journal)
        source, target = Path(entry["source"]), Path(entry["target"])
        if source.resolve() != target.resolve():
            _move_no_overwrite(source, target)
            completed.append((source, target))
            journal["operations"].append({
                "entry_id": entry["id"], "kind": "video", "source": str(source),
                "target": str(target), "at": _utc_now(),
            })
            _save_journal(run_dir, journal)
    except Exception:
        for source, target in reversed(completed):
            if target.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
        journal["operations"] = [op for op in journal["operations"] if op["entry_id"] != entry["id"]]
        _save_journal(run_dir, journal)
        raise


def apply_manifest(manifest_value: str, confirm_run: str, batch_size: int = 20) -> dict[str, Any]:
    _manifest_path, manifest, root, run_dir = _load_manifest(manifest_value)
    if confirm_run != manifest["run_id"]:
        raise MigrationError("confirmation_mismatch")
    if manifest.get("conflicts"):
        raise MigrationConflictError("manifest_has_conflicts")
    if not 1 <= batch_size <= 20:
        raise MigrationError("invalid_batch_size")
    journal = _load_journal(run_dir, manifest["run_id"])
    completed_ids = {op["entry_id"] for op in journal["operations"] if op["kind"] == "video"}
    pending = [entry for entry in manifest["entries"] if entry["status"] == "planned" and entry["id"] not in completed_ids]
    pending += [
        entry for entry in manifest["manual_entries"]
        if entry["status"] == "planned_manual_move" and entry["id"] not in completed_ids
    ]
    selected = pending[:batch_size]
    errors = {entry["id"]: values for entry in selected if (values := _preflight(entry, root))}
    if errors:
        _atomic_json(run_dir / "apply-preflight-errors.json", errors)
        raise MigrationConflictError("preflight_failed")
    for entry in selected:
        _execute_entry(entry, journal, run_dir)
    journal["last_batch_at"] = _utc_now()
    _save_journal(run_dir, journal)
    return {
        "moved_this_batch": len(selected),
        "remaining": len(pending) - len(selected),
        "journal": str(run_dir / "journal.json"),
    }


def verify_manifest(manifest_value: str) -> dict[str, Any]:
    _manifest_path, manifest, root, run_dir = _load_manifest(manifest_value)
    inventory = _read_json(run_dir / "inventory.json")
    journal = _load_journal(run_dir, manifest["run_id"])
    moved = {op["entry_id"]: Path(op["target"]) for op in journal["operations"] if op["kind"] == "video"}
    sidecars = {
        (op["entry_id"], os.path.normcase(op["source"])): Path(op["target"])
        for op in journal["operations"] if op["kind"] == "sidecar"
    }
    problems = []
    for entry in [*manifest["entries"], *manifest["manual_entries"]]:
        expected = moved.get(entry["id"], Path(entry["source"]))
        if not expected.exists():
            problems.append({"entry_id": entry["id"], "problem": "video_missing", "expected": str(expected)})
        elif expected.stat().st_size != entry["size"] or _quick_fingerprint(expected) != entry["quick_fingerprint"]:
            problems.append({"entry_id": entry["id"], "problem": "video_changed", "expected": str(expected)})
        for action in entry.get("sidecars", []):
            key = (entry["id"], os.path.normcase(action["source"]))
            expected_sidecar = sidecars.get(key, Path(action["source"]))
            if not expected_sidecar.exists():
                problems.append({"entry_id": entry["id"], "problem": "sidecar_missing"})
            elif expected_sidecar.stat().st_size != action["size"] or _sha256(expected_sidecar) != action["sha256"]:
                problems.append({"entry_id": entry["id"], "problem": "sidecar_changed"})
    current = _all_files(root, _video_extensions(), include_manual=True)
    current_bytes = sum(path.stat().st_size for path in current)
    baseline = inventory["baseline"]
    if len(current) != baseline["video_count"]:
        problems.append({"problem": "video_count_mismatch"})
    if current_bytes != baseline["video_bytes"]:
        problems.append({"problem": "video_bytes_mismatch"})
    result = {
        "success": not problems,
        "moved": len(moved),
        "video_count": len(current),
        "video_bytes": current_bytes,
        "problems": problems,
    }
    _atomic_json(run_dir / "verification.json", result)
    return result


def rollback_manifest(manifest_value: str, confirm_run: str, batch_size: int = 20) -> dict[str, Any]:
    _manifest_path, manifest, root, run_dir = _load_manifest(manifest_value)
    if confirm_run != manifest["run_id"]:
        raise MigrationError("confirmation_mismatch")
    if not 1 <= batch_size <= 20:
        raise MigrationError("invalid_batch_size")
    journal = _load_journal(run_dir, manifest["run_id"])
    entry_order = []
    for operation in journal["operations"]:
        if operation["kind"] == "video" and operation["entry_id"] not in entry_order:
            entry_order.append(operation["entry_id"])
    selected_ids = set(entry_order[-batch_size:])
    selected = [operation for operation in journal["operations"] if operation["entry_id"] in selected_ids]
    for operation in reversed(selected):
        source, target = Path(operation["source"]), Path(operation["target"])
        if not _is_under(source, root) or not _is_under(target, root):
            raise MigrationError("rollback_path_outside_root")
        if not target.exists() or source.exists():
            raise MigrationConflictError("rollback_preflight_failed")
    for operation in reversed(selected):
        source, target = Path(operation["source"]), Path(operation["target"])
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(source))
        journal["operations"].remove(operation)
        _save_journal(run_dir, journal)
    return {
        "rolled_back_entries": len(selected_ids),
        "rolled_back_operations": len(selected),
        "remaining_operations": len(journal["operations"]),
    }
