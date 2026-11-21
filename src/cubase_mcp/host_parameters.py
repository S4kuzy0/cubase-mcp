"""Verified access to the optional MIDI Remote DirectAccess extension."""

from __future__ import annotations

import json
import math
import secrets
import time

import mido

from . import cubase, live_midi
from .errors import IntegrationError
from .storage import file_lock, state_dir

PREFIX = (125, 67, 80)
MIX_FIELDS = {"volume", "pan", "mute", "solo"}


def _rpc(incoming, outgoing, request):
    nonce = secrets.randbelow(126) + 1
    text = json.dumps(request, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    if len(text) > 770:
        raise IntegrationError("DIRECT_REQUEST_TOO_LARGE", "Reduce the object selection or parameters in this call.")
    for _ in incoming.iter_pending():
        pass
    outgoing.send(mido.Message("sysex", data=(*PREFIX, 6, nonce, *text.encode("ascii"))))
    deadline = time.monotonic() + 3
    parts = {}
    total = None
    while time.monotonic() < deadline:
        for message in incoming.iter_pending():
            payload = message.data if message.type == "sysex" else ()
            if tuple(payload[:5]) != (*PREFIX, 7, nonce):
                continue
            if len(payload) < 7 or (len(payload) - 7) % 3:
                raise IntegrationError("INVALID_DIRECT_REPLY", "Incomplete SysEx reply from the host.")
            index, count = payload[5:7]
            if not 0 <= index < count <= 80 or total not in (None, count):
                raise IntegrationError("INVALID_DIRECT_REPLY", "Inconsistent host reply frame numbering.")
            total = count
            fragment = tuple(payload[7:])
            if index in parts and parts[index] != fragment:
                raise IntegrationError("INVALID_DIRECT_REPLY", "Conflicting duplicate host reply frame.")
            parts[index] = fragment
            if len(parts) == total:
                flat = [item for k in range(total) for item in parts[k]]
                try:
                    data = bytearray()
                    for a, b, c in zip(flat[::3], flat[1::3], flat[2::3]):
                        data.extend((a | (b << 7) | (c << 14)).to_bytes(2, "little"))
                    reply = json.loads(data.decode("utf-16-le"))
                except (ValueError, OverflowError) as exc:
                    raise IntegrationError("INVALID_DIRECT_REPLY", "The host reply could not be decoded.") from exc
                if not isinstance(reply, dict) or not ("error" in reply or "result" in reply):
                    raise IntegrationError("INVALID_DIRECT_REPLY", "The host reply lacks a result.")
                error = reply.get("error")
                if error:
                    raise IntegrationError("DIRECT_ACCESS_REJECTED", str(error))
                return reply["result"]
        time.sleep(.015)
    raise IntegrationError("DIRECT_ACCESS_NO_REPLY", "Load the new MIDI Remote driver and inspect the host state before retrying a write.")


def _with_track(track_id, revision, action):
    with file_lock(state_dir() / "live-daw"):
        cubase._idle(cubase.project_root())
        snapshot = live_midi.list_tracks()
        track = live_midi.requested_track(snapshot, track_id, revision)
        live_midi.select_track(track_id, revision)
        source, target = live_midi.selected_ports()
        with (file_lock(state_dir() / "live-midi"),
              mido.open_input(source) as incoming, mido.open_output(target) as outgoing):
            current = live_midi._inventory(incoming, outgoing)
            if current["session"] != snapshot["session"] or current["selected"]["uid"] != track["uid"]:
                raise IntegrationError("TRACK_SELECTION_CHANGED", "The target channel could not be reverified.")

            def ask(op, path, **fields):
                return _rpc(incoming, outgoing, {"op": op, "session": current["session"],
                            "track_uid": track["uid"], "path": path, **fields})

            return action(ask)


def _path(path):
    if len(path) > 10 or any(type(i) is not int or not 0 <= i <= 1023 for i in path):
        raise IntegrationError("INVALID_OBJECT_PATH", "Use zero-based child indexes from a current host inspection.")
    return path


def inspect(track_id, revision, path, offset):
    _path(path)
    if not 0 <= offset <= 10000:
        raise IntegrationError("INVALID_PAGE", "Use a nonnegative offset up to 10000.")
    return _with_track(track_id, revision, lambda ask: ask("inspect", path, offset=offset)) | {"path": path}


def _identity(node):
    return {f"expected_{key}": node[key] for key in ("id", "uid", "name", "type")}


def set_parameter(track_id, revision, path, node, tag, title, wanted, expected):
    _path(path)
    if not math.isfinite(wanted) or not math.isfinite(expected) or not 0 <= wanted <= 1 or not 0 <= expected <= 1:
        raise IntegrationError("INVALID_VALUE", "Use finite normalized parameter values from 0 to 1.")

    def work(ask):
        answer = ask("parameter_set", path, **_identity(node), tag=tag, expected_title=title,
                     value=wanted, expected_value=expected)
        actual = answer["after"]["value"]
        for attempt in range(4):
            if isinstance(actual, (int, float)) and abs(actual - wanted) <= .00001:
                return answer | {"verified": True}
            time.sleep(.08)
            observed = ask("parameter_read", path, **_identity(node), tag=tag, expected_title=title)
            answer["after"] = observed
            actual = observed["value"]
        return answer | {"verified": False}

    return _with_track(track_id, revision, work)


def plugin_catalog(track_id, revision, path, offset):
    _path(path)
    if not 0 <= offset <= 10000:
        raise IntegrationError("INVALID_PAGE", "Use a nonnegative offset up to 10000.")
    return _with_track(track_id, revision, lambda ask: ask("plugin_catalog", path, offset=offset))


def load_plugin(track_id, revision, path, node, plugin_uid):
    _path(path)
    if not plugin_uid or len(plugin_uid) > 128:
        raise IntegrationError("INVALID_PLUGIN_UID", "Use a plugin UID from the slot's current catalog.")

    def work(ask):
        submitted = ask("plugin_load", path, **_identity(node), plugin_uid=plugin_uid)
        after = None
        if submitted["dispatched"]:
            for _ in range(10):
                time.sleep(.15)
                after = ask("inspect", path, **_identity(node))
                if any(plugin_uid.casefold() in (str(child["uid"]).casefold(), str(child["name"]).casefold())
                       for child in after["child_page"]):
                    return submitted | {"verified": True, "after": after}
        return submitted | {"verified": False, "after": after}

    return _with_track(track_id, revision, work)


def meter(track_id, revision):
    return _with_track(track_id, revision, lambda ask: ask("meter", []))


def mix_channels(revision, changes):
    if not 1 <= len(changes) <= 32:
        raise IntegrationError("INVALID_MIX_BATCH", "Use 1 to 32 distinct track/control pairs.")
    pairs = {(item["track_id"], item["field"]) for item in changes}
    if len(pairs) != len(changes):
        raise IntegrationError("INVALID_MIX_BATCH", "Use each track/control pair once.")
    for item in changes:
        field, value = item["field"], item["value"]
        if field not in MIX_FIELDS or not 0 <= value <= 1 or (field in ("mute", "solo") and value not in (0, 1)):
            raise IntegrationError("INVALID_MIX_BATCH", "Use normalized volume/pan or binary mute/solo values.")
    with file_lock(state_dir() / "live-daw"):
        cubase._idle(cubase.project_root())
        snapshot = live_midi.list_tracks()
        targets = [(live_midi.requested_track(snapshot, entry["track_id"], revision), entry) for entry in changes]
        feedback = live_midi.get_state()
        if not feedback["connected"] or feedback["state"]["playing"] != 0 or feedback["state"]["recording"] != 0:
            raise IntegrationError("TRANSPORT_NOT_STOPPED", "The mixer batch requires verified stopped transport.")
        previous = next((row for row in snapshot["tracks"] if row["uid"] == snapshot["selected"]["uid"]), None)
        if previous is None:
            raise IntegrationError("TRACK_ID_PENDING", "The current selection must be verifiable before a batch.")
        applied = []
        errors = []
        try:
            for track, change in targets:
                live_midi.select_track(track["id"], revision)
                value = live_midi.control(change["field"], change["value"], expected_track=(track["uid"], revision))
                applied.append({"track_id": track["id"], "field": change["field"], **value})
        except IntegrationError as exc:
            errors.append({"code": exc.code, "message": str(exc)})
        try:
            live_midi.select_track(previous["id"], revision)
        except IntegrationError as exc:
            errors.append({"code": exc.code, "message": str(exc)})
        return {"verified": not errors, "results": applied, "errors": errors,
                "previous_track_id": previous["id"], "requested_changes": len(changes)}
