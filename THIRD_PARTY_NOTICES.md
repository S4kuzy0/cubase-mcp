# Third-party notices

## CubaseTools

Source: https://github.com/schwifty00/CubaseTools

Pinned commit: `b525d6c0e6d01a6ec13c3c4d3401c7c7ac25155d`.

The `cubasetools/core` parser components and package initializer are vendored with the MIT license. The wrapper replaces name-based final track deduplication. GUI and cleanup operations are not exposed through this MCP.

## Runtime dependencies

The official MCP Python SDK, lxml, mido, python-rtmidi, construct and construct-typing are installed as dependencies, with their upstream licenses. Versions are fixed in the tested dependency snapshot. No code from the alternative AGPL yoka server is included.

## Steinberg interface reference

The original MIDI Remote bridge uses the documented interfaces at https://github.com/steinbergmedia/midiremote_api_doc . The documentation was consulted as an API reference; no Steinberg implementation or documentation source is vendored. Cubase, Piapro, commercial plugins, voicebanks and local CPR templates are not distributed with this project.

## Windows OCR interface

The original `native_visual.py` and `assets/windows_ocr.ps1` helper use Windows window messaging, PrintWindow and the installed Windows.Media.Ocr runtime. No third-party OCR engine, model or Windows component is bundled or downloaded. An available Windows OCR language is required. The original EVEC caption helper also recognizes exact observed zero/one glyph outlines in the validated default caption font, independently of the requested count; no font file is bundled. UI Automation was evaluated with built-in system interfaces for diagnostics; it is not a runtime dependency.
