"""JavSP-style inbox organizer for the personal OpenAver branch."""

from __future__ import annotations

import asyncio
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
from core.db_inflow import try_inflow_upsert
from core.empty_folders import configured_gallery_roots
from core.library_categories import category_root_for
from core.library_migration import MANUAL_REVIEW_FOLDER, SIDECAR_EXTENSIONS
from core.organizer import (
    check_subtitle,
    crop_to_poster,
    download_image,
    extract_chinese_title,
    find_subtitle_files,
    format_string,
    generate_nfo,
    sanitize_filename,
    truncate_title,
    truncate_to_chars,
)
from core.path_utils import normalize_path
from core.scraper import search_jav, search_jav_single_source
from core.scrapers.models import clean_actress_names
from core.scrapers.utils import extract_number, has_japanese
from core.translate_service import create_translate_service
from core.video_extensions import get_video_extensions

INBOX_FOLDER = MANUAL_REVIEW_FOLDER
RUN_FOLDER = ".openaver-migration"
MAX_BATCH_SIZE = 10000
DEFAULT_APPLY_BATCH_SIZE = 20
PART_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(cd|dvd|disc|disk|part|pt)[-_.\s]*([1-9A-Z])(?=[-_.\s\[\]()]|$)"
)
MULTIPART_KW_RE = re.compile(r"(cd|dvd|disc|disk|part|pt)([1-9])", re.IGNORECASE)
VR_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(?:(?:vr)|(?:180[-_.\s]?vr)|(?:360[-_.\s]?vr))(?=[-_.\s\[\]()]|$)")
PLACEHOLDER_TITLE_PATTERNS = (
    "标题未定",
    "標題未定",
    "未知标题",
    "未知標題",
    "unknown title",
    "title unknown",
)


class InboxOrganizerError(RuntimeError):
    """Safe, user-correctable inbox organizer failure."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise InboxOrganizerError("manifest_not_found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InboxOrganizerError("invalid_manifest") from exc


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_root(root: str | None, roots: list[Path]) -> Path | None:
    if root is None:
        return None
    try:
        selected = Path(normalize_path(root)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise InboxOrganizerError("invalid_root") from exc
    selected_key = os.path.normcase(str(selected))
    for candidate in roots:
        if selected_key == os.path.normcase(str(candidate)):
            return candidate
    raise InboxOrganizerError("root_not_configured")


def _roots(config: dict[str, Any] | None = None) -> list[Path]:
    roots = configured_gallery_roots(config)
    if not roots:
        raise InboxOrganizerError("gallery_not_configured")
    return roots


def _inbox(root: Path) -> Path:
    return root / INBOX_FOLDER


def get_inbox_roots(*, config: dict[str, Any] | None = None) -> dict[str, Any]:
    selected_config = config or load_config()
    roots = _roots(selected_config)
    items = []
    for root in roots:
        inbox = _inbox(root)
        exists = inbox.exists() and inbox.is_dir()
        video_count = 0
        if exists:
            extensions = {ext.lower() for ext in get_video_extensions(selected_config)}
            try:
                video_count = sum(
                    1 for item in inbox.rglob("*")
                    if item.is_file() and item.suffix.lower() in extensions
                )
            except OSError:
                video_count = 0
        items.append({
            "root": str(root),
            "inbox": str(inbox),
            "exists": exists,
            "video_count": video_count,
        })
    return {"manual_folder": INBOX_FOLDER, "roots": items}


def _video_files(root: Path, config: dict[str, Any]) -> list[Path]:
    inbox = _inbox(root)
    if not inbox.exists() or not inbox.is_dir():
        return []
    extensions = {ext.lower() for ext in get_video_extensions(config)}
    videos = []
    for path in inbox.rglob("*"):
        if path.is_file() and path.suffix.lower() in extensions:
            videos.append(path.resolve(strict=False))
    return sorted(videos, key=lambda item: str(item).casefold())


def _number_from_path(path: Path) -> str:
    extracted = extract_number(path.name) or extract_number(path.stem)
    return extracted or ""


def inventory_inbox(
    *,
    root: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    roots = _roots(selected_config)
    selected_root = _resolve_root(root, roots)
    target_roots = [selected_root] if selected_root else roots
    entries = []
    for library_root in target_roots:
        for video in _video_files(library_root, selected_config):
            number = _number_from_path(video)
            entries.append({
                "id": uuid.uuid4().hex,
                "root": str(library_root),
                "source": str(video),
                "filename": video.name,
                "number": number,
                "status": "identified" if number else "needs_number",
                "reason": "" if number else "number_not_found",
                "size": video.stat().st_size,
                "mtime": video.stat().st_mtime,
            })
    return {
        "manual_folder": INBOX_FOLDER,
        "summary": {
            "file_count": len(entries),
            "identified_count": sum(1 for item in entries if item["number"]),
            "needs_number_count": sum(1 for item in entries if not item["number"]),
        },
        "entries": entries,
    }


def _search_one(number: str, source: str, proxy_url: str = "") -> dict[str, Any] | None:
    if source and source != "auto":
        return search_jav_single_source(number, source, proxy_url=proxy_url)
    return search_jav(number, proxy_url=proxy_url)


def _translate_metadata_title(metadata: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:
    """Translate scraped title using the existing translate configuration.

    Returns ``(translated_title, status)`` where status is a compact log code.
    Translation is best-effort: failures never block organizer search/apply.
    """
    original_title = str(metadata.get("title") or "").strip()
    if not original_title:
        return "", "translation_skipped_no_title"
    if _is_untrusted_title(original_title, metadata.get("number")):
        return "", "translation_skipped_untrusted_title"

    translate_config = config.get("translate", {}) or {}
    if not translate_config.get("enabled", False):
        return "", "translation_disabled"

    locale = config.get("general", {}).get("locale", "zh-TW")
    if locale == "ja":
        return "", "translation_skipped_ja_locale"

    if not has_japanese(original_title):
        return "", "translation_skipped_no_japanese"

    try:
        service = create_translate_service(translate_config, locale)
        context = {
            "actors": metadata.get("actors") or metadata.get("actresses") or [],
            "number": metadata.get("number") or "",
        }
        translated = asyncio.run(service.translate_single(original_title, context))
    except Exception:
        return "", "translation_failed"

    translated = str(translated or "").strip()
    if not translated:
        return "", "translation_empty"
    return translated, "translation_success"


def _enrich_metadata_for_inbox(metadata: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(metadata)
    enriched = _normalize_metadata_actors(enriched)
    translated, status = _translate_metadata_title(enriched, config)
    enriched["_translation_status"] = status
    if translated:
        enriched["translated_title"] = translated
    return enriched


def _normalize_metadata_actors(metadata: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(metadata)
    actors = normalized.get("actors") or normalized.get("actresses") or []
    if actors:
        cleaned = clean_actress_names([str(actor) for actor in actors])
        normalized["actors"] = cleaned
        if "actresses" in normalized:
            normalized["actresses"] = cleaned
    return normalized


def search_inbox(
    entries: list[dict[str, Any]],
    *,
    source: str = "auto",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    proxy_url = selected_config.get("search", {}).get("proxy_url", "")
    results = []
    for entry in entries:
        item = dict(entry)
        number = (item.get("manual_number") or item.get("number") or "").strip()
        if not number:
            item.update({"status": "needs_number", "reason": "number_not_found", "metadata": None})
            results.append(item)
            continue
        try:
            metadata = _search_one(number, source, proxy_url=proxy_url)
        except Exception as exc:
            item.update({"status": "search_failed", "reason": str(exc) or "search_failed", "metadata": None})
            results.append(item)
            continue
        if not metadata:
            item.update({"status": "not_found", "reason": "metadata_not_found", "metadata": None})
            results.append(item)
            continue
        metadata = _enrich_metadata_for_inbox(dict(metadata), selected_config)
        metadata["number"] = metadata.get("number") or number
        effective_source = metadata.get("_source") or metadata.get("source") or source
        item.update({
            "number": metadata["number"],
            "status": "found",
            "reason": "",
            "source_id": effective_source,
            "metadata": metadata,
        })
        results.append(item)
    return {
        "summary": {
            "total": len(results),
            "found_count": sum(1 for item in results if item["status"] == "found"),
            "needs_review_count": sum(1 for item in results if item["status"] != "found"),
        },
        "entries": results,
    }


def _detect_suffixes(filename: str, keywords: list[str]) -> str:
    lower = filename.lower()
    found = []
    for keyword in keywords or []:
        token = str(keyword).strip()
        if token and re.search(re.escape(token.lower()) + r"(?=[^a-z0-9]|$)", lower):
            found.append(token)
    return "".join(dict.fromkeys(found))


def _is_multipart_kw(keyword: str) -> bool:
    stripped = str(keyword or "").strip().lstrip("-_. ")
    return bool(stripped and MULTIPART_KW_RE.fullmatch(stripped))


def _part_tail(filename: str, external_manager: str) -> str:
    match = PART_RE.search(Path(filename).stem)
    return f"-{match.group(1).upper()}{match.group(2)}" if match else ""


def _strip_part_token(stem: str) -> str:
    if not stem:
        return stem
    matches = list(PART_RE.finditer(stem))
    if not matches:
        return stem
    last = matches[-1]
    strip_from = last.start()
    token_end = last.end()
    if strip_from > 0 and stem[strip_from - 1] in "-_. \t([":
        strip_from -= 1
    return stem[:strip_from].rstrip("-_. ") + stem[token_end:]


def _folder_layers(scraper_config: dict[str, Any]) -> list[str]:
    raw_layers = scraper_config.get("folder_layers") or []
    if not raw_layers:
        raw_format = scraper_config.get("folder_format", "{actor}/{num}")
        raw_layers = [raw_format]
    layers: list[str] = []
    for raw_layer in raw_layers:
        for part in re.split(r"[\\/]+", str(raw_layer)):
            clean = part.strip()
            if clean:
                layers.append(clean)
    return layers or ["{actor}", "{num}"]


def _is_placeholder_title(title: str | None) -> bool:
    text = (title or "").strip()
    if not text:
        return True
    lowered = text.casefold()
    return any(pattern.casefold() in lowered for pattern in PLACEHOLDER_TITLE_PATTERNS)


def _normalized_title(value: str | None) -> str:
    return re.sub(r"[\s\[\]【】()（）_-]+", "", (value or "").casefold())


def _is_untrusted_title(title: str | None, number: str | None = None) -> bool:
    if _is_placeholder_title(title):
        return True
    title_norm = _normalized_title(title)
    number_norm = _normalized_title(number)
    return bool(title_norm and number_norm and title_norm == number_norm)


def _nfo_has_placeholder_title(nfo: Path, number: str | None = None) -> bool:
    if not nfo.exists():
        return False
    try:
        root = ET.parse(str(nfo)).getroot()
    except ET.ParseError:
        return False
    for tag in ("title", "originaltitle"):
        elem = root.find(tag)
        if elem is not None and _is_untrusted_title(elem.text, number):
            return True
    return False


def _text(root: ET.Element, tag: str) -> str:
    elem = root.find(tag)
    return (elem.text or "").strip() if elem is not None and elem.text else ""


def _offline_metadata_from_nfo(video: Path, root: Path) -> tuple[dict[str, Any] | None, str]:
    nfo = video.with_suffix(".nfo")
    if not nfo.exists():
        return None, "offline_nfo_missing"
    try:
        xml_root = ET.parse(str(nfo)).getroot()
    except ET.ParseError:
        return None, "offline_nfo_invalid"
    number = _text(xml_root, "num") or _text(xml_root, "id") or _number_from_path(video)
    if not number:
        return None, "number_not_found"
    title = _text(xml_root, "title") or _text(xml_root, "originaltitle") or number
    original_title = _text(xml_root, "originaltitle")
    actors = [
        (_text(actor, "name") if len(actor) else (actor.text or "").strip())
        for actor in xml_root.findall("actor")
    ]
    actors = [actor for actor in actors if actor]
    if not actors:
        try:
            relative = video.relative_to(_inbox(root))
            parts = relative.parts
            if len(parts) >= 3:
                actors = [parts[1]]
            elif len(parts) >= 2:
                actors = [parts[0]]
        except ValueError:
            actors = []
    tags = [elem.text.strip() for elem in xml_root.findall("tag") if elem.text and elem.text.strip()]
    genres = [elem.text.strip() for elem in xml_root.findall("genre") if elem.text and elem.text.strip()]
    metadata = {
        "number": number,
        "title": title,
        "original_title": original_title,
        "actors": actors,
        "tags": tags or genres,
        "date": _text(xml_root, "premiered") or _text(xml_root, "releasedate") or _text(xml_root, "year"),
        "maker": _text(xml_root, "studio"),
        "director": _text(xml_root, "director"),
        "series": _text(xml_root, "set"),
        "label": _text(xml_root, "label"),
        "duration": _text(xml_root, "runtime"),
        "_source": "nfo",
        "_offline": True,
    }
    return _normalize_metadata_actors(metadata), ""


def _format_target(
    source: Path,
    metadata: dict[str, Any],
    root: Path,
    config: dict[str, Any],
) -> tuple[Path, str, str]:
    scraper_config = config.get("scraper", {})
    number = metadata.get("number") or _number_from_path(source)
    actors = metadata.get("actors") or metadata.get("actresses") or []
    original_title = metadata.get("title", "")
    translated_title = metadata.get("translated_title", "")
    extracted_title = extract_chinese_title(source.name, number, actors)
    if _is_untrusted_title(translated_title, number):
        translated_title = ""
    if _is_untrusted_title(original_title, number):
        original_title = ""
    if _is_untrusted_title(extracted_title, number):
        extracted_title = ""
    title = translated_title or original_title or extracted_title or number
    title = truncate_title(title, scraper_config.get("max_title_length", 50))
    external_manager = scraper_config.get("external_manager", "off")
    suffix_keywords = scraper_config.get("suffix_keywords", [])
    detect_keywords = [
        keyword for keyword in suffix_keywords
        if external_manager == "off" or not _is_multipart_kw(keyword)
    ]
    suffix = _detect_suffixes(source.name, detect_keywords)
    vr_tail = "_VR" if VR_RE.search(source.stem) else ""
    part_tail = _part_tail(source.name, external_manager)
    data = {
        "number": number,
        "num": number,
        "title": title,
        "actors": actors,
        "actor": actors[0] if actors else "未知女優",
        "maker": metadata.get("maker", ""),
        "date": metadata.get("date", ""),
        "suffix": suffix,
    }

    layers = _folder_layers(scraper_config)
    max_component = min(scraper_config.get("max_filename_length", 60), 120)
    path_parts = []
    for layer in layers[:3]:
        formatted = format_string(str(layer), data, use_fallback=True)
        clean = truncate_to_chars(sanitize_filename(formatted), max_component)
        if clean:
            path_parts.append(clean)
    target_base = category_root_for(root, "jav", config)
    target_dir = target_base.joinpath(*path_parts) if path_parts else target_base

    filename_template = scraper_config.get("filename_format", "[{num}] {title}{suffix}")
    ext = source.suffix.lower()
    max_filename_chars = min(scraper_config.get("max_filename_length", 60), 120)
    max_chars = max(1, max_filename_chars - len(ext))
    base = format_string(filename_template, data)
    reserve = len(vr_tail) + len(part_tail)
    base = truncate_to_chars(sanitize_filename(base), max(1, max_chars - reserve))
    if part_tail:
        base = _strip_part_token(base)
    stem = truncate_to_chars(base + vr_tail + part_tail, max_chars)
    return target_dir / f"{stem}{ext}", stem, title


def _same_stem_sidecars(video: Path) -> list[Path]:
    stem = video.stem.casefold()
    suffix_stems = {stem, f"{stem}-poster", f"{stem}-fanart", f"{stem}-cover", f"{stem}-thumb"}
    sidecars = []
    try:
        children = list(video.parent.iterdir())
    except OSError:
        return sidecars
    for child in children:
        if not child.is_file() or child == video:
            continue
        if child.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        if child.stem.casefold() in suffix_stems:
            sidecars.append(child)
    return sorted(sidecars)


def _sidecar_target(sidecar: Path, target_video: Path, old_stem: str, new_stem: str) -> Path:
    name = sidecar.name
    if sidecar.stem.casefold() == old_stem.casefold():
        return target_video.with_suffix(sidecar.suffix)
    suffix = name[len(old_stem):] if name.lower().startswith(old_stem.lower()) else sidecar.suffix
    return target_video.with_name(new_stem + suffix)


def _offline_sidecars(video: Path) -> list[Path]:
    sidecars = {item.resolve(strict=False) for item in _same_stem_sidecars(video)}
    try:
        children = list(video.parent.iterdir())
    except OSError:
        return sorted(sidecars)
    for child in children:
        if child == video or not child.is_file():
            continue
        if child.suffix.lower() in SIDECAR_EXTENSIONS:
            sidecars.add(child.resolve(strict=False))
    return sorted(sidecars, key=lambda item: str(item).casefold())


def _offline_sidecar_target(sidecar: Path, target_video: Path, old_stem: str, new_stem: str) -> Path:
    if sidecar.stem.casefold() == old_stem.casefold() or sidecar.name.lower().startswith(old_stem.lower()):
        return _sidecar_target(sidecar, target_video, old_stem, new_stem)
    return target_video.parent / sidecar.name


def _offline_movie_dir(source: Path, root: Path) -> Path | None:
    try:
        relative = source.relative_to(_inbox(root))
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) >= 4:
        return _inbox(root).joinpath(*parts[:3])
    if len(parts) >= 3 and source.parent.name.startswith("["):
        return source.parent
    return None


def plan_inbox(
    entries: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    roots = _roots(selected_config)
    selected_run_id = run_id or f"inbox-organizer-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_root = roots[0] / RUN_FOLDER / selected_run_id
    reserved: set[str] = set()
    planned = []
    for entry in entries:
        source = Path(entry.get("source", "")).resolve(strict=False)
        root = Path(entry.get("root", "")).resolve(strict=False)
        metadata = _normalize_metadata_actors(entry.get("metadata") or {})
        status = "planned"
        reason = ""
        if not metadata:
            status = "skipped"
            reason = entry.get("reason") or "metadata_not_found"
        elif os.path.normcase(str(root)) not in {os.path.normcase(str(item)) for item in roots}:
            status = "skipped"
            reason = "root_not_configured"
        elif not source.exists():
            status = "skipped"
            reason = "source_missing"
        elif not _is_under(source, _inbox(root)):
            status = "skipped"
            reason = "source_not_in_inbox"

        target = None
        stem = ""
        title = metadata.get("title", "")
        sidecars = []
        if status == "planned":
            target, stem, title = _format_target(source, metadata, root, selected_config)
            move_dir = _offline_movie_dir(source, root) if metadata.get("_offline") else None
            if move_dir:
                target = target.parent
            key = os.path.normcase(str(target))
            if target.exists() or key in reserved:
                status = "conflict"
                reason = "target_exists"
            reserved.add(key)
            old_stem = source.stem
            if move_dir:
                sidecars = []
            else:
                sidecar_items = _offline_sidecars(source) if metadata.get("_offline") else _same_stem_sidecars(source)
                for sidecar in sidecar_items:
                    sidecar_target = (
                        _offline_sidecar_target(sidecar, target, old_stem, stem)
                        if metadata.get("_offline")
                        else _sidecar_target(sidecar, target, old_stem, stem)
                    )
                    sidecars.append({
                        "source": str(sidecar),
                        "target": str(sidecar_target),
                        "size": sidecar.stat().st_size,
                    })

        planned.append({
            "id": entry.get("id") or uuid.uuid4().hex,
            "status": status,
            "reason": reason,
            "root": str(root),
            "source": str(source),
            "target": str(target) if target else "",
            "number": metadata.get("number") or entry.get("number") or _number_from_path(source),
            "title": title,
            "metadata": metadata,
            "sidecars": sidecars,
            "move_dir": str(move_dir) if status == "planned" and move_dir else "",
            "size": source.stat().st_size if source.exists() else 0,
        })
    manifest = {
        "run_id": selected_run_id,
        "created_at": _utc_now(),
        "manual_folder": INBOX_FOLDER,
        "entries": planned,
        "journal": str(run_root / "inbox_organizer_journal.json"),
    }
    manifest_path = run_root / "inbox_organizer_manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "run_id": selected_run_id,
        "manifest": str(manifest_path),
        "summary": {
            "total": len(planned),
            "planned_count": sum(1 for item in planned if item["status"] == "planned"),
            "conflict_count": sum(1 for item in planned if item["status"] == "conflict"),
            "skipped_count": sum(1 for item in planned if item["status"] == "skipped"),
        },
        "entries": planned,
    }


def _load_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"operations": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"operations": []}


def _move_no_overwrite(source: Path, target: Path) -> None:
    if target.exists():
        raise InboxOrganizerError("target_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _write_new_sidecars(entry: dict[str, Any], target: Path, stem: str, config: dict[str, Any]) -> dict[str, str]:
    metadata = _normalize_metadata_actors(entry.get("metadata") or {})
    if metadata.get("_offline"):
        return {}
    final_title = entry.get("title") or metadata.get("translated_title") or metadata.get("title") or ""
    number = entry.get("number") or metadata.get("number") or ""
    scraper_config = config.get("scraper", {})
    external_manager = scraper_config.get("external_manager", "off")
    result = {}
    cover_url = metadata.get("cover") or metadata.get("img") or ""
    cover = target.with_suffix(".jpg")
    if cover_url and not cover.exists() and download_image(cover_url, str(cover)):
        result["cover_path"] = str(cover)
        if external_manager != "off":
            fanart = target.with_name(stem + "-fanart.jpg")
            poster = target.with_name(stem + "-poster.jpg")
            if not fanart.exists():
                shutil.copy2(str(cover), str(fanart))
                result["fanart_path"] = str(fanart)
            if not poster.exists() and crop_to_poster(str(cover), str(poster)):
                result["poster_path"] = str(poster)

    nfo = target.with_suffix(".nfo")
    nfo_existed = nfo.exists()
    should_write_nfo = not nfo_existed or _nfo_has_placeholder_title(nfo, number)
    if should_write_nfo:
        has_subtitle = check_subtitle(target.name) or bool(find_subtitle_files(str(target)))
        if generate_nfo(
            number=number,
            title=final_title,
            original_title=metadata.get("title", ""),
            actors=metadata.get("actors") or [],
            tags=metadata.get("tags") or [],
            user_tags=metadata.get("user_tags") or [],
            date=metadata.get("date", ""),
            maker=metadata.get("maker", ""),
            url=metadata.get("url", ""),
            has_subtitle=has_subtitle,
            has_vr=bool(VR_RE.search(target.stem)),
            output_path=str(nfo),
            has_poster=bool(result.get("poster_path")),
            has_fanart=bool(result.get("fanart_path")),
            director=metadata.get("director", ""),
            duration=metadata.get("duration"),
            series=metadata.get("series", ""),
            label=metadata.get("label", ""),
            summary=metadata.get("_summary", ""),
            rating=metadata.get("_rating"),
            external_manager=external_manager,
        ):
            result["nfo_rewritten" if nfo_existed else "nfo_path"] = str(nfo)
    return result


def _metadata_for_db(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = _normalize_metadata_actors(entry.get("metadata") or {})
    final_title = entry.get("title") or metadata.get("translated_title") or metadata.get("title") or ""
    if final_title:
        metadata["title"] = final_title
    if entry.get("number"):
        metadata["number"] = entry["number"]
    return metadata


def apply_inbox_manifest(
    manifest: str | Path,
    *,
    confirm: bool,
    batch_size: int = DEFAULT_APPLY_BATCH_SIZE,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise InboxOrganizerError("confirmation_required")
    selected_config = config or load_config()
    manifest_path = Path(manifest)
    data = _read_json(manifest_path)
    journal_path = Path(data.get("journal") or manifest_path.with_name("inbox_organizer_journal.json"))
    journal = _load_journal(journal_path)
    operations = journal.setdefault("operations", [])
    limit = max(1, int(batch_size or MAX_BATCH_SIZE))
    selected = [entry for entry in data.get("entries", []) if entry.get("status") == "planned"][:limit]
    moved = 0
    skipped = []
    for entry in selected:
        source = Path(entry["source"])
        target = Path(entry["target"])
        move_dir = Path(entry["move_dir"]) if entry.get("move_dir") else None
        move_source = move_dir or source
        if not source.exists() or not move_source.exists():
            entry["status"] = "skipped"
            skipped.append({"id": entry["id"], "reason": "source_missing"})
            continue
        if target.exists():
            entry["status"] = "conflict"
            skipped.append({"id": entry["id"], "reason": "target_exists"})
            continue
        moved_for_entry = []
        try:
            if move_dir:
                _move_no_overwrite(move_source, target)
                moved_for_entry.append((move_source, target, "directory"))
                generated = {}
                target_for_db = target / source.relative_to(move_source)
            else:
                for sidecar in entry.get("sidecars", []):
                    side_source = Path(sidecar["source"])
                    side_target = Path(sidecar["target"])
                    if side_source.exists():
                        _move_no_overwrite(side_source, side_target)
                        moved_for_entry.append((side_source, side_target, "sidecar"))
                _move_no_overwrite(source, target)
                moved_for_entry.append((source, target, "video"))
                target_for_db = target
                stem = target.stem
                generated = _write_new_sidecars(entry, target, stem, selected_config)
        except Exception as exc:
            for original, moved_path, _kind in reversed(moved_for_entry):
                if moved_path.exists() and not original.exists():
                    shutil.move(str(moved_path), str(original))
            entry["status"] = "skipped"
            skipped.append({"id": entry["id"], "reason": str(exc) or exc.__class__.__name__})
            continue
        for original, moved_path, kind in moved_for_entry:
            operations.append({
                "entry_id": entry["id"],
                "kind": kind,
                "source": str(original),
                "target": str(moved_path),
                "timestamp": _utc_now(),
            })
        for key, path in generated.items():
            operations.append({
                "entry_id": entry["id"],
                "kind": "generated",
                "role": key,
                "source": "",
                "target": path,
                "timestamp": _utc_now(),
            })
        entry["status"] = "moved"
        entry["moved_at"] = _utc_now()
        entry["target_video"] = str(target_for_db)
        entry["db_sync"] = try_inflow_upsert(
            str(target_for_db),
            old_file_path=str(source),
            scraped_metadata=_metadata_for_db(entry),
        )
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


def offline_plan_inbox(
    entries: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_config = config or load_config()
    roots = _roots(selected_config)
    prepared = []
    for entry in entries:
        item = dict(entry)
        source = Path(item.get("source", "")).resolve(strict=False)
        root = Path(item.get("root", "")).resolve(strict=False)
        if os.path.normcase(str(root)) not in {os.path.normcase(str(candidate)) for candidate in roots}:
            item.update({"status": "skipped", "reason": "root_not_configured", "metadata": None})
            prepared.append(item)
            continue
        if not source.exists():
            item.update({"status": "skipped", "reason": "source_missing", "metadata": None})
            prepared.append(item)
            continue
        if not _is_under(source, _inbox(root)):
            item.update({"status": "skipped", "reason": "source_not_in_inbox", "metadata": None})
            prepared.append(item)
            continue
        metadata, reason = _offline_metadata_from_nfo(source, root)
        if not metadata:
            item.update({"status": "needs_rescrape", "reason": reason, "metadata": None})
        else:
            item.update({
                "number": metadata["number"],
                "status": "found",
                "reason": "",
                "source_id": "nfo",
                "metadata": metadata,
            })
        prepared.append(item)

    result = plan_inbox(prepared, run_id=run_id, config=selected_config)
    changed_status = False
    for entry in result.get("entries", []):
        if entry.get("status") == "skipped" and entry.get("reason", "").startswith("offline_"):
            entry["status"] = "needs_rescrape"
            changed_status = True
    if changed_status and result.get("manifest"):
        data = _read_json(Path(result["manifest"]))
        by_id = {str(item.get("id")): item for item in result.get("entries", [])}
        for item in data.get("entries", []):
            replacement = by_id.get(str(item.get("id")))
            if replacement:
                item["status"] = replacement.get("status", item.get("status"))
                item["reason"] = replacement.get("reason", item.get("reason"))
        _write_json(Path(result["manifest"]), data)
    result["summary"] = {
        **(result.get("summary") or {}),
        "offline_ready_count": sum(1 for item in result.get("entries", []) if item.get("status") == "planned"),
        "needs_rescrape_count": sum(
            1 for item in result.get("entries", [])
            if item.get("status") in {"needs_rescrape", "skipped"} and item.get("reason") in {
                "offline_nfo_missing",
                "offline_nfo_invalid",
                "number_not_found",
                "metadata_not_found",
            }
        ),
    }
    return result


def rollback_inbox_manifest(
    manifest: str | Path,
    *,
    confirm: bool,
    batch_size: int = MAX_BATCH_SIZE,
) -> dict[str, Any]:
    if not confirm:
        raise InboxOrganizerError("confirmation_required")
    manifest_path = Path(manifest)
    data = _read_json(manifest_path)
    journal_path = Path(data.get("journal") or manifest_path.with_name("inbox_organizer_journal.json"))
    journal = _load_journal(journal_path)
    operations = journal.get("operations", [])
    selected = operations[-max(1, int(batch_size or MAX_BATCH_SIZE)):]
    rolled_back = 0
    for op in reversed(selected):
        target = Path(op["target"])
        source = Path(op["source"]) if op.get("source") else None
        if op.get("kind") == "generated":
            if target.exists():
                target.unlink()
                rolled_back += 1
        elif source and target.exists() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(source))
            rolled_back += 1
    if selected:
        journal["operations"] = operations[:-len(selected)]
    _write_json(journal_path, journal)
    return {
        "success": True,
        "rolled_back_operations": rolled_back,
        "remaining_operations": len(journal.get("operations", [])),
    }
