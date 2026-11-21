import mido
import pytest

from cubase_mcp import midi
from cubase_mcp.errors import IntegrationError


NOTE = {"start_tick": 0, "duration_tick": 120, "note_num": 60, "velocity": 91}


def test_midi_authoring_roundtrip():
    data = midi.create(
        [{"name": "主旋律", "channel": 0, "program": 81, "bank_msb": 2, "notes": [NOTE]},
         {"name": "Clap", "channel": 9, "notes": [NOTE | {"note_num": 39}]}],
        138, 4, 4, 480, 1920,
    )
    original = midi.inspect(data, True)
    assert [track["name"] for track in original["tracks"]] == ["", "主旋律", "Clap"]
    assert original["note_count"] == 2
    file, _ = midi.load(data)
    assert file.tracks[0][0].tempo == mido.bpm2tempo(138)
    assert all(sum(event.time for event in track) == 1920 for track in file.tracks)

    lead_id = original["notes"][0]["id"]
    replacement = NOTE | {"track_index": 1, "channel": 0, "note_num": 62}
    updated = midi.arrange(data, [replacement], [lead_id])
    revised = midi.inspect(updated, True)
    assert [(n["track_index"], n["note_num"]) for n in revised["notes"]] == [(1, 62), (2, 39)]
    assert revised["tracks"][1]["name"] == "主旋律"
    pitched = midi.set_initial_patch(updated, 1, 0, 87, None, 3)
    file, _ = midi.load(pitched)
    patch = [(m.type, m.control, m.value) if m.type == "control_change" else (m.type, m.program)
             for m in file.tracks[1] if m.type in ("control_change", "program_change")]
    assert patch == [("control_change", 0, 2), ("control_change", 32, 3), ("program_change", 87)]
    inspected = midi.inspect(pitched, True)
    assert inspected["tracks"][1]["initial_patches"] == [
        {"channel": 0, "program": 87, "bank_msb": 2, "bank_lsb": 3}
    ]
    assert inspected["notes"][-1] == revised["notes"][-1]


def test_new_same_pitch_overlap_is_rejected():
    data = midi.create([{"name": "Bass", "channel": 0, "notes": [NOTE]}], 138, 4, 4, 480, None)
    with pytest.raises(IntegrationError, match="MIDI_NOTE_OVERLAP"):
        midi.arrange(data, [NOTE | {"track_index": 1, "channel": 0, "start_tick": 60}], [])
