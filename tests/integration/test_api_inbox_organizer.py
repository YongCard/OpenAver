from core.inbox_organizer import InboxOrganizerError
from web.routers import inbox_organizer as inbox_router


def test_roots_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        inbox_router,
        "get_inbox_roots",
        lambda: {"manual_folder": "#待整理", "roots": [{"root": "D:/library"}]},
    )

    response = client.get("/api/inbox-organizer/roots")

    assert response.status_code == 200
    assert response.json()["manual_folder"] == "#待整理"


def test_inventory_endpoint(client, monkeypatch):
    captured = {}

    def fake_inventory(*, root=None):
        captured["root"] = root
        return {"summary": {"file_count": 1}, "entries": [{"filename": "SUN-20.avi"}]}

    monkeypatch.setattr(inbox_router, "inventory_inbox", fake_inventory)

    response = client.post("/api/inbox-organizer/inventory", json={"root": "D:/library"})

    assert response.status_code == 200
    assert response.json()["entries"][0]["filename"] == "SUN-20.avi"
    assert captured["root"] == "D:/library"


def test_search_plan_apply_rollback_endpoints(client, monkeypatch):
    monkeypatch.setattr(
        inbox_router,
        "search_inbox",
        lambda entries, *, source="auto": {"entries": entries, "summary": {"found_count": 1}, "source": source},
    )
    monkeypatch.setattr(
        inbox_router,
        "plan_inbox",
        lambda entries, *, run_id=None: {"manifest": "D:/manifest.json", "entries": entries, "run_id": run_id},
    )
    monkeypatch.setattr(
        inbox_router,
        "offline_plan_inbox",
        lambda entries, *, run_id=None: {"manifest": "D:/offline.json", "entries": entries, "run_id": run_id},
    )
    monkeypatch.setattr(
        inbox_router,
        "apply_inbox_manifest",
        lambda manifest, *, confirm, batch_size: {"success": True, "manifest": manifest, "moved_entries": batch_size},
    )
    monkeypatch.setattr(
        inbox_router,
        "rollback_inbox_manifest",
        lambda manifest, *, confirm, batch_size: {"success": True, "manifest": manifest, "rolled_back_operations": batch_size},
    )

    assert client.post(
        "/api/inbox-organizer/search",
        json={"entries": [{"number": "ABC-123"}], "source": "dmm"},
    ).json()["summary"]["found_count"] == 1
    assert client.post(
        "/api/inbox-organizer/plan",
        json={"entries": [{"number": "ABC-123"}], "run_id": "demo"},
    ).json()["manifest"] == "D:/manifest.json"
    assert client.post(
        "/api/inbox-organizer/offline-plan",
        json={"entries": [{"number": "ABC-123"}], "run_id": "offline"},
    ).json()["manifest"] == "D:/offline.json"
    assert client.post(
        "/api/inbox-organizer/apply",
        json={"manifest": "D:/manifest.json", "confirm": True, "batch_size": 20},
    ).json()["moved_entries"] == 20
    assert client.post(
        "/api/inbox-organizer/rollback",
        json={"manifest": "D:/manifest.json", "confirm": True, "batch_size": 3},
    ).json()["rolled_back_operations"] == 3


def test_apply_requires_confirm(client, monkeypatch):
    def fake_apply(*_args, **_kwargs):
        raise InboxOrganizerError("confirmation_required")

    monkeypatch.setattr(inbox_router, "apply_inbox_manifest", fake_apply)

    response = client.post("/api/inbox-organizer/apply", json={"manifest": "D:/manifest.json"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_batch_size_above_twenty_is_allowed(client, monkeypatch):
    monkeypatch.setattr(
        inbox_router,
        "apply_inbox_manifest",
        lambda manifest, *, confirm, batch_size: {"success": True, "batch_size": batch_size},
    )
    response = client.post(
        "/api/inbox-organizer/apply",
        json={"manifest": "D:/manifest.json", "confirm": True, "batch_size": 21},
    )

    assert response.status_code == 200
    assert response.json()["batch_size"] == 21


def test_apply_default_batch_size_is_twenty(client, monkeypatch):
    monkeypatch.setattr(
        inbox_router,
        "apply_inbox_manifest",
        lambda manifest, *, confirm, batch_size: {"success": True, "batch_size": batch_size},
    )
    response = client.post(
        "/api/inbox-organizer/apply",
        json={"manifest": "D:/manifest.json", "confirm": True},
    )

    assert response.status_code == 200
    assert response.json()["batch_size"] == 20


def test_apply_removes_moved_entries_from_restored_search_job(client, monkeypatch):
    monkeypatch.setattr(
        inbox_router,
        "apply_inbox_manifest",
        lambda manifest, *, confirm, batch_size: {
            "success": True,
            "manifest": manifest,
            "moved_entries": 1,
            "remaining": 0,
            "entries": [
                {"id": "done", "source": "D:/library/#待整理/ABC-123.mp4", "status": "moved"},
                {"id": "keep", "source": "D:/library/#待整理/XYZ-999.mp4", "status": "not_found"},
            ],
        },
    )
    inbox_router._SEARCH_JOB = {
        "job_id": "job-apply-sync",
        "phase": "done",
        "source": "auto",
        "selected_ids": ["done", "keep"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "current": 2,
        "total": 2,
        "current_label": "",
        "cancel_requested": False,
        "logs": [],
        "entries": [
            {"id": "done", "source": "D:/library/#待整理/ABC-123.mp4", "status": "found"},
            {"id": "keep", "source": "D:/library/#待整理/XYZ-999.mp4", "status": "not_found"},
        ],
    }

    response = client.post(
        "/api/inbox-organizer/apply",
        json={"manifest": "D:/manifest.json", "confirm": True, "batch_size": 10000},
    )

    assert response.status_code == 200
    current = client.get("/api/inbox-organizer/search-jobs/current").json()["job"]
    assert [entry["id"] for entry in current["entries"]] == ["keep"]


def test_search_job_start_status_and_current(client, monkeypatch):
    def fake_search(entries, *, source="auto"):
        item = dict(entries[0])
        item.update({
            "status": "found",
            "reason": "",
            "metadata": {"number": item.get("manual_number") or item.get("number"), "title": "OK"},
        })
        return {"summary": {"found_count": 1}, "entries": [item]}

    monkeypatch.setattr(inbox_router, "search_inbox", fake_search)

    response = client.post(
        "/api/inbox-organizer/search-jobs",
        json={
            "entries": [{"id": "a", "number": "ABC-123", "source": "D:/#待整理/ABC-123.mp4"}],
            "source": "dmm",
        },
    )

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["job_id"]
    assert job["phase"] in {"running", "done"}

    import time
    for _ in range(20):
        status = client.get(f"/api/inbox-organizer/search-jobs/{job['job_id']}").json()["job"]
        if status["phase"] == "done":
            break
        time.sleep(0.05)

    assert status["phase"] == "done"
    assert status["summary"]["found_count"] == 1
    assert status["entries"][0]["status"] == "found"

    current = client.get("/api/inbox-organizer/search-jobs/current").json()["job"]
    assert current["job_id"] == job["job_id"]


def test_search_job_selected_ids_only_preserves_other_entries(client, monkeypatch):
    def fake_search(entries, *, source="auto"):
        item = dict(entries[0])
        item.update({
            "status": "found",
            "number": item.get("manual_number") or item.get("number"),
            "metadata": {"number": item.get("manual_number") or item.get("number"), "title": "Manual"},
        })
        return {"summary": {"found_count": 1}, "entries": [item]}

    monkeypatch.setattr(inbox_router, "search_inbox", fake_search)

    response = client.post(
        "/api/inbox-organizer/search-jobs",
        json={
            "entries": [
                {"id": "keep", "number": "ABC-123", "status": "not_found"},
                {"id": "redo", "manual_number": "SUN-20", "status": "needs_number"},
            ],
            "source": "dmm",
            "selected_ids": ["redo"],
        },
    )
    job = response.json()["job"]

    import time
    for _ in range(20):
        status = client.get(f"/api/inbox-organizer/search-jobs/{job['job_id']}").json()["job"]
        if status["phase"] == "done":
            break
        time.sleep(0.05)

    entries = {item["id"]: item for item in status["entries"]}
    assert entries["keep"]["status"] == "not_found"
    assert entries["redo"]["status"] == "found"
    assert entries["redo"]["number"] == "SUN-20"


def test_search_job_second_start_returns_running_job(client, monkeypatch):
    import threading

    gate = threading.Event()

    def slow_search(entries, *, source="auto"):
        gate.wait(1)
        item = dict(entries[0])
        item.update({"status": "not_found", "reason": "metadata_not_found", "metadata": None})
        return {"entries": [item], "summary": {"found_count": 0}}

    monkeypatch.setattr(inbox_router, "search_inbox", slow_search)

    first = client.post(
        "/api/inbox-organizer/search-jobs",
        json={"entries": [{"id": "a", "number": "ABC-123"}]},
    ).json()["job"]
    second = client.post(
        "/api/inbox-organizer/search-jobs",
        json={"entries": [{"id": "b", "number": "XYZ-999"}]},
    ).json()["job"]
    gate.set()

    assert second["job_id"] == first["job_id"]


def test_search_job_cancel_endpoint(client, monkeypatch):
    import threading
    import time

    gate = threading.Event()

    def slow_search(entries, *, source="auto"):
        gate.wait(1)
        item = dict(entries[0])
        item.update({"status": "not_found", "reason": "metadata_not_found", "metadata": None})
        return {"entries": [item], "summary": {"found_count": 0}}

    monkeypatch.setattr(inbox_router, "search_inbox", slow_search)

    job = client.post(
        "/api/inbox-organizer/search-jobs",
        json={
            "entries": [
                {"id": "a", "number": "ABC-123"},
                {"id": "b", "number": "XYZ-999"},
            ]
        },
    ).json()["job"]

    cancel = client.post(f"/api/inbox-organizer/search-jobs/{job['job_id']}/cancel")
    gate.set()

    assert cancel.status_code == 200
    assert cancel.json()["job"]["phase"] == "canceling"
    for _ in range(20):
        status = client.get(f"/api/inbox-organizer/search-jobs/{job['job_id']}").json()["job"]
        if status["phase"] in {"canceled", "done"}:
            break
        time.sleep(0.05)
    assert status["phase"] == "canceled"
