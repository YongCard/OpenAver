import json
from pathlib import Path

import pytest

from core.inbox_organizer import (
    DEFAULT_APPLY_BATCH_SIZE,
    InboxOrganizerError,
    apply_inbox_manifest,
    inventory_inbox,
    offline_plan_inbox,
    plan_inbox,
    rollback_inbox_manifest,
    search_inbox,
)


def _config(root: Path) -> dict:
    return {
        "general": {"locale": "zh-CN"},
        "gallery": {
            "directories": [str(root)],
            "path_mappings": {},
            "extensions": [".mp4", ".avi"],
        },
        "scraper": {
            "folder_layers": ["{actor}", "{num}"],
            "filename_format": "[{num}] {title}{suffix}",
            "suffix_keywords": ["-4k", "-C"],
            "external_manager": "off",
            "download_cover": False,
            "max_title_length": 50,
            "max_filename_length": 120,
        },
        "search": {"proxy_url": ""},
        "translate": {"enabled": False, "provider": "ollama", "ollama": {}},
    }


def _metadata(number: str = "ABC-123") -> dict:
    return {
        "number": number,
        "title": "测试标题",
        "actors": ["三上悠亞"],
        "maker": "Maker",
        "date": "2024-01-02",
        "tags": [],
    }


def test_inventory_only_scans_new_inbox_and_keeps_unknown_files(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    old_manual = root / "#待人工整理"
    inbox.mkdir(parents=True)
    old_manual.mkdir(parents=True)
    (inbox / "SUN-20.avi").write_bytes(b"video")
    (old_manual / "ABC-123.mp4").write_bytes(b"legacy")

    result = inventory_inbox(config=_config(root))

    assert result["manual_folder"] == "#待整理"
    assert result["summary"]["file_count"] == 1
    assert result["summary"]["needs_number_count"] == 1
    assert result["entries"][0]["filename"] == "SUN-20.avi"
    assert result["entries"][0]["status"] == "needs_number"


def test_plan_targets_library_root_not_inbox_and_retains_sidecars(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123-CD1.mp4"
    nfo = inbox / "ABC-123-CD1.nfo"
    poster = inbox / "ABC-123-CD1-poster.jpg"
    video.write_bytes(b"video")
    nfo.write_text("<movie />", encoding="utf-8")
    poster.write_bytes(b"poster")

    inventory = inventory_inbox(config=_config(root))
    entry = inventory["entries"][0]
    entry["metadata"] = _metadata()
    entry["status"] = "found"
    result = plan_inbox([entry], config=_config(root))

    planned = result["entries"][0]
    assert planned["status"] == "planned"
    assert planned["target"].endswith(str(Path("三上悠亞") / "ABC-123" / "[ABC-123] 测试标题-CD1.mp4"))
    assert "#待整理" not in str(Path(planned["target"]).relative_to(root))
    sidecar_targets = {Path(item["target"]).name for item in planned["sidecars"]}
    assert "[ABC-123] 测试标题-CD1.nfo" in sidecar_targets
    assert "[ABC-123] 测试标题-CD1-poster.jpg" in sidecar_targets


def test_plan_splits_slash_folder_layer_into_nested_directories(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    video.write_bytes(b"video")
    config = _config(root)
    config["scraper"]["folder_layers"] = ["{actor}/{num}"]

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = _metadata()
    result = plan_inbox([entry], config=config)

    assert result["entries"][0]["target"].endswith(
        str(Path("三上悠亞") / "ABC-123" / "[ABC-123] 测试标题.mp4")
    )


def test_plan_cleans_scraped_actress_description_for_target_folder(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "LUXU-395.mp4"
    video.write_bytes(b"video")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "LUXU-395",
        "title": "ラグジュTV 435",
        "actors": ["吉川愛 32歳 元ウェディングプランナー"],
        "tags": [],
    }
    result = plan_inbox([entry], config=_config(root))

    assert result["entries"][0]["target"].endswith(
        str(Path("吉川愛") / "LUXU-395" / "[LUXU-395] ラグジュTV 435.mp4")
    )


def test_plan_keeps_explicit_folder_layers_unchanged(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    video.write_bytes(b"video")
    config = _config(root)
    config["scraper"]["folder_layers"] = ["{actor}", "{num}"]

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = _metadata()
    result = plan_inbox([entry], config=config)

    assert result["entries"][0]["target"].endswith(
        str(Path("三上悠亞") / "ABC-123" / "[ABC-123] 测试标题.mp4")
    )


def test_plan_falls_back_to_split_folder_format_when_layers_empty(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    video.write_bytes(b"video")
    config = _config(root)
    config["scraper"]["folder_layers"] = []
    config["scraper"]["folder_format"] = "{actor}/{num}"

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = _metadata()
    result = plan_inbox([entry], config=config)

    assert result["entries"][0]["target"].endswith(
        str(Path("三上悠亞") / "ABC-123" / "[ABC-123] 测试标题.mp4")
    )


def test_plan_splits_windows_backslash_folder_layer(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    video.write_bytes(b"video")
    config = _config(root)
    config["scraper"]["folder_layers"] = [r"{actor}\{num}"]

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = _metadata()
    result = plan_inbox([entry], config=config)

    assert result["entries"][0]["target"].endswith(
        str(Path("三上悠亞") / "ABC-123" / "[ABC-123] 测试标题.mp4")
    )


def test_plan_deduplicates_multipart_suffix_in_external_mode(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "[GIRO-94] 真实标题-CD1.wmv"
    video.write_bytes(b"video")
    config = _config(root)
    config["gallery"]["extensions"].append(".wmv")
    config["scraper"]["external_manager"] = "jellyfin"
    config["scraper"]["suffix_keywords"] = ["-cd1", "-cd2", "-cd3", "-4k"]

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = {
        "number": "GIRO-94",
        "title": "真实标题-CD1",
        "actors": ["Actor"],
        "tags": [],
    }

    result = plan_inbox([entry], config=config)

    name = Path(result["entries"][0]["target"]).name
    assert name == "[GIRO-94] 真实标题-CD1.wmv"
    assert name.lower().count("cd1") == 1


def test_plan_keeps_non_multipart_suffix_while_deduplicating_part_tail(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "GIRO-94-CD2-4k.wmv"
    video.write_bytes(b"video")
    config = _config(root)
    config["gallery"]["extensions"].append(".wmv")
    config["scraper"]["external_manager"] = "jellyfin"
    config["scraper"]["suffix_keywords"] = ["-cd1", "-cd2", "-4k", "-uc"]

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = {
        "number": "GIRO-94",
        "title": "真实标题-CD2",
        "actors": ["Actor"],
        "tags": [],
    }

    result = plan_inbox([entry], config=config)

    name = Path(result["entries"][0]["target"]).name
    assert name == "[GIRO-94] 真实标题-4k-CD2.wmv"
    assert name.lower().count("cd2") == 1
    assert "-4k" in name


def test_plan_external_mode_deduplicates_cd3_tail(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "GIRO-94-CD3.wmv"
    video.write_bytes(b"video")
    config = _config(root)
    config["gallery"]["extensions"].append(".wmv")
    config["scraper"]["external_manager"] = "jellyfin"
    config["scraper"]["suffix_keywords"] = ["-cd1", "-cd2", "-cd3"]

    entry = inventory_inbox(config=config)["entries"][0]
    entry["metadata"] = {
        "number": "GIRO-94",
        "title": "真实标题-CD3",
        "actors": ["Actor"],
        "tags": [],
    }

    result = plan_inbox([entry], config=config)

    name = Path(result["entries"][0]["target"]).name
    assert name == "[GIRO-94] 真实标题-CD3.wmv"
    assert name.lower().count("cd3") == 1


def test_plan_prefers_scraped_title_over_placeholder_filename(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "[KIDM-451] 标题未定.mp4"
    nfo = inbox / "[KIDM-451] 标题未定.nfo"
    video.write_bytes(b"video")
    nfo.write_text("<movie />", encoding="utf-8")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "KIDM-451",
        "title": "KingDom真实标题",
        "actors": ["今井メロ"],
        "tags": [],
    }
    result = plan_inbox([entry], config=_config(root))

    planned = result["entries"][0]
    assert planned["target"].endswith(str(Path("今井メロ") / "KIDM-451" / "[KIDM-451] KingDom真实标题.mp4"))
    assert planned["title"] == "KingDom真实标题"
    sidecar_targets = {Path(item["target"]).name for item in planned["sidecars"]}
    assert "[KIDM-451] KingDom真实标题.nfo" in sidecar_targets


def test_plan_discards_unknown_title_extracted_from_filename(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "[ABC-123] unknown title.mp4"
    video.write_bytes(b"video")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "ABC-123",
        "title": "Scraped Title",
        "actors": ["Actor"],
        "tags": [],
    }
    result = plan_inbox([entry], config=_config(root))

    assert result["entries"][0]["target"].endswith(str(Path("Actor") / "ABC-123" / "[ABC-123] Scraped Title.mp4"))


def test_plan_falls_back_to_real_filename_title_when_scraped_title_missing(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123 自己保留的标题.mp4"
    video.write_bytes(b"video")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "ABC-123",
        "title": "",
        "actors": ["Actor"],
        "tags": [],
    }
    result = plan_inbox([entry], config=_config(root))

    assert result["entries"][0]["target"].endswith(str(Path("Actor") / "ABC-123" / "[ABC-123] 自己保留的标题.mp4"))


def test_plan_reports_target_conflict_without_overwrite(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    target_dir = root / "三上悠亞" / "ABC-123"
    inbox.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    source = inbox / "ABC-123.mp4"
    target = target_dir / "[ABC-123] 测试标题.mp4"
    source.write_bytes(b"source")
    target.write_bytes(b"existing")
    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = _metadata()

    result = plan_inbox([entry], config=_config(root))

    assert result["summary"]["conflict_count"] == 1
    assert result["entries"][0]["status"] == "conflict"
    assert target.read_bytes() == b"existing"


def test_search_uses_effective_source_from_private_source(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    entry = {"id": "a", "source": str(root / "ABC-123.mp4"), "number": "ABC-123"}
    monkeypatch.setattr(
        "core.inbox_organizer._search_one",
        lambda *_args, **_kwargs: {"number": "ABC-123", "title": "Title", "_source": "dmm", "source": "javbus"},
    )

    result = search_inbox([entry], source="auto", config=_config(root))

    assert result["entries"][0]["status"] == "found"
    assert result["entries"][0]["source_id"] == "dmm"


def test_search_uses_metadata_source_when_private_source_missing(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    entry = {"id": "a", "source": str(root / "ABC-123.mp4"), "number": "ABC-123"}
    monkeypatch.setattr(
        "core.inbox_organizer._search_one",
        lambda *_args, **_kwargs: {"number": "ABC-123", "title": "Title", "source": "javbus"},
    )

    result = search_inbox([entry], source="auto", config=_config(root))

    assert result["entries"][0]["source_id"] == "javbus"


def test_search_falls_back_to_requested_source(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    entry = {"id": "a", "source": str(root / "ABC-123.mp4"), "number": "ABC-123"}
    monkeypatch.setattr(
        "core.inbox_organizer._search_one",
        lambda *_args, **_kwargs: {"number": "ABC-123", "title": "Title"},
    )

    result = search_inbox([entry], source="auto", config=_config(root))

    assert result["entries"][0]["source_id"] == "auto"


def test_search_translates_scraped_title_when_enabled(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    config = _config(root)
    config["translate"]["enabled"] = True
    entry = {"id": "a", "source": str(root / "BF-231.mp4"), "number": "BF-231"}

    class FakeTranslateService:
        async def translate_single(self, title, context=None):
            assert title == "中出しチアリーダー 野宮さとみ"
            assert context["number"] == "BF-231"
            return "内射啦啦队 野宫里美"

    monkeypatch.setattr(
        "core.inbox_organizer._search_one",
        lambda *_args, **_kwargs: {
            "number": "BF-231",
            "title": "中出しチアリーダー 野宮さとみ",
            "actors": ["野宮さとみ"],
        },
    )
    monkeypatch.setattr(
        "core.inbox_organizer.create_translate_service",
        lambda *_args, **_kwargs: FakeTranslateService(),
    )

    result = search_inbox([entry], source="auto", config=config)

    metadata = result["entries"][0]["metadata"]
    assert metadata["translated_title"] == "内射啦啦队 野宫里美"
    assert metadata["_translation_status"] == "translation_success"


def test_search_translation_failure_keeps_scraped_title(monkeypatch, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    config = _config(root)
    config["translate"]["enabled"] = True
    entry = {"id": "a", "source": str(root / "BF-231.mp4"), "number": "BF-231"}

    def boom(*_args, **_kwargs):
        raise RuntimeError("translator unavailable")

    monkeypatch.setattr(
        "core.inbox_organizer._search_one",
        lambda *_args, **_kwargs: {
            "number": "BF-231",
            "title": "中出しチアリーダー 野宮さとみ",
            "actors": ["野宮さとみ"],
        },
    )
    monkeypatch.setattr("core.inbox_organizer.create_translate_service", boom)

    result = search_inbox([entry], source="auto", config=config)

    metadata = result["entries"][0]["metadata"]
    assert metadata["title"] == "中出しチアリーダー 野宮さとみ"
    assert "translated_title" not in metadata
    assert metadata["_translation_status"] == "translation_failed"


def test_apply_requires_confirmation(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": []}), encoding="utf-8")

    with pytest.raises(InboxOrganizerError, match="confirmation_required"):
        apply_inbox_manifest(manifest, confirm=False, config=_config(tmp_path))


def test_apply_moves_all_requested_entries_and_rollback_restores(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    entries = []
    for index in range(21):
        video = inbox / f"ABC-{index:03d}.mp4"
        video.write_bytes(f"video-{index}".encode())
        item = {
            "id": f"id-{index}",
            "root": str(root),
            "source": str(video),
            "number": f"ABC-{index:03d}",
            "metadata": _metadata(f"ABC-{index:03d}"),
            "status": "found",
        }
        entries.append(item)

    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", lambda *_args, **_kwargs: "synced")
    monkeypatch.setattr("core.inbox_organizer._write_new_sidecars", lambda *_args, **_kwargs: {})
    plan = plan_inbox(entries, config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, batch_size=99, config=_config(root))

    assert result["moved_entries"] == 21
    assert result["remaining"] == 0
    assert len(list(inbox.glob("*.mp4"))) == 0

    rollback = rollback_inbox_manifest(plan["manifest"], confirm=True, batch_size=21)

    assert rollback["rolled_back_operations"] == 21
    assert len(list(inbox.glob("*.mp4"))) == 21


def test_apply_default_batch_size_is_twenty(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    entries = []
    for index in range(21):
        video = inbox / f"ABC-{index:03d}.mp4"
        video.write_bytes(b"video")
        entries.append({
            "id": f"id-{index}",
            "root": str(root),
            "source": str(video),
            "number": f"ABC-{index:03d}",
            "metadata": _metadata(f"ABC-{index:03d}"),
            "status": "found",
        })

    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", lambda *_args, **_kwargs: "synced")
    monkeypatch.setattr("core.inbox_organizer._write_new_sidecars", lambda *_args, **_kwargs: {})
    plan = plan_inbox(entries, config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, config=_config(root))

    assert DEFAULT_APPLY_BATCH_SIZE == 20
    assert result["moved_entries"] == 20
    assert result["remaining"] == 1


def test_offline_plan_reads_same_stem_nfo_without_searching(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    video.write_bytes(b"video")
    (inbox / "ABC-123.nfo").write_text(
        "<movie><title>本地标题</title><num>ABC-123</num><actor><name>本地女优</name></actor></movie>",
        encoding="utf-8",
    )
    monkeypatch.setattr("core.inbox_organizer._search_one", lambda *_args, **_kwargs: pytest.fail("offline plan searched"))

    entry = inventory_inbox(config=_config(root))["entries"][0]
    result = offline_plan_inbox([entry], config=_config(root))

    planned = result["entries"][0]
    assert planned["status"] == "planned"
    assert planned["metadata"]["_offline"] is True
    assert planned["target"].endswith(str(Path("本地女优") / "ABC-123" / "[ABC-123] 本地标题.mp4"))
    assert result["summary"]["offline_ready_count"] == 1


def test_offline_plan_marks_missing_nfo_for_rescrape(tmp_path):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    video.write_bytes(b"video")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    result = offline_plan_inbox([entry], config=_config(root))

    assert result["entries"][0]["status"] == "needs_rescrape"
    assert result["entries"][0]["reason"] == "offline_nfo_missing"
    assert result["summary"]["needs_rescrape_count"] == 1


def test_offline_plan_moves_scraped_movie_directory_intact(tmp_path, monkeypatch):
    root = tmp_path / "library"
    movie_dir = root / "#待整理" / "ABP" / "あやみ旬果" / "[ABP-566] 本地标题"
    movie_dir.mkdir(parents=True)
    video = movie_dir / "ABP-566.mp4"
    video.write_bytes(b"video")
    (movie_dir / "ABP-566.nfo").write_text(
        "<movie><title>本地标题</title><num>ABP-566</num><actor><name>あやみ旬果</name></actor></movie>",
        encoding="utf-8",
    )
    (movie_dir / "poster.jpg").write_bytes(b"poster")
    (movie_dir / "extrafanart").mkdir()
    (movie_dir / "extrafanart" / "fanart1.jpg").write_bytes(b"fanart")
    synced = []
    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", lambda target, **kwargs: synced.append(target) or "synced")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    plan = offline_plan_inbox([entry], config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, config=_config(root))

    target_dir = root / "あやみ旬果" / "ABP-566"
    assert result["moved_entries"] == 1
    assert target_dir.is_dir()
    assert (target_dir / "ABP-566.mp4").exists()
    assert (target_dir / "poster.jpg").exists()
    assert (target_dir / "extrafanart" / "fanart1.jpg").exists()
    assert result["entries"][0]["target_video"] == str(target_dir / "ABP-566.mp4")
    assert synced == [str(target_dir / "ABP-566.mp4")]


def test_apply_rewrites_placeholder_nfo_with_scraped_metadata(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "[KIDM-451] 标题未定.mp4"
    nfo = inbox / "[KIDM-451] 标题未定.nfo"
    video.write_bytes(b"video")
    nfo.write_text("<movie><title>标题未定</title><originaltitle>标题未定</originaltitle></movie>", encoding="utf-8")
    synced = []

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "KIDM-451",
        "title": "KingDom真实标题",
        "actors": ["今井メロ"],
        "tags": ["tag-a"],
        "date": "2026-01-02",
        "maker": "King Dom",
    }

    def fake_upsert(target, *, old_file_path=None, scraped_metadata=None):
        synced.append((target, old_file_path, scraped_metadata))
        return "synced"

    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", fake_upsert)
    plan = plan_inbox([entry], config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, config=_config(root))

    target = Path(result["entries"][0]["target"])
    target_nfo = target.with_suffix(".nfo")
    nfo_text = target_nfo.read_text(encoding="utf-8")
    assert result["moved_entries"] == 1
    assert "KingDom真实标题" in nfo_text
    assert "标题未定" not in nfo_text
    assert "nfo_rewritten" in {
        op.get("role")
        for op in json.loads(Path(result["journal"]).read_text(encoding="utf-8"))["operations"]
    }
    assert synced and synced[0][2]["title"] == "KingDom真实标题"


def test_apply_rewrites_number_only_nfo_and_syncs_translated_title(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "BF-231.mp4"
    nfo = inbox / "BF-231.nfo"
    video.write_bytes(b"video")
    nfo.write_text("<movie><title>BF-231</title><originaltitle>BF-231</originaltitle></movie>", encoding="utf-8")
    synced = []

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "BF-231",
        "title": "中出しチアリーダー 野宮さとみ",
        "translated_title": "内射啦啦队 野宫里美",
        "actors": ["野宮さとみ"],
        "tags": [],
    }

    def fake_upsert(target, *, old_file_path=None, scraped_metadata=None):
        synced.append(scraped_metadata)
        return "synced"

    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", fake_upsert)
    plan = plan_inbox([entry], config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, config=_config(root))

    target = Path(result["entries"][0]["target"])
    nfo_text = target.with_suffix(".nfo").read_text(encoding="utf-8")
    assert "[BF-231] 内射啦啦队 野宫里美" in nfo_text
    assert "<originaltitle>中出しチアリーダー 野宮さとみ</originaltitle>" in nfo_text
    assert synced and synced[0]["title"] == "内射啦啦队 野宫里美"


def test_apply_keeps_trusted_existing_nfo(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "ABC-123.mp4"
    nfo = inbox / "ABC-123.nfo"
    trusted_nfo = "<movie><title>手工可信标题</title><plot>keep me</plot></movie>"
    video.write_bytes(b"video")
    nfo.write_text(trusted_nfo, encoding="utf-8")

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = _metadata()

    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", lambda *_args, **_kwargs: "synced")
    plan = plan_inbox([entry], config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, config=_config(root))

    target_nfo = Path(result["entries"][0]["target"]).with_suffix(".nfo")
    assert target_nfo.read_text(encoding="utf-8") == trusted_nfo


def test_apply_uses_cleaned_actor_for_nfo_and_db_metadata(tmp_path, monkeypatch):
    root = tmp_path / "library"
    inbox = root / "#待整理"
    inbox.mkdir(parents=True)
    video = inbox / "LUXU-395.mp4"
    video.write_bytes(b"video")
    synced = []

    entry = inventory_inbox(config=_config(root))["entries"][0]
    entry["metadata"] = {
        "number": "LUXU-395",
        "title": "ラグジュTV 435",
        "actors": ["吉川愛 32歳 元ウェディングプランナー"],
        "tags": [],
    }

    def fake_upsert(target, *, old_file_path=None, scraped_metadata=None):
        synced.append(scraped_metadata)
        return "synced"

    monkeypatch.setattr("core.inbox_organizer.try_inflow_upsert", fake_upsert)
    plan = plan_inbox([entry], config=_config(root))
    result = apply_inbox_manifest(plan["manifest"], confirm=True, config=_config(root))

    target_nfo = Path(result["entries"][0]["target"]).with_suffix(".nfo")
    nfo_text = target_nfo.read_text(encoding="utf-8")
    assert "<name>吉川愛</name>" in nfo_text
    assert "元ウェディングプランナー" not in nfo_text
    assert synced and synced[0]["actors"] == ["吉川愛"]
