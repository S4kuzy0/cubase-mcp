import struct

from cubase_mcp import cpr_midi, midi


def _record(name, version, body):
    return b"\xff" * 8 + struct.pack(">I", len(name) + 1) + name + b"\0" + struct.pack(">HQ", version, len(body)) + body


def _chunk(tag, body):
    return tag + struct.pack(">Q", len(body)) + body


def _saved_project(part_start, velocity, track_name=b"Lead", track_kind=b"MMidiTrackEvent", *, loop_note=False):
    name = b"\0\0\0\x08Lead\0\xef\xbb\xbf"
    track_label = struct.pack(">I", len(track_name) + 4) + track_name + b"\0\xef\xbb\xbf"
    native_note = struct.pack(">BdBBB", 0x90, 120.0, 1, 60, velocity) + bytes(6)
    if loop_note:
        native_note = native_note[:-1] + b"\x06ECOV\0\1" + bytes(8)
    native_note += b"GLFX\0\1" + bytes(50)
    native_note += b"adcn\0\1" + struct.pack(">IId", 40 if loop_note else 32, 0, 60.0)
    clip = _record(b"MMidiPart", 2, name + native_note)
    event = _record(b"MMidiPartEvent", 2, b"\0\0" + struct.pack(">ddd", part_start, 960.0, 0.0) + clip)
    track = _record(track_kind, 4, bytes(42) + track_label + event)
    archive = _chunk(b"ROOT", b"\0\0\0\x0cPArrangement") + _chunk(b"ARCH", bytes(8) + track)
    return b"RIF2" + struct.pack(">Q", len(archive)) + b"NUND" + archive


def test_saved_cpr_verification_checks_placement_and_note_data(tmp_path):
    project = tmp_path / "test.cpr"
    source = tmp_path / "source.mid"
    source.write_bytes(midi.create([{"name": "Lead", "channel": 1, "notes": [
        {"start_tick": 240, "duration_tick": 120, "note_num": 60, "velocity": 91}
    ]}], 138, 4, 4, 960, 1920))

    project.write_bytes(_saved_project(0, 91))
    result = cpr_midi.verify(str(project), str(source), 0)
    assert result["verified"] is True
    assert result["tracks"][0]["first_note_offset_ticks"] == 120
    assert result["tracks"][0]["part_length_matches_source"] is True

    project.write_bytes(_saved_project(120, 90))
    result = cpr_midi.verify(str(project), str(source), 0)
    assert result["verified"] is False
    assert result["tracks"][0]["part_starts_at_expected_tick"] is False
    assert result["tracks"][0]["missing_or_changed_notes"] == 1
    assert result["tracks"][0]["extra_or_changed_notes"] == 1

    project.write_bytes(_saved_project(0, 91, b"Lead VST", b"MInstrumentTrackEvent"))
    result = cpr_midi.verify(str(project), str(source), 0, {"Lead": "Lead VST"})
    assert result["verified"] is True
    assert result["tracks"][0]["name"] == "Lead"
    assert result["tracks"][0]["target_name"] == "Lead VST"
    assert result["tracks"][0]["kind"] == "instrument"

    project.write_bytes(_saved_project(0, 91, b"Lead", b"MInstrumentTrackEvent", loop_note=True))
    result = cpr_midi.verify(str(project), str(source), 0)
    assert result["verified"] is True
    assert result["tracks"][0]["note_count"] == 1
