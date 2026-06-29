from pathlib import Path

from web.routers import media_merge as media_merge_router


def test_ffmpeg_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        media_merge_router,
        "resolve_ffmpeg",
        lambda: {"available": True, "path": "D:/ffmpeg.exe", "source": "bundled", "version": "ffmpeg version test"},
    )
    monkeypatch.setattr(
        media_merge_router,
        "load_config",
        lambda: {"media_merge": {"cleanup_sources_default": True}},
    )

    response = client.get("/api/media-merge/ffmpeg")

    assert response.status_code == 200
    assert response.json()["source"] == "bundled"
    assert response.json()["cleanup_sources_default"] is True
    assert "cleanup_supported" in response.json()


def test_preview_endpoint_orders_cd_parts(client, tmp_path):
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")

    response = client.post(
        "/api/media-merge/preview",
        json={"paths": [str(cd2), str(cd1)]},
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [Path(item["path"]).name for item in items] == ["ABC-123-cd1.mp4", "ABC-123-cd2.mp4"]


def test_run_endpoint_delegates_to_merge_videos(client, monkeypatch, tmp_path):
    captured = {}
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")

    def fake_merge(paths, output_path, config, *, overwrite=False, cleanup_sources=False, cleanup_sidecars=None):
        captured["paths"] = paths
        captured["output_path"] = output_path
        captured["overwrite"] = overwrite
        captured["cleanup_sources"] = cleanup_sources
        captured["cleanup_sidecars"] = cleanup_sidecars
        return {
            "success": True,
            "output_path": output_path,
            "input_count": len(paths),
            "verification": {"duration": 12.0, "size": 1024},
            "sidecars": {"cleanup_sidecar_count": 0},
            "cleanup": {"moved_to_recycle_bin": 0, "warning": ""},
        }

    monkeypatch.setattr(media_merge_router, "merge_videos", fake_merge)
    response = client.post(
        "/api/media-merge/run",
        json={
            "paths": [str(cd1), str(cd2)],
            "output_path": str(tmp_path / "ABC-123-merged.mp4"),
            "overwrite": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["input_count"] == 2
    assert captured["overwrite"] is True
    assert captured["cleanup_sources"] is False
    assert captured["cleanup_sidecars"] is None


def test_run_endpoint_accepts_and_remembers_cleanup_preference(client, monkeypatch, tmp_path):
    captured = {}
    saved = {}
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")

    def fake_merge(paths, output_path, config, *, overwrite=False, cleanup_sources=False, cleanup_sidecars=None):
        captured["cleanup_sources"] = cleanup_sources
        captured["cleanup_sidecars"] = cleanup_sidecars
        return {
            "success": True,
            "output_path": output_path,
            "input_count": len(paths),
            "verification": {"duration": 12.0, "size": 1024},
            "sidecars": {"cleanup_sidecar_count": 2},
            "cleanup": {"moved_to_recycle_bin": 2, "warning": ""},
        }

    def fake_mutate_config(mutator):
        cfg = {}
        mutator(cfg)
        saved.update(cfg)

    monkeypatch.setattr(media_merge_router, "merge_videos", fake_merge)
    monkeypatch.setattr(media_merge_router, "mutate_config", fake_mutate_config)
    response = client.post(
        "/api/media-merge/run",
        json={
            "paths": [str(cd1), str(cd2)],
            "output_path": str(tmp_path / "ABC-123-merged.mp4"),
            "cleanup_sources": True,
            "remember_cleanup": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["cleanup"]["moved_to_recycle_bin"] == 2
    assert captured["cleanup_sources"] is True
    assert saved["media_merge"]["cleanup_sources_default"] is True


def test_run_stream_endpoint_emits_progress_and_done(client, monkeypatch, tmp_path):
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")

    def fake_merge(paths, output_path, config, *, overwrite=False, cleanup_sources=False, cleanup_sidecars=None, progress_callback=None):
        progress_callback({"type": "stage", "stage": "merging", "percent": 0})
        progress_callback({"type": "progress", "stage": "merging", "percent": 50})
        return {
            "success": True,
            "output_path": output_path,
            "input_count": len(paths),
            "verification": {"duration": 12.0, "size": 1024},
            "sidecars": {"cleanup_sidecar_count": 1},
            "cleanup": {"moved_to_recycle_bin": 3, "moved_sidecars_to_recycle_bin": 1, "warning": ""},
        }

    monkeypatch.setattr(media_merge_router, "merge_videos", fake_merge)
    response = client.post(
        "/api/media-merge/run-stream",
        json={
            "paths": [str(cd1), str(cd2)],
            "output_path": str(tmp_path / "ABC-123-merged.mp4"),
            "cleanup_sources": True,
        },
    )

    assert response.status_code == 200
    body = response.text
    assert '"type": "stage"' in body
    assert '"type": "progress"' in body
    assert '"type": "done"' in body
    assert '"moved_sidecars_to_recycle_bin": 1' in body


def test_run_stream_endpoint_emits_error_log_tail(client, monkeypatch, tmp_path):
    cd1 = tmp_path / "ABC-123-cd1.mp4"
    cd2 = tmp_path / "ABC-123-cd2.mp4"
    cd1.write_bytes(b"part1")
    cd2.write_bytes(b"part2")

    def fake_merge(*_args, **_kwargs):
        from core.media_merger import MediaMergeError

        raise MediaMergeError("ffmpeg_failed", log_tail="ffmpeg says no")

    monkeypatch.setattr(media_merge_router, "merge_videos", fake_merge)
    response = client.post(
        "/api/media-merge/run-stream",
        json={
            "paths": [str(cd1), str(cd2)],
            "output_path": str(tmp_path / "ABC-123-merged.mp4"),
        },
    )

    assert response.status_code == 200
    assert '"type": "error"' in response.text
    assert '"code": "ffmpeg_failed"' in response.text
    assert '"log_tail": "ffmpeg says no"' in response.text


def test_media_merge_page_renders(client):
    response = client.get("/media-merge")

    assert response.status_code == 200
    assert "media-merge/main.js" in response.text
