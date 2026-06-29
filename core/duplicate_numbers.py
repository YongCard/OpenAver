"""Read-only duplicate number detector for the local OpenAver library."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.database import VideoRepository
from core.path_utils import uri_to_fs_path

PART_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(cd|dvd|disc|disk|part|pt)[-_.\s]*([1-9A-Z])(?=[-_.\s\[\]()]|$)"
)
VARIANT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("subtitle", re.compile(r"(?i)(?<![A-Za-z0-9])(sub|subs|subtitle|字幕|中字|中文)(?=[-_.\s\[\]()]|$)")),
    ("uncensored", re.compile(r"(?i)(?<![A-Za-z0-9])(uncensored|uncensor|無碼|无码|uc|u)(?=[-_.\s\[\]()]|$)")),
    ("vr", re.compile(r"(?i)(?<![A-Za-z0-9])vr(?=[-_.\s\[\]()]|$)")),
    ("4k", re.compile(r"(?i)(?<![A-Za-z0-9])(4k|2160p|uhd)(?=[-_.\s\[\]()]|$)")),
    ("1080p", re.compile(r"(?i)(?<![A-Za-z0-9])(1080p|fhd)(?=[-_.\s\[\]()]|$)")),
    ("720p", re.compile(r"(?i)(?<![A-Za-z0-9])720p(?=[-_.\s\[\]()]|$)")),
]


@dataclass(frozen=True)
class DuplicateVideo:
    """Minimal video fields used by duplicate detection."""

    id: int | None
    number: str
    path: str
    title: str
    actresses: list[str]
    maker: str
    size_bytes: int
    mtime: float


def _canonical_number(number: str | None) -> str:
    return (number or "").strip().upper()


def _display_number(items: list[DuplicateVideo]) -> str:
    for item in items:
        if item.number.strip():
            return item.number.strip()
    return ""


def _path_stem(path_value: str) -> str:
    try:
        fs_path = uri_to_fs_path(path_value)
    except Exception:
        fs_path = path_value
    return Path(fs_path).stem


def _folder_path(path_value: str) -> str:
    try:
        fs_path = uri_to_fs_path(path_value)
    except Exception:
        fs_path = path_value
    return str(Path(fs_path).parent)


def _folder_key(path_value: str) -> str:
    return _folder_path(path_value).casefold()


def detect_part_label(path_value: str) -> str:
    """Return a normalized multipart label such as CD1 or Part2."""
    stem = _path_stem(path_value)
    matches = list(PART_RE.finditer(stem))
    if matches:
        prefix, value = matches[-1].groups()
        value = {"A": "1", "B": "2"}.get(value.upper(), value.upper())
        return f"CD{value}" if prefix.lower() in {"cd", "dvd", "disc", "disk"} else f"Part{value}"
    tail = re.search(r"(?i)(?:^|[-_.\s])([AB])$", stem)
    if tail:
        return "CD1" if tail.group(1).upper() == "A" else "CD2"
    return ""


def detect_variant_tags(path_value: str) -> list[str]:
    stem = _path_stem(path_value)
    return [name for name, pattern in VARIANT_PATTERNS if pattern.search(stem)]


def _path_exists(path_value: str) -> bool:
    try:
        fs_path = uri_to_fs_path(path_value)
    except Exception:
        fs_path = path_value
    try:
        return Path(fs_path).exists()
    except OSError:
        return False


def _video_to_duplicate_video(video: Any) -> DuplicateVideo:
    return DuplicateVideo(
        id=getattr(video, "id", None),
        number=getattr(video, "number", "") or "",
        path=getattr(video, "path", "") or "",
        title=getattr(video, "title", "") or "",
        actresses=list(getattr(video, "actresses", []) or []),
        maker=getattr(video, "maker", "") or "",
        size_bytes=int(getattr(video, "size_bytes", 0) or 0),
        mtime=float(getattr(video, "mtime", 0) or 0),
    )


def _classify_group(items: list[DuplicateVideo]) -> tuple[str, str]:
    labels = [detect_part_label(item.path) for item in items]
    counts = Counter(labels)
    if all(labels) and all(count == 1 for count in counts.values()):
        return "multipart", "complementary_multipart"
    if counts.get("", 0) >= 2:
        return "duplicate", "multiple_unlabeled_files"
    repeated_parts = sorted(label for label, count in counts.items() if label and count > 1)
    if repeated_parts:
        return "duplicate", "duplicate_part:" + ",".join(repeated_parts)
    return "duplicate", "mixed_unlabeled_and_multipart"


def _item_payload(item: DuplicateVideo, *, include_missing_paths: bool) -> dict[str, Any]:
    payload = {
        "id": item.id,
        "path": item.path,
        "folder_path": _folder_path(item.path),
        "folder_key": _folder_key(item.path),
        "title": item.title,
        "actresses": item.actresses,
        "maker": item.maker,
        "size_bytes": item.size_bytes,
        "mtime": item.mtime,
        "part_label": detect_part_label(item.path),
        "variant_tags": detect_variant_tags(item.path),
        "show_open_folder": False,
        "delete_allowed": False,
    }
    if include_missing_paths:
        payload["exists"] = _path_exists(item.path)
    return payload


def find_duplicate_numbers(
    *,
    include_multipart: bool = False,
    include_missing_paths: bool = True,
    limit: int = 500,
    repo: VideoRepository | None = None,
) -> dict[str, Any]:
    """Find number groups that map to more than one local video.

    The detector is intentionally read-only. It reports suspicious groups but
    never modifies the database or filesystem.
    """

    if limit < 1:
        limit = 1
    if limit > 5000:
        limit = 5000

    selected_repo = repo or VideoRepository()
    videos = [_video_to_duplicate_video(video) for video in selected_repo.get_all()]
    groups_by_number: dict[str, list[DuplicateVideo]] = defaultdict(list)
    total_numbered_videos = 0
    for video in videos:
        canonical = _canonical_number(video.number)
        if not canonical:
            continue
        total_numbered_videos += 1
        groups_by_number[canonical].append(video)

    groups: list[dict[str, Any]] = []
    duplicate_group_count = 0
    multipart_group_count = 0
    duplicate_file_count = 0
    missing_path_count = 0
    hidden_multipart_count = 0

    for canonical, items in sorted(groups_by_number.items()):
        if len(items) < 2:
            continue
        classification, reason = _classify_group(items)
        item_payloads = [_item_payload(item, include_missing_paths=include_missing_paths) for item in items]
        seen_folders: set[str] = set()
        for payload in item_payloads:
            folder_key = payload["folder_key"]
            payload["show_open_folder"] = folder_key not in seen_folders
            seen_folders.add(folder_key)
            payload["delete_allowed"] = (
                classification != "multipart"
                and payload.get("exists", True) is not False
            )
        if include_missing_paths:
            missing_path_count += sum(1 for item in item_payloads if not item.get("exists", True))
        if classification == "multipart":
            multipart_group_count += 1
            if not include_multipart:
                hidden_multipart_count += 1
                continue
        else:
            duplicate_group_count += 1
            duplicate_file_count += len(items)

        if len(groups) >= limit:
            continue

        variant_tags = sorted({tag for item in item_payloads for tag in item["variant_tags"]})
        part_labels = sorted({label for item in item_payloads if (label := item["part_label"])})
        groups.append({
            "number": _display_number(items) or canonical,
            "canonical_number": canonical,
            "classification": classification,
            "reason": reason,
            "count": len(items),
            "variant_tags": variant_tags,
            "part_labels": part_labels,
            "items": item_payloads,
        })

    return {
        "summary": {
            "total_videos": len(videos),
            "total_numbered_videos": total_numbered_videos,
            "total_numbers": len(groups_by_number),
            "duplicate_group_count": duplicate_group_count,
            "multipart_group_count": multipart_group_count,
            "hidden_multipart_count": hidden_multipart_count,
            "duplicate_file_count": duplicate_file_count,
            "missing_path_count": missing_path_count,
            "returned_group_count": len(groups),
            "limit": limit,
        },
        "groups": groups,
    }
