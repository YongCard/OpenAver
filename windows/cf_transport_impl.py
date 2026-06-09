# windows/cf_transport_impl.py
"""
PyWebView implementation of the CfTransport Protocol.

Provides:
  _wv_fetch(window, url, timeout)  — module-level helper (column 0)
  PyWebViewCfTransport             — CfTransport implementation

Design:
  - _wv_fetch uses queue.Queue(1) + evaluate_js(callback) bridge: non-blocking
    from the GUI thread's perspective.  Only the FastAPI threadpool worker
    blocks on result_q.get(timeout=...).
  - fetch() unpacks the tuple (C1 contract) and detects CF challenge.
  - begin_solve() is non-blocking: show → load_url → set over18 cookie.
  - is_ready() is a fast non-blocking check: reads title + head HTML slice.
  - No blocking wait loop (C2: POC wait_for_ready is NOT ported here).

Import note:
  standalone.py uses sibling import: from cf_transport_impl import PyWebViewCfTransport
  (windows/ has no __init__.py; WINDOWS_DIR is already in sys.path via standalone.py L22-24)
"""
from __future__ import annotations

import json
import queue
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import webview

from bs4 import BeautifulSoup

from core.cf_transport import CfChallengeRequired, CfTransport
from core.scrapers.javlibrary import (
    JAVLIBRARY_ORIGIN,
    _is_age_gate,
    _is_cf_challenge,
)
from core.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# Module-level helper (column 0)
# ──────────────────────────────────────────────────────────────

def _wv_fetch(window: webview.Window, url: str, timeout: float = 40.0) -> tuple[str, int, str]:
    """
    Execute fetch(url) inside the WebView (same-origin, credentials:include)
    and return (final_url, http_status, html_text).

    Mechanism:
      evaluate_js(script, callback) is non-blocking + Promise-aware.
      We bridge via queue.Queue(1): the callback puts the result in,
      the worker thread blocking-gets with timeout.

    Raises:
        TimeoutError:   callback not called within timeout seconds.
        RuntimeError:   JS-layer error (e.g. network failure).
    """
    result_q: queue.Queue[dict] = queue.Queue(maxsize=1)

    js_url = json.dumps(url)
    js_code = (
        f"(async()=>{{"
        f"  try {{"
        f"    const r = await fetch({js_url}, {{credentials:'include'}});"
        f"    const html = await r.text();"
        f"    return {{finalUrl: r.url, status: r.status, html: html}};"
        f"  }} catch(e) {{"
        f"    return {{finalUrl: {js_url}, status: 0, html: '', error: e.toString()}};"
        f"  }}"
        f"}})()"
    )

    def _cb(result: Any) -> None:
        """pywebview Promise callback — called in pywebview's internal thread."""
        try:
            result_q.put_nowait(result if isinstance(result, dict) else {})
        except queue.Full:
            pass  # guard against duplicate callbacks (extremely rare)

    window.evaluate_js(js_code, callback=_cb)

    try:
        data = result_q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"_wv_fetch timed out after {timeout}s for {url}")

    final_url = data.get("finalUrl") or url
    status = data.get("status") or 0
    html = data.get("html") or ""

    if js_err := data.get("error"):
        raise RuntimeError(f"JS fetch error: {js_err}")

    return final_url, status, html


# ──────────────────────────────────────────────────────────────
# Transport implementation
# ──────────────────────────────────────────────────────────────

class PyWebViewCfTransport:
    """
    CfTransport implementation backed by a dedicated hidden PyWebView window.

    The window stays hidden at rest.  begin_solve() shows it so the user can
    complete the CF challenge + age gate.  is_ready() polls state without
    blocking; when ready it auto-hides the window.
    """

    def __init__(self, jl_window: webview.Window) -> None:
        self._win = jl_window

    # ------------------------------------------------------------------
    # CfTransport Protocol
    # ------------------------------------------------------------------

    def fetch(self, url: str, cache_key: str = 'javlibrary') -> str:
        """
        Fetch url via same-origin WebView request and return HTML string.

        Raises CfChallengeRequired if the returned page is a CF challenge.
        Does NOT check age gate (age gate is only checked in is_ready()).
        """
        final_url, status, html = _wv_fetch(self._win, url)  # C1: unpack correctly

        # Detect CF challenge via title
        soup = BeautifulSoup(html, 'html.parser')
        title_tag = soup.title
        title = title_tag.string if title_tag else ""
        title = title or ""

        if _is_cf_challenge(title, html):
            raise CfChallengeRequired(f'CF challenge detected for {url}')

        return html

    def begin_solve(self, origin_url: str, cache_key: str = 'javlibrary') -> None:
        """
        Non-blocking: show the window, navigate to origin_url, set over18 cookie.
        Returns immediately — does NOT wait for the user to solve the challenge.
        """
        self._win.show()
        self._win.load_url(origin_url)
        self._win.evaluate_js(
            "document.cookie='over18=1; path=/; domain=.javlibrary.com';"
        )

    def is_ready(self, cache_key: str = 'javlibrary') -> bool:
        """
        Non-blocking fast check: has the user passed the CF challenge?

        Only checks for CF challenge (title / hidden field markers).
        Age gate is handled exclusively via the over18=1 cookie set on every
        call — post-CF pages will not re-show the age gate if the cookie is
        present (same design as POC scrape_b / javm).

        Sets over18 cookie on every call (idempotent, prevents age gate re-appear).
        When first ready, auto-hides the window.
        """
        # 1. Set over18 cookie every time (idempotent)
        self._win.evaluate_js(
            "document.cookie='over18=1; path=/; domain=.javlibrary.com';"
        )

        # 2. Read title (sync form: no callback → evaluate_js returns synchronously)
        title = self._win.evaluate_js("document.title") or ""

        # 3. Read head HTML (truncated to avoid serialising large pages)
        head = self._win.evaluate_js(
            "document.documentElement.outerHTML.slice(0, 4000)"
        ) or ""

        # 4. Evaluate readiness.
        # Positive "loaded-page" guard: real JavLibrary pages always have a
        #   non-empty <title>.  An empty title means the page is still
        #   navigating (evaluate_js returned None/""  before the DOM is ready),
        #   so we keep the window open and let the poll loop retry rather than
        #   misreporting ready and hiding the window prematurely.
        # CF challenge: never ready, user must wait for Turnstile.
        # Age-gate (agreeBtn): not ready, window stays visible so user can click
        #   the agree button. over18 cookie (step 1) prevents re-appearance in
        #   most cases; if the interstitial still shows, _is_age_gate (agreeBtn-
        #   only, narrow) catches it and keeps the window open. Normal content
        #   pages that contain "利用規約"/"18歳"/"over18" in the footer are NOT
        #   caught because the narrowed _is_age_gate only matches agreeBtn.
        ready = bool(title.strip()) and not _is_cf_challenge(title, head) and not _is_age_gate(head)

        # 5. Auto-hide when first ready
        if ready:
            self._win.hide()

        return ready
