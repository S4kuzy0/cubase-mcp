"""Prepare Cubase 14 instrument MIDI Loops from a native sfizz-multi export."""

from __future__ import annotations

import struct
from copy import deepcopy
from pathlib import Path
from textwrap import wrap

from lxml import etree

from . import cpr_midi, storage
from .errors import IntegrationError


SFIZZ_MULTI_UID = "C9DA927443794873A900ED81D1946115"
NOTE_FIELDS = ("Start", "Length", "Data1", "Data2", "Channel")


def _require(condition: bool) -> None:
    if not condition:
        raise IntegrationError(
            "UNSUPPORTED_MIDI_LOOP",
            "Use a native Cubase 14 MIDI Loop for a single sfizz-multi instrument with a loaded SFZ and a note part starting at tick zero.",
        )


def _one(parent, xpath, **variables):
    nodes = parent.xpath(xpath, **variables)
    _require(len(nodes) == 1)
    return nodes[0]


def _load(path):
    if Path(path).suffix.lower() != ".midiloop":
        raise IntegrationError("FORMAT_MISMATCH", "Specify a native .midiloop file.")
    file, data, sha = storage.read_file(path)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise IntegrationError("UNSUPPORTED_MIDI_LOOP", "Expected Cubase MIDI Loop XML.") from exc
    _require(root.tag == "midiloop" and not root.getroottree().docinfo.doctype)
    return file, root, sha


def _record(root, name):
    pointer = _one(root, "./rootObjects/root[@name=$name][@ID]", name=name)
    return _one(root, "./obj[@ID=$oid]", oid=pointer.get("ID"))


def _value(parent, name):
    return _one(parent, "./*[@name=$name][@value]", name=name)


def _part(root):
    piece = _record(root, "midipart")
    _require(_value(piece, "Start").get("value") == "0")
    part = _one(piece, './obj[@name="Node"][@class="MMidiPart"]')
    events = _one(part, './list[@name="Events"][@type="obj"]')
    prototype = events.find('obj[@class="MMidiNote"]')
    _require(prototype is not None and not prototype.xpath(".//*[@ID]"))
    return _value(piece, "Length"), part, events, prototype


def _instrument(root):
    track = _record(root, "tracktemplate")
    _require(_value(track, "tracktype").get("value") == "Instrument")
    plugin = _one(track, './/member[@name="Synth Slot"]/member[@name="Plugin"]')
    uid = _one(plugin, './member[@name="Plugin UID"]/string[@name="GUID"]').get("value")
    _require(uid == SFIZZ_MULTI_UID and _value(plugin, "Plugin Name").get("value") == "sfizz-multi")
    state = _one(plugin, './bin[@name="audioComponent"]')
    try:
        payload = bytes.fromhex(state.text or "")
        version, count = struct.unpack_from("<QI", payload)
        path = payload[12:12 + count]
        _require(version == 5 and 1 < count == len(path) and path[-1:] == b"\0" and b"\0" not in path[:-1])
        bank = Path(path[:-1].decode("utf-8"))
    except (ValueError, struct.error) as exc:
        raise IntegrationError("UNSUPPORTED_MIDI_LOOP", "Could not read the sfizz-multi version 5 state.") from exc
    _require(bank.is_absolute() and bank.suffix.lower() == ".sfz" and len(payload) > 12 + count)
    return track, state, bank, payload[12 + count:]


def inspect(path: str) -> dict:
    file, root, sha = _load(path)
    length, part, events, _ = _part(root)
    _, _, bank, _ = _instrument(root)
    metadata = _one(_record(root, "metadata"), './member[@name="Additional Attributes"]')
    return {
        "path": str(file), "sha256": sha, "plugin": "sfizz-multi", "sfz_path": str(bank),
        "name": _value(part, "Name").get("value"),
        "note_count": len(events.findall('obj[@class="MMidiNote"]')),
        "part_length_ticks": float(length.get("value")), "ticks_per_quarter": cpr_midi.TICKS_PER_QUARTER,
        "template_tempo_bpm": float(_value(metadata, "MusicalTempo").get("value")),
    }


def create(template_path: str, expected_template_sha256: str, midi_path: str,
           expected_midi_sha256: str, track_name: str, sfz_path: str, output_path: str) -> dict:
    file, root, sha = _load(template_path)
    if Path(midi_path).suffix.lower() not in {".mid", ".midi"}:
        raise IntegrationError("FORMAT_MISMATCH", "Specify an SMF (.mid or .midi).")
    source, smf, midi_sha = storage.read_file(midi_path)
    if sha != expected_template_sha256.lower() or midi_sha != expected_midi_sha256.lower():
        raise IntegrationError("FILE_CHANGED", "Inspect the template and MIDI again and use their current SHA-256.")
    target = Path(output_path).expanduser().resolve()
    if target.suffix.lower() != ".midiloop" or target == file:
        raise IntegrationError("INVALID_MIDI_LOOP_OUTPUT", "Choose a new .midiloop output path.")
    bank = Path(sfz_path).expanduser().resolve()
    if bank.suffix.lower() != ".sfz" or not bank.is_file():
        raise IntegrationError("INVALID_SFZ_PATH", "Specify an existing .sfz file.")

    selected = next((track for track in cpr_midi._source_tracks(smf) if track["name"] == track_name), None)
    if selected is None:
        raise IntegrationError("MIDI_TRACK_NOT_FOUND", "Choose an exact named MIDI track containing notes.")
    ticks = cpr_midi.TICKS_PER_QUARTER
    part_length = selected["source_length_ticks"]
    notes = selected["notes"]
    if part_length <= 0 or part_length % ticks or any(
        start != int(start) or length != int(length) or start < 0
        or length <= 0 or start + length > part_length
        for start, length, *_ in notes
    ):
        raise IntegrationError(
            "UNSUPPORTED_MIDI_LOOP_TIMING",
            "Use whole 480-PPQ note ticks and end the MIDI track on a quarter-note boundary.",
        )

    length_node, part, events, prototype = _part(root)
    track, state, _, state_tail = _instrument(root)
    metadata = _one(_record(root, "metadata"), './member[@name="Additional Attributes"]')
    beats = _value(metadata, "MusicalBeats")
    _require(float(length_node.get("value")) == ticks * int(beats.get("value")))
    length_node.set("value", str(int(part_length)))
    beats.set("value", str(int(part_length) // ticks))

    part_name = _value(part, "Name")
    node = _one(track, './obj[@name="Node"][@class="MListNode"]')
    track_label = _value(node, "Name")
    device = _one(track, './obj[@name="Track Device"]/member[@name="DeviceAttributes"]')
    main_channel = _one(device, './member[@name="Name"]/string[@name="String"]')
    outputs = device.xpath(
        './member[@name="Synth Slot"]/list[@name="Output Channels"]/item'
        '/member[@name="Output Channel"]/member[@name="Name"]/string[@name="String"]'
    )
    for label in outputs:
        if label.get("value") == main_channel.get("value"):
            label.set("value", track_name)
    for label in (part_name, track_label, main_channel):
        label.set("value", track_name)

    used_ids = set(root.xpath('//@ID'))
    new_id = 1_000_000
    events[:] = []
    for note, repeats in sorted(notes.items()):
        for _ in range(repeats):
            while str(new_id) in used_ids:
                new_id += 1
            event = deepcopy(prototype)
            event.set("ID", str(new_id))
            new_id += 1
            for field, value in zip(NOTE_FIELDS, note, strict=True):
                _value(event, field).set("value", str(int(value)))
            _value(event, "HrData").set("value", repr((note[3] - 1) / 126))
            attributes = _one(event, './member[@name="Additional Attributes"]')
            _value(attributes, "VOCE").set("value", "0")
            events.append(event)

    path_bytes = bank.as_posix().encode("utf-8") + b"\0"
    payload = struct.pack("<QI", 5, len(path_bytes)) + path_bytes + state_tail
    state.text = "\n" + "\n".join(wrap(payload.hex().upper(), width=64)) + "\n"
    data = etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
    storage.verify_hash(source, midi_sha)
    return storage.write_result(file, str(target), data.replace(b"\n", b"\r\n"), sha) | {
        "template_sha256": sha, "midi_sha256": midi_sha, "track_name": track_name,
        "sfz_path": str(bank), "note_count": notes.total(), "part_length_ticks": int(part_length),
        "ticks_per_quarter": ticks, "live_state_verified": False,
    }
