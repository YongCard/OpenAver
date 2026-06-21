"""TASK-79-T3 — client-log beacon 端點整合測試（TDD-lite）。

驗證 POST /api/client-log：
  - 合法 payload（含 message）→ 204、無 body
  - 缺 message → 422（FastAPI validation，OpenAver 全域 handler）
  - caplog 斷言 record 落在 OpenAver.frontend channel
    + level split（error→WARNING / boot,post_alpine→INFO，CD10）
  - 超長 message 截斷 [:4000]
"""
import logging


def test_valid_payload_returns_204(client):
    resp = client.post(
        "/api/client-log",
        json={"phase": "boot", "message": "boot", "path": "/"},
    )
    assert resp.status_code == 204
    assert resp.content == b""


def test_missing_message_returns_422(client):
    resp = client.post("/api/client-log", json={"phase": "boot"})
    assert resp.status_code == 422


def test_error_phase_logs_warning(client, caplog):
    with caplog.at_level(logging.WARNING, logger="OpenAver.frontend"):
        resp = client.post(
            "/api/client-log",
            json={
                "phase": "error",
                "message": "boom",
                "stack": "at x",
                "kind": "js",
                "user_agent": "UA",
                "path": "/p",
            },
        )
    assert resp.status_code == 204
    warnings = [
        r for r in caplog.records
        if r.name == "OpenAver.frontend" and r.levelno == logging.WARNING
    ]
    assert any("boom" in r.getMessage() for r in warnings)


def test_boot_phase_logs_info(client, caplog):
    with caplog.at_level(logging.INFO, logger="OpenAver.frontend"):
        resp = client.post(
            "/api/client-log",
            json={
                "phase": "boot",
                "message": "boot",
                "user_agent": "UA",
                "path": "/",
            },
        )
    assert resp.status_code == 204
    frontend_records = [r for r in caplog.records if r.name == "OpenAver.frontend"]
    info_records = [r for r in frontend_records if r.levelno == logging.INFO]
    assert info_records, "expected an INFO-level OpenAver.frontend record"
    # boot 不應是 WARNING（CD10 level split）
    assert all(r.levelno != logging.WARNING for r in frontend_records)


def test_long_message_truncated(client, caplog):
    raw = "x" * 5000
    with caplog.at_level(logging.INFO, logger="OpenAver.frontend"):
        resp = client.post(
            "/api/client-log",
            json={"phase": "boot", "message": raw, "path": "/"},
        )
    assert resp.status_code == 204
    frontend_records = [r for r in caplog.records if r.name == "OpenAver.frontend"]
    assert frontend_records, "expected an OpenAver.frontend record"
    # 完整 5000-char raw message 不應出現（已截斷到 [:4000]）
    for r in frontend_records:
        msg = r.getMessage()
        assert raw not in msg
        # payload-message 部分（連續 'x' run）≤ 4000
        run = 0
        best = 0
        for ch in msg:
            if ch == "x":
                run += 1
                best = max(best, run)
            else:
                run = 0
        assert best <= 4000
