import io
import json
import struct
import wave

import mido
import pytest

from cubase_mcp import host_parameters, midi, project_io
from cubase_mcp.errors import IntegrationError


def test_mixdown_checks_complete_audio_data():
    sink = io.BytesIO()
    with wave.open(sink, "wb") as output:
        output.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))
    raw = sink.getvalue()
    checked = project_io._wav_header(raw)
    assert checked["frames"] == 2
    assert checked["sample_rate"] == 48000
    assert checked["channels"] == 2
    with pytest.raises(IntegrationError, match="MIXDOWN_WAV_PENDING"):
        project_io._wav_header(raw[:-2])


def test_save_as_rejects_an_existing_output_before_host_commands(tmp_path, monkeypatch):
    output = tmp_path / "existing.cpr"
    output.write_bytes(b"original")
    monkeypatch.setattr(project_io, "_stopped_project", lambda name: pytest.fail("opened Cubase"))
    with pytest.raises(IntegrationError, match="OUTPUT_EXISTS"):
        project_io.save_as("fixture", str(output))
    assert output.read_bytes() == b"original"


def test_direct_reply_reassembles_Unicode_and_reversed_frames():
    class Incoming:
        pending = []

        def iter_pending(self):
            messages, self.pending = self.pending, []
            return iter(messages)

    class Outgoing:
        def send(self, message):
            nonce = message.data[4]
            data = json.dumps({"result": {"name": "低音"}}, ensure_ascii=False).encode("utf-16-le")
            packed = []
            for (unit,) in struct.iter_unpack("<H", data):
                packed.extend((unit & 127, (unit >> 7) & 127, (unit >> 14) & 3))
            for index, part in ((1, packed[12:]), (0, packed[:12])):
                incoming.pending.append(mido.Message("sysex", data=(125, 67, 80, 7, nonce, index, 2, *part)))

    incoming = Incoming()
    assert host_parameters._rpc(incoming, Outgoing(), {"op": "inspect"}) == {"name": "低音"}


def test_invalid_mixer_batch_has_no_side_effects(monkeypatch):
    monkeypatch.setattr(host_parameters.cubase, "project_root", lambda: pytest.fail("opened Cubase"))
    with pytest.raises(IntegrationError, match="INVALID_MIX_BATCH"):
        host_parameters.mix_channels("test", [{"track_id": "x", "field": "mute", "value": .3}])


def test_controller_merge_preserves_notes_and_other_channels():
    file = mido.MidiFile()
    track = file.add_track("Controller fixture")
    track.append(mido.Message("note_on", note=60, velocity=90))
    track.append(mido.Message("control_change", channel=0, control=11, value=30, time=30))
    track.append(mido.Message("control_change", channel=1, control=11, value=40))
    track.append(mido.Message("note_off", note=60, time=90))
    sink = io.BytesIO()
    file.save(file=sink)
    source = sink.getvalue()

    output = midi.controller_points(source, 0, 0, 11, [{"tick": 30, "value": 70}, {"tick": 90, "value": 50}], "merge")
    assert midi.controller_points(output, 0, 0, 11) == [{"tick": 30, "value": 70}, {"tick": 90, "value": 50}]
    assert midi.controller_points(output, 0, 1, 11) == [{"tick": 30, "value": 40}]
    assert midi.inspect(output, include_notes=True) == midi.inspect(source, include_notes=True)
