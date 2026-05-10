"""56c-T2 Lint Guard — GhostFly cropMode 邊界守衛

確保 right-half 裁切邏輯只能在 `web/static/js/shared/ghost-fly.js` 出現，
caller 不可自算右半邊 rect / 自設 objectPosition: right。

規則：
  1. `cropMode` 字串只能在 `shared/ghost-fly.js` 出現。
  2. `'right-half'` 字面量只能在 `shared/ghost-fly.js` 出現。
  3. showcase state 檔（state-similar.js / state-lightbox.js / state-base.js）
     禁止 `objectPosition.*right` regex 命中（caller 不得自算右半邊裁切）。
  4. 白名單 motion-lab namespace：`MotionLab.createCoverGhost` 是另一個函式，
     不接受 cropMode 也不會出現 cropMode / 'right-half'，自然不會被命中；
     此規則僅以註解備忘，不需特別 skip。
"""
from pathlib import Path

import pytest

JS_ROOT = Path(__file__).parent.parent.parent / "web" / "static" / "js"
GHOST_FLY_JS = JS_ROOT / "shared" / "ghost-fly.js"

# 規則 3 目標檔案：caller 範圍（state-similar.js 在 57c-T4+T5 rename 後）
SHOWCASE_DIR = JS_ROOT / "pages" / "showcase"
CALLER_SCOPE_FILES = [
    SHOWCASE_DIR / "state-similar.js",  # 57c-T4+T5 showcase similar mode host
    SHOWCASE_DIR / "state-lightbox.js",
    SHOWCASE_DIR / "state-base.js",
]

# 合法 caller 白名單：這些檔案可持有 cropMode / 'right-half' / objectPosition: right，
# 因為它們透過 GhostFly API 呼叫而非自行實作 strip 邏輯。
# 新增條件見 TASK-56c-T4fix8 Maintainer Note。
CROPMODE_CALLER_WHITELIST = {
    SHOWCASE_DIR / "state-similar.js",  # 57c-T4+T5: createCoverGhost cropMode caller (via GhostFly API)
}

import re


class TestGhostFlyCropModeBoundary:
    """56c-T2 lint guard — cropMode / right-half 裁切邏輯封裝在 ghost-fly.js 內"""

    def _all_js_files(self):
        """遞迴 glob 所有 JS 檔。"""
        return list(JS_ROOT.rglob("*.js"))

    def test_ghost_fly_js_exists(self):
        """前置條件：shared/ghost-fly.js 必須存在。"""
        assert GHOST_FLY_JS.is_file(), f"missing: {GHOST_FLY_JS}"

    def test_cropmode_string_only_in_ghost_fly(self):
        """規則 1：`cropMode` 只能在 shared/ghost-fly.js 出現。"""
        offenders = []
        for js_path in self._all_js_files():
            if js_path.resolve() == GHOST_FLY_JS.resolve():
                continue
            if js_path.resolve() in {p.resolve() for p in CROPMODE_CALLER_WHITELIST}:
                continue
            content = js_path.read_text(encoding="utf-8")
            if "cropMode" in content:
                offenders.append(str(js_path.relative_to(JS_ROOT)))
        assert not offenders, (
            "`cropMode` may only appear in shared/ghost-fly.js. "
            f"Leaked into: {offenders}"
        )

    def test_right_half_literal_only_in_ghost_fly(self):
        """規則 2：`'right-half'` 字面量只能在 shared/ghost-fly.js 出現。"""
        offenders = []
        # 同時檢查單引號與雙引號版本
        patterns = ("'right-half'", '"right-half"')
        for js_path in self._all_js_files():
            if js_path.resolve() == GHOST_FLY_JS.resolve():
                continue
            if js_path.resolve() in {p.resolve() for p in CROPMODE_CALLER_WHITELIST}:
                continue
            content = js_path.read_text(encoding="utf-8")
            if any(p in content for p in patterns):
                offenders.append(str(js_path.relative_to(JS_ROOT)))
        assert not offenders, (
            "`'right-half'` literal may only appear in shared/ghost-fly.js. "
            f"Leaked into: {offenders}"
        )

    @pytest.mark.parametrize("caller_path", CALLER_SCOPE_FILES, ids=lambda p: p.name)
    def test_caller_scope_no_object_position_right(self, caller_path):
        """規則 3：showcase state 檔不得用 objectPosition: right 自算右半邊裁切。

        若 caller 需要 right-half 視覺，必須走 `createCoverGhost(..., { cropMode: 'right-half' })`，
        不可自設 inline `objectPosition = 'right ...'`。
        """
        if caller_path.resolve() in {p.resolve() for p in CROPMODE_CALLER_WHITELIST}:
            pytest.skip(
                f"{caller_path.name} is a whitelisted cropMode caller "
                f"(uses GhostFly API, not raw strip logic). "
                f"See CROPMODE_CALLER_WHITELIST in this file."
            )

        if not caller_path.is_file():
            pytest.skip(f"{caller_path.name} not yet created (56c-T5 will add)")

        content = caller_path.read_text(encoding="utf-8")
        # 禁止 `objectPosition` 後跟任何字元再出現 `right`（涵蓋 = 'right center' / : "right top" etc）
        matches = re.findall(r"objectPosition.*right", content)
        assert not matches, (
            f"{caller_path.name} must not set `objectPosition: right ...` directly. "
            f"Use `createCoverGhost(src, rect, {{ cropMode: 'right-half' }})` instead. "
            f"Matches: {matches}"
        )
