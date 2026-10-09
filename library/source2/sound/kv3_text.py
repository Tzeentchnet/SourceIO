from __future__ import annotations

import json
import math
import numbers
import re
from collections.abc import Mapping, Sequence

from ..keyvalues3.enums import Specifier
from ..keyvalues3.types import BinaryBlob, Bool, NullObject
from .types import MalformedSoundError


KV3_TEXT_HEADER = (
    "<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} "
    "format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->"
)
_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STRING_SPECIFIERS = {
    Specifier.RESOURCE: "resource",
    Specifier.RESOURCE_NAME: "resource_name",
    Specifier.PANORAMA: "panorama",
    Specifier.SOUNDEVENT: "soundevent",
    Specifier.SUBCLASS: "subclass",
    Specifier.ENTITY_NAME: "entity_name",
    Specifier.LOCALIZE: "localize",
}


def _key(value: object) -> str:
    key = str(value)
    return key if _PLAIN_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _string(value: str) -> str:
    specifier = getattr(value, "specifier", Specifier.UNSPECIFIED)
    prefix = _STRING_SPECIFIERS.get(specifier)
    encoded = json.dumps(str(value), ensure_ascii=False)
    return f"{prefix}:{encoded}" if prefix else encoded


def _scalar(value: object) -> str | None:
    if value is None or isinstance(value, NullObject):
        return "null"
    if isinstance(value, (bool, Bool)):
        return "true" if bool(value) else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if not math.isfinite(number):
            raise MalformedSoundError("CTRL metadata contains a non-finite number")
        text = repr(number)
        return text if any(character in text for character in ".eE") else f"{text}.0"
    return None


def _blob_lines(value: bytes, indent: int) -> list[str]:
    prefix = "\t" * indent
    lines = ["#["]
    for offset in range(0, len(value), 16):
        lines.append(f"{prefix}\t" + " ".join(f"{byte:02X}" for byte in value[offset:offset + 16]))
    lines.append(f"{prefix}]")
    return lines


def _value_lines(value: object, indent: int) -> list[str]:
    scalar = _scalar(value)
    if scalar is not None:
        return [scalar]
    if isinstance(value, (bytes, bytearray, memoryview, BinaryBlob)):
        return _blob_lines(bytes(value), indent)
    if isinstance(value, Mapping):
        prefix = "\t" * indent
        lines = ["{"]
        for key, child in value.items():
            child_lines = _value_lines(child, indent + 1)
            lines.append(f"{prefix}\t{_key(key)} = {child_lines[0]}")
            lines.extend(child_lines[1:])
        lines.append(f"{prefix}}}")
        return lines
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, Sequence):
        prefix = "\t" * indent
        lines = ["["]
        for child in value:
            child_lines = _value_lines(child, indent + 1)
            lines.append(f"{prefix}\t{child_lines[0]}")
            lines.extend(child_lines[1:])
        lines.append(f"{prefix}]")
        return lines
    raise MalformedSoundError(f"CTRL metadata contains unsupported value type {type(value).__name__}")


def ctrl_to_compiler_text(ctrl: Mapping[str, object]) -> str:
    root_lines = _value_lines({"VrfExportedSound": ctrl}, 0)
    return f"{KV3_TEXT_HEADER}\n\n" + "\n".join(root_lines) + "\n"
