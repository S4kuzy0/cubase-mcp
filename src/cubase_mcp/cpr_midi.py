"""Compare a saved Cubase 14 RIF2 MIDI import with its source SMF."""

from __future__ import annotations

import math
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

from . import audio_templates, midi
from .errors import IntegrationError
from .storage import read_file


TICKS_PER_QUARTER = 480
NOTE_BLOCK = re.compile(rb"\x90.{17}GLFX\x00\x01.{50}adcn\x00\x01\x00\x00\x00\x20", re.DOTALL)
# Notes imported with a native MIDI Loop carry an extra voice attribute.
LOOP_NOTE_BLOCK = re.compile(
    rb"\x90.{11}\x00{5}\x06ECOV\x00\x01.{8}GLFX\x00\x01.{50}adcn\x00\x01\x00\x00\x00\x28",
    re.DOTALL,
)


def _require(condition: bool) -> None:
    if not condition:
        raise IntegrationError(
            "UNSUPPORTED_CPR_MIDI_LAYOUT",
            "Expected Cubase 14 RIF2 tracks with inline note records. Only one non-looped part per matched track can be verified.",
        )


def _track_records(data: bytes, base: int, limit: int, name: bytes) -> list[audio_templates.Record]:
    header = struct.pack(">I", len(name) + 1) + name + b"\0"
    if data.find(audio_templates.OBJECT_DEF + header, base, limit) >= 0:
        return audio_templates._records(data, base, limit, name, 4)

    # With instrument tracks present, Cubase can declare the MIDI superclass
    # without an object body. Its ordinary track instances then use references.
    declaration = b"\xff" * 7 + b"\xfe" + header + b"\0\x04"
    first = data.find(declaration, base, limit)
    if first == -1:
        _require(data.find(b"\0" + name + b"\0", base, limit) == -1)
        return []
    _require(data.find(declaration, first + len(declaration), limit) == -1)
    offset = first + 8
    reference = struct.pack(">Q", audio_templates.OBJECT_REF | (offset - base))
    refs = re.compile(re.escape(reference)).finditer(data, first + len(declaration), limit)
    records = []
    for match in refs:
        start = match.end() + 8
        _require(start < limit)
        end = start + struct.unpack_from(">Q", data, match.end())[0]
        _require(start < end <= limit)
        records.append(audio_templates.Record(match.start(), start, end))
    return records


def _tracks(data: bytes) -> list[dict]:
    try:
        base, limit = audio_templates._arrangement(data)
        tracks = [
            (record, kind) for name, kind in (
                (b"MMidiTrackEvent", "midi"), (b"MInstrumentTrackEvent", "instrument")
            ) for record in _track_records(data, base, limit, name)
        ]
        tracks.sort(key=lambda item: item[0].header)
        events = audio_templates._records(data, base, limit, b"MMidiPartEvent", 2)
        parts = audio_templates._records(data, base, limit, b"MMidiPart", 2)
        output = []
        for track, kind in tracks:
            name = audio_templates._string(data, track.start + 42, track.end).text
            owned = [event for event in events if track.contains(event)]
            summary = {"name": name, "kind": kind, "part_count": len(owned)}
            if len(owned) != 1:
                output.append(summary)
                continue
            event = owned[0]
            clips = [part for part in parts if event.contains(part)]
            _require(len(clips) == 1 and event.start + 26 <= clips[0].header)
            start, length, offset = struct.unpack_from(">ddd", data, event.start + 2)
            _require(math.isfinite(start) and math.isfinite(length) and length > 0 and offset == 0)
            clip = clips[0]
            clip_name = audio_templates._string(data, clip.start, clip.end)
            # Cubase marks both notes and other MIDI messages with GLFX. Only
            # the supported adcn note block carries the matching note duration.
            notes = Counter()
            blocks = [
                block for layout in (NOTE_BLOCK, LOOP_NOTE_BLOCK)
                for block in layout.finditer(data, clip_name.end + 4, clip.end)
            ]
            _require(len(blocks) == data.count(b"adcn\x00\x01", clip.start, clip.end))
            for block in blocks:
                _require(block.end() + 12 <= clip.end)
                status, position, channel, pitch, velocity = struct.unpack_from(">BdBBB", data, block.start())
                duration = struct.unpack_from(">d", data, block.end() + 4)[0]
                _require(status == 0x90 and 0 <= channel < 16 and 0 <= pitch < 128 and 0 < velocity < 128)
                _require(0 <= position < length and 0 < duration <= length)
                notes[(round(position, 8), round(duration, 8), pitch, velocity, channel)] += 1
            output.append(summary | {"part_name": clip_name.text,
                           "part_start_tick": start, "part_length_ticks": length, "notes": notes})
        return output
    except IntegrationError as exc:
        if exc.code != "UNSUPPORTED_AUDIO_TEMPLATE":
            raise
        raise IntegrationError(
            "UNSUPPORTED_CPR_MIDI_LAYOUT", "Expected Cubase 14 RIF2 track and part records."
        ) from exc


def _source_tracks(data: bytes) -> list[dict]:
    file, tracks = midi.load(data)
    if file.ticks_per_beat <= 0:
        raise IntegrationError("UNSUPPORTED_MIDI", "Verification requires a musical ticks-per-quarter time base.")
    notes = defaultdict(Counter)
    for row, index, *_ in midi.note_entries(file, tracks):
        start = round(row["start_tick"] * TICKS_PER_QUARTER / file.ticks_per_beat, 8)
        duration = round(row["duration_tick"] * TICKS_PER_QUARTER / file.ticks_per_beat, 8)
        notes[index][(start, duration, row["note_num"], row["velocity"], row["channel"])] += 1
    sources = [
        {"name": midi._display_name(track.name), "notes": notes[index],
         "source_length_ticks": round(tracks[index][-1][0] * TICKS_PER_QUARTER / file.ticks_per_beat, 8)}
        for index, track in enumerate(file.tracks) if index in notes
    ]
    if not sources or any(not track["name"] for track in sources) or len({
        track["name"] for track in sources
    }) != len(sources):
        raise IntegrationError("AMBIGUOUS_MIDI_TRACKS", "Give every source note track a distinct nonempty name.")
    return sources


def _compare(source: dict, targets: list[dict], expected_start_tick: int, target_name: str) -> dict:
    name = source["name"]
    notes = source["notes"]
    matches = [track for track in targets if track["name"] == target_name]
    result = {"name": name, "expected_note_count": notes.total(),
              "source_length_ticks": source["source_length_ticks"], "matching_tracks": len(matches)}
    if len(matches) != 1 or matches[0]["part_count"] != 1:
        details = matches[0] if len(matches) == 1 else {"part_count": None}
        return details | result | {"target_name": target_name, "verified": False}
    target = matches[0].copy()
    actual = target.pop("notes")
    note_match = actual == notes
    start_match = target["part_start_tick"] == expected_start_tick
    return target | result | {
        "target_name": target_name,
        "verified": note_match and start_match, "note_count": actual.total(),
        "missing_or_changed_notes": (notes - actual).total(),
        "extra_or_changed_notes": (actual - notes).total(),
        "first_note_offset_ticks": min((note[0] for note in actual), default=None),
        "part_length_matches_source": target["part_length_ticks"] == source["source_length_ticks"],
        "part_starts_at_expected_tick": start_match,
    }


def verify(path: str, midi_path: str, expected_start_tick: int, track_map: dict[str, str] | None = None) -> dict:
    if expected_start_tick < 0:
        raise IntegrationError("INVALID_START_TICK", "Use a nonnegative project tick at 480 ticks per quarter note.")
    if Path(path).suffix.lower() != ".cpr" or Path(midi_path).suffix.lower() not in {".mid", ".midi"}:
        raise IntegrationError("FORMAT_MISMATCH", "Specify a saved CPR and an SMF (.mid or .midi).")
    project, cpr_data, cpr_hash = read_file(path)
    source, smf_data, smf_hash = read_file(midi_path)
    expected = _source_tracks(smf_data)
    names = {track["name"] for track in expected}
    overrides = track_map or {}
    mapping = {name: overrides.get(name, name) for name in names}
    if overrides.keys() - names or any(not name for name in mapping.values()) or len(set(mapping.values())) != len(names):
        raise IntegrationError("INVALID_MIDI_TRACK_MAP", "Map source note tracks to distinct, nonempty CPR track names.")
    targets = _tracks(cpr_data)
    result = [_compare(track, targets, expected_start_tick, mapping[track["name"]]) for track in expected]
    return {"path": str(project), "sha256": cpr_hash, "midi_path": str(source), "midi_sha256": smf_hash,
            "ticks_per_quarter": TICKS_PER_QUARTER, "expected_part_start_tick": expected_start_tick,
            "verified": all(track["verified"] for track in result), "tracks": result,
            "scope": "Saved MIDI and instrument track parts, starts, and note-on position, length, pitch, velocity and channel."}
