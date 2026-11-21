"""Cubase-owned project commands and verified Windows file dialogs."""

from __future__ import annotations

import ctypes
import math
import re
import secrets
import struct
import tempfile
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from . import cpr_midi, cubase, live_midi, native_visual, projects, windows
from .errors import IntegrationError
from .storage import digest, file_lock, read_file, state_dir, write_result


def _menu_label(menu, index):
    read = ctypes.windll.user32.GetMenuStringW
    read.argtypes = (wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT)
    buffer = ctypes.create_unicode_buffer(1024)
    read(menu, index, buffer, len(buffer), 0x400)
    return buffer.value.split("\t")[0].replace("&", "").strip().replace("…", "...")


def _invoke(root, path):
    _, gui, _ = windows.api()
    cubase._idle(root)
    hwnd = root["handle"]
    menu = gui.GetMenu(hwnd)
    for index, label in enumerate(path):
        if not menu:
            raise IntegrationError("CUBASE_MENU_UNAVAILABLE", "Cubase does not expose the requested native menu.")
        matches = [i for i in range(gui.GetMenuItemCount(menu)) if _menu_label(menu, i) == label]
        if len(matches) != 1:
            raise IntegrationError("CUBASE_MENU_UNAVAILABLE", " / ".join(path[:index + 1]))
        position = matches[0]
        if gui.GetMenuState(menu, position, 0x400) & 3:
            raise IntegrationError("CUBASE_MENU_DISABLED", label)
        if index == len(path) - 1:
            command = gui.GetMenuItemID(menu, position)
            if not 0 < command < 65535 or gui.GetWindowText(hwnd) != root["title"]:
                raise IntegrationError("CUBASE_MENU_UNAVAILABLE", label)
            gui.PostMessage(hwnd, 0x111, command, 0)
            return
        menu = gui.GetSubMenu(menu, position)


def _new_path(value, suffix):
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != suffix or not path.parent.is_dir():
        raise IntegrationError("INVALID_OUTPUT_PATH", f"Use an absolute, new {suffix} path in an existing folder.")
    if path.exists():
        raise IntegrationError("OUTPUT_EXISTS", "Cubase output must not overwrite an existing file.")
    return path.resolve()


def _stopped_project(expected_project):
    root = cubase.project_root()
    if not expected_project or not root["title"].endswith("Project - " + expected_project):
        raise IntegrationError("CUBASE_PROJECT_MISMATCH", "Supply the exact active project name, without .cpr.")
    state = live_midi.get_state()
    if not state.get("connected"):
        raise IntegrationError("NO_CUBASE_FEEDBACK", "Verify the stopped Cubase transport before proceeding.")
    if state["state"].get("playing") != 0 or state["state"].get("recording") != 0:
        raise IntegrationError("TRANSPORT_RUNNING", "Stop playback and recording before a native file command.")
    return root


def _wait_for_file_dialog(pid, titles, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dialogs = windows.dialogs(pid)
        if dialogs:
            if len(dialogs) == 1 and dialogs[0]["title"] in titles:
                return dialogs[0]
            raise IntegrationError("UNEXPECTED_CUBASE_DIALOG", ", ".join(w["title"] for w in dialogs))
        time.sleep(.1)
    raise IntegrationError("CUBASE_DIALOG_PENDING", f"Expected one of: {', '.join(titles)}")


def _set_filename(dialog, path, button_label):
    _, gui, _ = windows.api()
    controls = [w for w in windows.children(dialog["handle"]) if w["visible"]
                and gui.IsWindowEnabled(w["handle"])]
    edits = [w for w in controls if w["class"] == "Edit" and gui.GetDlgCtrlID(w["handle"]) in (1001, 1148)]
    buttons = [w for w in controls if w["class"] == "Button" and gui.GetDlgCtrlID(w["handle"]) == 1
               and w["title"].replace("&", "") == button_label]
    if len(edits) != 1 or len(buttons) != 1:
        raise IntegrationError("CUBASE_FILE_DIALOG_LAYOUT", "The filename field or confirmation button is ambiguous.")
    _replace_edit_text(edits[0]["handle"], str(path))
    gui.PostMessage(buttons[0]["handle"], 0xf5, 0, 0)


def _replace_edit_text(hwnd, text):
    _, gui, _ = windows.api()
    # Replace the selection so the native Edit notifies its owning dialog.
    gui.SendMessageTimeout(hwnd, 0xb1, 0, -1, 2, 1500)
    gui.SendMessageTimeout(hwnd, 0xc2, 1, text, 2, 1500)
    buffer = ctypes.create_unicode_buffer(len(text) + 8)
    send = ctypes.windll.user32.SendMessageW
    send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p)
    send(hwnd, 0xd, len(buffer), ctypes.byref(buffer))
    if buffer.value != text:
        raise IntegrationError("CUBASE_FILENAME_UNVERIFIED", "The native dialog did not retain the exact filename.")


def _saved_as(root, destination, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dialogs = windows.dialogs(root["pid"])
        if any(w["title"] != "Save As" for w in dialogs):
            raise IntegrationError("UNEXPECTED_CUBASE_DIALOG", ", ".join(w["title"] for w in dialogs))
        matches = [w for w in cubase.project_windows() if w["pid"] == root["pid"]
                   and w["handle"] == root["handle"] and w["title"].endswith("Project - " + destination.stem)]
        if not dialogs and len(matches) == 1 and destination.is_file():
            path, data, sha = read_file(destination)
            if len(data) > 64 and data.startswith((b"RIF2", b"RIFF")):
                return {"path": str(path), "sha256": sha, "bytes": len(data),
                        "project": matches[0]["title"], "verified": True}
            raise IntegrationError("INVALID_SAVED_CPR", "The file produced by Cubase did not have a supported CPR header.")
        time.sleep(.15)
    raise IntegrationError("SAVE_AS_UNVERIFIED", "The new CPR and matching active project title were not both observed.")


def _save_as(root, destination):
    _invoke(root, ("File", "Save As..."))
    dialog = _wait_for_file_dialog(root["pid"], ("Save As",))
    if destination.exists():
        raise IntegrationError("OUTPUT_EXISTS", "The requested output appeared before confirmation.")
    _set_filename(dialog, destination, "Save")
    return _saved_as(root, destination)


def save_as(expected_project: str, output_path: str) -> dict:
    destination = _new_path(output_path, ".cpr")
    with file_lock(state_dir() / "live-daw"), file_lock(destination):
        root = _stopped_project(expected_project)
        if destination.stem.casefold() == expected_project.casefold():
            raise IntegrationError("OUTPUT_NAME_AMBIGUOUS", "Use a different project name so the result can be verified.")
        return _save_as(root, destination)


def _press_cubase_label(window, view, label, *, require_uncovered=False):
    matches = [row["box"] for row in view["rows"] if row["text"].casefold() == label.casefold()]
    if len(matches) != 1:
        raise IntegrationError("CUBASE_PROMPT_LAYOUT", f"Expected one {label!r} label.")
    x1, y1, x2, y2 = matches[0]
    _, gui, _ = windows.api()
    hwnd = window["handle"]
    with cubase._physical_coordinates():
        rect = list(gui.GetWindowRect(hwnd))
        if (rect != view["window_rect"] or gui.GetWindowText(hwnd) != window["title"]
                or not gui.IsWindowVisible(hwnd) or not gui.IsWindowEnabled(hwnd)):
            raise IntegrationError("CUBASE_PROMPT_CHANGED", "The verified Cubase prompt changed before the input.")
        scale = 2 * view["scale"]
        point = rect[0] + (x1 + x2) // scale, rect[1] + (y1 + y2) // scale
        if require_uncovered and gui.GetAncestor(gui.WindowFromPoint(point), 2) != hwnd:
            raise IntegrationError("CUBASE_PROMPT_OBSCURED", "Cubase did not expose the verified control.")
        x, y = gui.ScreenToClient(hwnd, point)
        position = y << 16 | x & 65535
        for message, button in ((0x200, 0), (0x201, 1), (0x202, 0)):
            gui.PostMessage(hwnd, message, button, position)


def _choose_current_project(root, proof_path):
    _, gui, _ = windows.api()
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        visible = [w for w in windows.windows() if w["visible"] and w["pid"] == root["pid"]]
        direct = [w for w in visible if w["class"] == "#32770" and w["title"] == "Import MIDI File"]
        if len(direct) == 1:
            return direct[0]
        prompts = [w for w in visible if w["title"] in {"Cubase LE", "Cubase AI", "Cubase Elements",
                                                        "Cubase Artist", "Cubase Pro"}
                   and w["class"].startswith("SteinbergWindowClass")]
        if len(prompts) == 1 and not gui.IsWindowEnabled(root["handle"]):
            view = native_visual.recognize(native_visual.capture(prompts[0], proof_path, scale=2))
            labels = {row["text"].casefold() for row in view["rows"]}
            if not {"no", "cancel"} <= labels or not any("want to create a new pro" in row for row in labels):
                raise IntegrationError("CUBASE_PROMPT_LAYOUT", "Cubase's new-project question could not be verified.")
            _press_cubase_label(prompts[0], view, "No")
            return _wait_for_file_dialog(root["pid"], ("Import MIDI File",))
        time.sleep(.1)
    raise IntegrationError("CUBASE_DIALOG_PENDING", "No supported MIDI import window appeared.")


def _imported_tracks(before, names):
    previous = {track["uid"]: (track["name"], track["kind"]) for track in before["tracks"]}
    end = time.monotonic() + 15
    while time.monotonic() < end:
        current = live_midi.list_tracks()
        if current["session"] != before["session"] or current["overflow"]:
            raise IntegrationError("CUBASE_SESSION_CHANGED", "The MIDI Remote session or inventory changed during import.")
        tracks = {track["uid"]: (track["name"], track["kind"]) for track in current["tracks"] if track["uid"]}
        if any(tracks.get(uid) != signature for uid, signature in previous.items()):
            raise IntegrationError("UNVERIFIED_MIDI_IMPORT", "An existing track changed identity during import.")
        added = [track for track in current["tracks"] if track["uid"] and track["uid"] not in previous]
        if len(added) == len(names) and {track["name"] for track in added} == names:
            return added
        time.sleep(.2)
    raise IntegrationError("UNVERIFIED_MIDI_IMPORT", "The expected newly imported tracks did not all report stable UIDs.")


def import_midi(expected_project, midi_path, expected_sha256, output_cpr, expected_start_tick=0,
                allow_dialog_fallback=False):
    if not allow_dialog_fallback:
        raise IntegrationError("DIALOG_FALLBACK_REQUIRED", "Cubase may ask whether to make a new project. "
                               "Explicitly allow answering only the verified import question.")
    if expected_start_tick < 0:
        raise IntegrationError("INVALID_START_TICK", "Use a nonnegative project tick at 480 ticks per quarter note.")
    destination = _new_path(output_cpr, ".cpr")
    source, data, sha = projects.read_expected(midi_path, expected_sha256)
    if source.suffix.lower() not in {".mid", ".midi"}:
        raise IntegrationError("FORMAT_MISMATCH", "Use a standard MIDI file.")
    names = {track["name"] for track in cpr_midi._source_tracks(data)}
    with file_lock(state_dir() / "live-daw"), file_lock(destination):
        root = _stopped_project(expected_project)
        if destination.stem.casefold() == expected_project.casefold():
            raise IntegrationError("OUTPUT_NAME_AMBIGUOUS", "Use a different checkpoint project name.")
        inventory = live_midi.list_tracks()
        if inventory["overflow"] or len(inventory["tracks"]) + len(names) > 64:
            raise IntegrationError("TRACK_CAPACITY_EXCEEDED", "The resulting tracks must fit the verified inventory.")
        if any(not track["uid"] for track in inventory["tracks"]):
            raise IntegrationError("TRACK_ID_PENDING", "Wait for existing native track IDs before importing.")
        if {track["name"] for track in inventory["tracks"]} & names:
            raise IntegrationError("AMBIGUOUS_MIDI_TARGETS", "Give each source track a name absent from the active project.")
        with tempfile.TemporaryDirectory(prefix="native-midi-", dir=state_dir()) as temporary:
            staged = Path(temporary) / source.name
            staged.write_bytes(data)
            if digest(staged.read_bytes()) != sha:
                raise IntegrationError("MIDI_STAGE_FAILED", "Could not stage the exact input bytes.")
            _invoke(root, ("File", "Import", "MIDI File..."))
            dialog = _choose_current_project(root, staged.with_suffix(".bmp"))
            _set_filename(dialog, staged, "Open")
            added = _imported_tracks(inventory, names)
            saved = _save_as(_stopped_project(expected_project), destination)
            notes = cpr_midi.verify(str(destination), str(staged), expected_start_tick)
    notes["midi_path"] = str(source)
    return {"verified": notes["verified"], "checkpoint": saved, "live_tracks": added, "notes": notes}


def _mixdown_window(pid):
    stop = time.monotonic() + 12
    while time.monotonic() < stop:
        matching = [w for w in windows.windows() if w["pid"] == pid and w["visible"]
                    and w["title"] == "Export Audio Mixdown" and w["class"].startswith("SteinbergWindowClass")]
        if len(matching) == 1:
            return matching[0]
        time.sleep(.1)
    raise IntegrationError("MIXDOWN_DIALOG_PENDING", "No unique Cubase Export Audio Mixdown window appeared.")


def _compact_ocr(text):
    return re.sub(r"[^a-z0-9:/._-]", "", text.casefold().replace("\\", "/")).replace("1", "i")


def _verify_mixdown_settings(view, native, *, check_name=True):
    text = [row["text"] for row in view["rows"]]
    words = {_compact_ocr(line) for line in text}
    basename = _compact_ocr(native.name)
    outdir = _compact_ocr(str(native.parent))
    # Cubase clips long display paths. Require the visible prefix to include
    # the beginning of the actual Mixdown folder, rather than just a drive.
    path = [line for line in words if line.endswith("..") and outdir.startswith(line.rstrip("."))
            and len(line.rstrip(".")) >= len(outdir) - 4]
    if ("stereoout" not in words or (check_name and basename not in words) or
            (outdir not in words and not path) or "alwaysask" not in words):
        raise IntegrationError("MIXDOWN_SETTINGS_UNVERIFIED",
            "Verify Stereo Out, WAV preview, the project Mixdown folder and Always Ask on filename conflicts.")


@contextmanager
def _expose_mixdown_panel(panel):
    _, gui, _ = windows.api()
    hwnd = panel["handle"]
    if gui.IsIconic(hwnd):
        gui.ShowWindow(hwnd, 9)
    was_topmost = bool(gui.GetWindowLong(hwnd, -20) & 8)
    gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x13)
    try:
        yield
    finally:
        if not was_topmost and gui.IsWindow(hwnd):
            gui.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x13)


def _set_mixdown_name(panel, view, native):
    names = [row["box"] for row in view["rows"] if _compact_ocr(row["text"]) == "name"]
    if len(names) != 1:
        raise IntegrationError("MIXDOWN_SETTINGS_UNVERIFIED", "The mixdown filename label is ambiguous.")
    _, gui, _ = windows.api()
    left, top, right, _ = view["window_rect"]
    _, name_top, name_right, name_bottom = names[0]
    scale = view["scale"]
    name_y = (name_top + name_bottom) // 2
    values = [row["text"] for row in view["rows"]
              if name_right < row["box"][0] < (right - left) * scale * .7
              and row["box"][1] <= name_y <= row["box"][3]]
    previews = [row["text"] for row in view["rows"] if _compact_ocr(row["text"]) in {"preview", "prevlew"}]
    if len(values) != 1 or len(previews) != 1:
        raise IntegrationError("MIXDOWN_SETTINGS_UNVERIFIED", "The current mixdown filename or preview is ambiguous.")
    _press_cubase_label(panel, view, values[0], require_uncovered=True)
    # Cubase replaces the existing native Edit as the filename field activates.
    time.sleep(.3)
    target_y = top + name_y // scale
    with cubase._physical_coordinates():
        fields = []
        deadline = time.monotonic() + 1.5
        while not fields and time.monotonic() < deadline:
            for row in windows.children(panel["handle"]):
                if row["class"] != "Edit" or not row["visible"] or not gui.IsWindowEnabled(row["handle"]):
                    continue
                x1, y1, _, y2 = gui.GetWindowRect(row["handle"])
                if x1 > left + name_right / scale and y1 <= target_y <= y2:
                    fields.append(row["handle"])
            if not fields:
                time.sleep(.05)
        if len(fields) != 1:
            raise IntegrationError("MIXDOWN_SETTINGS_UNVERIFIED", "The writable mixdown filename field is ambiguous.")
        _replace_edit_text(fields[0], native.stem)
    _press_cubase_label(panel, view, previews[0], require_uncovered=True)


def _dispatch_mixdown(panel, native):
    with _expose_mixdown_panel(panel), tempfile.TemporaryDirectory(prefix="mixdown-ocr-", dir=state_dir()) as temporary:
        proof = Path(temporary) / "settings.bmp"
        view = native_visual.recognize(native_visual.capture(panel, proof, scale=2))
        _verify_mixdown_settings(view, native, check_name=False)
        _set_mixdown_name(panel, view, native)
        time.sleep(.3)
        view = native_visual.recognize(native_visual.capture(panel, proof, scale=2))
        _verify_mixdown_settings(view, native)
        labels = [row["text"] for row in view["rows"] if _compact_ocr(row["text"]) in {"exportaudio", "exportaudi0"}]
        if len(labels) != 1 or native.exists():
            raise IntegrationError("MIXDOWN_DISPATCH_UNVERIFIED", "The export control or output is no longer unique.")
        _press_cubase_label(panel, view, labels[0], require_uncovered=True)
        _, gui, _ = windows.api()
        deadline = time.monotonic() + 2.5
        while gui.IsWindowVisible(panel["handle"]) and not native.exists() and time.monotonic() < deadline:
            time.sleep(.1)


def _wav_header(data):
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise IntegrationError("INVALID_MIXDOWN_WAV", "Cubase did not produce a RIFF WAVE file.")
    limit = struct.unpack_from("<I", data, 4)[0] + 8
    if limit > len(data):
        raise IntegrationError("MIXDOWN_WAV_PENDING", "The WAV header declares more bytes than have been written.")
    offset = 12
    fmt = None
    sample_bytes = None
    while offset + 8 <= limit:
        name, size = struct.unpack_from("<4sI", data, offset)
        start = offset + 8
        if start + size > limit:
            raise IntegrationError("MIXDOWN_WAV_PENDING", "The WAV audio data is incomplete.")
        if name == b"fmt " and size >= 16:
            fmt = struct.unpack_from("<HHIIHH", data, start)
        if name == b"data":
            sample_bytes = size
        offset = start + size + size % 2
    if not fmt or not sample_bytes:
        raise IntegrationError("INVALID_MIXDOWN_WAV", "The WAV has no format or audio samples.")
    codec, channels, rate, unused, frame_bytes, bits = fmt
    if codec not in (1, 3, 65534) or channels <= 0 or rate <= 0 or frame_bytes <= 0 or sample_bytes % frame_bytes:
        raise IntegrationError("INVALID_MIXDOWN_WAV", "The sample format or block alignment is unsupported.")
    frames = sample_bytes // frame_bytes
    return {"channels": channels, "sample_rate": rate, "bits_per_sample": bits,
            "frames": frames, "duration_seconds": frames / rate}


def export_mixdown(project_path, output_path, expected_duration_seconds, allow_dialog_fallback=False):
    if not allow_dialog_fallback:
        raise IntegrationError("DIALOG_FALLBACK_REQUIRED", "The supported export verifies Cubase settings through local OCR.")
    if expected_duration_seconds is not None and (not math.isfinite(expected_duration_seconds)
                                                   or expected_duration_seconds <= 0):
        raise IntegrationError("INVALID_MIXDOWN_DURATION", "Use a positive expected duration in seconds.")
    project = Path(project_path)
    if not project.is_absolute() or not project.is_file() or project.suffix.lower() != ".cpr":
        raise IntegrationError("INVALID_PROJECT_PATH", "Use the absolute path of the active saved CPR.")
    target = _new_path(output_path, ".wav")
    mixdown = project.resolve().parent / "Mixdown"
    token = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(12))
    native = mixdown / f"mcp-{token}.wav"
    with file_lock(state_dir() / "live-daw"), file_lock(native):
        root = _stopped_project(project.stem)
        if native.exists() or target.exists():
            raise IntegrationError("OUTPUT_EXISTS", "A mixdown output appeared before export.")
        _, gui, _ = windows.api()
        if gui.IsIconic(root["handle"]):
            gui.ShowWindow(root["handle"], 9)
        windows.notify_activation(root["handle"])
        for command in (("Edit", "Select", "All"), ("Transport", "Locators", "Set Locators to Selection Range"),
                        ("Transport", "Transport Commands", "Go to Project Start"),
                        ("Transport", "Locators", "Set Left Locator to Project Cursor Position"),
                        ("File", "Export", "Audio Mixdown...")):
            _invoke(root, command)
            time.sleep(.2)
        if native.exists() or target.exists():
            raise IntegrationError("OUTPUT_EXISTS", "A mixdown output appeared before dispatch.")
        _dispatch_mixdown(_mixdown_window(root["pid"]), native)
        deadline = time.monotonic() + 50
        last_size = 0
        while time.monotonic() < deadline:
            dialogs = windows.dialogs(root["pid"])
            if dialogs:
                raise IntegrationError("UNEXPECTED_CUBASE_DIALOG", ", ".join(w["title"] for w in dialogs))
            size = native.stat().st_size if native.is_file() else 0
            if size > 44 and size == last_size:
                _, data, sha = read_file(native)
                try:
                    geometry = _wav_header(data)
                except IntegrationError as exc:
                    if exc.code != "MIXDOWN_WAV_PENDING":
                        raise
                else:
                    break
            last_size = size
            time.sleep(.35)
        else:
            raise IntegrationError("MIXDOWN_UNVERIFIED", "Cubase did not finalize the expected WAV. Check the output before retrying.")
        duration_ok = expected_duration_seconds is None or abs(geometry["duration_seconds"] - expected_duration_seconds) <= .05
        result = {"path": str(native), "sha256": sha, "bytes": len(data)}
        if duration_ok:
            result = write_result(None, str(target), data)
            try:
                if digest(native.read_bytes()) == sha:
                    native.unlink()
            except OSError:
                result["native_export_path"] = str(native)
        return result | geometry | {"verified": duration_ok, "duration_independently_checked": expected_duration_seconds is not None,
                                     "scope": "Stereo Out; project start to the selection locator for all project events"}
