from core.library_migration import MigrationConflictError, MigrationError
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
