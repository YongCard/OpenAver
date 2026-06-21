"""CI workflow contract guards (TASK-78-T3 / feature/78).

防止 `.github/workflows/test.yml` 的 lint-frontend job 被靜默移除——lint 守衛
（eslint + stylelint + ruff）必須在 CI 跑才 load-bearing（翻 reference_ci_no_eslint
前提）。解析 YAML 後檢查語意，不依賴 attribute 順序。
"""

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "test.yml"
_REQUIREMENTS = _REPO_ROOT / "requirements-test.txt"
_REQUIREMENTS_RUNTIME = _REPO_ROOT / "requirements.txt"


@pytest.fixture(scope="module")
def workflow():
    assert _WORKFLOW.exists(), f"CI workflow 不存在：{_WORKFLOW}"
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _run_commands(job: dict) -> list[str]:
    """收集 job 內所有 step 的 `run` 字串（block scalar 多行也含）。"""
    return [step["run"] for step in job.get("steps", []) if isinstance(step, dict) and "run" in step]


def test_test_job_still_present(workflow):
    """既有 pytest job 不可被誤刪。"""
    jobs = workflow["jobs"]
    assert "test" in jobs, "既有 test (pytest) job 消失了"
    runs = " ".join(_run_commands(jobs["test"]))
    assert "pytest" in runs, "test job 不再跑 pytest"


def test_lint_frontend_job_exists(workflow):
    assert "lint-frontend" in workflow["jobs"], "CI 缺 lint-frontend job（lint 守衛未進 CI）"


def test_lint_frontend_runs_npm_lint_and_ruff(workflow):
    """lint job 必須跑 npm run lint（eslint+stylelint）與 ruff check。"""
    runs = " ".join(_run_commands(workflow["jobs"]["lint-frontend"]))
    assert "npm ci" in runs, "lint-frontend 未跑 npm ci（無可重現安裝）"
    assert "npm run lint" in runs, "lint-frontend 未跑 npm run lint（eslint+stylelint）"
    assert "ruff check" in runs, "lint-frontend 未跑 ruff check"


def test_lint_frontend_is_independent(workflow):
    """lint-frontend 與 test 平行（無 needs），任一紅各自擋 PR。"""
    assert "needs" not in workflow["jobs"]["lint-frontend"], "lint-frontend 不應依賴其他 job（平行擋 PR）"


def test_ci_ruff_pin_matches_requirements(workflow):
    """CI 的 ruff pin 必須與 requirements-test.txt 一致——

    pip `-c` constraints 無法消費含 extras 的 requirements-test.txt（uvicorn[standard]
    → pip 拒絕），故 ruff 版本必須在兩處各寫一次（CI step + requirements）。本守衛把
    這個「兩處重複」鎖成 single source of truth：任一漂移即 RED，防 upstream ruff
    自動升級或人為忘記同步在 repo 無改動下讓 CI 轉紅。
    """
    req_match = re.search(r"^ruff==(\S+)", _REQUIREMENTS.read_text(encoding="utf-8"), re.MULTILINE)
    assert req_match, "requirements-test.txt 缺 `ruff==<version>` 精確 pin（lint 是 PR gate，需鎖版本）"
    req_version = req_match.group(1).split("#")[0].strip()

    runs = " ".join(_run_commands(workflow["jobs"]["lint-frontend"]))
    ci_match = re.search(r"ruff==(\S+)", runs)
    assert ci_match, "CI lint-frontend 未以 `ruff==<version>` 精確 pin 安裝 ruff（避免版本漂移）"
    ci_version = ci_match.group(1).split("#")[0].strip()

    assert ci_version == req_version, (
        f"CI ruff pin（{ci_version}）與 requirements-test.txt（{req_version}）不一致；"
        "兩處必須同步（single source of truth）"
    )


# ── exact-pin 守衛（TASK-79-T6）─────────────────────────────────────────────
# 兩份 requirements 必須 exact `==` pin（綠色軟體可重現 build：同 git tag = 同 ZIP）。
# float floor（`>=` 等）→ pip 抓最新 → 不同機器/時間建出不同依賴樹。

def _requirement_lines(path: Path) -> list[str]:
    """回傳實際依賴行（去掉註解 + 空行 + pip 選項行如 `-r`；inline `# comment` 也剝除）。"""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.split("#", 1)[0].strip()
        if stripped and not stripped.startswith("-"):  # 跳過 `-r requirements.txt` 等 pip 選項
            lines.append(stripped)
    return lines


def test_requirements_are_exact_pinned():
    """requirements.txt / requirements-test.txt 每行都須 `==` exact-pin，
    不得含 `>=` / `<=` / `~=` / bare `>` / bare `<`（含 uvicorn[standard]==0.46.0）。"""
    loose = re.compile(r">=|<=|~=|>|<")
    for path in (_REQUIREMENTS_RUNTIME, _REQUIREMENTS):
        assert path.exists(), f"requirements 檔不存在：{path}"
        for line in _requirement_lines(path):
            assert "==" in line, (
                f"{path.name} 有未 exact-pin 的依賴行（缺 `==`）：{line!r}"
            )
            assert not loose.search(line), (
                f"{path.name} 有 loose 約束（>= / <= / ~= / > / <），須改 `==`：{line!r}"
            )


def test_requirements_test_inherits_runtime_pins():
    """requirements-test.txt 必須以 `-r requirements.txt` 繼承 runtime pinned 依賴。

    CI test job 只裝 requirements-test.txt（.github/workflows/test.yml）；若 runtime 依賴
    （fastapi/starlette/pydantic…）不在本檔，test 就跑在浮動的 transitive 版本上，與
    runtime/build 出貨版本不一致 → 破壞可重現性、且漏接 framework 簽名漂移
    （見 tests/integration/test_page_routes_render.py 守的 Starlette TemplateResponse 變更）。
    用 `-r` 繼承＝結構上保證 test 環境 = runtime pinned + 測試工具，杜絕「漏鏡像」漂移
    （Codex T6 修正：原本 starlette 只 pin 在 requirements.txt，test 檔遺漏）。"""
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    assert re.search(r"^\s*-r\s+requirements\.txt\s*$", text, re.MULTILINE), (
        "requirements-test.txt 必須含 `-r requirements.txt`（繼承 runtime pinned 依賴）；"
        "否則 CI test job 會跑在浮動的 runtime-only 依賴版本上"
    )


# ── mypy 殭屍防復活（TASK-78-T5）────────────────────────────────────────────
# mypy config + 依賴齊全但 CI 從不執行＝殭屍（spec D4）。已於 feature/78 刪除；
# 以下守衛防它被無意識復活（config 在但永不跑的假象保護）。

def test_no_mypy_ini():
    assert not (_REPO_ROOT / "mypy.ini").exists(), "mypy.ini 殭屍復活（spec D4：已刪除，CI 從不跑 mypy）"


def test_requirements_test_has_no_mypy():
    txt = (_REPO_ROOT / "requirements-test.txt").read_text(encoding="utf-8")
    lines = [ln.split("#")[0].strip().lower() for ln in txt.splitlines()]
    offenders = [ln for ln in lines if ln.startswith("mypy") or ln.startswith("types-requests")]
    assert not offenders, f"requirements-test.txt 不應有 mypy/types-requests 依賴（已刪）：{offenders}"


# ============================================================
# build.py Allowlist 模型契約（T2：棄 pip freeze + denylist）
# 緣起：T1 前 denylist 反覆漏 transitive（mypy orphan +11MB、playwright +32MB、uvloop +16MB）。
# T2 改為 allowlist 模型（requirements.txt 解析 + manifest extract），根除漂移根因。
# 本段守衛驗「allowlist 解析結果不含測試/開發工具、含必備 runtime」合約。
# ============================================================

def _direct_pkgs(req_path: Path) -> set:
    """抽 requirements 檔的直列套件名（跳 `-r`/註解/空行；去版本/extras、標準化）。"""
    names = set()
    for line in req_path.read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s or s.startswith("-"):
            continue
        name = re.split(r"[=<>!~\[]", s, maxsplit=1)[0].strip().lower().replace("_", "-")
        if name:
            names.add(name)
    return names


def _get_allowlist_names() -> set[str]:
    """取得 build.py allowlist 解析結果的套件名集合（標準化）。

    包含 parse_requirements_allowlist() + _UVICORN_WIN_SAFE_EXTRAS + extra_deps。
    """
    import build
    import re as _re

    def _norm(spec: str) -> str:
        return _re.split(r"[=<>!~\[]", spec, maxsplit=1)[0].strip().lower().replace("_", "-")

    names: set[str] = set()
    for dep in build.parse_requirements_allowlist():
        names.add(_norm(dep))
    for dep in build._UVICORN_WIN_SAFE_EXTRAS:
        names.add(_norm(dep))
    # extra_deps 改讀模組級常數 EXTRA_DEPS_NO_DEPS（N2）：新增 extra dep 即被守衛涵蓋，
    # 不再用 hardcoded list 代理（避免漏守衛）。proxy-tools 亦在此清單，SDIST_OK 另有專門守衛。
    for dep in build.EXTRA_DEPS_NO_DEPS:
        names.add(_norm(dep))
    return names


def test_build_allowlist_excludes_dev_tools():
    """allowlist 解析結果不得含 uvloop / playwright / mypy / pytest / ruff 等測試/開發工具。

    T2 allowlist 模型：依賴來源 = requirements.txt，無 pip freeze，無 dev venv 污染。
    uvloop 尤其重要：曾以 Linux .so（16MB）混入每個 release ZIP（P2 bug）。
    playwright 曾 +32MB；mypy 曾 +11MB——三者在 T1/T2 前長期隨 release 送到用戶。
    """
    banned = {"uvloop", "playwright", "mypy", "mypyc", "mypy-extensions",
              "pytest", "pytest-asyncio", "pytest-mock", "pytest-cov",
              "pytest-playwright", "ruff", "pyee", "langdetect"}
    names = _get_allowlist_names()
    found = sorted(banned & names)
    assert not found, (
        f"build.py allowlist 含不應出現的測試/開發工具：{found}\n"
        f"（T2 allowlist 模型：這些套件不在 requirements.txt，不應被 parse_requirements_allowlist 引入）"
    )


def test_build_allowlist_contains_required_runtime():
    """allowlist 必須含所有必備 runtime 套件——否則 build 缺套件、用戶端壞掉。"""
    required = {
        "fastapi", "uvicorn", "starlette", "jinja2", "python-multipart",
        "requests", "httpx", "beautifulsoup4", "lxml", "curl-cffi",
        "pydantic", "websockets", "pillow", "pywebview",
        "httptools", "watchfiles", "python-dotenv", "pyyaml",
        "bottle", "clr-loader", "pythonnet", "win32-setctime", "colorama",
        "proxy-tools",
    }
    names = _get_allowlist_names()
    # 標準化比較（curl_cffi → curl-cffi, clr_loader → clr-loader 等）
    missing = sorted(required - names)
    assert not missing, (
        f"build.py allowlist 缺少必備 runtime 套件（會做出缺套件的 ZIP）：{missing}"
    )


def test_build_allowlist_no_uvicorn_standard_extra():
    """allowlist 中 uvicorn 不應帶 [standard] extra。

    uvicorn[standard] 在 pip download --platform win_amd64 時會嘗試解 uvloop
    （marker sys_platform != 'win32' 不被求值）→ 無 win_amd64 wheel → build 失敗。
    T2 fix：uvicorn 去 [standard]，win-safe extras 改由 _UVICORN_WIN_SAFE_EXTRAS 明列。
    """
    import build
    for dep in build.parse_requirements_allowlist():
        assert "[standard]" not in dep, (
            f"parse_requirements_allowlist() 仍含 uvicorn[standard]：{dep!r}\n"
            f"應改為 uvicorn（去 extra），win-safe extras 由 _UVICORN_WIN_SAFE_EXTRAS 明列"
        )


def test_build_sdist_ok_contains_proxy_tools():
    """SDIST_OK 必須含 proxy-tools（PyPI 只有 sdist，無 wheel）。"""
    import build
    assert "proxy-tools" in build.SDIST_OK, (
        "build.SDIST_OK 缺 'proxy-tools'（PyPI 從未發 wheel，只有 proxy_tools-0.1.0.tar.gz）"
    )


def test_build_skip_if_no_win_wheel_contains_uvloop():
    """SKIP_IF_NO_WIN_WHEEL 必須含 uvloop（Windows 合法缺席，無 win_amd64 wheel）。"""
    import build
    assert "uvloop" in build.SKIP_IF_NO_WIN_WHEEL, (
        "build.SKIP_IF_NO_WIN_WHEEL 缺 'uvloop'（uvloop 無 win_amd64 wheel，應 skip + warning）"
    )


def test_build_greenlet_not_wrongly_excluded():
    """greenlet 不應被 allowlist 模型排除（pywebview Windows backend 的合法 transitive）。

    T1 前的 denylist 模型曾被誤列；T2 allowlist 模型無 denylist，greenlet 由 pip 依賴解析
    自動帶入（若 pywebview 需要）。此守衛確保 greenlet 未被誤加入任何排除機制。
    """
    import build
    # T2 無 EXCLUDE_PACKAGES；確認 SKIP_IF_NO_WIN_WHEEL 和 SDIST_OK 也未誤含 greenlet
    assert "greenlet" not in build.SKIP_IF_NO_WIN_WHEEL, (
        "SKIP_IF_NO_WIN_WHEEL 誤含 greenlet（greenlet 有 win_amd64 wheel，不應 skip）"
    )
    assert "greenlet" not in build.SDIST_OK, (
        "SDIST_OK 誤含 greenlet（greenlet 有 win_amd64 wheel，非 sdist-only）"
    )


def test_build_no_exclude_packages_attribute():
    """T2 後 build.py 不再有 EXCLUDE_PACKAGES（已由 allowlist 模型取代）。

    EXCLUDE_PACKAGES 是 denylist 模型的遺跡，必須移除。
    若此 test 失敗，代表 denylist 被復活——需重新評估是否違反 T2 設計。
    """
    import build
    assert not hasattr(build, "EXCLUDE_PACKAGES"), (
        "build.py 仍有 EXCLUDE_PACKAGES（denylist 模型遺跡）；"
        "T2 改為 allowlist + manifest-based extract，EXCLUDE_PACKAGES 應移除"
    )


def test_extra_deps_no_deps_all_pinned():
    """EXTRA_DEPS_NO_DEPS 每個項目都必須精確 pin（==）。

    未 pin 的 Phase 2 套件有兩個失效路徑：
    1. CI cache 有舊版 → 名稱命中跳下載 → 出貨舊版（stale-reuse）。
    2. cache 同時有多版 → cache-hit loop 全部加入 manifest → 兩版都解壓
       → last-writer-wins 覆蓋（multi-version corruption）。
    Pin 後 stale-cleanup 能靠版本號偵測並強制重下，確保 reproducible build。
    """
    import build
    unpinned = [dep for dep in build.EXTRA_DEPS_NO_DEPS if "==" not in dep]
    assert not unpinned, (
        f"EXTRA_DEPS_NO_DEPS 有未 exact-pin（==）的項目：{unpinned}\n"
        "所有 Phase 2 套件必須精確釘版本，防止 CI cache stale-reuse 和多版本解壓污染。"
    )


def test_uvicorn_win_safe_extras_all_pinned():
    """_UVICORN_WIN_SAFE_EXTRAS 每個項目都必須精確 pin（==）。

    httptools / watchfiles / python-dotenv / PyYAML 是 uvicorn[standard] 的 win-safe 子集，
    經 Phase 1 with-deps 下載。若無 pin，cold cache 或任一套件發佈新版時，pip 抓最新
    → 不同時間 / 不同機器建出不同版本 → 破壞可重現 build 契約（與 EXTRA_DEPS_NO_DEPS 同規範）。
    """
    import build
    unpinned = [dep for dep in build._UVICORN_WIN_SAFE_EXTRAS if "==" not in dep]
    assert not unpinned, (
        f"_UVICORN_WIN_SAFE_EXTRAS 有未 exact-pin（==）的項目：{unpinned}\n"
        "所有 win-safe extras 必須精確釘版本，確保 reproducible build（同 git tag = 同 ZIP）。"
    )


def test_parse_allowlist_rewrites_only_uvicorn_standard():
    """uvicorn[standard]==X → uvicorn==X（去 extra）；其餘無 extra 的行原樣保留。"""
    import build
    out = build._parse_allowlist_lines([
        "uvicorn[standard]==0.46.0",
        "fastapi==0.136.1",
        "# comment",
        "-r requirements.txt",
    ])
    assert "uvicorn==0.46.0" in out, f"uvicorn[standard] 未正確改寫：{out}"
    assert all("[standard]" not in d for d in out), f"殘留 [standard]：{out}"
    assert "fastapi==0.136.1" in out


def test_parse_allowlist_fails_closed_on_unexpected_extra():
    """非 uvicorn[standard] 的任何 extra → hard-fail（不靜默剝除子依賴）。

    Codex P2：blanket 剝 extra 會讓未來 `pkg[extra]==...` 的子依賴從 Windows ZIP
    無聲消失（正是 T2 要根除的漂移）。必須 fail-closed，逼維護者顯式處理。
    """
    import build
    with pytest.raises(SystemExit):
        build._parse_allowlist_lines(["redis[hiredis]==5.0.0"])
    # uvicorn 帶非 standard extra 也須 fail-closed（只認 [standard]）
    with pytest.raises(SystemExit):
        build._parse_allowlist_lines(["uvicorn[foo]==0.46.0"])


# ============================================================
# TASK-80-BUILD-T4b：build.py 模型「三禁」契約（防退回舊模型）
# 與 T2 的 allowlist 斷言互補：T2 驗「解析結果對」，本段驗「模型沒退回」。
# 與 T4a（build.yml 真實 ZIP 產物斷言）互補：本段早、快、便宜，在 PR pytest 階段先報紅。
# 三禁：① 不得回到 pip freeze 取安裝集 ② 不得 glob 整個 cache extract ③ 不得平台不符 fallback。
# ============================================================

def _build_source_no_comments() -> str:
    """build.py 原始碼，逐行剝除 `#` 註解（避免註解中提到的字觸發守衛誤判）。"""
    src = (_REPO_ROOT / "build.py").read_text(encoding="utf-8")
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def test_build_no_pip_freeze():
    """① build.py 不得用 pip freeze 取安裝集（T2 棄 freeze；舊函式 get_all_dependencies 須消失）。

    freeze 凍結的是「當前 dev venv」→ 任何測試/orphan 套件混入即被打包（denylist 漂移根因）。
    註解可提及 freeze（歷史說明），但程式碼不得再呼叫。
    """
    import build
    assert not hasattr(build, "get_all_dependencies"), (
        "build.py 仍有 get_all_dependencies（pip freeze 取安裝集的舊函式）；T2 已改 allowlist，應移除"
    )
    code = _build_source_no_comments()
    assert "freeze" not in code, (
        "build.py 程式碼（非註解）仍出現 'freeze'；不得退回 pip freeze 取安裝集模型"
    )


def test_build_extract_uses_manifest_not_glob_all():
    """② extract 必須只解壓 extract_manifest，不得 glob 整個 cache（殘留 orphan 會被帶入）。"""
    code = _build_source_no_comments()
    assert "for f in extract_manifest" in code, (
        "build.py 未見『for f in extract_manifest』；extract 應只迭代本次解析的 manifest"
    )
    assert 'glob("*.whl")' not in code, (
        "build.py 出現 glob(\"*.whl\")（舊 extract-整個-cache 模式）；應改 manifest-based extract"
    )


class _FakePipFail:
    """模擬 pip download 失敗的 CompletedProcess。"""
    returncode = 1
    stdout = ""
    stderr = "ERROR: No matching distribution found"


def test_build_download_fail_closed_no_platform_fallback(monkeypatch, tmp_path):
    """③ 必要套件無 win wheel → 硬失敗、且只嘗試一次（無平台不符 fallback retry）。

    舊模型：win wheel 失敗 → 再 `pip download`（不限平台）拉 Linux wheel（uvloop .so 混入途徑）。
    本守衛 mock pip 失敗，斷言 _download_one_package 對非 skip 套件 SystemExit 且只呼叫 pip 一次。
    """
    import build
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakePipFail()

    monkeypatch.setattr(build.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        build._download_one_package("somerequiredpkg==1.0.0", tmp_path)
    assert len(calls) == 1, (
        f"非 skip 套件無 win wheel 時應只嘗試一次（無 fallback retry），實際 {len(calls)} 次"
    )


def test_build_download_skip_if_no_win_wheel(monkeypatch, tmp_path):
    """SKIP_IF_NO_WIN_WHEEL 成員（uvloop）無 win wheel → skip（不 raise）、回空集、只嘗試一次。"""
    import build
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakePipFail()

    monkeypatch.setattr(build.subprocess, "run", fake_run)
    result = build._download_one_package("uvloop==0.22.1", tmp_path)
    assert result == set(), f"uvloop 應 skip 並回空集，實際 {result}"
    assert len(calls) == 1, f"應只嘗試一次（不 fallback），實際 {len(calls)} 次"
