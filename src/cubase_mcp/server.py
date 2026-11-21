"""Local stdio tools. Logs go to stderr."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from . import __version__, audio_templates, cpr_midi, cubase, live_midi, midiloop
from . import project_templates, projects, routing, storage, vst_catalog, windows
from .errors import IntegrationError

mcp = MCPServer(
    "cubase",
    instructions=(
        "Cubase project, MIDI and host tools. Inspect before file writes and use the inspected SHA-256. "
        "Read live track IDs and their revision before track writes. Transport and mixer writes require host feedback. "
        "Routing is an explicit fallback for the validated Cubase 14 English layout. "
        "Use the separate Piapro MCP to load a vocal instrument or edit a score."
    ),
)
READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)


class NoteChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    start_tick: int | None = None
    duration_tick: int | None = None
    note_num: int | None = None
    velocity: int | None = None
    lyric: str | None = None
    phoneme: str | None = None


class AudioReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    expected_sha256: str


class MidiNote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start_tick: int
    duration_tick: int
    note_num: int
    velocity: int = 80


class MidiTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    channel: int
    notes: list[MidiNote]
    program: int | None = None
    bank_msb: int | None = None
    bank_lsb: int | None = None


class MidiPlacedNote(MidiNote):
    track_index: int
    channel: int


@mcp.tool(annotations=READ)
def get_capabilities() -> dict:
    """Report transport, file formats and the bounds of the live Cubase adapter."""
    return {
        "version": __version__,
        "transport": "stdio",
        "file_editing": ["standard-midi"],
        "cpr": "partial read-only inspection; separate projects can be built from verified local templates",
        "live_control": "transport and selected-channel mixer with MIDI Remote readback",
        "live_tracks": "first 64 supported audio/instrument/sampler/MIDI channels, native UID and revision",
        "track_creation": "audio and MIDI; MIDI uses the validated Cubase 14 English dialog fallback",
        "midi_routing": "validated Cubase 14 English inspector with explicit bounded fallback",
        "vst3_catalog": "configured Cubase scan cache; metadata is independent of plugin assignment",
        "state_dir": str(storage.state_dir()),
    }


@mcp.tool(annotations=READ)
def inspect_project(
    path: str, include_notes: bool = False, offset: int = 0, limit: int = 200
) -> dict:
    """Read MIDI or partial CPR. Returns current SHA-256; note pages have stable IDs for this hash."""
    return projects.inspect_project(path, include_notes, offset, limit)


@mcp.tool(annotations=WRITE)
def edit_notes(
    path: str, output_path: str, changes: list[NoteChange], expected_sha256: str
) -> dict:
    """Edit specified existing notes directly. Same-path overwrite requires the inspected hash and creates a durable backup. Other outputs must be new."""
    return projects.edit_notes(
        path,
        output_path,
        [change.model_dump(exclude_none=True) for change in changes],
        expected_sha256,
    )


@mcp.tool(annotations=WRITE)
def transpose_notes(
    path: str,
    output_path: str,
    semitones: int,
    expected_sha256: str,
    track_index: int | None = None,
) -> dict:
    """Transpose existing notes in a file or one track, preserving all unrelated data; live editor is not changed."""
    return projects.transpose(
        path, output_path, semitones, expected_sha256, track_index
    )


@mcp.tool(annotations=READ)
def list_recovery_copies(original_path: str | None = None) -> dict:
    """List up to 100 recorded recovery copies, optionally for an exact original path."""
    return {"backups": [item for item in storage.list_backups(original_path)
                        if Path(item["original_path"]).suffix.lower() in ('.mid', '.midi')]}


@mcp.tool(annotations=WRITE)
def restore_recovery_copy(
    backup_path: str, target_path: str, expected_current_sha256: str
) -> dict:
    """Restore a recorded backup to its original target, after checking current hash and backing up the current version."""
    if Path(target_path).suffix.lower() not in ('.mid', '.midi'):
        raise IntegrationError("UNSUPPORTED_FORMAT", "Use the MCP that owns this file format.")
    return storage.restore_backup(backup_path, target_path, expected_current_sha256)


@mcp.tool(annotations=READ)
def daw_get_status() -> dict:
    """Read Cubase windows and MIDI feedback status; no focus, clicks or mutations."""
    return {
        "windows": windows.windows() if os.name == "nt" else [],
        "midi": live_midi.get_state(),
    }


@mcp.tool(annotations=WRITE)
def create_midi(
    output_path: str, tracks: list[MidiTrack], tempo_bpm: float,
    numerator: int = 4, denominator: int = 4, ticks_per_beat: int = 480,
    length_ticks: int | None = None,
) -> dict:
    """Write a new Standard MIDI File type 1 with a conductor and named note tracks. Channels, program and optional bank bytes are zero-based. Program Change is MIDI data, independent of VST assignment. File-only."""
    return projects.create_midi(output_path, [track.model_dump() for track in tracks], tempo_bpm,
                                numerator, denominator, ticks_per_beat, length_ticks)


@mcp.tool(annotations=WRITE)
def arrange_midi_notes(
    path: str, output_path: str, expected_sha256: str,
    additions: list[MidiPlacedNote] | None = None, deletions: list[str] | None = None,
) -> dict:
    """Insert or delete notes in a Standard MIDI File. Use current file SHA-256 and inspected note IDs. Returns a saved file and per-track counts. File-only."""
    return projects.arrange_midi_notes(path, output_path, expected_sha256,
                                       [note.model_dump() for note in additions or []], deletions or [])


@mcp.tool(annotations=WRITE)
def set_midi_track_patch(
    path: str, output_path: str, expected_sha256: str, track_index: int, channel: int,
    program: int, bank_msb: int | None = None, bank_lsb: int | None = None,
) -> dict:
    """Set the initial Bank Select/Program Change on the specified SMF track and channel. Values and indexes are zero-based. Later Program Change events are preserved. This writes MIDI data only."""
    return projects.set_midi_track_patch(path, output_path, expected_sha256,
                                         track_index, channel, program, bank_msb, bank_lsb)


@mcp.tool(annotations=WRITE)
def cubase_get_midi_routing(track_id: str, expected_revision: str,
                            allow_dialog_fallback: bool = False) -> dict:
    """Select a fresh live host UID and read recognized MIDI routes through the Cubase 14 English Routing inspector. Explicit UI fallback required: local Windows OCR and bounded window-relative messages; no physical cursor. Requires stopped transport and an expanded supported inspector. Returns actual selected labels, route IDs, host_revision and routing revision. Instrument tracks expose input only; MIDI tracks expose both. This is UI readback, not a native routing API."""
    return routing.get_routing(track_id, expected_revision, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def cubase_set_midi_routing(track_id: str, expected_revision: str, expected_routing_revision: str,
                            input_route_id: str | None = None, output_route_id: str | None = None,
                            allow_dialog_fallback: bool = False) -> dict:
    """Set current route IDs from cubase_get_midi_routing, with its host_revision and routing revision. Explicit bounded UI fallback required. Reopens menus to verify actual input/output and retains a recovery checkpoint. Reserved MCP ports and unverified All MIDI Inputs are blocked. MIDI output applies ONLY to MIDI tracks; never replaces an instrument slot. No automatic Cubase save. On failure inspect recovery status before retrying."""
    return routing.set_routing(track_id, expected_revision, expected_routing_revision,
                               input_route_id, output_route_id, allow_dialog_fallback)


@mcp.tool(annotations=WRITE)
def cubase_control(action: str, value: float | None = None) -> dict:
    """Control play/stop/record or selected channel volume/pan/mute/solo via MIDI Remote. Success requires host feedback. Record starts recording."""
    return live_midi.control(action, value)


@mcp.tool(annotations=READ)
def cubase_list_templates() -> dict:
    """List hash-verified local standalone CPR templates. Templates contain no bundled commercial plugins or voicebanks."""
    return project_templates.list_templates()


@mcp.tool(annotations=WRITE)
def cubase_create_project(
    template_id: str, project_directory: str, name: str | None = None,
    open_project: bool = True, timeout_seconds: int = 40,
) -> dict:
    """Create a native project by byte-copying a registered local template into a NEW absolute directory. Never replaces existing content or closes another project. Opening requires no currently open project. Check live_state_verified; startup/licensing may remain pending. The Piapro MCP can verify its editor separately."""
    return cubase.create_project(template_id, project_directory, name, open_project, timeout_seconds)


@mcp.tool(annotations=READ)
def cubase_inspect_audio_template(path: str) -> dict:
    """Read a supported Cubase 14 RIF2 template's WAV slots, track names, geometry, and source SHA-256. A PCM WAV path returns replacement format and SHA-256. The CPR must use one full-length WAV at frame zero per track, copied to its Audio folder."""
    return audio_templates.inspect(path)


@mcp.tool(annotations=READ)
def cubase_verify_midi_import(
    path: str, midi_path: str, expected_start_tick: int = 0, track_map: dict[str, str] | None = None,
) -> dict:
    """Compare SMF note tracks with single parts on MIDI or instrument tracks in a saved Cubase 14 RIF2 project, including notes from native MIDI Loops. Optionally map exact source names to destination names. Checks placement, timing, length, pitch, velocity and channel at 480 ticks per quarter. Reports part length separately. Read-only."""
    return cpr_midi.verify(path, midi_path, expected_start_tick, track_map)


@mcp.tool(annotations=READ)
def cubase_inspect_midi_loop(path: str) -> dict:
    """Read an exported Cubase 14 sfizz-multi instrument MIDI Loop's hash, part, tempo and SFZ path. File-only."""
    return midiloop.inspect(path)


@mcp.tool(annotations=WRITE)
def cubase_create_midi_loop(
    template_path: str, expected_template_sha256: str, midi_path: str,
    expected_midi_sha256: str, track_name: str, sfz_path: str, output_path: str,
) -> dict:
    """Create a new .midiloop from one named SMF note track, an existing SFZ, and a hash-checked native sfizz-multi loop. Keeps the template tempo and meter. Notes use whole ticks at 480 PPQ and the track ends on a quarter note. Copies note events only. Import into empty project space in Cubase to create the instrument track. No CPR writes, host actions, or playback verification."""
    return midiloop.create(template_path, expected_template_sha256, midi_path,
                           expected_midi_sha256, track_name, sfz_path, output_path)


@mcp.tool(annotations=READ)
def cubase_list_vst3_instruments() -> dict:
    """List instrument classes and class IDs from the configured Cubase LE VST3 scan cache. Scanned metadata does not establish current license, loaded state, or assignability."""
    return vst_catalog.list_instruments()


@mcp.tool(annotations=WRITE)
def cubase_create_audio_project(
    path: str, expected_sha256: str, project_directory: str,
    name: str | None = None, replacements: dict[str, AudioReplacement] | None = None,
) -> dict:
    """Create a separate native audio project with its own Audio folder. Use a new sibling directory with a name and CPR filename matching the template's UTF-8 byte widths. Key replacements by inspected slot filename and supply hash-checked PCM WAVs of the same format and frame count. File-only, with no Cubase launch or live playback verification."""
    return audio_templates.create(path, expected_sha256, project_directory, name, {
        slot: (item.path, item.expected_sha256) for slot, item in (replacements or {}).items()
    })


@mcp.tool(annotations=READ)
def cubase_list_tracks() -> dict:
    """Read actual live track IDs, names, plugin identities and selection via MIDI Remote. Returns the revision required by track writes. Scope: first 64 audio/instrument/sampler/MIDI channels, including hidden channels; excludes folders, inputs and outputs. CPR indexes cannot be substituted."""
    return live_midi.list_tracks()


@mcp.tool(annotations=WRITE)
def cubase_select_track(track_id: str, expected_revision: str) -> dict:
    """Select a live host ID from cubase_list_tracks. Reject a stale revision; requires repeated real selected-track feedback. No cursor operations."""
    with storage.file_lock(storage.state_dir() / "live-daw"):
        return live_midi.select_track(track_id, expected_revision)


@mcp.tool(annotations=WRITE)
def cubase_add_track(
    track_type: str, name: str, expected_revision: str,
    allow_dialog_fallback: bool = False,
) -> dict:
    """Add one audio or MIDI track and verify its native identity. MIDI selectors use the explicit Cubase 14 English dialog fallback."""
    if track_type not in ("audio", "midi"):
        raise IntegrationError("UNSUPPORTED_TRACK_TYPE", "Use audio or midi. Piapro loading is in the Piapro MCP.")
    return cubase.add_track(track_type, name, expected_revision, allow_dialog_fallback)


def main():
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
