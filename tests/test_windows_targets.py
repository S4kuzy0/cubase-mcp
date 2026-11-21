from types import SimpleNamespace

import pytest

from cubase_mcp import windows


@pytest.mark.parametrize("child", [False, True])
def test_transient_windows_may_disappear_during_enumeration(monkeypatch, child):
    class WindowError(Exception):
        winerror = 1400
    def record(hwnd):
        if hwnd == 1:
            raise WindowError("window closed between enumeration and GetClassName")
        return {"handle": hwnd, "pid": 42}
    def enumerate_windows(visit, value):
        visit(1, value)
        visit(2, value)
    gui = SimpleNamespace(EnumWindows=enumerate_windows,
                          EnumChildWindows=lambda root, visit, value: enumerate_windows(visit, value))
    api = SimpleNamespace(OpenProcess=lambda *args: SimpleNamespace(Close=lambda: None))
    process = SimpleNamespace(GetModuleFileNameEx=lambda *args: "C:/Cubase14.exe")
    monkeypatch.setattr(windows, "api", lambda: (api, gui, process))
    monkeypatch.setattr(windows, "window_record", record)
    result = windows.children(123) if child else windows.windows()
    assert [item["handle"] for item in result] == [2]


@pytest.mark.parametrize("child", [False, True])
def test_window_enumeration_does_not_mask_unrelated_errors(monkeypatch, child):
    class WindowError(Exception):
        winerror = 5
    def record(hwnd):
        raise WindowError("unrelated API failure")
    gui = SimpleNamespace(EnumWindows=lambda visit, value: visit(1, value),
                          EnumChildWindows=lambda root, visit, value: visit(1, value))
    monkeypatch.setattr(windows, "api", lambda: (None, gui, None))
    monkeypatch.setattr(windows, "window_record", record)
    with pytest.raises(WindowError):
        windows.children(123) if child else windows.windows()

@pytest.mark.parametrize("image, accepted", [
    ("C:/Steinberg/Cubase14.exe", True),
    ("C:/Steinberg/Cubase 16.exe", True),
    ("C:/Steinberg/Cubase Helper.exe", False),
])
def test_host_window_discovery_checks_the_actual_executable(monkeypatch, image, accepted):
    gui = SimpleNamespace(EnumWindows=lambda visit, value: visit(1, value))
    api = SimpleNamespace(OpenProcess=lambda *args: SimpleNamespace(Close=lambda: None))
    process = SimpleNamespace(GetModuleFileNameEx=lambda *args: image)
    monkeypatch.setattr(windows, "api", lambda: (api, gui, process))
    monkeypatch.setattr(windows, "window_record", lambda hwnd: {"handle": hwnd, "pid": 42})
    assert [item["executable"] for item in windows.windows()] == ([image] if accepted else [])
