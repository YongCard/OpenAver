"""
TASK-80a-T2: standalone.py HOST 拆分 AST/source 守衛

驗證 windows/standalone.py 中：
  (a) BIND_HOST = "0.0.0.0"  模組層賦值存在
  (b) CLIENT_HOST = "127.0.0.1" 模組層賦值存在
  (c) uvicorn Config/Server 呼叫使用 host=BIND_HOST（不是 CLIENT_HOST）
  (d) find_free_port 的 sock.bind 使用 CLIENT_HOST（不是 BIND_HOST）
  (e) wait_for_server health URL 使用 CLIENT_HOST（不是 BIND_HOST）
  (f) main window create_window URL（非 JL window）使用 CLIENT_HOST
  (g) 無裸 HOST 模組層賦值殘留（`HOST = "..."` 形式）

Mirror 慣例：Path.read_text() + ast.parse，不 import windows.standalone
（test env 無 webview 套件）。
"""
import ast
import pathlib

STANDALONE_PATH = pathlib.Path(__file__).parents[2] / "windows" / "standalone.py"


def _parse():
    src = STANDALONE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(STANDALONE_PATH))
    return tree, src


def _module_assignments(tree: ast.Module) -> list[ast.Assign]:
    """頂層 Assign 節點（非巢狀在函式/類別內）"""
    return [
        node for node in ast.iter_child_nodes(tree)
        if isinstance(node, ast.Assign)
    ]


def _assignment_name_value(node: ast.Assign):
    """回傳 (name_str | None, value)；僅支援單名稱賦值（Name target）。"""
    if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id, node.value
    return None, None


class TestStandaloneHostSplitGuard:
    """TASK-80a-T2 CD #1：HOST 拆分守衛"""

    def test_bind_host_module_assignment_exists(self):
        """BIND_HOST = "0.0.0.0" 模組層賦值存在"""
        tree, _ = _parse()
        assigns = _module_assignments(tree)
        found = False
        for a in assigns:
            name, val = _assignment_name_value(a)
            if name == "BIND_HOST":
                assert isinstance(val, ast.Constant) and val.value == "0.0.0.0", (
                    f"BIND_HOST 存在但值不是 '0.0.0.0'，實際：{ast.unparse(val)}"
                )
                found = True
                break
        assert found, "BIND_HOST 模組層賦值未找到（需為 BIND_HOST = '0.0.0.0'）"

    def test_client_host_module_assignment_exists(self):
        """CLIENT_HOST = "127.0.0.1" 模組層賦值存在"""
        tree, _ = _parse()
        assigns = _module_assignments(tree)
        found = False
        for a in assigns:
            name, val = _assignment_name_value(a)
            if name == "CLIENT_HOST":
                assert isinstance(val, ast.Constant) and val.value == "127.0.0.1", (
                    f"CLIENT_HOST 存在但值不是 '127.0.0.1'，實際：{ast.unparse(val)}"
                )
                found = True
                break
        assert found, "CLIENT_HOST 模組層賦值未找到（需為 CLIENT_HOST = '127.0.0.1'）"

    def test_no_bare_host_module_assignment(self):
        """不存在裸 HOST = ... 模組層賦值（無殭屍別名）"""
        tree, _ = _parse()
        assigns = _module_assignments(tree)
        violations = []
        for a in assigns:
            name, _ = _assignment_name_value(a)
            if name == "HOST":
                violations.append(a.lineno)
        assert not violations, (
            f"裸 HOST 模組層賦值殘留在 line(s) {violations}，"
            "應刪除 HOST 並改用 BIND_HOST / CLIENT_HOST"
        )

    def test_uvicorn_config_uses_bind_host(self):
        """
        uvicorn.Config(app, host=BIND_HOST, ...) 使用 BIND_HOST。
        以 source text 搜尋確認 host=BIND_HOST 關鍵字出現，且無 host=CLIENT_HOST
        出現在 uvicorn Config 鄰近行。
        """
        _, src = _parse()
        lines = src.splitlines()

        # 找 uvicorn.Config 或 uvicorn.run 呼叫段落
        uvicorn_lines = [
            (i, line) for i, line in enumerate(lines, 1)
            if "uvicorn" in line and ("Config" in line or "run(" in line)
        ]
        assert uvicorn_lines, "standalone.py 中找不到 uvicorn.Config / uvicorn.run 呼叫"

        # 找 host=BIND_HOST 出現
        host_bind_lines = [
            (i, line) for i, line in enumerate(lines, 1)
            if "host=BIND_HOST" in line
        ]
        assert host_bind_lines, (
            "找不到 host=BIND_HOST — uvicorn 應用 BIND_HOST（0.0.0.0）而非 CLIENT_HOST"
        )

        # 確認無 host=CLIENT_HOST（該鍵不應出現在 uvicorn config 中）
        host_client_in_uvicorn = [
            (i, line) for i, line in enumerate(lines, 1)
            if "host=CLIENT_HOST" in line
        ]
        assert not host_client_in_uvicorn, (
            f"uvicorn 誤用 host=CLIENT_HOST 在 line(s) "
            f"{[i for i, _ in host_client_in_uvicorn]}；"
            "應使用 host=BIND_HOST"
        )

    def test_find_free_port_uses_client_host(self):
        """
        find_free_port 的 sock.bind 使用 CLIENT_HOST。
        在 source text 確認 sock.bind((CLIENT_HOST, port)) 出現。
        """
        _, src = _parse()
        assert "sock.bind((CLIENT_HOST," in src, (
            "find_free_port 的 sock.bind 應使用 CLIENT_HOST（不是 BIND_HOST）"
        )

    def test_wait_for_server_uses_client_host(self):
        """
        wait_for_server health URL 使用 CLIENT_HOST。
        """
        _, src = _parse()
        assert "CLIENT_HOST}" in src or "{CLIENT_HOST}" in src, (
            "wait_for_server URL 應使用 CLIENT_HOST（不是 BIND_HOST）"
        )
        # 確認 health URL 行包含 CLIENT_HOST
        health_lines = [
            line for line in src.splitlines()
            if "/api/health" in line
        ]
        assert health_lines, "找不到 /api/health URL 行"
        for line in health_lines:
            assert "CLIENT_HOST" in line, (
                f"health URL 行不含 CLIENT_HOST：{line!r}"
            )

    def test_main_window_create_window_uses_client_host(self):
        """
        main window（OpenAver）的 create_window URL 使用 CLIENT_HOST、非 BIND_HOST。

        主視窗 URL 字面為單引號、無路徑：f'http://{CLIENT_HOST}:{port}'，
        與 health URL（雙引號 + /api/health 後綴）不同 → 此斷言能特定鎖定主視窗，
        若主視窗誤改回 BIND_HOST 會 RED（mutation-sensitive）。
        """
        _, src = _parse()
        assert "f'http://{CLIENT_HOST}:{port}'" in src, (
            "主視窗 create_window URL 應為 f'http://{CLIENT_HOST}:{port}'（CLIENT_HOST、非 BIND_HOST）"
        )
        # 反向：主視窗 URL 不得用 BIND_HOST（避免桌面 App 載入 0.0.0.0）
        assert "f'http://{BIND_HOST}:{port}'" not in src, (
            "主視窗 URL 不可用 BIND_HOST（桌面 App 須走 loopback）"
        )
