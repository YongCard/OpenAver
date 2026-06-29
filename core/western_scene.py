"""Helpers for western studio-scene filenames.

OpenAver's classic parser is number-centric.  Western studio scenes usually
arrive as ``studio.yy.mm.dd.performer.title.ext``; this module keeps that
recognition separate so JAV exact-number matching stays strict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_WESTERN_DATE_RE = re.compile(
    r"^(?P<studio>[A-Za-z][A-Za-z0-9_-]*)[._ -]+"
    r"(?P<yy>\d{2})[._ -]+(?P<mm>\d{2})[._ -]+(?P<dd>\d{2})[._ -]+"
    r"(?P<rest>.+)$"
)

_QUALITY_TOKENS = {
    "1080p", "720p", "2160p", "4k", "uhd", "hdrip", "webrip", "webdl",
    "web-dl", "x264", "x265", "h264", "h265", "ktr", "fhd",
}


@dataclass(frozen=True)
class WesternSceneInfo:
    studio: str
    date: str
    performer: str
    title: str
    scene_query: str
    scene_id: str


def _title_case_token(token: str) -> str:
    return token[:1].upper() + token[1:].lower() if token else token


def _display_name(text: str) -> str:
    parts = [p for p in re.split(r"[._\s-]+", text.strip()) if p]
    return " ".join(_title_case_token(p) for p in parts)


def _slug(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", text.upper()).strip("-")
    return value or "UNKNOWN"


def _clean_rest(rest: str) -> list[str]:
    tokens = [t for t in re.split(r"[._\s-]+", rest) if t]
    cleaned: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower in _QUALITY_TOKENS:
            continue
        cleaned.append(token)
    return cleaned


def parse_western_scene_filename(filename: str) -> WesternSceneInfo | None:
    """Parse common western scene filenames.

    Examples:
        bangbus.19.08.28.dylann.vox.mp4
        PublicBang.15.03.13.Lolly.Gartner.1080p-KTR.mp4
    """
    stem = Path(filename).stem
    match = _WESTERN_DATE_RE.match(stem)
    if not match:
        return None

    yy = int(match.group("yy"))
    year = 2000 + yy if yy < 70 else 1900 + yy
    month = int(match.group("mm"))
    day = int(match.group("dd"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None

    tokens = _clean_rest(match.group("rest"))
    if not tokens:
        return None

    if len(tokens) >= 2:
        performer_tokens = tokens[:2]
        title_tokens = tokens[2:]
    else:
        performer_tokens = tokens[:1]
        title_tokens = []

    studio = _display_name(match.group("studio"))
    performer = _display_name(" ".join(performer_tokens))
    title = _display_name(" ".join(title_tokens)) if title_tokens else performer
    date = f"{year:04d}-{month:02d}-{day:02d}"
    scene_query = " ".join(part for part in [studio, date, performer, title] if part)
    scene_id = f"WEST-{_slug(studio)}-{year:04d}{month:02d}{day:02d}-{_slug(performer)}"

    return WesternSceneInfo(
        studio=studio,
        date=date,
        performer=performer,
        title=title,
        scene_query=scene_query,
        scene_id=scene_id,
    )
