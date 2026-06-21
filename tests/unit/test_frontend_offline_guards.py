"""Frontend offline-reliability static guards — feature/79.

CI-effective protection: CI runs pytest only (not eslint/stylelint).
These pytest guards are the load-bearing backstop for things eslint's JS-AST
cannot reach — HTML template markup strings (CDN hosts, beacon endpoints,
<script> tag attributes, colliding id="" attributes) and one cross-module
router↔capabilities contract. eslint `no-restricted-syntax` walks the JS AST
and structurally cannot touch `.html` markup; it can also only *ban* a JS
pattern, never *require* one to exist. Per CLAUDE.md lint-routing rule +
pre-merge.md "eslint structurally cannot cover" exception (plan-79 CD14 / §3),
these stay in pytest.

All guards are pure static analysis (pathlib + re). No app needed
(except guard 4, which imports a router module's constant).
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_HTML = REPO_ROOT / "web" / "templates" / "base.html"
LIGHTBOX_JS = REPO_ROOT / "web" / "static" / "js" / "pages" / "showcase" / "state-lightbox.js"
SCANNER_HTML = REPO_ROOT / "web" / "templates" / "scanner.html"


# ─── Guard 1 ─────────────────────────────────────────────────────────────────

def test_base_html_references_no_cdn_host():
    """base.html must NOT reference the jsDelivr CDN host (T1 vendored everything).

    PERMANENT regression fingerprint (offline-reliability contract — NOT transient):
    feature/79-T1 swapped all 13 third-party <script>/<link> deps from
    cdn.jsdelivr.net into local /static/vendor so the UI stays fully functional
    when offline or when the CDN is unreachable. If any dep regresses back to the
    CDN host, the page silently breaks without network — this guard fails closed.
    """
    content = BASE_HTML.read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in content, (
        "base.html references cdn.jsdelivr.net — feature/79-T1 vendored all "
        "third-party deps into /static/vendor for offline reliability. A CDN "
        "reference reintroduces a network dependency that breaks the UI offline."
    )


# ─── Guard 2 ─────────────────────────────────────────────────────────────────

def test_beacon_targets_relative_client_log_only():
    """The diagnostics beacon <script> must POST only to the relative /api/client-log.

    Binds to the beacon block (the <script> containing 'client-log'), located via
    re.DOTALL — NOT hardcoded line numbers — so base.html edits don't make this
    guard brittle. Asserts BOTH the relative endpoint is present AND no absolute
    http(s):// beacon URL exists (zero-egress contract C3): asserting presence
    alone could be satisfied by an empty/wrong block; the negative assertion is
    what enforces "never egress to a remote host".
    """
    content = BASE_HTML.read_text(encoding="utf-8")
    m = re.search(r"<script\b[^>]*>(?:(?!</script>).)*?client-log.*?</script>",
                  content, re.DOTALL)
    assert m is not None, (
        "base.html: could not locate the beacon <script> block containing 'client-log'"
    )
    block = m.group(0)
    assert "/api/client-log" in block, (
        "beacon <script> must POST to the relative '/api/client-log' endpoint"
    )
    assert re.search(r"https?://[^\"'\s]*client-log", block) is None, (
        "beacon <script> must NOT use an absolute http(s):// client-log URL "
        "(zero-egress C3 — diagnostics stay on the local server only)"
    )


# ─── Guard 3 ─────────────────────────────────────────────────────────────────

def test_beacon_script_is_parser_blocking_classic():
    """The FIRST <script> in <head> (the beacon) must be a parser-blocking classic script.

    Binds to the first <script ...> opening tag inside <head> (the beacon must be
    first) and asserts its opening tag contains NONE of type="module" / defer /
    async. A classic parser-blocking script runs before any CDN/module/Alpine code,
    so the window 'error' listener is installed before later scripts can fail — if
    the beacon became deferred/async/module it would miss early boot errors.
    """
    content = BASE_HTML.read_text(encoding="utf-8")
    head_m = re.search(r"<head\b[^>]*>", content, re.IGNORECASE)
    assert head_m is not None, "base.html: <head> not found"
    after_head = content[head_m.end():]
    script_m = re.search(r"<script\b[^>]*>", after_head, re.IGNORECASE)
    assert script_m is not None, "base.html: no <script> found inside <head>"
    open_tag = script_m.group(0)
    assert 'type="module"' not in open_tag, (
        f"first <head> <script> (beacon) must NOT be type=\"module\": {open_tag!r}"
    )
    assert "defer" not in open_tag, (
        f"first <head> <script> (beacon) must NOT be defer: {open_tag!r}"
    )
    assert "async" not in open_tag, (
        f"first <head> <script> (beacon) must NOT be async: {open_tag!r}"
    )


# ─── Guard 4 ─────────────────────────────────────────────────────────────────

def test_client_log_excluded_from_capabilities():
    """/api/client-log must NOT be disclosed in the capabilities tool list (CD13).

    Cross-module router↔capabilities contract: /api/client-log is a pure diagnostic
    sink (write-only debug.log), not an AI-usable capability, so it must never appear
    in the disclosed _TOOLS list.
    """
    from web.routers.capabilities import _TOOLS

    assert all(t.get("path") != "/api/client-log" for t in _TOOLS), (
        "/api/client-log must NOT be in capabilities _TOOLS — it is a pure "
        "diagnostic sink, not a disclosed capability (CD13)."
    )


# ─── Guard 5 ─────────────────────────────────────────────────────────────────

def test_lightbox_keydown_guards_delete_modal():
    """handleKeydown must keep the C-1 delete-modal guard (deleteVideoModalOpen + cancelDeleteVideo).

    eslint can BAN the presence of a JS pattern but cannot REQUIRE one to exist → pytest.
    Isolates the handleKeydown(e) body and asserts it references BOTH
    deleteVideoModalOpen AND cancelDeleteVideo: this is the guard that makes Esc close
    only the delete-confirm modal (not the lightbox) and stops arrow keys from
    navigating to the next video while the modal is open. If the guard is deleted,
    Esc closes the lightbox underneath and arrows leak through.
    """
    content = LIGHTBOX_JS.read_text(encoding="utf-8")
    body_m = re.search(r"handleKeydown\s*\(\s*e\s*\)\s*\{(.*?)\n        \}",
                       content, re.DOTALL)
    scope = body_m.group(1) if body_m else content
    assert "deleteVideoModalOpen" in scope, (
        "state-lightbox.js handleKeydown must reference deleteVideoModalOpen (C-1 guard)"
    )
    assert "cancelDeleteVideo" in scope, (
        "state-lightbox.js handleKeydown must call cancelDeleteVideo (C-1 guard)"
    )


# ─── Guard 6 ─────────────────────────────────────────────────────────────────

def test_scanner_html_drops_self_colliding_ids():
    """scanner.html must NOT carry the 4 self-colliding id="" attributes (T4 Part B removed them).

    The id="" attributes duplicated x-text bindings, so Alpine's getElementById-based
    lookups collided with its own reactive bindings. T4 Part B removed the literal
    id="..." attributes (keeping only the x-text bindings); this guard fails closed if
    any of the four colliding ids is reintroduced.
    """
    content = SCANNER_HTML.read_text(encoding="utf-8")
    for colliding_id in (
        'id="outputPathDisplay"',
        'id="progressStatus"',
        'id="statTotal"',
        'id="statLastRun"',
    ):
        assert colliding_id not in content, (
            f"scanner.html still contains {colliding_id} — T4 Part B removed the "
            "self-colliding id attributes (they duplicated x-text bindings)."
        )
