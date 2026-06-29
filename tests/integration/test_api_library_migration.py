from core.library_migration import MigrationConflictError, MigrationError
from core.title_placeholder import TitlePlaceholderError
from web.routers import library_migration as migration_router


def test_inventory_endpoint(client, monkeypatch):
    captured = {}

    def fake_inventory(root, run_id, **kwargs):
        captured.update(kwargs)
        return {"run_id": run_id, "root": root, "video_count": 3}

    monkeypatch.setattr(
        migration_router,
        "inventory_library",
        fake_inventory,
    )

    response = client.post(
        "/api/library-migration/inventory",
        json={"root": "X:/library", "run_id": "demo", "include_manual": True},
    )

    assert response.status_code == 200
    assert response.json()["video_count"] == 3
    assert captured["include_manual"] is True


def test_all_operation_endpoints(client, monkeypatch):
    monkeypatch.setattr(migration_router, "plan_library", lambda run_dir, **kwargs: {"run_dir": run_dir})
    monkeypatch.setattr(
        migration_router,
        "apply_manifest",
        lambda manifest, confirm_run, batch_size: {"batch_size": batch_size},
    )
    monkeypatch.setattr(migration_router, "verify_manifest", lambda manifest: {"success": True})
    monkeypatch.setattr(
        migration_router,
        "rollback_manifest",
        lambda manifest, confirm_run, batch_size: {"rolled_back_entries": batch_size},
    )

    assert client.post(
        "/api/library-migration/plan", json={"run_dir": "X:/run"},
    ).status_code == 200
    assert client.post(
        "/api/library-migration/apply",
        json={"manifest": "X:/manifest.json", "confirm_run": "demo", "batch_size": 20},
    ).json()["batch_size"] == 20
    assert client.post(
        "/api/library-migration/verify", json={"manifest": "X:/manifest.json"},
    ).json()["success"] is True
    assert client.post(
        "/api/library-migration/rollback",
        json={"manifest": "X:/manifest.json", "confirm_run": "demo", "batch_size": 2},
    ).json()["rolled_back_entries"] == 2


def test_batch_size_above_twenty_is_rejected(client):
    response = client.post(
        "/api/library-migration/apply",
        json={"manifest": "X:/manifest.json", "confirm_run": "demo", "batch_size": 21},
    )
    assert response.status_code == 422


def test_errors_use_fixed_messages(client, monkeypatch):
    def conflict(*_args, **_kwargs):
        raise MigrationConflictError("private path details")

    monkeypatch.setattr(migration_router, "apply_manifest", conflict)
    response = client.post(
        "/api/library-migration/apply",
        json={"manifest": "X:/secret/manifest.json", "confirm_run": "demo"},
    )
    assert response.status_code == 409
    assert "private path details" not in response.text

    monkeypatch.setattr(
        migration_router,
        "verify_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MigrationError("internal")),
    )
    response = client.post(
        "/api/library-migration/verify", json={"manifest": "X:/manifest.json"},
    )
    assert response.status_code == 400
    assert "internal" not in response.text


def test_duplicates_endpoint(client, monkeypatch):
    captured = {}

    def fake_duplicates(**kwargs):
        captured.update(kwargs)
        return {
            "summary": {"duplicate_group_count": 1},
            "groups": [{"number": "ABC-123", "classification": "duplicate"}],
        }

    monkeypatch.setattr(migration_router, "find_duplicate_numbers", fake_duplicates)

    response = client.get(
        "/api/library-migration/duplicates?include_multipart=true&include_missing_paths=false&limit=12"
    )

    assert response.status_code == 200
    assert response.json()["summary"]["duplicate_group_count"] == 1
    assert captured == {
        "include_multipart": True,
        "include_missing_paths": False,
        "limit": 12,
    }


def test_duplicate_delete_preview_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        migration_router,
        "preview_duplicate_delete",
        lambda path: {"path": path, "files": [{"name": "ABC-123.mp4"}]},
    )

    response = client.post(
        "/api/library-migration/duplicate-delete/preview",
        json={"path": "file:///D:/library/ABC-123.mp4"},
    )

    assert response.status_code == 200
    assert response.json()["files"][0]["name"] == "ABC-123.mp4"


def test_duplicate_delete_apply_requires_confirm(client):
    response = client.post(
        "/api/library-migration/duplicate-delete/apply",
        json={"path": "file:///D:/library/ABC-123.mp4"},
    )

    assert response.status_code == 400


def test_duplicate_delete_apply_endpoint(client, monkeypatch):
    captured = {}

    def fake_apply(path, *, confirm):
        captured["path"] = path
        captured["confirm"] = confirm
        return {"success": True, "deleted_db_rows": 1}

    monkeypatch.setattr(migration_router, "apply_duplicate_delete", fake_apply)

    response = client.post(
        "/api/library-migration/duplicate-delete/apply",
        json={"path": "file:///D:/library/ABC-123.mp4", "confirm": True},
    )

    assert response.status_code == 200
    assert response.json()["deleted_db_rows"] == 1
    assert captured == {"path": "file:///D:/library/ABC-123.mp4", "confirm": True}


def test_empty_folders_preview_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        migration_router,
        "preview_empty_folders",
        lambda paths=None: {"folder_count": 1, "folders": [{"path": "D:/library/Actor"}]},
    )

    response = client.post("/api/library-migration/empty-folders/preview", json={})

    assert response.status_code == 200
    assert response.json()["folder_count"] == 1


def test_empty_folders_apply_requires_confirm(client):
    response = client.post("/api/library-migration/empty-folders/apply", json={})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_empty_folders_apply_endpoint(client, monkeypatch):
    captured = {}

    def fake_apply(*, confirm, paths=None):
        captured["confirm"] = confirm
        captured["paths"] = paths
        return {"success": True, "removed_empty_folder_count": 1}

    monkeypatch.setattr(migration_router, "apply_empty_folders", fake_apply)

    response = client.post(
        "/api/library-migration/empty-folders/apply",
        json={"confirm": True, "paths": ["D:/library/Actor"]},
    )

    assert response.status_code == 200
    assert response.json()["removed_empty_folder_count"] == 1
    assert captured == {"confirm": True, "paths": ["D:/library/Actor"]}


def test_title_placeholder_preview_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        migration_router,
        "preview_title_placeholders",
        lambda run_id=None: {
            "run_id": run_id or "demo",
            "manifest": "D:/library/.openaver-migration/demo/title_placeholder_manifest.json",
            "summary": {"candidate_count": 1},
            "entries": [{"number": "KIDM-451"}],
        },
    )

    response = client.post(
        "/api/library-migration/title-placeholder/preview",
        json={"run_id": "demo"},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["candidate_count"] == 1


def test_title_placeholder_apply_requires_confirm(client, monkeypatch):
    def fake_apply(*_args, **_kwargs):
        raise TitlePlaceholderError("confirmation_required")

    monkeypatch.setattr(migration_router, "apply_title_placeholder_manifest", fake_apply)

    response = client.post(
        "/api/library-migration/title-placeholder/apply",
        json={"manifest": "D:/manifest.json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_title_placeholder_apply_endpoint(client, monkeypatch):
    captured = {}

    def fake_apply(manifest, *, confirm, batch_size):
        captured["manifest"] = manifest
        captured["confirm"] = confirm
        captured["batch_size"] = batch_size
        return {"success": True, "moved_entries": 2, "remaining": 0}

    monkeypatch.setattr(migration_router, "apply_title_placeholder_manifest", fake_apply)

    response = client.post(
        "/api/library-migration/title-placeholder/apply",
        json={"manifest": "D:/manifest.json", "confirm": True, "batch_size": 20},
    )

    assert response.status_code == 200
    assert response.json()["moved_entries"] == 2
    assert captured == {"manifest": "D:/manifest.json", "confirm": True, "batch_size": 20}


def test_title_placeholder_rollback_endpoint(client, monkeypatch):
    captured = {}

    def fake_rollback(manifest, *, confirm, batch_size):
        captured["manifest"] = manifest
        captured["confirm"] = confirm
        captured["batch_size"] = batch_size
        return {"success": True, "rolled_back_operations": 3}

    monkeypatch.setattr(migration_router, "rollback_title_placeholder_manifest", fake_rollback)

    response = client.post(
        "/api/library-migration/title-placeholder/rollback",
        json={"manifest": "D:/manifest.json", "confirm": True, "batch_size": 3},
    )

    assert response.status_code == 200
    assert response.json()["rolled_back_operations"] == 3
    assert captured == {"manifest": "D:/manifest.json", "confirm": True, "batch_size": 3}
