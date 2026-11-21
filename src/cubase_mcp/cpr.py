"""Partial, read-only CPR inspection using the pinned CubaseTools core."""

from __future__ import annotations

import dataclasses
import enum
import sys
from collections import Counter
from pathlib import Path

from . import audio_templates
from .errors import IntegrationError

sys.path.insert(0, str(Path(__file__).parent / "_vendor"))
from cubasetools.core.cpr_parser import CprParser


class PreservingParser(CprParser):
    def _extract_audio_per_track(self):
        try:
            slots = audio_templates.read_layout(self.data).slots
        except IntegrationError:
            super()._extract_audio_per_track()
            return
        names = Counter(track.name for track in self.project.tracks)
        slots_by_name = {slot.track: slot for slot in slots}
        if len(slots_by_name) != len(slots) or any(names[name] != 1 for name in slots_by_name):
            return
        for track in self.project.tracks:
            slot = slots_by_name.get(track.name)
            if slot is not None:
                track.audio_files = [slot.name]
                track.has_content = True

    def _postprocess(self):
        # The upstream implementation merges tracks by name. Real projects may
        # legitimately contain several tracks with identical names.
        for index, track in enumerate(self.project.tracks):
            track.index = index


def clean(value):
    if dataclasses.is_dataclass(value):
        return {
            field.name: clean(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.name != "raw_chunk"
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, bytes):
        return {"opaque_bytes": len(value)}
    return value


def inspect(path: Path):
    project = PreservingParser(path).parse()
    return {
        "format": "cpr",
        "read_only": True,
        "partial": True,
        "data": clean(project),
        "warnings": [
            "CPR is proprietary. Extracted fields are heuristic observations, not a complete project model.",
            "Defaulted mixer fields and parsed track indexes are not verified live DAW values or stable track IDs.",
            "Only saved state is visible. Unsaved changes and opaque plugin contents are not available.",
        ],
    }
