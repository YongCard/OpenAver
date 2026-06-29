from pathlib import Path

import pytest

from core.database import Video
from core.path_utils import to_file_uri
from core.western_organizer import apply_western_manifest, preview_western_organize


def _config(root: Path) -> dict:
    return {
        "gallery": {"directories": [str(root)], "path_mappings": {}},
        "scraper_profiles": {
            "western": {
                "create_folder": True,
                "folder_format": "{studio}/{year}/{date} {title}",
                "filename_format": "[{date}] {title} - {performers}{suffix}",
                "external_manager": "jellyfin",
            }
        },
    }


def _video(path: Path) -> Video:
    return Video(
        path=to_file_uri(str(path)),
        number="WEST-BANGBUS-20190828-DYLANN-VOX",
        title="Stripper With Double D Hops on The Bus",
        original_title="",
        actresses=["Dylann Vox"],
        maker="Bang Bus",
        release_date="2019-08-28",
        tags=["Bus"],
        nfo_mtime=1.0,
        cover_path=to_file_uri(str(path.with_suffix(".jpg"))),
    )


class FakeRepo:
    def __init__(self, videos):
        self._videos = videos

    def get_all(self):
        return self._videos


def test_preview_western_uses_studio_year_scene_folder(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "bangbus.19.08.28.dylann.vox.mp4"
    source.write_bytes(b"video")
    source.with_suffix(".nfo").write_text("<movie />", encoding="utf-8")
    source.with_suffix(".jpg").write_bytes(b"jpg")
    monkeypatch.setattr("core.western_organizer.VideoRepository", lambda: FakeRepo([_video(source)]))

    result = preview_western_organize(config=_config(root))

    entry = result["entries"][0]
    assert result["summary"]["planned_count"] == 1
    assert entry["target"].replace("\\", "/").endswith(
        "欧美/Bang Bus/2019/2019-08-28 Stripper With Double D Hops on The Bus/"
        "[2019-08-28] Stripper With Double D Hops on The Bus - Dylann Vox.mp4"
    )
    assert {Path(item["target"]).suffix for item in entry["sidecars"]} == {".nfo", ".jpg"}
    assert source.exists(), "preview must not move the source video"


def test_preview_detects_target_conflict(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "bangbus.19.08.28.dylann.vox.mp4"
    source.write_bytes(b"video")
    target_dir = root / "欧美" / "Bang Bus" / "2019" / "2019-08-28 Stripper With Double D Hops on The Bus"
    target_dir.mkdir(parents=True)
    target = target_dir / "[2019-08-28] Stripper With Double D Hops on The Bus - Dylann Vox.mp4"
    target.write_bytes(b"existing")
    monkeypatch.setattr("core.western_organizer.VideoRepository", lambda: FakeRepo([_video(source)]))

    result = preview_western_organize(config=_config(root))

    assert result["entries"][0]["status"] == "conflict"
    assert result["entries"][0]["reason"] == "target_exists"


def test_preview_relocates_mojibake_western_folder_from_nas_config(tmp_path, monkeypatch):
    root = tmp_path / "欧美"
    root.mkdir()
    source = root / "bangbus.19.08.28.dylann.vox.mp4"
    source.write_bytes(b"video")
    source.with_suffix(".nfo").write_text("<movie />", encoding="utf-8")
    source.with_suffix(".jpg").write_bytes(b"jpg")
    bad_path = tmp_path / "Å·ÃÀ" / source.name
    video = _video(bad_path)
    config = {
        **_config(root),
        "gallery": {"directories": [str(tmp_path / "Å·ÃÀ")], "path_mappings": {}},
        "nas": {
            "shares": [{
                "enabled": True,
                "host": str(tmp_path),
                "share": "欧美",
                "subpath": "",
            }]
        },
    }
    monkeypatch.setattr("core.western_organizer.VideoRepository", lambda: FakeRepo([video]))
    monkeypatch.setattr("core.western_organizer._unc_roots_from_nas", lambda _config: [root])

    result = preview_western_organize(config=config)

    assert result["summary"]["planned_count"] == 1
    assert Path(result["entries"][0]["source"]) == source


def test_apply_moves_video_sidecars_and_syncs_db(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "bangbus.19.08.28.dylann.vox.mp4"
    source.write_bytes(b"video")
    source.with_suffix(".nfo").write_text("<movie />", encoding="utf-8")
    source.with_suffix(".jpg").write_bytes(b"jpg")
    source.with_suffix(".srt").write_text("subtitle", encoding="utf-8")
    monkeypatch.setattr("core.western_organizer.VideoRepository", lambda: FakeRepo([_video(source)]))
    synced = []

    def fake_upsert(target, *, old_file_path=None, scraped_metadata=None):
        synced.append((target, old_file_path, scraped_metadata))
        return "synced"

    synced_sidecars = []

    def fake_sync_sidecars(target, config=None):
        synced_sidecars.append(target)

    monkeypatch.setattr("core.western_organizer.try_inflow_upsert", fake_upsert)
    monkeypatch.setattr("core.western_organizer._sync_moved_sidecar_db", fake_sync_sidecars)
    plan = preview_western_organize(config=_config(root))
    result = apply_western_manifest(plan["manifest"], confirm=True)

    entry = result["entries"][0]
    target = Path(entry["target"])
    assert result["moved_entries"] == 1
    assert target.exists()
    assert target.with_suffix(".nfo").exists()
    assert target.with_suffix(".jpg").exists()
    assert target.with_suffix(".srt").exists()
    assert not source.exists()
    assert synced == [(str(target), str(source), entry["metadata"])]
    assert synced_sidecars == [target]


def test_apply_requires_confirmation(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "bangbus.19.08.28.dylann.vox.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr("core.western_organizer.VideoRepository", lambda: FakeRepo([_video(source)]))
    plan = preview_western_organize(config=_config(root))

    with pytest.raises(RuntimeError, match="confirmation_required"):
        apply_western_manifest(plan["manifest"], confirm=False)
