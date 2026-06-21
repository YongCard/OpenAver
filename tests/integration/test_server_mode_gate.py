"""
TASK-80a-T2: LAN 存取閘門矩陣測試

驗證 web/app.py lan_access_gate middleware 的行為矩陣：
  - loopback（127.0.0.1）× 單機/伺服器模式 → 永遠 200
  - 遠端（192.168.1.50）× 單機模式          → 403
  - 遠端（192.168.1.50）× 伺服器模式        → 200
  - 遠端 + XFF: 127.0.0.1 + 單機模式        → 仍 403（不信 XFF）
  - client=None（ASGI 邊界）× 單機模式      → 403（fail-closed）

另測試 _lan_access_allowed 純函式的直接行為。

Mirror 慣例：monkeypatch web.app.load_config，使用 TestClient client= kwarg
指定 TCP 對端位址（Starlette 預設 client=("testclient", 50000) 非 loopback）。
"""
from fastapi.testclient import TestClient
from web.app import app, _lan_access_allowed

REMOTE_CLIENT = ("192.168.1.50", 12345)
LOOPBACK_CLIENT = ("127.0.0.1", 12345)

# ── 純函式單元測試 ──────────────────────────────────────────────────────────


class TestLanAccessAllowed:
    """_lan_access_allowed 直接行為覆蓋（無 I/O）"""

    def test_loopback_v4_always_allowed(self):
        assert _lan_access_allowed("127.0.0.1", False) is True

    def test_loopback_v4_server_mode_also_allowed(self):
        assert _lan_access_allowed("127.0.0.1", True) is True

    def test_loopback_v6_always_allowed(self):
        assert _lan_access_allowed("::1", False) is True

    def test_remote_single_mode_blocked(self):
        assert _lan_access_allowed("192.168.1.5", False) is False

    def test_remote_server_mode_allowed(self):
        assert _lan_access_allowed("192.168.1.5", True) is True

    def test_none_host_single_mode_fail_closed(self):
        """client=None → host=None → 非 loopback → 單機模式應 fail-closed（False）"""
        assert _lan_access_allowed(None, False) is False

    def test_none_host_server_mode_allowed(self):
        """client=None + server_mode=True → 放行"""
        assert _lan_access_allowed(None, True) is True


# ── 中介層矩陣測試 ─────────────────────────────────────────────────────────


def _single_mode_patch(monkeypatch):
    """將 load_config 固定回傳 server_mode=False（單機模式）"""
    monkeypatch.setattr(
        "web.app.load_config",
        lambda: {"general": {"server_mode": False}},
    )


def _server_mode_patch(monkeypatch):
    """將 load_config 固定回傳 server_mode=True（伺服器模式）"""
    monkeypatch.setattr(
        "web.app.load_config",
        lambda: {"general": {"server_mode": True}},
    )


class TestLanAccessGateMiddleware:
    """
    lan_access_gate middleware 矩陣。
    使用 /api/health（無 side-effect、無 template，最單純的 200 路由）。
    """

    def test_loopback_single_mode_200(self, monkeypatch):
        """loopback + 單機模式 → 200"""
        _single_mode_patch(monkeypatch)
        c = TestClient(app, client=LOOPBACK_CLIENT)
        r = c.get("/api/health")
        assert r.status_code == 200

    def test_loopback_server_mode_200(self, monkeypatch):
        """loopback + 伺服器模式 → 200"""
        _server_mode_patch(monkeypatch)
        c = TestClient(app, client=LOOPBACK_CLIENT)
        r = c.get("/api/health")
        assert r.status_code == 200

    def test_remote_single_mode_403(self, monkeypatch):
        """遠端 + 單機模式 → 403"""
        _single_mode_patch(monkeypatch)
        c = TestClient(app, client=REMOTE_CLIENT)
        r = c.get("/api/health")
        assert r.status_code == 403

    def test_remote_server_mode_200(self, monkeypatch):
        """遠端 + 伺服器模式 → 200"""
        _server_mode_patch(monkeypatch)
        c = TestClient(app, client=REMOTE_CLIENT)
        r = c.get("/api/health")
        assert r.status_code == 200

    def test_remote_xff_spoofed_still_403(self, monkeypatch):
        """遠端偽造 X-Forwarded-For: 127.0.0.1 + 單機模式 → 仍 403（不信 XFF）"""
        _single_mode_patch(monkeypatch)
        c = TestClient(app, client=REMOTE_CLIENT)
        r = c.get("/api/health", headers={"X-Forwarded-For": "127.0.0.1"})
        assert r.status_code == 403

    def test_remote_xff_localhost_still_403(self, monkeypatch):
        """遠端偽造 X-Forwarded-For: localhost + 單機模式 → 仍 403"""
        _single_mode_patch(monkeypatch)
        c = TestClient(app, client=REMOTE_CLIENT)
        r = c.get("/api/health", headers={"X-Forwarded-For": "localhost"})
        assert r.status_code == 403

    def test_server_mode_toggle_takes_effect(self, monkeypatch):
        """
        middleware per-request 讀 config：同一個 TestClient 實例下，翻轉 server_mode
        後下次 request 即生效（以 load_config 呼叫計數驗證動態路徑）。
        """
        call_count = {"n": 0}
        server_mode_val = {"v": False}

        def _dynamic_load_config():
            call_count["n"] += 1
            return {"general": {"server_mode": server_mode_val["v"]}}

        monkeypatch.setattr("web.app.load_config", _dynamic_load_config)

        c = TestClient(app, client=REMOTE_CLIENT)

        # 第一次：單機模式 → 403，且 load_config 有被呼叫（非 loopback 路徑）
        r1 = c.get("/api/health")
        assert r1.status_code == 403
        assert call_count["n"] >= 1

        # 翻轉 server_mode → 伺服器模式 → 200
        server_mode_val["v"] = True
        r2 = c.get("/api/health")
        assert r2.status_code == 200

    def test_loopback_does_not_call_load_config(self, monkeypatch):
        """loopback 短路：middleware 不讀 config（零 I/O 成本）。

        /api/health 本身不讀 config，故 loopback request 後 load_config 呼叫數應為 0；
        若 loopback 短路被移除（落到 config 讀取分支），count 會 ≥1 → 此測 RED。
        """
        call_count = {"n": 0}

        def _counting_load_config():
            call_count["n"] += 1
            return {"general": {"server_mode": False}}

        monkeypatch.setattr("web.app.load_config", _counting_load_config)

        c = TestClient(app, client=LOOPBACK_CLIENT)
        r = c.get("/api/health")
        assert r.status_code == 200
        assert call_count["n"] == 0, (
            f"loopback 短路應跳過 load_config，實際呼叫 {call_count['n']} 次"
        )
