"""Standard MIDI file edits with matched note-off and preserved metadata."""

from __future__ import annotations

import io
import math
from collections import Counter, defaultdict, deque
import mido

from .errors import IntegrationError
from .note_fields import validate_change


def load(data):
    file = mido.MidiFile(file=io.BytesIO(data))
    if file.type == 2:
        raise IntegrationError(
            "UNSUPPORTED_MIDI", "Asynchronous type-2 MIDI is not supported."
        )
    events = []
    for track in file.tracks:
        tick = 0
        absolute = []
        for index, message in enumerate(track):
            tick += message.time
            absolute.append([tick, index, message])
        events.append(absolute)
    return file, events


def note_entries(file, tracks):
    rows = []
    for ti, events in enumerate(tracks):
        active = defaultdict(deque)
        for tick, index, message in events:
            if message.type == "note_on" and message.velocity:
                active[(message.channel, message.note)].append((tick, index, message))
            elif message.type == "note_off" or (
                message.type == "note_on" and not message.velocity
            ):
                queue = active[(message.channel, message.note)]
                if queue:
                    start, on_index, on = queue.popleft()
                    rows.append(
                        (
                            {
                                "id": f"t{ti}:e{on_index}",
                                "track_index": ti,
                                "part_index": 0,
                                "start_tick": start,
                                "duration_tick": tick - start,
                                "note_num": on.note,
                                "velocity": on.velocity,
                                "channel": on.channel,
                            },
                            ti,
                            on_index,
                            index,
                        )
                    )
        if any(active.values()):
            raise IntegrationError(
                "UNMATCHED_MIDI_NOTES",
                "A note has no matching note-off; editing would be ambiguous.",
            )
    return rows


def inspect(data: bytes, include_notes=False):
    file, events = load(data)
    rows = [row for row, *_ in note_entries(file, events)]
    counts = Counter(row["track_index"] for row in rows)
    result = {
        "format": "midi",
        "type": file.type,
        "ticks_per_beat": file.ticks_per_beat,
        "track_count": len(file.tracks),
        "note_count": len(rows),
        "tracks": [
            {
                "track_index": index,
                "name": _display_name(track.name),
                "channels": sorted({message.channel for message in track
                                    if hasattr(message, "channel")}),
                "initial_patches": _initial_patches(events[index]),
                "note_count": counts[index],
            }
            for index, track in enumerate(file.tracks)
        ],
    }
    if include_notes:
        result["notes"] = rows
    return result


def _initial_patches(events):
    banks = defaultdict(dict)
    patches = {}
    for tick, _, message in events:
        if tick != 0:
            break
        if message.type == "control_change" and message.control in (0, 32):
            field = "bank_msb" if message.control == 0 else "bank_lsb"
            banks[message.channel][field] = message.value
        if message.type == "program_change":
            patches[message.channel] = {"channel": message.channel, "program": message.program,
                                        **banks[message.channel]}
    return list(patches.values())


def _display_name(name):
    try:
        return name.encode("latin1").decode("utf-8")
    except UnicodeError:
        return name


def edit(data: bytes, changes: list[dict]) -> bytes:
    file, tracks = load(data)
    lookup = {
        row["id"]: (row, ti, on, off) for row, ti, on, off in note_entries(file, tracks)
    }
    seen = set()
    for change in changes:
        validate_change(change)
        key = change["id"]
        if key in seen or key not in lookup:
            raise IntegrationError("INVALID_NOTE_ID", key)
        seen.add(key)
        if {"lyric", "phoneme"} & change.keys():
            raise IntegrationError(
                "UNSUPPORTED_MIDI_FIELD",
                "Use VSQX for phonemes or note-attached lyrics.",
            )
        row, ti, on, off = lookup[key]
        updated = row | change
        tracks[ti][on][0] = updated["start_tick"]
        tracks[ti][off][0] = updated["start_tick"] + updated["duration_tick"]
        tracks[ti][on][2].note = updated["note_num"]
        tracks[ti][off][2].note = updated["note_num"]
        tracks[ti][on][2].velocity = updated["velocity"]
    return _serialize(file, tracks)


def _serialize(file, tracks):
    for track, events in zip(file.tracks, tracks):
        events.sort(key=lambda entry: (entry[0], entry[1]))
        track.clear()
        previous = 0
        end = []
        for tick, _, message in events:
            if message.type == "end_of_track":
                end.append(tick)
                continue
            track.append(message.copy(time=tick - previous))
            previous = tick
        track.append(
            mido.MetaMessage(
                "end_of_track", time=max(0, max(end, default=previous) - previous)
            )
        )
    output = io.BytesIO()
    file.save(file=output)
    inspect(output.getvalue())
    return output.getvalue()


def controller_points(data, track_index, channel, controller, replacements=None, mode="replace"):
    """Inspect or edit one standard MIDI CC lane using absolute ticks."""
    file, tracks = load(data)
    if not 0 <= track_index < len(tracks) or not 0 <= channel <= 15 or not 0 <= controller <= 119:
        raise IntegrationError("INVALID_MIDI_CONTROLLER", "Use an existing track, MIDI channel 0–15 and controller 0–119.")

    def matches(message):
        return message.type == "control_change" and message.channel == channel and message.control == controller

    events = tracks[track_index]
    if replacements is None:
        return [{"tick": tick, "value": message.value} for tick, _, message in events if matches(message)]
    if mode not in {"replace", "merge"} or len(replacements) > 10000:
        raise IntegrationError("INVALID_CONTROLLER_CHANGESET", "Use replace or merge and at most 10000 points.")
    points = {row["tick"]: row["value"] for row in replacements}
    if len(points) != len(replacements) or any(type(tick) is not int or type(value) is not int
                                             or not 0 <= tick <= 0x0fffffff or not 0 <= value <= 127
                                             for tick, value in points.items()):
        raise IntegrationError("INVALID_CONTROLLER_POINTS", "Use distinct absolute ticks and CC values from 0 to 127.")
    kept = [event for event in events
            if not (matches(event[2]) and (mode == "replace" or event[0] in points))]
    next_index = max((index for _, index, _ in events), default=-1) + 1
    kept.extend([tick, index, mido.Message("control_change", channel=channel, control=controller, value=value)]
                for index, (tick, value) in enumerate(sorted(points.items()), start=next_index))
    tracks[track_index] = kept
    return _serialize(file, tracks)


def _note_events(note, channel, index):
    validate_change({"id": "_new", **note})
    start, duration = note["start_tick"], note["duration_tick"]
    if not 0 <= channel < 16 or not 1 <= note["velocity"] <= 127 or start + duration > 0x0fffffff:
        raise IntegrationError("INVALID_MIDI_NOTE", "Use channel 0–15, nonzero velocity and a valid MIDI tick range.")
    number = note["note_num"]
    return [
        [start, index, mido.Message("note_on", channel=channel, note=number, velocity=note["velocity"])],
        [start + duration, index + 1, mido.Message("note_off", channel=channel, note=number, velocity=0)],
    ]


def _insert_notes(track, notes, channel, occupied):
    next_index = max((index for _, index, _ in track), default=-1) + 1
    for note in notes:
        new = _note_events(note, channel, next_index)
        start, end = new[0][0], new[1][0]
        spans = occupied[(channel, note["note_num"])]
        if any(start < right and left < end for left, right in spans):
            raise IntegrationError("MIDI_NOTE_OVERLAP", "The new note overlaps the same pitch and channel.")
        spans.append((start, end))
        track.extend(new)
        next_index += len(new)


def _patch_messages(channel, program, bank_msb=None, bank_lsb=None):
    if not 0 <= channel < 16 or any(not 0 <= value <= 127
                                    for value in (program, bank_msb, bank_lsb) if value is not None):
        raise IntegrationError("INVALID_MIDI_PATCH", "Use channel 0–15 and program or bank values 0–127.")
    messages = [mido.Message("control_change", channel=channel, control=control, value=value)
                for control, value in ((0, bank_msb), (32, bank_lsb)) if value is not None]
    messages.append(mido.Message("program_change", channel=channel, program=program))
    return messages


def create(tracks, tempo_bpm, numerator, denominator, ticks_per_beat, length_ticks):
    if not math.isfinite(tempo_bpm) or tempo_bpm <= 0:
        raise IntegrationError("INVALID_MIDI_TEMPO", "Use a positive, finite BPM.")
    if not 60000000 / 0xffffff <= tempo_bpm <= 60000000:
        raise IntegrationError("INVALID_MIDI_TEMPO", "BPM is outside the Standard MIDI File tempo range.")
    if not 1 <= numerator <= 255 or denominator not in (1, 2, 4, 8, 16, 32, 64):
        raise IntegrationError("INVALID_MIDI_METER", "Use a valid MIDI time signature.")
    if not 1 <= ticks_per_beat <= 32767 or not 1 <= len(tracks) <= 64:
        raise IntegrationError("INVALID_MIDI_SCORE", "Use 1–64 tracks and a positive PPQ up to 32767.")
    if length_ticks is not None and not 0 <= length_ticks <= 0x0fffffff:
        raise IntegrationError("INVALID_MIDI_LENGTH", "Use a valid absolute MIDI tick length.")
    if not 1 <= sum(len(track["notes"]) for track in tracks) <= 10000:
        raise IntegrationError("INVALID_CHANGESET", "Supply 1–10000 MIDI notes.")

    file = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat, charset="utf-8")
    conductor = [
        [0, 0, mido.MetaMessage("set_tempo", tempo=round(60000000 / tempo_bpm))],
        [0, 1, mido.MetaMessage("time_signature", numerator=numerator, denominator=denominator)],
    ]
    rows = [conductor]
    for track in tracks:
        name, channel = track["name"], track["channel"]
        if not 1 <= len(name.encode("utf-8")) <= 255 or "\0" in name or not 0 <= channel < 16:
            raise IntegrationError("INVALID_MIDI_TRACK", "Use a nonempty track name and channel 0–15.")
        events = [[0, 0, mido.MetaMessage("track_name", name=name)]]
        if track.get("program") is not None:
            patch = _patch_messages(channel, track["program"], track.get("bank_msb"), track.get("bank_lsb"))
            events.extend([0, index, message] for index, message in enumerate(patch, start=1))
        elif track.get("bank_msb") is not None or track.get("bank_lsb") is not None:
            raise IntegrationError("INVALID_MIDI_PATCH", "Bank selection requires a program number.")
        _insert_notes(events, track["notes"], channel, defaultdict(list))
        rows.append(events)
    last_note_tick = max(tick for events in rows for tick, _, _ in events)
    end = last_note_tick if length_ticks is None else length_ticks
    if end < last_note_tick:
        raise IntegrationError("INVALID_MIDI_LENGTH", "The requested length is shorter than the score.")
    for events in rows:
        file.add_track()
        events.append([end, len(events), mido.MetaMessage("end_of_track")])
    return _serialize(file, rows)


def arrange(data, additions, deletions):
    ids = set(deletions)
    if not 1 <= len(additions) + len(deletions) <= 10000 or len(ids) != len(deletions):
        raise IntegrationError("INVALID_CHANGESET", "Supply 1–10000 changes without duplicate deletion IDs.")
    file, tracks = load(data)
    note_rows = note_entries(file, tracks)
    if ids - {row["id"] for row, *_ in note_rows}:
        raise IntegrationError("INVALID_NOTE_ID", "Use an existing MIDI note ID from inspection.")
    removed = defaultdict(set)
    occupied = defaultdict(lambda: defaultdict(list))
    for row, track_index, on, off in note_rows:
        if row["id"] in ids:
            removed[track_index].update((on, off))
        else:
            start = row["start_tick"]
            occupied[track_index][(row["channel"], row["note_num"])].append((start, start + row["duration_tick"]))
    for track_index, indexes in removed.items():
        tracks[track_index] = [entry for entry in tracks[track_index] if entry[1] not in indexes]
    queued = defaultdict(list)
    for item in additions:
        track_index, channel = item["track_index"], item["channel"]
        if not 0 <= track_index < len(tracks):
            raise IntegrationError("INVALID_MIDI_TRACK", "Use a track index from inspection.")
        note = {key: item[key] for key in ("start_tick", "duration_tick", "note_num", "velocity")}
        queued[(track_index, channel)].append(note)
    for (track_index, channel), new_notes in queued.items():
        _insert_notes(tracks[track_index], new_notes, channel, occupied[track_index])
    return _serialize(file, tracks)


def set_initial_patch(data, track_index, channel, program, bank_msb, bank_lsb):
    file, tracks = load(data)
    if not 0 <= track_index < len(tracks):
        raise IntegrationError("INVALID_MIDI_TRACK", "Use a track index from inspection.")
    patch = _patch_messages(channel, program, bank_msb, bank_lsb)
    bank_controls = {message.control for message in patch if message.type == "control_change"}
    initial = set()
    for tick, index, message in tracks[track_index]:
        if tick == 0 and getattr(message, "channel", None) == channel:
            if message.type == "program_change" or (
                message.type == "control_change" and message.control in bank_controls
            ):
                initial.add(index)
    remaining = [entry for entry in tracks[track_index] if entry[1] not in initial]
    # Existing bank messages that the caller kept must still precede Program Change.
    kept_bank = []
    rest = []
    for entry in remaining:
        tick, _, message = entry
        if tick == 0 and message.type == "control_change" and message.channel == channel and message.control in (0, 32):
            kept_bank.append(message)
        else:
            rest.append(entry)
    prefix = kept_bank + patch
    tracks[track_index] = [[0, index - len(prefix), message] for index, message in enumerate(prefix)] + rest
    return _serialize(file, tracks)
