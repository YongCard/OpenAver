"""
tests/unit/test_window_state.py
windows/window_state.py の load/save/attach 行為テスト

pywebview は Windows/macOS 専用。すべての window/events をモックする。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def state_module(monkeypatch, tmp_path):
    """重新載入 window_state，並把 STATE_PATH 指向 tmp_path"""
    # 確保 windows package 可 import（windows/ 下無 __init__.py 也能走 file spec）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "window_state_test",
        Path(__file__).parent.parent.parent / "windows" / "window_state.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE_PATH", tmp_path / "window_state.json")
    return module


# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------
def test_load_state_no_file_returns_default(state_module):
    s = state_module.load_state()
    assert s["width"] == state_module.DEFAULT_WIDTH
    assert s["height"] == state_module.DEFAULT_HEIGHT
    assert s["x"] is None
    assert s["y"] is None
    assert s["maximized"] is False


def test_load_state_valid_file(state_module):
    state_module.STATE_PATH.write_text(json.dumps({
        "width": 1600, "height": 1000, "x": 100, "y": 50, "maximized": True
    }), encoding="utf-8")
    s = state_module.load_state()
    assert s["width"] == 1600
    assert s["height"] == 1000
    assert s["x"] == 100
    assert s["y"] == 50
    assert s["maximized"] is True


def test_load_state_corrupted_json_falls_back(state_module):
    state_module.STATE_PATH.write_text("not valid json {{{", encoding="utf-8")
    s = state_module.load_state()
    assert s["width"] == state_module.DEFAULT_WIDTH


def test_load_state_out_of_bounds_falls_back(state_module):
    state_module.STATE_PATH.write_text(json.dumps({
        "width": 99999, "height": 99999
    }), encoding="utf-8")
    s = state_module.load_state()
    assert s["width"] == state_module.DEFAULT_WIDTH


def test_load_state_too_small_falls_back(state_module):
    state_module.STATE_PATH.write_text(json.dumps({
        "width": 10, "height": 10
    }), encoding="utf-8")
    s = state_module.load_state()
    assert s["width"] == state_module.DEFAULT_WIDTH


def test_load_state_xy_out_of_bounds_dropped(state_module):
    """拔螢幕後的舊座標 → 丟棄，避免視窗開在畫面外"""
    state_module.STATE_PATH.write_text(json.dumps({
        "width": 1200, "height": 800, "x": 99999, "y": 50
    }), encoding="utf-8")
    s = state_module.load_state()
    assert s["x"] is None
    assert s["y"] is None
    assert s["width"] == 1200  # 尺寸保留


def test_load_state_non_int_xy_falls_to_none(state_module):
    state_module.STATE_PATH.write_text(json.dumps({
        "width": 1200, "height": 800, "x": "abc", "y": "xyz"
    }), encoding="utf-8")
    s = state_module.load_state()
    assert s["x"] is None
    assert s["y"] is None


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------
def test_save_state_writes_json(state_module):
    state_module.save_state({
        "width": 1400, "height": 900, "x": 0, "y": 0, "maximized": False
    })
    data = json.loads(state_module.STATE_PATH.read_text(encoding="utf-8"))
    assert data["width"] == 1400
    assert data["height"] == 900


def test_save_state_creates_parent_dir(state_module, tmp_path, monkeypatch):
    nested = tmp_path / "nested" / "deep" / "window_state.json"
    monkeypatch.setattr(state_module, "STATE_PATH", nested)
    state_module.save_state({"width": 1200, "height": 800, "x": None, "y": None, "maximized": False})
    assert nested.exists()


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------
def _make_mock_window():
    """模擬 pywebview window，events 屬性可被 += handler"""
    window = MagicMock()
    handlers = {}

    class FakeEvent:
        def __init__(self, name):
            self.name = name
            self.subscribers = []

        def __iadd__(self, handler):
            self.subscribers.append(handler)
            handlers.setdefault(self.name, []).append(handler)
            return self

    for name in ("resized", "moved", "maximized", "restored", "closing"):
        setattr(window.events, name, FakeEvent(name))

    return window, handlers


def test_attach_resize_updates_state(state_module):
    window, handlers = _make_mock_window()
    initial = state_module._default_state()
    state = state_module.attach(window, initial)
    handlers["resized"][0](1500, 950)
    assert state["width"] == 1500
    assert state["height"] == 950


def test_attach_move_updates_state(state_module):
    window, handlers = _make_mock_window()
    state = state_module.attach(window, state_module._default_state())
    handlers["moved"][0](200, 100)
    assert state["x"] == 200
    assert state["y"] == 100


def test_attach_maximized_blocks_resize_overwrite(state_module):
    """maximized 期間的 resize 不應蓋掉還原尺寸（否則下次 restore 變成全螢幕大小）"""
    window, handlers = _make_mock_window()
    state = state_module.attach(window, {
        "width": 1200, "height": 800, "x": None, "y": None, "maximized": False
    })
    handlers["maximized"][0]()
    handlers["resized"][0](1920, 1080)
    assert state["width"] == 1200  # 還原尺寸不變
    assert state["height"] == 800
    assert state["maximized"] is True

    handlers["restored"][0]()
    handlers["resized"][0](1500, 900)
    assert state["width"] == 1500  # restore 後恢復追蹤


def test_attach_closing_writes_state(state_module):
    window, handlers = _make_mock_window()
    state_module.attach(window, {
        "width": 1400, "height": 900, "x": 50, "y": 50, "maximized": False
    })
    handlers["closing"][0]()
    data = json.loads(state_module.STATE_PATH.read_text(encoding="utf-8"))
    assert data["width"] == 1400


def test_attach_missing_event_does_not_raise(state_module):
    """老版 pywebview 沒有 moved 事件也要能正常 attach"""
    window = MagicMock()

    class FakeEvent:
        def __iadd__(self, handler):
            return self

    window.events = MagicMock(spec=["resized", "closing"])
    window.events.resized = FakeEvent()
    window.events.closing = FakeEvent()
    # moved/maximized/restored 不存在 → getattr 回 None → 跳過

    state_module.attach(window, state_module._default_state())  # 不應 raise
