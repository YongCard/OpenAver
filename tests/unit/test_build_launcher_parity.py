"""build.py launcher script parity 守衛。

Windows 的 OpenAver_Debug.bat 需與 macOS 的 OpenAver_Debug.command 對齊，
兩者都應設 OPENAVER_DEBUG=1（把 console log level 從 INFO 降到 DEBUG）。

debug_bat_content 是 create_launcher_scripts() 內的 inline triple-quoted 字串，
非回傳值、非 module-level 常數，故走「讀 build.py 原始檔文字」的存在性守衛。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_debug_bat_sets_openaver_debug_for_macos_parity():
    """Windows Debug.bat 需含 set OPENAVER_DEBUG=1（與 macOS Debug.command 對等）。"""
    build_py = (REPO_ROOT / "build.py").read_text(encoding="utf-8")
    assert "set OPENAVER_DEBUG=1" in build_py
