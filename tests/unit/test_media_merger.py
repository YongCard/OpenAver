import os
from types import SimpleNamespace

import pytest

from core.media_merger import (
    MediaMergeError,
    build_default_output_path,
    bundled_ffmpeg_candidates,
    cleanup_sidecars_for_inputs,
    detect_part_number,
    merge_videos,
    parse_ffmpeg_progress_line,
    preview_merge,
    sort_merge_inputs,
    validate_merge_inputs,
)


def test_detect_part_number_supports_cd_and_part_tokens():
    assert detect_part_number("ABC-123-CD1.mp4") == 1
    assert detect_part_number("ABC-123-part2.mkv") == 2
    assert detect_part_number("ABC-123.mp4") is None


def test_sort_merge_inputs_uses_part_number_before_original_order(tmp_path):
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1 = tmp_path / "ABC-123-cd1.mp4"

    assert sort_merge_inputs([str(cd2), str(cd1)]) == [str(cd1), str(cd2)]


def test_default_output_strips_part_token(tmp_path):
    first = tmp_path / "ABC-123-CD1.mp4"

    assert build_default_output_path([str(first)]) == str(tmp_path / "ABC-123-merged.mp4")


def test_preview_merge_validates_and_orders_inputs(tmp_path):
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")

    data = preview_merge([str(cd2), str(cd1)], {"scraper": {"video_extensions": [".mp4"]}})

    assert [item["filename"] for item in data["items"]] == ["ABC-123-cd1.mp4", "ABC-123-cd2.mp4"]
    assert data["output_path"].endswith("ABC-123-merged.mp4")
    assert data["copy_mode"] is True


def test_validate_merge_inputs_rejects_non_video(tmp_path):
    one = tmp_path / "ABC-123-cd1.txt"
    two = tmp_path / "ABC-123-cd2.mp4"
    one.write_text("x", encoding="utf-8")
    two.write_bytes(b"x")

    with pytest.raises(MediaMergeError, match="input_not_video"):
        validate_merge_inputs([str(one), str(two)], {"scraper": {"video_extensions": [".mp4"]}})


def test_bundled_ffmpeg_candidates_include_tools_ffmpeg_bin():
    candidates = [str(path).replace("\\", "/") for path in bundled_ffmpeg_candidates()]
    binary = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    assert any(path.endswith(f"tools/ffmpeg/bin/{binary}") for path in candidates)


def test_parse_ffmpeg_progress_line_calculates_percent():
    event = parse_ffmpeg_progress_line("out_time_ms=5000000", 20.0)

    assert event == {"type": "progress", "stage": "merging", "percent": 25, "seconds": 5.0}
    assert parse_ffmpeg_progress_line("progress=end", 20.0)["percent"] == 100


def test_ffmpeg_output_decode_replaces_invalid_bytes(monkeypatch):
    import core.media_merger as media_merger

    events = []

    class FakeProcess:
        stdout = [b"out_time_ms=5000000\n", b"bad-byte-\xa2\n"]

        def wait(self):
            return 1

    monkeypatch.setattr(media_merger.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    result = media_merger._run_ffmpeg_with_progress(["ffmpeg"], 10.0, events.append)

    assert result.returncode == 1
    assert "bad-byte-�" in result.stderr
    assert events[0]["percent"] == 50


def _write_parts(tmp_path):
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")
    return cd1, cd2


def _patch_successful_merge(monkeypatch, events=None):
    events = events if events is not None else []
    import core.media_merger as media_merger

    monkeypatch.setattr(
        media_merger,
        "resolve_ffmpeg",
        lambda: {"available": True, "path": "D:/ffmpeg/bin/ffmpeg.exe", "source": "bundled"},
    )
    monkeypatch.setattr(media_merger, "resolve_ffprobe", lambda _path=None: "D:/ffmpeg/bin/ffprobe.exe")
    monkeypatch.setattr(media_merger, "resolve_ffprobe", lambda _path=None: "D:/ffmpeg/bin/ffprobe.exe")

    def fake_run(_command, capture_output=True, text=True, check=False):
        if "-show_entries" in _command:
            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout='{"format":{"duration":"6","size":"1024"},"streams":[{"codec_type":"video","codec_name":"h264"}]}',
            )
        events.append("ffmpeg")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    class FakeStdout:
        def __iter__(self):
            return iter(["out_time_ms=3000000\n", "progress=end\n"])

    class FakeStderr:
        def read(self):
            return ""

    class FakeProcess:
        stdout = FakeStdout()
        stderr = FakeStderr()

        def wait(self):
            events.append("ffmpeg")
            return 0

    def fake_verify(_inputs, _output_path, *, ffprobe_path=None):
        events.append("verify")
        return {"duration": 12.0, "expected_duration": 12.0, "size": 1024, "video": {"codec_name": "h264"}}

    monkeypatch.setattr(media_merger.subprocess, "run", fake_run)
    monkeypatch.setattr(media_merger.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(media_merger, "verify_merge_output", fake_verify)


def test_merge_success_without_cleanup_does_not_call_recycle(monkeypatch, tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    _patch_successful_merge(monkeypatch)

    def unexpected_recycle(_paths):
        raise AssertionError("recycle should not be called")

    result = merge_videos(
        [str(cd1), str(cd2)],
        str(tmp_path / "ABC-123-merged.mp4"),
        {"scraper": {"video_extensions": [".mp4"]}},
        cleanup_sources=False,
        recycle_func=unexpected_recycle,
    )

    assert result["success"] is True
    assert result["cleanup"]["requested"] is False
    assert result["cleanup"]["moved_to_recycle_bin"] == 0


def test_merge_cleanup_runs_after_ffmpeg_and_verification(monkeypatch, tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    events = []
    _patch_successful_merge(monkeypatch, events)
    import core.media_merger as media_merger

    monkeypatch.setattr(media_merger, "_recycle_bin_supported", lambda: True)

    (tmp_path / "ABC-123-cd1.nfo").write_text("<movie/>", encoding="utf-8")
    (tmp_path / "ABC-123-cd2-poster.jpg").write_bytes(b"jpg")

    def fake_recycle(paths):
        events.append("recycle")
        assert paths == [
            cd1,
            cd2,
            tmp_path / "ABC-123-cd1.nfo",
            tmp_path / "ABC-123-cd2-poster.jpg",
        ]

    result = merge_videos(
        [str(cd2), str(cd1)],
        str(tmp_path / "ABC-123-merged.mp4"),
        {"scraper": {"video_extensions": [".mp4"]}},
        cleanup_sources=True,
        recycle_func=fake_recycle,
    )

    assert events == ["ffmpeg", "verify", "recycle"]
    assert result["cleanup"]["moved_to_recycle_bin"] == 4
    assert result["cleanup"]["moved_sidecars_to_recycle_bin"] == 2
    assert result["sidecars"]["cleanup_sidecar_count"] == 2
    assert result["cleanup"]["warning"] == ""


def test_cleanup_sidecars_for_inputs_keeps_same_stem_files_only(tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    keep = tmp_path / "ABC-123-cd1.nfo"
    poster = tmp_path / "ABC-123-cd2-poster.jpg"
    unrelated = tmp_path / "ABC-123-merged.nfo"
    folder = tmp_path / "extrafanart"
    keep.write_text("nfo", encoding="utf-8")
    poster.write_bytes(b"jpg")
    unrelated.write_text("merged", encoding="utf-8")
    folder.mkdir()
    (folder / "fanart1.jpg").write_bytes(b"jpg")

    sidecars = cleanup_sidecars_for_inputs([str(cd1), str(cd2)])

    assert sidecars == [keep, poster]


def test_merge_rejects_output_matching_input(monkeypatch, tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    import core.media_merger as media_merger

    monkeypatch.setattr(
        media_merger,
        "resolve_ffmpeg",
        lambda: {"available": True, "path": "D:/ffmpeg/bin/ffmpeg.exe", "source": "bundled"},
    )

    with pytest.raises(MediaMergeError, match="output_matches_input"):
        merge_videos(
            [str(cd1), str(cd2)],
            str(cd1),
            {"scraper": {"video_extensions": [".mp4"]}},
            overwrite=True,
            cleanup_sources=True,
            recycle_func=lambda _paths: None,
        )


def test_merge_ffmpeg_failure_does_not_cleanup(monkeypatch, tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    import core.media_merger as media_merger

    monkeypatch.setattr(
        media_merger,
        "resolve_ffmpeg",
        lambda: {"available": True, "path": "D:/ffmpeg/bin/ffmpeg.exe", "source": "bundled"},
    )
    monkeypatch.setattr(media_merger, "resolve_ffprobe", lambda _path=None: "D:/ffmpeg/bin/ffprobe.exe")
    monkeypatch.setattr(
        media_merger.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout='{"format":{"duration":"6","size":"1024"},"streams":[{"codec_type":"video"}]}',
        ),
    )

    class FailedProcess:
        stdout = ["Permission denied\n"]

        def wait(self):
            return 1

    monkeypatch.setattr(media_merger.subprocess, "Popen", lambda *_args, **_kwargs: FailedProcess())

    def unexpected_recycle(_paths):
        raise AssertionError("recycle should not be called")

    with pytest.raises(MediaMergeError, match="output_permission_denied"):
        merge_videos(
            [str(cd1), str(cd2)],
            str(tmp_path / "ABC-123-merged.mp4"),
            {"scraper": {"video_extensions": [".mp4"]}},
            cleanup_sources=True,
            recycle_func=unexpected_recycle,
        )


def test_merge_ffmpeg_failure_carries_log_tail(monkeypatch, tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    import core.media_merger as media_merger

    monkeypatch.setattr(
        media_merger,
        "resolve_ffmpeg",
        lambda: {"available": True, "path": "D:/ffmpeg/bin/ffmpeg.exe", "source": "bundled"},
    )
    monkeypatch.setattr(media_merger, "resolve_ffprobe", lambda _path=None: "D:/ffmpeg/bin/ffprobe.exe")
    monkeypatch.setattr(
        media_merger.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout='{"format":{"duration":"6","size":"1024"},"streams":[{"codec_type":"video"}]}',
        ),
    )

    class FailedProcess:
        stdout = [b"custom ffmpeg failure \xa2\n"]

        def wait(self):
            return 1

    monkeypatch.setattr(media_merger.subprocess, "Popen", lambda *_args, **_kwargs: FailedProcess())

    with pytest.raises(MediaMergeError) as exc_info:
        merge_videos(
            [str(cd1), str(cd2)],
            str(tmp_path / "ABC-123-merged.mp4"),
            {"scraper": {"video_extensions": [".mp4"]}},
        )

    assert str(exc_info.value) == "ffmpeg_failed"
    assert "custom ffmpeg failure" in exc_info.value.log_tail


def test_merge_cleanup_failure_is_non_fatal(monkeypatch, tmp_path):
    cd1, cd2 = _write_parts(tmp_path)
    _patch_successful_merge(monkeypatch)
    import core.media_merger as media_merger

    monkeypatch.setattr(media_merger, "_recycle_bin_supported", lambda: True)

    def failing_recycle(_paths):
        raise RuntimeError("recycle failed")

    result = merge_videos(
        [str(cd1), str(cd2)],
        str(tmp_path / "ABC-123-merged.mp4"),
        {"scraper": {"video_extensions": [".mp4"]}},
        cleanup_sources=True,
        recycle_func=failing_recycle,
    )

    assert result["success"] is True
    assert result["output_path"].endswith("ABC-123-merged.mp4")
    assert result["cleanup"]["warning"] == "cleanup_failed"
