"""Validation shared by the MIDI and score adapters."""

from .errors import IntegrationError


def validate_change(change: dict):
    ranges = {
        "start_tick": (0, 2**31 - 1),
        "duration_tick": (1, 2**31 - 1),
        "note_num": (0, 127),
        "velocity": (0, 127),
    }
    if "id" not in change:
        raise IntegrationError(
            "NOTE_ID_REQUIRED", "Use the note id returned by list_notes."
        )
    for key, value in change.items():
        if key == "id":
            continue
        if key not in ranges and key not in ("lyric", "phoneme"):
            raise IntegrationError("UNSUPPORTED_FIELD", key)
        if key in ranges:
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not ranges[key][0] <= value <= ranges[key][1]
            ):
                raise IntegrationError("INVALID_NOTE", f"Invalid {key}: {value}")
        elif not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
            raise IntegrationError("INVALID_NOTE", f"Invalid {key}")
