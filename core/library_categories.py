"""Library category helpers for parent-root scanner layouts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.path_utils import normalize_path

DEFAULT_LIBRARY_CATEGORIES = {
    "enabled": True,
    "jav": "日韩",
    "western": "欧美",
    "auto_create": True,
}

MOJIBAKE_CATEGORY_REPLACEMENTS = {
    "Å·ÃÀ": "欧美",
}


def library_category_config(config: dict[str, Any] | None) -> dict[str, Any]:
    raw = (config or {}).get("library_categories", {})
    raw = raw if isinstance(raw, dict) else {}
    result = {**DEFAULT_LIBRARY_CATEGORIES, **raw}
    result["enabled"] = bool(result.get("enabled", True))
    result["auto_create"] = bool(result.get("auto_create", True))
    result["jav"] = str(result.get("jav") or DEFAULT_LIBRARY_CATEGORIES["jav"]).strip() or DEFAULT_LIBRARY_CATEGORIES["jav"]
    result["western"] = str(result.get("western") or DEFAULT_LIBRARY_CATEGORIES["western"]).strip() or DEFAULT_LIBRARY_CATEGORIES["western"]
    return result


def category_names(config: dict[str, Any] | None) -> set[str]:
    cfg = library_category_config(config)
    names = {cfg["jav"], cfg["western"]}
    names.update(MOJIBAKE_CATEGORY_REPLACEMENTS)
    return {name for name in names if name}


def normalize_category_segment(value: str, config: dict[str, Any] | None = None) -> str:
    selected = (value or "").strip()
    for bad, good in MOJIBAKE_CATEGORY_REPLACEMENTS.items():
        selected = selected.replace(bad, good)
    return selected


def category_kind_for_path(path: str | Path, config: dict[str, Any] | None = None) -> str | None:
    cfg = library_category_config(config)
    jav = cfg["jav"].casefold()
    western = cfg["western"].casefold()
    for part in Path(str(path)).parts:
        normalized = normalize_category_segment(part, config).casefold()
        if normalized == western:
            return "western"
        if normalized == jav:
            return "jav"
    return None


def strip_category_suffix(path: str | Path, config: dict[str, Any] | None = None) -> Path:
    selected = Path(str(path))
    if normalize_category_segment(selected.name, config) in category_names(config):
        return selected.parent
    return selected


def category_root_for(root: str | Path, kind: str, config: dict[str, Any] | None = None) -> Path:
    cfg = library_category_config(config)
    base = strip_category_suffix(root, config)
    name = cfg["western"] if kind == "western" else cfg["jav"]
    return base / name


def matching_gallery_root(path: Path, roots: list[Path], config: dict[str, Any] | None = None) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for root in roots:
        normalized_root = strip_category_suffix(root, config)
        for candidate in (root, normalized_root):
            try:
                path.relative_to(candidate)
                candidates.append((len(candidate.parts), normalized_root))
            except ValueError:
                continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def dedupe_scan_directories(directories: list[str], config: dict[str, Any] | None = None) -> list[str]:
    normalized: list[tuple[str, str]] = []
    for raw in directories or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            path = Path(normalize_path(raw.strip())).expanduser()
        except (OSError, RuntimeError, ValueError):
            path = Path(raw.strip())
        path = strip_category_suffix(path, config)
        key = os.path.normcase(str(path))
        normalized.append((key, str(path)))

    keys = {key for key, _value in normalized}
    result: list[str] = []
    seen: set[str] = set()
    for key, value in normalized:
        if key in seen:
            continue
        path = Path(value)
        parent_key = os.path.normcase(str(path.parent))
        if parent_key in keys:
            continue
        seen.add(key)
        result.append(value)
    return result
