# tests/integration/test_static_mime.py
"""
契約測試：/static mount 的 .js/.mjs/.css MIME 兩層強制（issue #66）。

精簡版 Windows registry 把 HKEY_CLASSES_ROOT\\.js Content Type 污染成 text/plain，
會讓 ES module 被瀏覽器 strict-MIME 拒收。NoCacheStaticFiles 在 super().file_response()
回傳後 post-construction 覆寫 Content-Type → 對 OS registry / guess_type 污染免疫。

依 CLAUDE.md「Lint 守衛規則」：此為「副檔名 → Content-Type 強制 API contract」→ pytest 正確。
"""
from fastapi.testclient import TestClient
from web.app import app


def test_js_forced_to_text_javascript(client):
    resp = client.get("/static/js/pages/scanner/main.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")


def test_css_forced_to_text_css(client):
    resp = client.get("/static/css/theme.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")


def test_js_forced_even_when_guess_type_polluted(monkeypatch):
    # Starlette 以 `from mimetypes import guess_type` 把函式 bind 進 starlette.responses
    # 自己的 namespace；要污染對 Starlette 生效須 patch starlette.responses.guess_type
    # （patch mimetypes.guess_type 會是 silent no-op，喪失對抗性）。
    monkeypatch.setattr(
        "starlette.responses.guess_type",
        lambda *a, **k: ("text/plain", None),
    )
    # Starlette 在 FileResponse 建構時（每 request）讀 guess_type，故 patch 後再建
    # 一個 TestClient GET 即可讓污染生效；主修仍會把 header 覆寫回正確值。
    polluted_client = TestClient(app)
    resp = polluted_client.get("/static/js/pages/scanner/main.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")


def test_css_forced_even_when_guess_type_polluted(monkeypatch):
    # 與 .js 對稱的對抗測試：healthy registry 下 test_css_forced_to_text_css 會 vacuously
    # 綠（OS 原生 .css→text/css），無法守住 _FORCED_CONTENT_TYPES['.css'] 被刪的回歸。
    # 此測試污染 guess_type→text/plain，證明 .css 的 override 同樣 load-bearing。
    monkeypatch.setattr(
        "starlette.responses.guess_type",
        lambda *a, **k: ("text/plain", None),
    )
    polluted_client = TestClient(app)
    resp = polluted_client.get("/static/css/theme.css")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/css")


def test_png_not_overridden(client):
    # C4 negative control：非清單副檔名不碰 Content-Type。
    resp = client.get("/static/favicon.png")
    assert resp.status_code == 200
    ct = resp.headers["content-type"]
    assert not ct.startswith("text/javascript")
    assert not ct.startswith("text/css")
    assert ct.startswith("image/png")


def test_cache_control_still_no_cache(client):
    # 非回歸：Content-Type 強制與 Cache-Control 並存。
    resp = client.get("/static/js/pages/scanner/main.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["content-type"].startswith("text/javascript")
