import os
import struct
import wave

import pytest

from cubase_mcp import audio_templates
from cubase_mcp.errors import IntegrationError
from cubase_mcp.storage import digest


def _string(value):
    raw = value.encode("utf-8") + b"\0\xef\xbb\xbf"
    return struct.pack(">I", len(raw)) + raw


def _chunk(tag, data):
    return tag + struct.pack(">Q", len(data)) + data


def _file_record(name, tag, extension, kind, folder, *, empty_extension=False):
    second_extension = b"\0" * 4 if empty_extension else _string(extension)
    return (_string(name) + tag + _string(extension) + second_extension + _string(kind)
            + b"\0\0\0\x01\0\0" + _string(folder))


def _native_fixture(directory, *, resaved=False):
    archive = bytearray(b"\xff" * 8)
    classes = {}

    def declare(name, version):
        if name in classes:
            archive.extend(struct.pack(">Q", 1 << 63 | classes[name]))
        else:
            classes[name] = len(archive)
            archive.extend(b"\xff" * 8 + struct.pack(">I", len(name) + 1)
                           + name + b"\0" + struct.pack(">H", version))
        offset = len(archive)
        archive.extend(b"\0" * 8)
        return offset

    def end(offset):
        struct.pack_into(">Q", archive, offset, len(archive) - offset - 8)

    audio_folder = audio_templates._folder(directory / "Audio")
    for track_name, wav_name in [("Bass track", "Bass.wav"), ("Lead track", "Lead.wav")]:
        track = declare(b"MAudioTrackEvent", 1)
        archive.extend(b"\0" * 42 + _string(track_name))
        audio = declare(b"MAudioEvent", 6)
        archive.extend(struct.pack(">Hdd", 0, 0, 4))
        primary = declare(b"FNPath", 0)
        archive.extend(_file_record(wav_name, b"WAVE", "wav", "Wave File", audio_folder))
        end(primary)
        archive.extend(b"\0\0\0\x09OrigPath\0\0\x22")
        origin = declare(b"FNPath", 0)
        archive.extend(_file_record(wav_name, b"WAVE", "wav", "Wave File", audio_folder + ".\\"))
        end(origin)
        end(audio)
        end(track)

    home = declare(b"FNPath", 0)
    archive.extend(_string(directory.name) + b"\0" * 19 + b"\x01\x00\x01"
                   + _string(audio_templates._folder(directory.parent)))
    end(home)
    project = declare(b"FNPath", 0)
    archive.extend(_file_record(directory.name + ".cpr", b"NDPR", "cpr", "Cubase Project File",
                                audio_templates._folder(directory), empty_extension=resaved))
    end(project)
    contents = _chunk(b"ROOT", b"\0\0\0\x0cPArrangement") + _chunk(b"ARCH", archive)
    return b"RIF2" + struct.pack(">Q", len(contents)) + b"NUND" + contents


def _write_wav(path, sample, frames=4):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 44100, frames, "NONE", "not compressed"))
        wav.writeframes(struct.pack("<h", sample) * frames)


@pytest.mark.skipif(os.name != "nt", reason="Cubase Windows paths")
@pytest.mark.parametrize("resaved", [False, True])
def test_relocate_audio_project_and_validate_replacement(tmp_path, monkeypatch, resaved):
    monkeypatch.setenv("CUBASE_PIAPRO_STATE_DIR", str(tmp_path / "state"))
    template = tmp_path / "Template"
    (template / "Audio").mkdir(parents=True)
    cpr = template / "Template.cpr"
    cpr.write_bytes(_native_fixture(template, resaved=resaved))
    _write_wav(template / "Audio" / "Bass.wav", 1)
    _write_wav(template / "Audio" / "Lead.wav", 2)
    replacement = tmp_path / "Replacement.wav"
    target = tmp_path / "New Drop"
    fixture = audio_templates.inspect(str(cpr))
    with pytest.raises(IntegrationError, match="AUDIO_TEMPLATE_PATH_WIDTH"):
        audio_templates.create(str(cpr), fixture["sha256"], str(tmp_path / "Different width"), None, {})
    assert not (tmp_path / "Different width").exists()

    def create(replacement_path, sha):
        return audio_templates.create(str(cpr), fixture["sha256"], str(target), None,
                                      {"Bass.wav": (str(replacement_path), sha)})

    _write_wav(replacement, 3, frames=5)
    with pytest.raises(IntegrationError, match="AUDIO_FORMAT_MISMATCH"):
        create(replacement, digest(replacement.read_bytes()))
    assert not target.exists()
    _write_wav(replacement, 3)
    replacement_hash = digest(replacement.read_bytes())
    output = create(replacement, replacement_hash)
    assert not output["live_state_verified"]
    opened = audio_templates.inspect(output["path"])
    assert [(item["track"], item["slot"]) for item in opened["media"]] == [
        ("Bass track", "Bass.wav"), ("Lead track", "Lead.wav"),
    ]
    assert opened["media"][0]["sha256"] == replacement_hash
    assert opened["media"][1]["sha256"] == fixture["media"][1]["sha256"]
    assert fixture == audio_templates.inspect(str(cpr))
    assert str(template).encode("utf-8") not in (target / "New Drop.cpr").read_bytes()
    with pytest.raises(IntegrationError, match="PROJECT_DIRECTORY_EXISTS"):
        create(replacement, replacement_hash)
