import struct

import pytest
from lxml import etree

from cubase_mcp import midi, midiloop, storage
from cubase_mcp.errors import IntegrationError


def _template(path, bank):
    encoded = bank.as_posix().encode("utf-8") + b"\0"
    state = (struct.pack("<QI", 5, len(encoded)) + encoded + b"opaque-tail").hex()
    path.write_text(f"""<midiloop>
  <rootObjects>
    <root name="midipart" ID="1"/><root name="tracktemplate" ID="2"/><root name="metadata" ID="3"/>
  </rootObjects>
  <obj ID="1">
    <float name="Start" value="0"/><float name="Length" value="480"/>
    <obj class="MMidiPart" name="Node">
      <string name="Name" value="Reference"/>
      <list name="Events" type="obj">
        <obj class="MMidiNote" ID="1000000">
          <float name="Start" value="0"/><float name="Length" value="10"/>
          <int name="Data1" value="60"/><int name="Data2" value="65"/><int name="Channel" value="0"/>
          <float name="HrData" value="0"/>
          <member name="Additional Attributes"><int name="VOCE" value="1"/></member>
        </obj>
        <obj class="MMidiProgramChange" ID="8"/>
      </list>
    </obj>
  </obj>
  <obj ID="2">
    <string name="tracktype" value="Instrument"/>
    <obj class="MListNode" name="Node"><string name="Name" value="Reference"/></obj>
    <obj name="Track Device">
      <member name="DeviceAttributes">
        <member name="Name"><string name="String" value="Reference"/></member>
        <member name="Synth Slot">
          <member name="Plugin">
            <member name="Plugin UID"><string name="GUID" value="{midiloop.SFIZZ_MULTI_UID}"/></member>
            <string name="Plugin Name" value="sfizz-multi"/><bin name="audioComponent">{state}</bin>
          </member>
          <list name="Output Channels"><item><member name="Output Channel"><member name="Name">
            <string name="String" value="Reference"/>
          </member></member></item></list>
        </member>
      </member>
    </obj>
  </obj>
  <obj ID="3"><member name="Additional Attributes">
    <int name="MusicalBeats" value="1"/><float name="MusicalTempo" value="120"/>
  </member></obj>
</midiloop>""", encoding="utf-8")


def test_midi_loop_copies_notes_and_patches_sfizz_state_in_new_file(tmp_path):
    template = tmp_path / "reference.midiloop"
    source = tmp_path / "source.mid"
    output = tmp_path / "pulse.midiloop"
    bank = tmp_path / "New bank.sfz"
    bank.touch()
    _template(template, tmp_path / "original.sfz")
    smf = midi.create([{"name": "Pulse", "channel": 1, "notes": [
        {"start_tick": 240, "duration_tick": 120, "note_num": 42, "velocity": 91}
    ]}], 120, 4, 4, 960, 1920)
    source.write_bytes(smf)
    template_sha = midiloop.inspect(str(template))["sha256"]
    args = (str(template), template_sha, str(source), storage.digest(smf), "Pulse", str(bank))

    with pytest.raises(IntegrationError, match="FILE_CHANGED"):
        midiloop.create(*args[:3], "stale", *args[4:], str(output))
    with pytest.raises(IntegrationError, match="INVALID_MIDI_LOOP_OUTPUT"):
        midiloop.create(*args, str(template))
    assert not output.exists()

    result = midiloop.create(*args, str(output))
    assert result["note_count"] == 1 and result["live_state_verified"] is False
    inspected = midiloop.inspect(str(output))
    assert inspected["name"] == "Pulse"
    assert inspected["part_length_ticks"] == 960
    assert inspected["sfz_path"] == str(bank)
    root = etree.parse(str(output))
    assert root.xpath('//*[@name="MusicalBeats"]/@value') == ["2"]
    assert len(root.xpath('//*[@value="Pulse"]')) == 4
    events = root.xpath('//list[@name="Events"]/obj')
    assert [event.get("class") for event in events] == ["MMidiNote"]
    assert [events[0].find(f'*[@name="{field}"]').get("value") for field in midiloop.NOTE_FIELDS] == [
        "120", "60", "42", "91", "1",
    ]
    assert bytes.fromhex(root.xpath('//bin[@name="audioComponent"]')[0].text).endswith(b"opaque-tail")
    assert storage.digest(template.read_bytes()) == template_sha

    with pytest.raises(IntegrationError, match="TARGET_EXISTS"):
        midiloop.create(*args, str(output))
    assert storage.digest(output.read_bytes()) == result["sha256"]
