"""Copy a Cubase RIF2 audio template whose native layout can be verified."""

from __future__ import annotations

import io
import json
import ntpath
import re
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .errors import IntegrationError
from .project_templates import project_name
from .storage import digest, file_lock, read_file, verify_hash


UTF8_END = b"\0\xef\xbb\xbf"
OBJECT_DEF = b"\xff" * 8
OBJECT_REF = 1 << 63
ORIGIN_FIELD = b"\x00\x00\x00\x09OrigPath\0\0\x22"
HOME_FIELD = b"\0" * 19 + b"\x01\x00\x01"


def _require(condition: bool) -> None:
    if not condition:
        raise IntegrationError(
            "UNSUPPORTED_AUDIO_TEMPLATE",
            "Expected a Cubase RIF2 project with one full-length WAV event per audio track "
            "and media copied into the project's Audio directory.",
        )


def _number(data: bytes, offset: int, fmt: str = ">Q") -> int:
    _require(0 <= offset <= len(data) - struct.calcsize(fmt))
    return struct.unpack_from(fmt, data, offset)[0]


@dataclass(frozen=True)
class NativeString:
    start: int
    end: int
    text: str


def _string(data: bytes, offset: int, limit: int) -> NativeString:
    start = offset + 4
    end = start + _number(data, offset, ">I")
    _require(start + len(UTF8_END) <= end <= limit)
    _require(data[end - len(UTF8_END):end] == UTF8_END)
    payload = data[start:end - len(UTF8_END)]
    _require(b"\0" not in payload)
    try:
        return NativeString(start, end - len(UTF8_END), payload.decode("utf-8"))
    except UnicodeError as exc:
        raise IntegrationError("UNSUPPORTED_AUDIO_TEMPLATE", "Invalid UTF-8 CPR field.") from exc


@dataclass(frozen=True)
class Record:
    header: int
    start: int
    end: int

    def contains(self, other: Record) -> bool:
        return self.start <= other.header and other.end <= self.end


def _arrangement(data: bytes) -> tuple[int, int]:
    _require(data[:4] == b"RIF2" and data[12:16] == b"NUND")
    _require(_number(data, 4) == len(data) - 16)
    offset = 16
    root = b""
    arrangements = []
    while offset < len(data):
        tag = data[offset:offset + 4]
        start = offset + 12
        end = start + _number(data, offset + 4)
        _require(end <= len(data) and tag in (b"ROOT", b"ARCH"))
        if tag == b"ROOT":
            root = data[start:end]
        elif root.endswith(b"\0\0\0\x0cPArrangement"):
            # The first eight ARCH payload bytes precede the class reference base.
            arrangements.append((start + 8, end))
        offset = end
    _require(len(arrangements) == 1)
    return arrangements[0]


def _records(data: bytes, base: int, limit: int, name: bytes, version: int) -> list[Record]:
    definition = OBJECT_DEF + struct.pack(">I", len(name) + 1) + name + b"\0"
    first = data.find(definition, base, limit)
    _require(first >= base and data.find(definition, first + len(definition), limit) == -1)
    version_offset = first + len(definition)
    _require(_number(data, version_offset, ">H") == version)
    reference = struct.pack(">Q", OBJECT_REF | (first + len(OBJECT_DEF) - base))
    offsets = [(first, version_offset + 2)]
    references = re.compile(re.escape(reference)).finditer(data, version_offset + 2, limit)
    offsets.extend((match.start(), match.end()) for match in references)
    records = []
    for header, size_offset in offsets:
        start = size_offset + 8
        end = start + _number(data, size_offset)
        _require(start < end <= limit)
        records.append(Record(header, start, end))
    return records


def _folder(path: PureWindowsPath | Path) -> str:
    return str(PureWindowsPath(path)).rstrip("\\") + "\\"


def _file_location(data: bytes, record: Record, name: NativeString) -> tuple[bytes, NativeString]:
    offset = name.end + len(UTF8_END)
    tag = data[offset:offset + 4]
    _require(tag in (b"WAVE", b"NDPR"))
    offset += 4
    for index in range(3):
        # NDPR can leave its second extension field empty after a native re-save.
        size = _number(data, offset, ">I")
        valid_empty = tag == b"NDPR" and index == 1
        _require((size > 0 or valid_empty) and size <= 100 and offset + 4 + size <= record.end)
        offset += 4 + size
    _require(data[offset:offset + 6] == b"\0\0\0\x01\0\0")
    folder = _string(data, offset + 6, record.end)
    _require(folder.end + len(UTF8_END) == record.end)
    return tag, folder


@dataclass(frozen=True)
class Slot:
    track: str
    name: str
    frames: int
    primary: NativeString
    origin: NativeString


@dataclass(frozen=True)
class Layout:
    home_name: NativeString
    home_parent: NativeString
    project_name: NativeString
    project_folder: NativeString
    slots: list[Slot]


def read_layout(data: bytes) -> Layout:
    base, limit = _arrangement(data)
    path_records = _records(data, base, limit, b"FNPath", 0)
    track_records = _records(data, base, limit, b"MAudioTrackEvent", 1)
    audio_records = _records(data, base, limit, b"MAudioEvent", 6)
    _require(len(track_records) == len(audio_records) <= 64)
    _require(len(path_records) == 2 * len(audio_records) + 2)
    slots = []
    used_paths = set()
    for track in track_records:
        events = [event for event in audio_records if track.contains(event)]
        _require(len(events) == 1)
        event = events[0]
        _require(track.start + 42 < event.header and event.start + 18 <= event.end)
        _require(struct.unpack_from(">d", data, track.start + 2)[0] == 0.0)
        track_name = _string(data, track.start + 42, event.header).text
        start, length = struct.unpack_from(">dd", data, event.start + 2)
        _require(start == 0.0 and 0 < length <= 256 * 1024 * 1024 and length.is_integer())
        media = [path for path in path_records if event.contains(path)]
        _require(len(media) == 2 and not used_paths.intersection(media))
        primary, origin = media
        _require(data[origin.header - len(ORIGIN_FIELD):origin.header] == ORIGIN_FIELD)
        primary_name = _string(data, primary.start, primary.end)
        origin_name = _string(data, origin.start, origin.end)
        _require(primary_name.text == origin_name.text and primary_name.text.lower().endswith(".wav"))
        filename = primary_name.text
        project_name(filename)
        primary_tag, primary_folder = _file_location(data, primary, primary_name)
        origin_tag, origin_folder = _file_location(data, origin, origin_name)
        _require(primary_tag == origin_tag == b"WAVE")
        slots.append(Slot(track_name, filename, int(length), primary_folder, origin_folder))
        used_paths.update(media)
    _require(len({slot.name.casefold() for slot in slots}) == len(slots))

    home, project = [path for path in path_records if path not in used_paths]
    home_name = _string(data, home.start, home.end)
    home_offset = home_name.end + len(UTF8_END)
    _require(data[home_offset:home_offset + len(HOME_FIELD)] == HOME_FIELD)
    home_parent = _string(data, home_offset + len(HOME_FIELD), home.end)
    _require(home_parent.end + len(UTF8_END) == home.end)
    filename = _string(data, project.start, project.end)
    tag, project_folder = _file_location(data, project, filename)
    stored = PureWindowsPath(project_folder.text)
    _require(tag == b"NDPR" and filename.text.lower().endswith(".cpr") and stored.is_absolute())
    _require(PureWindowsPath(home_parent.text) / home_name.text == stored)
    _require(all(PureWindowsPath(slot.primary.text) == stored / "Audio" for slot in slots))
    return Layout(home_name, home_parent, filename, project_folder, slots)


def _wave_info(data: bytes) -> dict:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels, width, rate, frames, _, _ = wav.getparams()
            if not frames or len(wav.readframes(frames + 1)) != channels * width * frames:
                raise wave.Error("Incomplete or unaligned sample data")
    except (wave.Error, EOFError, ValueError, struct.error) as exc:
        raise IntegrationError("UNSUPPORTED_PCM_WAV", "Use a complete, uncompressed PCM WAV.") from exc
    return {"channels": channels, "sample_width": width, "sample_rate": rate, "frames": frames}


def _template_media(source: Path, layout: Layout) -> list[dict]:
    folder = source.parent / "Audio"
    media = []
    for slot in layout.slots:
        path, data, sha = read_file(folder / slot.name)
        _require(path == folder / slot.name)
        info = _wave_info(data)
        _require(info["frames"] == slot.frames)
        media.append({"track": slot.track, "slot": slot.name, "sha256": sha,
                      "path": str(path), "start_frame": 0, **info})
    return media


def inspect(path: str) -> dict:
    source, data, sha = read_file(path)
    if source.suffix.lower() == ".wav":
        return {"path": str(source), "sha256": sha, **_wave_info(data)}
    _require(source.suffix.lower() == ".cpr")
    layout = read_layout(data)
    return {"path": str(source), "sha256": sha, "stored_project_folder": layout.project_folder.text,
            "project_folder_name_utf8_bytes": layout.home_name.end - layout.home_name.start,
            "project_filename_utf8_bytes": layout.project_name.end - layout.project_name.start,
            "media": _template_media(source, layout)}


def _relocate(data: bytes, layout: Layout, directory: Path, filename: str) -> bytes:
    project_folder = _folder(directory)
    audio_folder = _folder(directory / "Audio")
    project = bytearray(data)

    def replace(field: NativeString, new: str) -> None:
        value = new.encode("utf-8")
        if len(value) != field.end - field.start:
            raise IntegrationError(
                "AUDIO_TEMPLATE_PATH_WIDTH",
                "Use a new sibling project folder and CPR filename with the template's UTF-8 byte lengths.",
            )
        project[field.start:field.end] = value

    replace(layout.home_name, directory.name)
    replace(layout.home_parent, _folder(directory.parent))
    replace(layout.project_name, filename)
    replace(layout.project_folder, project_folder)
    for slot in layout.slots:
        replace(slot.primary, audio_folder)
        # Keep the native archive offsets and make the original-import fallback
        # resolve to the same local Audio directory.
        extra = slot.origin.end - slot.origin.start - len(audio_folder.encode("utf-8"))
        _require(extra >= 0 and extra % 2 == 0)
        replace(slot.origin, audio_folder + ".\\" * (extra // 2))
    result = bytes(project)
    relocated = read_layout(result)
    _require(relocated.project_folder.text == project_folder and all(
        slot.primary.text == audio_folder
        and ntpath.normpath(slot.origin.text) == ntpath.normpath(audio_folder)
        for slot in relocated.slots
    ))
    return result


def create(path: str, expected_sha256: str, project_directory: str, name: str | None,
           replacements: dict[str, tuple[str, str]]) -> dict:
    source, data, sha = read_file(path)
    if sha != expected_sha256.lower():
        raise IntegrationError("FILE_CHANGED", "Inspect the current template before creating a project.")
    _require(source.suffix.lower() == ".cpr")
    layout = read_layout(data)
    directory = Path(project_directory).expanduser()
    if not directory.is_absolute():
        raise IntegrationError("ABSOLUTE_PATH_REQUIRED", "Specify the complete new project directory.")
    directory = directory.resolve()
    _require(PureWindowsPath(directory).is_absolute())
    filename = project_name(name if name is not None else directory.name) + ".cpr"
    project = _relocate(data, layout, directory, filename)
    template_media = _template_media(source, layout)
    if replacements.keys() - {slot.name for slot in layout.slots}:
        raise IntegrationError("UNKNOWN_AUDIO_SLOT", "Use the WAV slot names from template inspection.")
    output_media = []
    with file_lock(directory):
        if directory.exists():
            raise IntegrationError("PROJECT_DIRECTORY_EXISTS", "Choose a new project directory.")
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".mcp-audio-", dir=directory.parent) as scratch:
            stage = Path(scratch) / "project"
            (stage / "Audio").mkdir(parents=True)
            for item in template_media:
                slot = item["slot"]
                wav_path, wav_hash = replacements.get(slot, (item["path"], item["sha256"]))
                _, wav, actual_sha = read_file(wav_path)
                if actual_sha != wav_hash.lower():
                    raise IntegrationError("FILE_CHANGED", f"Inspect the current WAV for {slot}.")
                if any(item[key] != value for key, value in _wave_info(wav).items()):
                    raise IntegrationError("AUDIO_FORMAT_MISMATCH", f"Match the PCM format and frame count of {slot}.")
                (stage / "Audio" / slot).write_bytes(wav)
                output_media.append(item | {"path": str(directory / "Audio" / slot), "sha256": actual_sha})
            (stage / filename).write_bytes(project)
            record = {"path": str(directory / filename), "project_path": str(directory / filename),
                      "sha256": digest(project), "template_sha256": sha, "project_created": True,
                      "media": output_media, "live_state_verified": False}
            (stage / ".mcp-project.local.json").write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                                          encoding="utf-8")
            verify_hash(source, sha)
            stage.rename(directory)
    return record
