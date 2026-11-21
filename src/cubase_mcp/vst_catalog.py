"""Inspect the local Cubase LE VST3 scan cache without loading plugins."""

import os
import re
from pathlib import Path

from lxml import etree

from .errors import IntegrationError
from .storage import read_file


def _instruments(data: bytes) -> list[dict]:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise IntegrationError("INVALID_VST3_CACHE", "Could not read Cubase's VST3 scan metadata.") from exc
    if root.tag != "plugins" or root.getroottree().docinfo.doctype:
        raise IntegrationError("INVALID_VST3_CACHE", "Unexpected Cubase VST3 scan metadata format.")
    by_id = {}
    for module in root.iterfind("plugin"):
        for entry in module.iterfind("class"):
            if entry.findtext("category") != "Audio Module Class":
                continue
            categories = entry.findtext("subCategories", "").split("|")
            if "Instrument" not in categories:
                continue
            class_id = entry.findtext("cid", "").upper()
            name = entry.findtext("name", "")
            if not re.fullmatch(r"[0-9A-F]{32}", class_id) or not name:
                raise IntegrationError("INVALID_VST3_CACHE", "An instrument has no usable class ID or name.")
            record = {"class_id": class_id, "name": name, "categories": categories,
                      "vendor": entry.findtext("vendor", module.findtext("vendor", "")),
                      "version": entry.findtext("version", "")}
            if by_id.setdefault(class_id, record) != record:
                raise IntegrationError("AMBIGUOUS_VST3_CLASS", f"Conflicting cache entries for VST3 class {class_id}.")
    return sorted(by_id.values(), key=lambda entry: (entry["name"].casefold(), entry["class_id"]))


def list_instruments() -> dict:
    configured = os.environ.get("CUBASE_PIAPRO_CUBASE_PREFS")
    if configured:
        preferences = Path(configured)
    else:
        profile = os.environ.get("APPDATA")
        if not profile:
            raise IntegrationError("CUBASE_PROFILE_NOT_FOUND", "The Windows roaming profile is unavailable.")
        preferences = Path(profile) / "Steinberg" / "Cubase 14_64"
    cache = preferences / "Cubase LE VST3 Cache" / "vst3plugins.xml"
    if not cache.is_file():
        raise IntegrationError("VST3_CACHE_NOT_FOUND", f"Open Cubase LE to populate the VST3 scan cache for {preferences.name}.")
    _, data, sha = read_file(cache)
    version = re.fullmatch(r"Cubase (\d+)_64", preferences.name, re.I)
    source = f"Cubase LE {version[1]} VST3 scan cache" if version else "Cubase LE VST3 scan cache"
    return {"source": source, "sha256": sha, "instruments": _instruments(data),
            "live_availability_verified": False}
