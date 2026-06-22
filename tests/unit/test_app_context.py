"""
tests/unit/test_app_context.py — web.app helper unit tests（feature/82 T4）

_is_windows_desktop() 四象限真值表測試：
  OPENAVER_STANDALONE env × sys.platform 組合

Note: we patch the `sys` module reference *inside* web.app (i.e. `web.app.sys`),
not the global `sys.platform`, to avoid triggering win32-only code paths in uvicorn
that reference `signal.SIGBREAK` during import-time checks.
"""
import pytest


@pytest.mark.parametrize("standalone_val,platform_val,expected", [
    ("1",   "win32",  True),   # 桌面 Windows → True
    ("1",   "linux",  False),  # standalone 但非 win32 → False
    ("0",   "win32",  False),  # win32 但非 standalone → False
    (None,  "win32",  False),  # env 未設 + win32 → False
])
def test_is_windows_desktop_truth_table(standalone_val, platform_val, expected, monkeypatch):
    """四象限：OPENAVER_STANDALONE × sys.platform（透過 web.app 模組內的 sys 引用 patch）"""
    import web.app as _web_app

    # Clear / set env var
    monkeypatch.delenv("OPENAVER_STANDALONE", raising=False)
    if standalone_val is not None:
        monkeypatch.setenv("OPENAVER_STANDALONE", standalone_val)

    # Patch sys.platform only within web.app's reference to avoid uvicorn win32 guards
    monkeypatch.setattr(_web_app.sys, "platform", platform_val)

    assert _web_app._is_windows_desktop() is expected, (
        f"standalone_val={standalone_val!r}, platform={platform_val!r} → expected {expected}"
    )
