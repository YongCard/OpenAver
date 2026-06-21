"""共用 fixtures — integration 測試層"""
import pytest
import json
from pathlib import Path
from fastapi.testclient import TestClient
from web.app import app
from core import config as core_config

# ── LAN access gate（feature/80）測試相容 ──────────────────────────────
# web.app 的 lan_access_gate middleware 用 request.client.host 判 loopback。
# Starlette TestClient 預設 client host = "testclient"（非 loopback）→ 單機模式
# （預設）會擋掉所有既有整合測試。整合測試的 TestClient 一律代表「桌面 App 自連」
# = loopback，故在此把預設 client 設成 127.0.0.1。
#
# 為何是 module-level class patch（而非 autouse fixture）：本目錄有數個測試在
# **import 時**就 `client = TestClient(app)`（如 test_api_translate.py），早於任何
# fixture 執行；patch 必須在 conftest import（先於 test module import）即生效才能
# 涵蓋。替代方案是逐檔顯式傳 loopback client（~24 檔 churn），取捨後選集中一處。
#
# 取捨與邊界：
#   - process-global（patch class __init__）：full `pytest tests/` 下會延續到 unit
#     階段。目前無 unit 測試依賴預設 "testclient" host，故無實害；未來若有 unit 測試
#     需要非 loopback 預設，須顯式傳 client= 或在該測試還原。
#   - setdefault：顯式 client=(ip, port)（如 gate 矩陣測遠端）永遠覆寫此預設。
#   - idempotent guard：避免重複 wrap。
import starlette.testclient as _starlette_testclient

if not getattr(_starlette_testclient.TestClient, "_openaver_loopback_patched", False):
    _orig_testclient_init = _starlette_testclient.TestClient.__init__

    def _loopback_default_init(self, *args, **kwargs):
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        _orig_testclient_init(self, *args, **kwargs)

    _starlette_testclient.TestClient.__init__ = _loopback_default_init
    _starlette_testclient.TestClient._openaver_loopback_patched = True


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """自動隔離所有 integration 測試的 config — 防止寫入真實 web/config.json"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "test_config.json"
    default_file = config_dir / "test_config.default.json"

    default_config = core_config.AppConfig().model_dump()
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(default_config, f)

    monkeypatch.setattr(core_config, "CONFIG_PATH", config_file)
    monkeypatch.setattr(core_config, "CONFIG_DEFAULT_PATH", default_file)


@pytest.fixture
def client():
    """共用 integration 層的 TestClient"""
    return TestClient(app)

@pytest.fixture
def parse_sse_events():
    """Helper: 解析 SSE response text，返回所有 event data 的列表。"""
    def _parse(response_text: str) -> list:
        events = []
        for line in response_text.strip().split('\n'):
            if line.startswith('data: '):
                try:
                    event_data = json.loads(line[6:])
                    events.append(event_data)
                except json.JSONDecodeError:
                    pass
        return events
    return _parse
