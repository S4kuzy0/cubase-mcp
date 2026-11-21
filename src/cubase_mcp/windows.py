"""Native Cubase window identity, enumeration and activation."""

from __future__ import annotations

import ctypes
import os
import re
import time
from pathlib import Path


from .errors import IntegrationError


def api():
    if os.name != "nt":
        raise IntegrationError(
            "WINDOWS_REQUIRED", "Live Cubase control requires Windows."
        )
    import win32api
    import win32gui
    import win32process

    return win32api, win32gui, win32process


def window_record(hwnd):
    wa, wg, wp = api()
    _, pid = wp.GetWindowThreadProcessId(hwnd)
    return {
        "handle": hwnd,
        "pid": pid,
        "title": wg.GetWindowText(hwnd),
        "class": wg.GetClassName(hwnd),
        "visible": bool(wg.IsWindowVisible(hwnd)),
    }


def is_cubase_executable(name: str) -> bool:
    return re.fullmatch(r"Cubase(?: ?\d+)?\.exe", name, re.I) is not None


def windows():
    wa, wg, wp = api()
    records = []
    images = {}

    def visit(hwnd, _):
        try:
            record = window_record(hwnd)
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1400:
                return True  # A transient window may close during enumeration.
            raise
        pid = record["pid"]
        if pid not in images:
            try:
                handle = wa.OpenProcess(0x410, False, pid)
                try:
                    images[pid] = wp.GetModuleFileNameEx(handle, 0)
                finally:
                    handle.Close()
            except Exception:
                images[pid] = ""
        if is_cubase_executable(Path(images[pid]).name):
            record["executable"] = images[pid]
            records.append(record)
        return True

    wg.EnumWindows(visit, None)
    return records


def children(hwnd):
    _, wg, _ = api()
    found = []

    def visit(child, _):
        try:
            found.append(window_record(child))
        except Exception as exc:
            if getattr(exc, "winerror", None) != 1400:
                raise
        return True

    wg.EnumChildWindows(hwnd, visit, None)
    return found


def dialogs(pid):
    return [
        w
        for w in windows()
        if w["pid"] == pid and w["visible"] and w["class"] == "#32770"
    ]


def notify_activation(hwnd):
    """Notify a verified Cubase window without taking desktop focus or moving input."""
    _, wg, _ = api()
    matches = [window for window in windows() if window["handle"] == hwnd and window["visible"]]
    if len(matches) != 1 or not wg.IsWindowEnabled(hwnd):
        raise IntegrationError("FOCUS_TARGET_CHANGED", "The native target is no longer visible/enabled.")
    wg.SendMessageTimeout(hwnd, 0x6, 1, 0, 2, 1000)
    return "native_activation_notice"


def activate_window(hwnd):
    """Activate one verified Cubase-owned native window; never click its content."""
    wa, wg, _ = api()
    matches = [window for window in windows() if window["handle"] == hwnd and window["visible"]]
    if len(matches) != 1:
        raise IntegrationError("FOCUS_TARGET_CHANGED", "The Cubase window is no longer visible.")
    pending = dialogs(matches[0]["pid"])
    if pending and hwnd not in {dialog["handle"] for dialog in pending}:
        raise IntegrationError("DIALOG_BUSY", "Handle the existing native dialog before changing focus.")
    if wg.IsIconic(hwnd):
        wg.ShowWindow(hwnd, 9)
    try:
        wg.SetForegroundWindow(hwnd)
    except Exception:
        pass  # Windows may retain focus in the user's foreground application.
    deadline = time.monotonic() + .25
    while time.monotonic() < deadline:
        if wg.GetForegroundWindow() == hwnd:
            return "native"
        time.sleep(.025)
    if os.environ.get("CUBASE_PIAPRO_FOCUS_CLICK") != "1":
        raise IntegrationError(
            "FOCUS_REQUIRED",
            "Activate the Cubase window, or opt into the verified title-bar focus fallback with CUBASE_PIAPRO_FOCUS_CLICK=1.",
        )
    user32 = ctypes.windll.user32
    set_dpi = user32.SetThreadDpiAwarenessContext
    set_dpi.argtypes = [ctypes.c_void_p]
    set_dpi.restype = ctypes.c_void_p
    previous_dpi = set_dpi(ctypes.c_void_p(-4))
    cursor = wg.GetCursorPos()
    was_topmost = bool(wg.GetWindowLong(hwnd, -20) & 8)
    try:
        left, top, right, bottom = wg.GetWindowRect(hwnd)
        point = (left + 100, top + 20)
        if right - left < 300 or bottom - top < 100:
            raise IntegrationError(
                "FOCUS_REQUIRED", "The editor has no usable title bar."
            )
        wg.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)
        if wg.GetAncestor(wg.WindowFromPoint(point), 2) != hwnd:
            raise IntegrationError(
                "FOCUS_TARGET_CHANGED",
                "Another window covers the verified editor title bar.",
            )
        wa.SetCursorPos(point)
        wa.mouse_event(2, 0, 0, 0, 0)
        wa.mouse_event(4, 0, 0, 0, 0)
        deadline = time.monotonic() + .6
        while wg.GetForegroundWindow() != hwnd and time.monotonic() < deadline:
            time.sleep(.025)
    finally:
        wa.SetCursorPos(cursor)
        if not was_topmost:
            wg.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x13)
        set_dpi(previous_dpi)
    if wg.GetForegroundWindow() != hwnd:
        raise IntegrationError(
            "FOCUS_REQUIRED",
            "Windows did not activate the editor. No menu command was sent.",
        )
    return "title_bar_click"
