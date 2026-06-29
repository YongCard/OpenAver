"""Local video merge helpers backed by FFmpeg."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from core.logger import get_logger
from core.video_extensions import get_video_extensions

logger = get_logger(__name__)

_PART_RE = re.compile(
    r"(?<![A-Za-z0-9])(cd|dvd|part|pt|disc)([1-9])(?![0-9])(?=[-_.\s\[\]()]|$)",
    re.IGNORECASE,
)

SIDECAR_EXTENSIONS = {
    ".nfo", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx",
}
STEM_SIDECAR_SUFFIXES = (
    "-poster", "-fanart", "-cover", "-thumb", "-landscape", "-clearlogo", "-banner",
)
ProgressCallback = Callable[[dict[str, Any]], None]


class MediaMergeError(Exception):
    """Raised when a media merge request is invalid or FFmpeg fails."""

    def __init__(self, code: str, *, log_tail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.log_tail = log_tail


class MediaMergeWarning(RuntimeError):
    """Non-fatal merge warning such as cleanup failure."""


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundled_ffmpeg_candidates() -> list[Path]:
    root = project_root()
    names = ["ffmpeg.exe"] if os.name == "nt" else ["ffmpeg"]
    bases = [
        root / "tools" / "ffmpeg" / "bin",
        root / "tools" / "ffmpeg",
    ]
    return [base / name for base in bases for name in names]


def resolve_ffmpeg() -> dict[str, Any]:
    """Return FFmpeg availability info without running any merge."""
    env_path = os.environ.get("OPENAVER_FFMPEG_PATH", "").strip()
    candidates = [Path(env_path)] if env_path else []
    candidates.extend(bundled_ffmpeg_candidates())

    for candidate in candidates:
        if candidate.is_file():
            return _ffmpeg_info(str(candidate), "env" if candidate == Path(env_path) else "bundled")

    path_hit = shutil.which("ffmpeg")
    if path_hit:
        return _ffmpeg_info(path_hit, "path")

    return {
        "available": False,
        "path": "",
        "source": "missing",
        "version": "",
        "message": "ffmpeg_not_found",
    }


def resolve_ffprobe(ffmpeg_path: str | None = None) -> str | None:
    """Resolve ffprobe next to ffmpeg, or from PATH."""
    if ffmpeg_path:
        ffprobe = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if ffprobe.is_file():
            return str(ffprobe)
    return shutil.which("ffprobe")


def _ffmpeg_info(path: str, source: str) -> dict[str, Any]:
    version = ""
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        version = (result.stdout.splitlines() or [""])[0].strip()
    except Exception as exc:
        logger.warning("FFmpeg version probe failed: %s", exc)
    return {
        "available": True,
        "path": path,
        "source": source,
        "version": version,
        "message": "",
    }


def detect_part_number(path: str | Path) -> int | None:
    match = _PART_RE.search(Path(path).stem)
    return int(match.group(2)) if match else None


def sort_merge_inputs(paths: list[str]) -> list[str]:
    indexed = list(enumerate(paths))

    def key(item: tuple[int, str]) -> tuple[int, int]:
        index, path = item
        part = detect_part_number(path)
        return (part if part is not None else 99, index)

    return [path for _, path in sorted(indexed, key=key)]


def build_default_output_path(paths: list[str]) -> str:
    first = Path(paths[0])
    stem = _PART_RE.sub("", first.stem).rstrip("-_. []()") or first.stem
    return str(first.with_name(f"{stem}-merged{first.suffix}"))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(str(right.resolve(strict=False)))


def _recycle_bin_supported() -> bool:
    return os.name == "nt"


def validate_merge_inputs(paths: list[str], config: dict[str, Any]) -> list[str]:
    video_exts = get_video_extensions(config)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        value = str(raw or "").strip().strip('"')
        if not value:
            continue
        path = Path(value)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        if not path.is_file():
            raise MediaMergeError("input_not_found")
        if path.suffix.lower() not in video_exts:
            raise MediaMergeError("input_not_video")
        seen.add(key)
        cleaned.append(str(path))

    if len(cleaned) < 2:
        raise MediaMergeError("too_few_inputs")
    if len(cleaned) > 9:
        raise MediaMergeError("too_many_inputs")
    return sort_merge_inputs(cleaned)


def _validate_output_path(target: Path, inputs: list[str], *, overwrite: bool) -> None:
    for raw in inputs:
        if _same_path(target, Path(raw)):
            raise MediaMergeError("output_matches_input")
    if target.exists() and not overwrite:
        raise MediaMergeError("output_exists")


def preview_merge(paths: list[str], config: dict[str, Any]) -> dict[str, Any]:
    ordered = validate_merge_inputs(paths, config)
    exts = {Path(path).suffix.lower() for path in ordered}
    return {
        "items": [
            {
                "path": path,
                "filename": Path(path).name,
                "part": detect_part_number(path),
                "size": Path(path).stat().st_size,
            }
            for path in ordered
        ],
        "output_path": build_default_output_path(ordered),
        "copy_mode": True,
        "extension_warning": len(exts) > 1,
        "cleanup_supported": _recycle_bin_supported(),
    }


def _probe_format(path: str, ffprobe_path: str | None) -> dict[str, Any]:
    if not ffprobe_path:
        raise MediaMergeError("ffprobe_not_found")
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,avg_frame_rate",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("ffprobe failed: %s", result.stderr[-1000:])
        raise MediaMergeError("ffprobe_failed")
    try:
        import json

        return json.loads(result.stdout or "{}")
    except Exception as exc:
        raise MediaMergeError("ffprobe_failed") from exc


def _duration_seconds(probe: dict[str, Any]) -> float:
    try:
        return float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _emit(progress_callback: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress_callback is not None:
        progress_callback(event)


def _progress_percent(out_time_ms: int | float, total_duration: int | float) -> int:
    try:
        total = float(total_duration)
        current = float(out_time_ms) / 1_000_000
    except (TypeError, ValueError):
        return 0
    if total <= 0:
        return 0
    return max(0, min(100, int(round((current / total) * 100))))


def parse_ffmpeg_progress_line(line: str, total_duration: float) -> dict[str, Any] | None:
    """Parse one FFmpeg ``-progress`` line into a UI-friendly progress event."""
    key, sep, value = line.strip().partition("=")
    if not sep:
        return None
    if key == "out_time_ms":
        try:
            out_time_ms = int(value)
        except ValueError:
            return None
        return {
            "type": "progress",
            "stage": "merging",
            "percent": _progress_percent(out_time_ms, total_duration),
            "seconds": out_time_ms / 1_000_000,
        }
    if key == "progress" and value == "end":
        return {"type": "progress", "stage": "merging", "percent": 100}
    return None


def _decode_ffmpeg_output(raw: bytes | str) -> str:
    """Decode FFmpeg output without ever failing on Windows console encodings."""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def _log_tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if text else ""


def _total_duration(inputs: list[str], ffprobe_path: str | None) -> float:
    return sum(_duration_seconds(_probe_format(path, ffprobe_path)) for path in inputs)


def _run_ffmpeg_with_progress(
    command: list[str],
    total_duration: float,
    progress_callback: ProgressCallback | None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    stdout_lines: list[str] = []
    if process.stdout is not None:
        for raw in process.stdout:
            line = _decode_ffmpeg_output(raw)
            stdout_lines.append(line)
            event = parse_ffmpeg_progress_line(line, total_duration)
            if event is not None:
                _emit(progress_callback, event)
    returncode = process.wait()
    output = "".join(stdout_lines)
    return subprocess.CompletedProcess(command, returncode, output, output)


def _sidecars_for_video(video: Path) -> list[Path]:
    prefix = video.stem.casefold()
    matches: list[Path] = []
    try:
        children = list(video.parent.iterdir())
    except OSError:
        return matches
    for item in children:
        if not item.is_file() or item == video:
            continue
        if item.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        lowered_stem = item.stem.casefold()
        if lowered_stem == prefix or any(lowered_stem == f"{prefix}{suffix}" for suffix in STEM_SIDECAR_SUFFIXES):
            matches.append(item)
    return sorted(matches)


def cleanup_sidecars_for_inputs(inputs: list[str]) -> list[Path]:
    """Return same-stem sidecars for old split videos; never includes folders."""
    cleanup_sidecars: list[Path] = []
    for video in [Path(path) for path in inputs]:
        cleanup_sidecars.extend(_sidecars_for_video(video))
    cleanup_deduped: list[Path] = []
    seen: set[str] = set()
    for item in cleanup_sidecars:
        key = os.path.normcase(str(item))
        if key not in seen:
            cleanup_deduped.append(item)
            seen.add(key)
    return cleanup_deduped


def _sidecar_cleanup_result(sidecars: list[Path]) -> dict[str, Any]:
    return {
        "cleanup_sidecar_count": len(sidecars),
        "cleanup_sidecars": [str(path) for path in sidecars],
    }


def verify_merge_output(
    inputs: list[str],
    output_path: str,
    *,
    ffprobe_path: str | None = None,
) -> dict[str, Any]:
    output = Path(output_path)
    if not output.is_file() or output.stat().st_size <= 0:
        raise MediaMergeError("output_missing")
    resolved_ffprobe = ffprobe_path or resolve_ffprobe()
    input_probes = [_probe_format(path, resolved_ffprobe) for path in inputs]
    output_probe = _probe_format(str(output), resolved_ffprobe)
    output_streams = output_probe.get("streams") or []
    video_stream = next((s for s in output_streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise MediaMergeError("output_no_video")
    input_duration = sum(_duration_seconds(probe) for probe in input_probes)
    output_duration = _duration_seconds(output_probe)
    tolerance = max(2.0, input_duration * 0.01)
    if input_duration > 0 and abs(output_duration - input_duration) > tolerance:
        raise MediaMergeError("duration_mismatch")
    return {
        "duration": output_duration,
        "expected_duration": input_duration,
        "size": output.stat().st_size,
        "video": {
            "codec_name": video_stream.get("codec_name", ""),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "avg_frame_rate": video_stream.get("avg_frame_rate", ""),
        },
    }


def merge_videos(
    paths: list[str],
    output_path: str | None,
    config: dict[str, Any],
    *,
    overwrite: bool = False,
    cleanup_sources: bool = False,
    cleanup_sidecars: bool | None = None,
    recycle_func=None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    started_at = time.monotonic()
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg["available"]:
        raise MediaMergeError("ffmpeg_not_found")

    _emit(progress_callback, {"type": "stage", "stage": "preparing", "percent": 0})
    ordered = validate_merge_inputs(paths, config)
    target = Path((output_path or "").strip().strip('"') or build_default_output_path(ordered))
    if not target.suffix:
        target = target.with_suffix(Path(ordered[0]).suffix)
    _validate_output_path(target, ordered, overwrite=overwrite)
    target.parent.mkdir(parents=True, exist_ok=True)
    ffprobe_path = resolve_ffprobe(ffmpeg["path"])
    total_duration = _total_duration(ordered, ffprobe_path)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        list_path = Path(handle.name)
        for path in ordered:
            handle.write(f"file '{_escape_concat_path(path)}'\n")

    try:
        command = [
            ffmpeg["path"],
            "-hide_banner",
            "-nostats",
            "-y" if overwrite else "-n",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-progress",
            "pipe:1",
            str(target),
        ]
        _emit(progress_callback, {"type": "stage", "stage": "merging", "percent": 0})
        result = _run_ffmpeg_with_progress(command, total_duration, progress_callback)
        if result.returncode != 0:
            log_tail = _log_tail(result.stderr)
            logger.warning("FFmpeg merge failed: %s", log_tail)
            stderr = result.stderr.lower()
            if "permission denied" in stderr:
                raise MediaMergeError("output_permission_denied", log_tail=log_tail)
            raise MediaMergeError("ffmpeg_failed", log_tail=log_tail)
        _emit(progress_callback, {"type": "progress", "stage": "merging", "percent": 100})
        _emit(progress_callback, {"type": "stage", "stage": "validating", "percent": 100})
        verification = verify_merge_output(
            ordered,
            str(target),
            ffprobe_path=ffprobe_path,
        )
        effective_cleanup_sidecars = cleanup_sources if cleanup_sidecars is None else cleanup_sidecars
        cleanup_sidecars_to_recycle: list[Path] = []
        if effective_cleanup_sidecars:
            _emit(progress_callback, {"type": "stage", "stage": "collecting_sidecars", "percent": 100})
            cleanup_sidecars_to_recycle = cleanup_sidecars_for_inputs(ordered)
        cleanup = {
            "requested": bool(cleanup_sources),
            "supported": _recycle_bin_supported(),
            "moved_to_recycle_bin": 0,
            "moved_sidecars_to_recycle_bin": 0,
            "warning": "",
        }
        if cleanup_sources:
            if not _recycle_bin_supported():
                cleanup["warning"] = "recycle_bin_unavailable"
            else:
                try:
                    _emit(progress_callback, {"type": "stage", "stage": "cleaning", "percent": 100})
                    selected_recycle = recycle_func
                    if selected_recycle is None:
                        from core.duplicate_delete import move_files_to_recycle_bin

                        selected_recycle = move_files_to_recycle_bin
                    cleanup_files = [Path(path) for path in ordered]
                    if effective_cleanup_sidecars:
                        cleanup_files.extend(cleanup_sidecars_to_recycle)
                    selected_recycle(cleanup_files)
                    cleanup["moved_to_recycle_bin"] = len(cleanup_files)
                    cleanup["moved_sidecars_to_recycle_bin"] = len(cleanup_sidecars_to_recycle)
                except Exception as exc:
                    logger.warning("media merge cleanup failed: %s", exc, exc_info=True)
                    cleanup["warning"] = "cleanup_failed"
        _emit(progress_callback, {"type": "stage", "stage": "done", "percent": 100})
        return {
            "success": True,
            "output_path": str(target),
            "input_count": len(ordered),
            "ffmpeg_source": ffmpeg["source"],
            "verification": verification,
            "sidecars": _sidecar_cleanup_result(cleanup_sidecars_to_recycle),
            "cleanup": cleanup,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
        }
    finally:
        try:
            list_path.unlink()
        except OSError:
            pass


def _escape_concat_path(path: str) -> str:
    return str(Path(path)).replace("\\", "/").replace("'", r"'\''")
