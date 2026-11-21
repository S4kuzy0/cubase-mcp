"""Common file tool operations and source-hash contracts."""

from __future__ import annotations

from pathlib import Path

from . import cpr, midi
from .errors import IntegrationError
from .storage import read_file, write_result


def adapter(path):
    adapters = {".mid": midi, ".midi": midi}
    if path.suffix.lower() not in adapters:
        raise IntegrationError(
            "UNSUPPORTED_FORMAT",
            "Use a .mid or .midi file for MIDI note edits.",
        )
    return adapters[path.suffix.lower()]


def read_expected(path, expected_sha256):
    source, data, sha = read_file(path)
    if sha != expected_sha256.lower():
        raise IntegrationError(
            "FILE_CHANGED", "The input differs from the inspected source hash."
        )
    return source, data, sha


def inspect_project(
    path: str, include_notes: bool = False, offset: int = 0, limit: int = 200
):
    source, data, sha = read_file(path)
    result = (
        cpr.inspect(source)
        if source.suffix.lower() == ".cpr"
        else adapter(source).inspect(data, include_notes)
    )
    if "notes" in result:
        if offset < 0 or not 1 <= limit <= 2000:
            raise IntegrationError(
                "INVALID_PAGE", "Use nonnegative offset and a limit from 1 to 2000."
            )
        result["notes"] = result["notes"][offset : offset + limit]
        result["next_offset"] = (
            offset + limit if offset + limit < result["note_count"] else None
        )
    return {"path": str(source), "sha256": sha, "bytes": len(data)} | result


def edit_notes(path: str, output_path: str, changes: list[dict], expected_sha256: str):
    source, data, sha = read_expected(path, expected_sha256)
    if not changes or len(changes) > 10000:
        raise IntegrationError(
            "INVALID_CHANGESET", "Supply between 1 and 10000 note changes."
        )
    if source.suffix.lower() != Path(output_path).suffix.lower():
        raise IntegrationError(
            "FORMAT_MISMATCH",
            "Editing preserves the input format; use the same file extension.",
        )
    result = adapter(source).edit(data, changes)
    written = write_result(source, output_path, result, expected_sha256)
    return written | {
        "changed_notes": len(changes),
        "source_sha256": sha,
        "live_state_updated": False,
    }


def transpose(
    path: str,
    output_path: str,
    semitones: int,
    expected_sha256: str,
    track_index: int | None = None,
):
    if not -127 <= semitones <= 127:
        raise IntegrationError(
            "INVALID_TRANSPOSE", "Semitone offset must be between -127 and 127."
        )
    source, data, _ = read_expected(path, expected_sha256)
    rows = adapter(source).inspect(data, True)["notes"]
    changes = [
        {"id": row["id"], "note_num": row["note_num"] + semitones}
        for row in rows
        if track_index is None or row["track_index"] == track_index
    ]
    return edit_notes(path, output_path, changes, expected_sha256)


def _require_midi_path(path):
    if Path(path).suffix.lower() not in (".mid", ".midi"):
        raise IntegrationError("FORMAT_MISMATCH", "Use a .mid or .midi file.")


def create_midi(output_path, tracks, tempo_bpm, numerator, denominator, ticks_per_beat, length_ticks):
    _require_midi_path(output_path)
    result = midi.create(tracks, tempo_bpm, numerator, denominator, ticks_per_beat, length_ticks)
    return write_result(None, output_path, result) | midi.inspect(result) | {"live_state_updated": False}


def _write_midi_edit(path, output_path, expected_sha256, transform, *args):
    _require_midi_path(path)
    _require_midi_path(output_path)
    source, data, sha = read_expected(path, expected_sha256)
    result = transform(data, *args)
    return write_result(source, output_path, result, sha) | midi.inspect(result) | {"live_state_updated": False}


def arrange_midi_notes(path, output_path, expected_sha256, additions, deletions):
    return _write_midi_edit(path, output_path, expected_sha256, midi.arrange, additions, deletions)


def set_midi_track_patch(path, output_path, expected_sha256, track_index, channel, program, bank_msb, bank_lsb):
    return _write_midi_edit(path, output_path, expected_sha256, midi.set_initial_patch,
                            track_index, channel, program, bank_msb, bank_lsb)
