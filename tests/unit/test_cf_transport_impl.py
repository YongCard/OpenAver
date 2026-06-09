# tests/unit/test_cf_transport_impl.py
"""
TDD-lite tests for windows/cf_transport_impl.py

Import strategy: sys.path.insert + sibling import (matches standalone.py runtime)
Structural guards: grep assertions for core→windows and launcher purity.
"""
import subprocess
import sys
import pathlib
import queue

import pytest

# ──────────────────────────────────────────────────────────────
# sys.path setup — mirrors standalone.py L22-24
# ──────────────────────────────────────────────────────────────
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
WINDOWS_DIR = str(REPO_ROOT / 'windows')
if WINDOWS_DIR not in sys.path:
    sys.path.insert(0, WINDOWS_DIR)

import cf_transport_impl  # sibling import, same as standalone.py runtime
from cf_transport_impl import PyWebViewCfTransport, _wv_fetch
from core.cf_transport import CfChallengeRequired


# ──────────────────────────────────────────────────────────────
# FakeWindow
# ──────────────────────────────────────────────────────────────
class FakeWindow:
    """
    Mock for webview.Window.

    evaluate_js behaviour:
      - With callback: call callback(self._eval_callback_result['default'])
        unless self._never_callback is True (for TimeoutError tests).
      - Without callback: return self._eval_results.get(code[:20], None).

    Records all method calls in self.calls as (method, *args).
    """

    def __init__(self):
        self.calls: list[tuple] = []
        # Sync return values, keyed by first 20 chars of JS code
        self._eval_results: dict = {}
        # Dict result passed to callback; default = clean fetch result
        self._eval_callback_result: dict = {
            'default': {'finalUrl': 'https://www.javlibrary.com/ja/', 'status': 200, 'html': '<html><title>JavLibrary</title></html>'}
        }
        # Set True to make evaluate_js never call the callback (→ TimeoutError)
        self._never_callback: bool = False

    def show(self):
        self.calls.append(('show',))

    def hide(self):
        self.calls.append(('hide',))

    def load_url(self, url):
        self.calls.append(('load_url', url))

    def evaluate_js(self, code, callback=None):
        self.calls.append(('evaluate_js', code, callback))
        if callback is not None:
            if not self._never_callback:
                result = self._eval_callback_result.get('default', {})
                callback(result)
            return None
        else:
            # Sync return (is_ready path)
            return self._eval_results.get(code[:20], None)


# ──────────────────────────────────────────────────────────────
# Tests: _wv_fetch
# ──────────────────────────────────────────────────────────────

class TestWvFetch:
    def test_normal_returns_tuple(self):
        """Normal callback → returns (final_url, status, html) tuple."""
        win = FakeWindow()
        win._eval_callback_result['default'] = {
            'finalUrl': 'https://www.javlibrary.com/ja/',
            'status': 200,
            'html': '<html>ok</html>',
        }
        result = _wv_fetch(win, 'https://www.javlibrary.com/ja/')
        assert isinstance(result, tuple)
        assert len(result) == 3
        final_url, status, html = result
        assert final_url == 'https://www.javlibrary.com/ja/'
        assert status == 200
        assert html == '<html>ok</html>'

    def test_js_error_raises_runtime_error(self):
        """Callback with error key → raises RuntimeError."""
        win = FakeWindow()
        win._eval_callback_result['default'] = {
            'finalUrl': 'https://www.javlibrary.com/ja/',
            'status': 0,
            'html': '',
            'error': 'net::ERR_FAILED',
        }
        with pytest.raises(RuntimeError, match='JS fetch error'):
            _wv_fetch(win, 'https://www.javlibrary.com/ja/')

    def test_timeout_raises_timeout_error(self):
        """Callback never called → raises TimeoutError after short timeout."""
        win = FakeWindow()
        win._never_callback = True
        with pytest.raises(TimeoutError):
            _wv_fetch(win, 'https://www.javlibrary.com/ja/', timeout=0.05)

    def test_non_dict_callback_degrades_gracefully(self):
        """Non-dict passed to callback → put_nowait({}) → returns ('', 0, '')."""
        win = FakeWindow()

        # Override evaluate_js to pass a non-dict to callback
        original_evaluate_js = win.evaluate_js
        def patched_evaluate_js(code, callback=None):
            win.calls.append(('evaluate_js', code, callback))
            if callback is not None:
                callback("not-a-dict")
            return None
        win.evaluate_js = patched_evaluate_js

        result = _wv_fetch(win, 'https://www.javlibrary.com/ja/')
        assert isinstance(result, tuple)
        final_url, status, html = result
        assert final_url == 'https://www.javlibrary.com/ja/'  # falls back to input url
        assert status == 0
        assert html == ''


# ──────────────────────────────────────────────────────────────
# Tests: fetch()
# ──────────────────────────────────────────────────────────────

NORMAL_HTML = '<html><head><title>JavLibrary</title></head><body>content</body></html>'
CF_HTML = '<html><head><title>Just a moment...</title></head><body>cf stuff</body></html>'


class TestFetch:
    def test_normal_html_returns_str_not_tuple(self):
        """Normal page → returns str (C1: _wv_fetch unpacked correctly)."""
        win = FakeWindow()
        win._eval_callback_result['default'] = {
            'finalUrl': 'https://www.javlibrary.com/ja/',
            'status': 200,
            'html': NORMAL_HTML,
        }
        transport = PyWebViewCfTransport(win)
        result = transport.fetch('https://www.javlibrary.com/ja/')
        assert isinstance(result, str), "fetch() must return str, not tuple (C1 check)"
        assert result == NORMAL_HTML

    def test_cf_challenge_title_raises(self):
        """CF challenge title → raises CfChallengeRequired."""
        win = FakeWindow()
        win._eval_callback_result['default'] = {
            'finalUrl': 'https://www.javlibrary.com/ja/',
            'status': 200,
            'html': CF_HTML,
        }
        transport = PyWebViewCfTransport(win)
        with pytest.raises(CfChallengeRequired):
            transport.fetch('https://www.javlibrary.com/ja/')

    def test_timeout_bubbles_through_fetch(self, monkeypatch):
        """_wv_fetch TimeoutError bubbles out of fetch() unmodified."""
        def fake_wv_fetch(window, url, timeout=40.0):
            raise TimeoutError(f"_wv_fetch timed out after {timeout}s for {url}")

        monkeypatch.setattr(cf_transport_impl, '_wv_fetch', fake_wv_fetch)
        win = FakeWindow()
        transport = PyWebViewCfTransport(win)
        with pytest.raises(TimeoutError):
            transport.fetch('https://www.javlibrary.com/ja/')

    def test_fetch_does_not_raise_for_age_gate_page(self):
        """age gate check is NOT in fetch() — only in is_ready(). fetch returns html."""
        age_gate_html = '<html><head><title>JavLibrary</title></head><body>利用規約 over18</body></html>'
        win = FakeWindow()
        win._eval_callback_result['default'] = {
            'finalUrl': 'https://www.javlibrary.com/ja/',
            'status': 200,
            'html': age_gate_html,
        }
        transport = PyWebViewCfTransport(win)
        # Should NOT raise — age gate is only in is_ready()
        result = transport.fetch('https://www.javlibrary.com/ja/')
        assert isinstance(result, str)


# ──────────────────────────────────────────────────────────────
# Tests: begin_solve()
# ──────────────────────────────────────────────────────────────

class TestBeginSolve:
    def test_calls_show_load_url_and_over18_cookie(self):
        """begin_solve → show() + load_url(origin) + evaluate_js(over18 cookie)."""
        win = FakeWindow()
        transport = PyWebViewCfTransport(win)
        origin = 'https://www.javlibrary.com/ja/'
        transport.begin_solve(origin)

        method_calls = [c[0] for c in win.calls]
        assert 'show' in method_calls
        assert 'load_url' in method_calls
        assert 'evaluate_js' in method_calls

        # Check order: show before load_url before evaluate_js
        show_idx = next(i for i, c in enumerate(win.calls) if c[0] == 'show')
        load_idx = next(i for i, c in enumerate(win.calls) if c[0] == 'load_url')
        eval_idx = next(i for i, c in enumerate(win.calls) if c[0] == 'evaluate_js')
        assert show_idx < load_idx < eval_idx

        # load_url gets the origin
        load_call = next(c for c in win.calls if c[0] == 'load_url')
        assert load_call[1] == origin

        # evaluate_js contains over18 cookie
        eval_call = next(c for c in win.calls if c[0] == 'evaluate_js')
        assert 'over18' in eval_call[1]

    def test_returns_immediately(self):
        """begin_solve() must return immediately (no blocking)."""
        import time
        win = FakeWindow()
        transport = PyWebViewCfTransport(win)
        start = time.monotonic()
        transport.begin_solve('https://www.javlibrary.com/ja/')
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"begin_solve blocked for {elapsed:.2f}s (should return immediately)"


# ──────────────────────────────────────────────────────────────
# Tests: is_ready()
# ──────────────────────────────────────────────────────────────

READY_TITLE = 'JavLibrary'
READY_HEAD = '<html><head><title>JavLibrary</title></head>'
CF_TITLE = 'Just a moment'
CF_HEAD = '<html><head><title>Just a moment</title></head><body>cf</body>'
AGE_GATE_TITLE = 'JavLibrary'
# 真實同意閘（含 agreeBtn）→ is_ready 應回 False
AGE_GATE_HEAD = '<html><head><title>JavLibrary</title></head><body><button id="agreeBtn">同意</button></body>'
# 正常內容頁 footer（含 利用規約/18歳/over18 但無 agreeBtn）→ is_ready 應回 True
CONTENT_FOOTER_HEAD = '<html><head><title>JavLibrary</title></head><body>利用規約 18歳 over18</body>'


class FakeWindowIsReady(FakeWindow):
    """
    FakeWindow variant that maps specific document.title and
    document.documentElement.outerHTML.slice(0, 4000) JS codes
    to configured return values.
    """

    def __init__(self, title: str, head: str):
        super().__init__()
        self._title = title
        self._head = head

    def evaluate_js(self, code, callback=None):
        self.calls.append(('evaluate_js', code, callback))
        if callback is not None:
            # _wv_fetch path (not relevant for is_ready tests)
            if not self._never_callback:
                result = self._eval_callback_result.get('default', {})
                callback(result)
            return None
        # Sync returns (is_ready path)
        if 'document.title' in code:
            return self._title
        if 'outerHTML' in code:
            return self._head
        return None


class TestIsReady:
    def test_ready_page_returns_true_and_hides(self):
        """Ready page → True, window.hide() called."""
        win = FakeWindowIsReady(READY_TITLE, READY_HEAD)
        transport = PyWebViewCfTransport(win)
        result = transport.is_ready()
        assert result is True
        hide_calls = [c for c in win.calls if c[0] == 'hide']
        assert len(hide_calls) == 1, "hide() should be called once when ready"

    def test_cf_challenge_returns_false_no_hide(self):
        """CF challenge page → False, no hide()."""
        win = FakeWindowIsReady(CF_TITLE, CF_HEAD)
        transport = PyWebViewCfTransport(win)
        result = transport.is_ready()
        assert result is False
        hide_calls = [c for c in win.calls if c[0] == 'hide']
        assert len(hide_calls) == 0, "hide() must NOT be called when not ready"

    def test_content_footer_terms_returns_true_and_hides(self):
        """
        FIX-1 回歸（收窄語義版）：正常內容頁 footer 含 利用規約/18歳/over18
        但**無 agreeBtn** → is_ready() 應回 True 並 hide 視窗。

        narrow _is_age_gate 只看 agreeBtn，footer 字不再誤判 False，
        用戶過了 CF 後不會卡在等待循環。
        """
        win = FakeWindowIsReady(AGE_GATE_TITLE, CONTENT_FOOTER_HEAD)
        transport = PyWebViewCfTransport(win)
        result = transport.is_ready()
        assert result is True, (
            "正常內容頁 footer 含 利用規約/18歳/over18 但無 agreeBtn，"
            "is_ready() 應回 True（footer 字不觸發 age-gate 偵測）"
        )
        hide_calls = [c for c in win.calls if c[0] == 'hide']
        assert len(hide_calls) == 1, "is_ready()=True 時應 hide() 視窗"

    def test_real_age_gate_with_agree_btn_returns_false_no_hide(self):
        """
        spec-70b §2.3 point 5 契約：真實同意閘（含 agreeBtn）→ is_ready() 回 False，
        視窗不 hide，讓用戶手動點同意按鈕。
        """
        win = FakeWindowIsReady(AGE_GATE_TITLE, AGE_GATE_HEAD)
        transport = PyWebViewCfTransport(win)
        result = transport.is_ready()
        assert result is False, (
            "真實 age-gate 同意頁（含 agreeBtn）is_ready() 應回 False，"
            "保留彈窗讓用戶點同意（spec-70b §2.3 point 5）"
        )
        hide_calls = [c for c in win.calls if c[0] == 'hide']
        assert len(hide_calls) == 0, "age-gate 時不應 hide()，彈窗須保留"

    def test_blank_or_navigating_page_returns_false_no_hide(self):
        """
        FIX-P2A: 導航中頁面（document.title 回空字串）→ is_ready() 回 False，
        視窗不 hide，避免慢載入時誤判 ready 並提前關閉彈窗。
        """
        # Empty title simulates page still navigating (evaluate_js returns "" or None)
        win = FakeWindowIsReady("", READY_HEAD)
        transport = PyWebViewCfTransport(win)
        result = transport.is_ready()
        assert result is False, (
            "導航中/空 title 頁面 is_ready() 應回 False（loaded-page guard）"
        )
        hide_calls = [c for c in win.calls if c[0] == 'hide']
        assert len(hide_calls) == 0, "空 title 時不應 hide()，視窗須保留"

    def test_every_call_sets_over18_cookie(self):
        """Every is_ready() call sets over18 cookie (idempotent)."""
        win = FakeWindowIsReady(READY_TITLE, READY_HEAD)
        transport = PyWebViewCfTransport(win)
        transport.is_ready()
        transport.is_ready()

        over18_calls = [
            c for c in win.calls
            if c[0] == 'evaluate_js' and 'over18' in c[1]
        ]
        assert len(over18_calls) >= 2, "over18 cookie should be set on every is_ready() call"

    def test_evaluate_js_none_degrades_gracefully(self):
        """evaluate_js returning None (window not ready) → no exception, returns bool."""
        class NoneWindow(FakeWindow):
            def evaluate_js(self, code, callback=None):
                self.calls.append(('evaluate_js', code, callback))
                if callback is not None:
                    return None
                return None  # Always None

        win = NoneWindow()
        transport = PyWebViewCfTransport(win)
        # Should not raise; returns a bool (True or False — both are acceptable)
        result = transport.is_ready()
        assert isinstance(result, bool), "is_ready() must return bool even when evaluate_js returns None"


# ──────────────────────────────────────────────────────────────
# Structural guards
# ──────────────────────────────────────────────────────────────

class TestStructuralGuards:
    def test_no_core_importing_windows(self):
        """core/ must not import from windows/ (one-way dependency)."""
        result = subprocess.run(
            ['grep', '-rn', r'import windows|from windows', str(REPO_ROOT / 'core')],
            capture_output=True, text=True
        )
        # grep returns exit code 0 if found, 1 if not found
        assert result.returncode == 1 or result.stdout.strip() == '', \
            f"FAIL: core/ imports windows:\n{result.stdout}"

    def test_launcher_has_no_transport_register(self):
        """launcher.py must not import cf_transport_impl or register_cf_transport."""
        launcher_path = str(REPO_ROOT / 'windows' / 'launcher.py')
        result = subprocess.run(
            ['grep', '-n', 'cf_transport_impl|register_cf_transport', launcher_path],
            capture_output=True, text=True
        )
        assert result.returncode == 1 or result.stdout.strip() == '', \
            f"FAIL: launcher.py references transport:\n{result.stdout}"

    def test_wv_fetch_is_module_level(self):
        """_wv_fetch must be defined at module level (column 0), not inside a class."""
        impl_path = REPO_ROOT / 'windows' / 'cf_transport_impl.py'
        result = subprocess.run(
            ['grep', '-n', '^def _wv_fetch', str(impl_path)],
            capture_output=True, text=True
        )
        assert result.returncode == 0 and result.stdout.strip() != '', \
            "FAIL: _wv_fetch not found at module level (column 0)"
