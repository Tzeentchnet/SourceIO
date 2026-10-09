"""Normalize, validate, compare, and benchmark Source 2 compiled resources."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import math
import statistics
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures" / "source2_generated"
DEFAULT_SNAPSHOT = (
    REPOSITORY_ROOT
    / "tests"
    / "conformance"
    / "snapshots"
    / "normalized_summaries.json"
)
SCHEMA_ID = "https://sourceio.dev/schemas/source2-normalized-summary-v1"
SCHEMA_VERSION = 1
KV3_SIGNATURES = {
    b"VKV\x03",
    b"\x013VK",
    b"\x023VK",
    b"\x033VK",
    b"\x043VK",
    b"\x053VK",
}
RESOURCE_EXTENSIONS = {
    ".vtex_c": "texture",
    ".vmat_c": "material",
    ".vmesh_c": "mesh",
    ".vmdl_c": "model",
    ".vanim_c": "animation",
    ".vagrp_c": "animation_group",
    ".vseq_c": "animation_sequence",
    ".vsnd_c": "sound",
    ".vphys_c": "physics",
    ".vwrld_c": "world",
    ".vmap_c": "map",
    ".vdata_c": "data",
}
CAPABILITY_NAMES = (
    "header",
    "blocks",
    "dependencies",
    "kv3",
    "dimensions",
    "channels",
    "mesh",
    "skeleton",
    "animation",
    "sound",
)
TEXTURE_CHANNELS = {
    1: ("R", "G", "B"),
    2: ("R", "G", "B", "A"),
    3: ("I",),
    4: ("R", "G", "B", "A"),
    5: ("R",),
    6: ("R", "G"),
    7: ("R", "G", "B", "A"),
    8: ("R",),
    9: ("R", "G"),
    10: ("R", "G", "B", "A"),
    11: ("R",),
    12: ("R", "G"),
    13: ("R", "G", "B"),
    14: ("R", "G", "B", "A"),
    15: ("R", "G", "B", "A"),
    16: ("R", "G", "B", "A"),
    17: ("R", "G", "B", "A"),
    18: ("R", "G", "B", "A"),
    19: ("R", "G", "B"),
    20: ("R", "G", "B", "A"),
    21: ("R", "G"),
    22: ("I", "A"),
    23: ("R", "G", "B"),
    24: ("R", "G", "B", "A"),
    25: ("R",),
    26: ("R", "G"),
    27: ("R",),
    28: ("B", "G", "R", "A"),
    29: ("R", "G", "B", "A"),
    30: ("R", "G", "B", "A"),
    31: ("R",),
    32: ("A",),
    33: ("R",),
}


@dataclass(frozen=True)
class Limits:
    max_file_size: int = 256 * 1024 * 1024
    max_block_count: int = 4_096
    max_block_size: int = 64 * 1024 * 1024
    max_dependency_count: int = 16_384
    max_collection_items: int = 100_000
    max_value_nodes: int = 250_000
    max_value_depth: int = 64
    max_texture_dimension: int = 16_384
    max_vbib_buffers: int = 4_096


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class _BlockRecord:
    index: int
    name: str
    offset: int
    size: int
    valid_range: bool


class SnapshotMismatch(AssertionError):
    pass


class _ValueLimitExceeded(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _resource_identity(path: str | Path, data: bytes) -> dict[str, Any]:
    filename = Path(path).name
    lowered = filename.lower()
    compiled_extension = Path(filename).suffix.lower()
    resource_type = "unknown"
    stem = Path(filename).stem
    for extension, candidate_type in RESOURCE_EXTENSIONS.items():
        if lowered.endswith(extension):
            compiled_extension = extension
            resource_type = candidate_type
            stem = filename[: -len(extension)]
            break
    return {
        "name": stem,
        "path": filename,
        "resource_type": resource_type,
        "compiled_extension": compiled_extension,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "header_version": None,
        "resource_version": None,
        "compiler": None,
        "input_path": None,
        "confidence": None,
        "evidence": [],
    }


def empty_summary(path: str | Path, data: bytes) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "identity": _resource_identity(path, data),
        "blocks": [],
        "dependencies": [],
        "dimensions": {
            "width": None,
            "height": None,
            "depth": None,
            "mip_count": None,
        },
        "channels": {"count": None, "names": []},
        "mesh": {
            "mesh_count": 0,
            "vertex_count": 0,
            "index_count": 0,
            "primitive_count": 0,
        },
        "skeleton": {"bone_count": 0},
        "animation": {
            "clip_count": 0,
            "frame_count": None,
            "fps": None,
            "duration_seconds": None,
        },
        "sound": {
            "sample_rate": None,
            "channel_count": None,
            "sample_count": None,
            "duration_seconds": None,
            "bits_per_sample": None,
            "encoding": None,
        },
        "capabilities": {
            "operations": {
                "read": None,
                "extract": None,
                "render": None,
                "write": None,
            },
            "features": {
                "available": [],
                "unavailable": list(CAPABILITY_NAMES),
            },
        },
        "diagnostics": [],
    }


def _diagnostic(
    summary: MutableMapping[str, Any],
    severity: str,
    code: str,
    message: str,
    offset: int | None = None,
) -> None:
    record = {
        "severity": severity.lower(),
        "code": code,
        "message": message,
        "offset": offset,
    }
    if record not in summary["diagnostics"]:
        summary["diagnostics"].append(record)


def _has_error(summary: Mapping[str, Any], prefix: str | None = None) -> bool:
    return any(
        diagnostic["severity"] == "error"
        and (prefix is None or diagnostic["code"].startswith(prefix))
        for diagnostic in summary["diagnostics"]
    )


def _capability(summary: MutableMapping[str, Any], name: str) -> None:
    available = summary["capabilities"]["features"]["available"]
    if name not in available:
        available.append(name)


def _finalize(summary: MutableMapping[str, Any]) -> dict[str, Any]:
    summary["blocks"].sort(key=lambda item: item["index"])
    summary["dependencies"] = sorted(set(summary["dependencies"]))
    features = summary["capabilities"]["features"]
    features["available"] = sorted(set(features["available"]))
    features["unavailable"] = sorted(
        set(CAPABILITY_NAMES) - set(features["available"])
    )
    summary["diagnostics"].sort(
        key=lambda item: (
            item["severity"],
            item["code"],
            -1 if item["offset"] is None else item["offset"],
            item["message"],
        )
    )
    return dict(summary)


def _parse_blocks(
    data: bytes,
    summary: MutableMapping[str, Any],
    limits: Limits,
) -> list[_BlockRecord]:
    declared_size, header_version, resource_version, table_relative, block_count = struct.unpack_from(
        "<IHHII", data, 0
    )
    summary["identity"]["header_version"] = header_version
    summary["identity"]["resource_version"] = resource_version
    resource_type = summary["identity"]["resource_type"]
    sound_streaming_layout = (
        resource_type == "sound" and 16 <= declared_size <= len(data)
    )
    structural_size = declared_size if sound_streaming_layout else len(data)
    if not sound_streaming_layout and declared_size != len(data):
        _diagnostic(
            summary,
            "error",
            "header.file_size",
            f"declared file size {declared_size} does not match {len(data)} bytes",
            0,
        )
    if header_version != 12:
        _diagnostic(
            summary,
            "error",
            "header.version",
            f"unsupported compiled-header version {header_version}",
            4,
        )
    if not _has_error(summary, "header."):
        _capability(summary, "header")

    table_offset = 8 + table_relative
    if block_count > limits.max_block_count:
        _diagnostic(
            summary,
            "error",
            "limit.block_count",
            f"block count {block_count} exceeds {limits.max_block_count}",
            12,
        )
        return []
    if table_offset < 16:
        _diagnostic(
            summary,
            "error",
            "block_table.offset",
            f"block table starts inside the fixed header at {table_offset}",
            8,
        )
        return []
    table_end = table_offset + 12 * block_count
    if table_offset > structural_size or table_end > structural_size:
        _diagnostic(
            summary,
            "error",
            "block_table.range",
            f"block table range [{table_offset}, {table_end}) exceeds {structural_size} bytes",
            8,
        )
        return []

    records = []
    occupied: list[tuple[int, int, str]] = []
    for index in range(block_count):
        entry_offset = table_offset + 12 * index
        raw_name = data[entry_offset : entry_offset + 4]
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError:
            name = raw_name.hex()
            _diagnostic(
                summary,
                "error",
                "block.name",
                f"block {index} has a non-ASCII FourCC",
                entry_offset,
            )
        relative_offset, block_size = struct.unpack_from("<II", data, entry_offset + 4)
        absolute_offset = entry_offset + 4 + relative_offset
        valid_range = True
        if block_size > limits.max_block_size:
            valid_range = False
            _diagnostic(
                summary,
                "error",
                "limit.block_size",
                f"block {name} size {block_size} exceeds {limits.max_block_size}",
                entry_offset + 8,
            )
        if absolute_offset < table_end:
            valid_range = False
            _diagnostic(
                summary,
                "error",
                "block.overlap_header",
                f"block {name} starts before the block table ends",
                entry_offset + 4,
            )
        block_end = absolute_offset + block_size
        if absolute_offset > structural_size or block_end > structural_size:
            valid_range = False
            _diagnostic(
                summary,
                "error",
                "block.range",
                f"block {name} range [{absolute_offset}, {block_end}) exceeds {structural_size} bytes",
                entry_offset + 4,
            )
        if valid_range:
            for other_start, other_end, other_name in occupied:
                if absolute_offset < other_end and other_start < block_end:
                    valid_range = False
                    _diagnostic(
                        summary,
                        "error",
                        "block.overlap",
                        f"block {name} overlaps block {other_name}",
                        entry_offset + 4,
                    )
                    break
        if valid_range:
            occupied.append((absolute_offset, block_end, name))
        records.append(_BlockRecord(index, name, absolute_offset, block_size, valid_range))
        summary["blocks"].append(
            {
                "index": index,
                "name": name,
                "offset": absolute_offset,
                "size": block_size,
            }
        )

    if records and not _has_error(summary, "block") and not _has_error(summary, "limit.block"):
        _capability(summary, "blocks")
    return records


def _parse_rerl(
    payload: bytes,
    block_offset: int,
    summary: MutableMapping[str, Any],
    limits: Limits,
) -> None:
    if len(payload) < 8:
        _diagnostic(
            summary,
            "error",
            "payload.rerl_invalid",
            "RERL block is shorter than its fixed header",
            block_offset,
        )
        return
    entries_offset, count = struct.unpack_from("<II", payload, 0)
    if count > limits.max_dependency_count:
        _diagnostic(
            summary,
            "error",
            "limit.dependency_count",
            f"dependency count {count} exceeds {limits.max_dependency_count}",
            block_offset + 4,
        )
        return
    entries_end = entries_offset + 16 * count
    if entries_offset < 8 or entries_end > len(payload):
        _diagnostic(
            summary,
            "error",
            "payload.rerl_invalid",
            f"RERL entry range [{entries_offset}, {entries_end}) is invalid",
            block_offset,
        )
        return

    dependencies = []
    for index in range(count):
        entry_offset = entries_offset + 16 * index
        name_relative = struct.unpack_from("<I", payload, entry_offset + 8)[0]
        name_offset = entry_offset + 8 + name_relative
        if name_offset < entries_end or name_offset >= len(payload):
            _diagnostic(
                summary,
                "error",
                "payload.rerl_invalid",
                f"dependency {index} name offset {name_offset} is invalid",
                block_offset + entry_offset + 8,
            )
            return
        terminator = payload.find(b"\0", name_offset)
        if terminator < 0:
            _diagnostic(
                summary,
                "error",
                "payload.rerl_invalid",
                f"dependency {index} name is not null-terminated",
                block_offset + name_offset,
            )
            return
        try:
            dependency = payload[name_offset:terminator].decode("utf-8")
        except UnicodeDecodeError:
            _diagnostic(
                summary,
                "error",
                "payload.rerl_invalid",
                f"dependency {index} name is not UTF-8",
                block_offset + name_offset,
            )
            return
        dependencies.append(dependency.replace("\\", "/"))
    summary["dependencies"].extend(dependencies)
    _capability(summary, "dependencies")


def _parse_texture_data(
    payload: bytes,
    block_offset: int,
    summary: MutableMapping[str, Any],
    limits: Limits,
) -> None:
    if len(payload) < 40:
        _diagnostic(
            summary,
            "error",
            "payload.texture_invalid",
            "texture DATA block is shorter than 40 bytes",
            block_offset,
        )
        return
    values = struct.unpack_from("<HH4f3HBBIII", payload, 0)
    version = values[0]
    width, height, depth = values[6:9]
    pixel_format, mip_count = values[9:11]
    if version != 1:
        _diagnostic(
            summary,
            "error",
            "payload.texture_version",
            f"unsupported texture-data version {version}",
            block_offset,
        )
    if any(dimension > limits.max_texture_dimension for dimension in (width, height, depth)):
        _diagnostic(
            summary,
            "error",
            "limit.dimension",
            f"texture dimensions {width}x{height}x{depth} exceed {limits.max_texture_dimension}",
            block_offset + 20,
        )
        return
    summary["dimensions"].update(
        {
            "width": width,
            "height": height,
            "depth": depth,
            "mip_count": mip_count,
        }
    )
    channel_names = list(TEXTURE_CHANNELS.get(pixel_format, ()))
    summary["channels"].update(
        {
            "count": len(channel_names) if channel_names else None,
            "names": channel_names,
        }
    )
    _capability(summary, "dimensions")
    if channel_names:
        _capability(summary, "channels")


def _parse_sound_data(
    payload: bytes,
    block_offset: int,
    streaming_size: int,
    summary: MutableMapping[str, Any],
) -> None:
    if len(payload) < 48:
        _diagnostic(
            summary,
            "error",
            "payload.sound_invalid",
            "VSND v4 DATA block is shorter than 48 bytes",
            block_offset,
        )
        return
    (
        sample_rate,
        format_code,
        channels,
        loop_start,
        sample_count,
        duration,
        _sentence_relative,
        _header_relative,
        header_size,
        declared_streaming_size,
        _unknown_a,
        _unknown_b,
        _unknown_c,
        loop_end,
    ) = struct.unpack_from("<HBBiIfIIiIiiii", payload, 0)
    encodings = {
        0: ("pcm16", 16),
        1: ("pcm8", 8),
        2: ("mp3", 0),
        3: ("adpcm", 16),
    }
    if format_code not in encodings:
        _diagnostic(
            summary,
            "error",
            "payload.sound_invalid",
            f"unknown VSND v4 encoding code {format_code}",
            block_offset + 2,
        )
        return
    if sample_rate <= 0 or channels not in (1, 2):
        _diagnostic(
            summary,
            "error",
            "payload.sound_invalid",
            f"invalid sound format {sample_rate} Hz with {channels} channels",
            block_offset,
        )
        return
    if not math.isfinite(duration) or duration < 0:
        _diagnostic(
            summary,
            "error",
            "payload.sound_invalid",
            f"invalid sound duration {duration!r}",
            block_offset + 12,
        )
        return
    if header_size < 0 or loop_start < -1 or loop_start > sample_count:
        _diagnostic(
            summary,
            "error",
            "payload.sound_invalid",
            "sound loop or auxiliary-header metadata is invalid",
            block_offset,
        )
        return
    if loop_end not in (-1, 0) and not loop_start <= loop_end <= sample_count:
        _diagnostic(
            summary,
            "error",
            "payload.sound_invalid",
            "sound loop end is outside the sample range",
            block_offset + 44,
        )
        return
    if declared_streaming_size != streaming_size:
        _diagnostic(
            summary,
            "error",
            "payload.sound_stream_size",
            f"VSND declares {declared_streaming_size} streaming bytes, got {streaming_size}",
            block_offset + 28,
        )
        return
    encoding, bits_per_sample = encodings[format_code]
    if encoding in {"pcm8", "pcm16"}:
        expected_size = sample_count * channels * (bits_per_sample // 8)
        if declared_streaming_size != expected_size:
            _diagnostic(
                summary,
                "error",
                "payload.sound_stream_size",
                f"PCM metadata requires {expected_size} bytes, got {declared_streaming_size}",
                block_offset + 28,
            )
            return
    summary["sound"].update(
        {
            "sample_rate": sample_rate,
            "channel_count": channels,
            "sample_count": sample_count,
            "duration_seconds": round(float(duration), 9),
            "bits_per_sample": bits_per_sample,
            "encoding": encoding,
        }
    )
    _capability(summary, "sound")


def _sourceio_kv3_imports():
    repository_parent = str(REPOSITORY_ROOT.parent)
    if repository_parent not in sys.path:
        sys.path.insert(0, repository_parent)
    with contextlib.redirect_stdout(sys.stderr):
        from SourceIO.library.source2.keyvalues3.binary_keyvalues import (
            KV3UnsupportedVersion,
            read_valve_keyvalue3,
        )
        from SourceIO.library.source2.exceptions import KV3Error
        from SourceIO.library.utils import MemoryBuffer

    return read_valve_keyvalue3, KV3UnsupportedVersion, KV3Error, MemoryBuffer


def _bounded_value(value: Any, limits: Limits) -> Any:
    node_count = 0

    def convert(current: Any, depth: int) -> Any:
        nonlocal node_count
        node_count += 1
        if node_count > limits.max_value_nodes:
            raise _ValueLimitExceeded(
                "limit.value_nodes",
                f"decoded value exceeds {limits.max_value_nodes} nodes",
            )
        if depth > limits.max_value_depth:
            raise _ValueLimitExceeded(
                "limit.value_depth",
                f"decoded value exceeds depth {limits.max_value_depth}",
            )
        if isinstance(current, Mapping):
            if len(current) > limits.max_collection_items:
                raise _ValueLimitExceeded(
                    "limit.collection_items",
                    f"object has more than {limits.max_collection_items} members",
                )
            return {
                str(key): convert(child, depth + 1)
                for key, child in current.items()
                if str(key) != "__METADATA__"
            }
        if isinstance(current, (list, tuple)):
            if len(current) > limits.max_collection_items:
                raise _ValueLimitExceeded(
                    "limit.collection_items",
                    f"array has more than {limits.max_collection_items} items",
                )
            return [convert(child, depth + 1) for child in current]
        if isinstance(current, bytes):
            return {"byte_size": len(current), "sha256": hashlib.sha256(current).hexdigest()}
        if isinstance(current, bool) or current is None:
            return current
        if isinstance(current, int):
            return int(current)
        if isinstance(current, float):
            if not math.isfinite(float(current)):
                return str(float(current))
            return float(current)
        if isinstance(current, str):
            return str(current)
        if hasattr(current, "tolist"):
            converted = current.tolist()
            return convert(converted, depth + 1)
        raise TypeError(f"unsupported decoded KV3 value {type(current).__name__}")

    return convert(value, 0)


def _decode_kv3(
    payload: bytes,
    block_offset: int,
    summary: MutableMapping[str, Any],
    limits: Limits,
) -> Any | None:
    if len(payload) < 4 or payload[:4] not in KV3_SIGNATURES:
        _diagnostic(
            summary,
            "error",
            "payload.kv3_invalid",
            "DATA block does not contain a complete KV3 signature",
            block_offset,
        )
        return None
    try:
        read_valve_keyvalue3, unsupported_version, kv3_error, memory_buffer = (
            _sourceio_kv3_imports()
        )
    except ModuleNotFoundError as error:
        _diagnostic(
            summary,
            "warning",
            "sourceio.kv3_unavailable",
            f"SourceIO KV3 reader is unavailable: {error}",
            block_offset,
        )
        return None

    try:
        decoded = read_valve_keyvalue3(memory_buffer(payload))
        normalized = _bounded_value(decoded, limits)
    except _ValueLimitExceeded as error:
        _diagnostic(summary, "error", error.code, str(error), block_offset)
        return None
    except (
        unsupported_version,
        kv3_error,
        AssertionError,
        BufferError,
        EOFError,
        IndexError,
        KeyError,
        MemoryError,
        OverflowError,
        RuntimeError,
        struct.error,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        zlib.error,
    ) as error:
        _diagnostic(
            summary,
            "error",
            "payload.kv3_invalid",
            f"KV3 decode failed: {type(error).__name__}: {error}",
            block_offset,
        )
        return None
    _capability(summary, "kv3")
    return normalized


def _find_value(root: Any, names: Iterable[str]) -> Any | None:
    wanted = set(names)
    pending = [root]
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            for name in wanted:
                if name in current:
                    return current[name]
            pending.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            pending.extend(reversed(current))
    return None


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if integer else round(float(value), 9)


def _string(value: Any) -> str | None:
    return str(value) if isinstance(value, str) else None


def _extract_kv3_metadata(root: Any, summary: MutableMapping[str, Any]) -> None:
    resource_type = summary["identity"]["resource_type"]
    if resource_type == "mesh":
        mesh_fields = {
            "mesh_count": ("m_meshCount", "mesh_count"),
            "vertex_count": ("m_vertexCount", "vertex_count", "m_nVertexCount"),
            "index_count": ("m_indexCount", "index_count", "m_nIndexCount"),
            "primitive_count": ("m_primitiveCount", "primitive_count"),
        }
        mesh_found = False
        for output_name, input_names in mesh_fields.items():
            value = _number(_find_value(root, input_names), integer=True)
            if value is not None:
                summary["mesh"][output_name] = value
                mesh_found = True
        if mesh_found:
            _capability(summary, "mesh")

    if resource_type == "model":
        bone_names = _find_value(root, ("m_boneName", "bone_names"))
        bone_count = _number(_find_value(root, ("m_boneCount", "bone_count")), integer=True)
        if isinstance(bone_names, list):
            bone_count = len(bone_names)
        if bone_count is not None:
            summary["skeleton"]["bone_count"] = bone_count
            _capability(summary, "skeleton")

    if resource_type in {"animation", "animation_group", "animation_sequence"}:
        animation_fields = {
            "clip_count": (("m_nClipCount", "clip_count"), True),
            "frame_count": (("m_nFrames", "frame_count"), True),
            "fps": (("m_fps", "fps", "m_flFps"), False),
            "duration_seconds": (("m_flDuration", "duration", "duration_seconds"), False),
        }
        animation_found = False
        for output_name, (input_names, integer) in animation_fields.items():
            value = _number(_find_value(root, input_names), integer=integer)
            if value is not None:
                summary["animation"][output_name] = value
                animation_found = True
        if animation_found:
            _capability(summary, "animation")

    if resource_type == "sound":
        sound_fields = {
            "sample_rate": (("m_nSampleRate", "sample_rate"), True),
            "channel_count": (("m_nChannels", "channel_count"), True),
            "sample_count": (("m_nSampleCount", "sample_count"), True),
            "duration_seconds": (("m_flDuration", "duration", "duration_seconds"), False),
            "bits_per_sample": (("m_nBitsPerSample", "bits_per_sample"), True),
        }
        sound_found = False
        for output_name, (input_names, integer) in sound_fields.items():
            value = _number(_find_value(root, input_names), integer=integer)
            if value is not None:
                summary["sound"][output_name] = value
                sound_found = True
        encoding = _string(_find_value(root, ("m_encoding", "encoding", "codec")))
        if encoding is not None:
            summary["sound"]["encoding"] = encoding
            sound_found = True
        if sound_found:
            _capability(summary, "sound")


def _parse_vbib(
    payload: bytes,
    block_offset: int,
    summary: MutableMapping[str, Any],
    limits: Limits,
) -> None:
    if len(payload) < 16:
        _diagnostic(
            summary,
            "error",
            "payload.vbib_invalid",
            "VBIB block is shorter than 16 bytes",
            block_offset,
        )
        return
    vertex_offset, vertex_buffers, index_relative, index_buffers = struct.unpack_from(
        "<IIII", payload, 0
    )
    index_offset = 8 + index_relative
    if vertex_buffers > limits.max_vbib_buffers or index_buffers > limits.max_vbib_buffers:
        _diagnostic(
            summary,
            "error",
            "limit.vbib_buffers",
            "VBIB buffer count exceeds the safety limit",
            block_offset,
        )
        return
    if vertex_offset + 24 * vertex_buffers > len(payload) or index_offset + 24 * index_buffers > len(
        payload
    ):
        _diagnostic(
            summary,
            "error",
            "payload.vbib_invalid",
            "VBIB descriptor table exceeds the block",
            block_offset,
        )
        return

    vertex_count = sum(
        struct.unpack_from("<I", payload, vertex_offset + 24 * index)[0]
        for index in range(vertex_buffers)
    )
    index_count = sum(
        struct.unpack_from("<I", payload, index_offset + 24 * index)[0]
        for index in range(index_buffers)
    )
    summary["mesh"]["vertex_count"] = vertex_count
    summary["mesh"]["index_count"] = index_count
    if vertex_buffers or index_buffers:
        summary["mesh"]["mesh_count"] = max(summary["mesh"]["mesh_count"], 1)
        _capability(summary, "mesh")


def inspect_resource_bytes(
    data: bytes,
    path: str | Path = "<memory>.bin",
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Return a bounded normalized summary; malformed inputs become diagnostics."""
    data = bytes(data)
    summary = empty_summary(path, data)
    if len(data) > limits.max_file_size:
        _diagnostic(
            summary,
            "error",
            "limit.file_size",
            f"file size {len(data)} exceeds {limits.max_file_size}",
            0,
        )
        return _finalize(summary)
    if len(data) < 16:
        _diagnostic(
            summary,
            "error",
            "header.truncated",
            f"compiled header requires 16 bytes, got {len(data)}",
            len(data),
        )
        return _finalize(summary)

    records = _parse_blocks(data, summary, limits)
    resource_type = summary["identity"]["resource_type"]
    for record in records:
        if not record.valid_range:
            continue
        payload = data[record.offset : record.offset + record.size]
        if record.name == "RERL":
            _parse_rerl(payload, record.offset, summary, limits)
        elif record.name == "VBIB":
            _parse_vbib(payload, record.offset, summary, limits)
        elif record.name == "DATA":
            if resource_type == "texture":
                _parse_texture_data(payload, record.offset, summary, limits)
            elif resource_type == "sound":
                declared_metadata_size = struct.unpack_from("<I", data, 0)[0]
                _parse_sound_data(
                    payload,
                    record.offset,
                    len(data) - declared_metadata_size,
                    summary,
                )
            else:
                decoded = _decode_kv3(payload, record.offset, summary, limits)
                if decoded is not None:
                    _extract_kv3_metadata(decoded, summary)
    return _finalize(summary)


def summarize_path(
    path: str | Path,
    *,
    limits: Limits = DEFAULT_LIMITS,
    use_registry: bool = True,
) -> dict[str, Any]:
    resource_path = Path(path)
    data = resource_path.read_bytes()
    summary = inspect_resource_bytes(data, resource_path, limits=limits)
    if not use_registry or error_codes(summary):
        return summary
    registry = create_conformance_registry()
    resource = registry.parse(data, resource_path.name)
    return merge_public_contracts(
        summary,
        identity=resource.identity,
        capabilities=resource.capabilities,
        diagnostics=resource.diagnostics,
    )


def load_public_contracts() -> dict[str, type]:
    """Import the integration contracts only when registry integration is requested."""
    repository_parent = str(REPOSITORY_ROOT.parent)
    if repository_parent not in sys.path:
        sys.path.insert(0, repository_parent)
    with contextlib.redirect_stdout(sys.stderr):
        from SourceIO.library.source2.interfaces import (
            Diagnostic,
            ResourceCapabilities,
            ResourceIdentity,
            ResourceResolver,
        )
        from SourceIO.library.source2.resource_registry import ResourceRegistry

    return {
        "ResourceIdentity": ResourceIdentity,
        "ResourceCapabilities": ResourceCapabilities,
        "Diagnostic": Diagnostic,
        "ResourceResolver": ResourceResolver,
        "ResourceRegistry": ResourceRegistry,
    }


def create_conformance_registry():
    """Create a registry with optional resource-family plugins enabled."""
    contracts = load_public_contracts()
    registry = contracts["ResourceRegistry"]()
    with contextlib.redirect_stdout(sys.stderr):
        from SourceIO.library.source2.animation.loader import register_animation_resources
        from SourceIO.library.source2.resource_types.compiled_sound_resource import (
            register_sound_resource,
        )

    register_animation_resources(registry)
    register_sound_resource(registry)
    return registry


def _object_mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    names = (
        "name",
        "path",
        "resource_type",
        "type",
        "kind",
        "compiled_extension",
        "extension",
        "header_version",
        "resource_version",
        "compiler",
        "input_path",
        "confidence",
        "evidence",
        "read",
        "extract",
        "render",
        "write",
        "severity",
        "code",
        "message",
        "offset",
    )
    return {
        name: getattr(value, name)
        for name in names
        if hasattr(value, name)
    }


def merge_public_contracts(
    summary: Mapping[str, Any],
    *,
    identity: Any = None,
    capabilities: Any = None,
    diagnostics: Iterable[Any] = (),
) -> dict[str, Any]:
    """Merge ResourceIdentity/Capabilities/Diagnostic values into the stable schema."""
    contracts = load_public_contracts()
    if identity is not None and not isinstance(identity, contracts["ResourceIdentity"]):
        raise TypeError("identity must implement ResourceIdentity")
    if capabilities is not None and not isinstance(
        capabilities, contracts["ResourceCapabilities"]
    ):
        raise TypeError("capabilities must implement ResourceCapabilities")
    diagnostics = tuple(diagnostics)
    if any(
        not isinstance(diagnostic, contracts["Diagnostic"])
        for diagnostic in diagnostics
    ):
        raise TypeError("diagnostics must contain Diagnostic values")

    merged = json.loads(json.dumps(summary))
    identity_values = _object_mapping(identity)
    aliases = {
        "path": ("path",),
        "resource_type": ("kind", "resource_type", "type"),
        "compiled_extension": ("compiled_extension", "extension"),
        "header_version": ("header_version",),
        "resource_version": ("resource_version",),
        "compiler": ("compiler",),
        "input_path": ("input_path",),
        "confidence": ("confidence",),
        "evidence": ("evidence",),
    }
    for output_name, input_names in aliases.items():
        for input_name in input_names:
            if identity_values.get(input_name) is not None:
                value = identity_values[input_name]
                if output_name in {"header_version", "resource_version"}:
                    normalized = int(value)
                elif output_name == "confidence":
                    normalized = float(value)
                elif output_name == "evidence":
                    normalized = [str(item) for item in value]
                elif output_name == "path":
                    normalized = Path(str(value)).name
                else:
                    normalized = (
                        str(value.value)
                        if isinstance(value, Enum)
                        else str(value)
                    )
                merged["identity"][output_name] = normalized
                break

    capability_values = _object_mapping(capabilities)
    for operation in ("read", "extract", "render", "write"):
        value = capability_values.get(operation)
        if value is not None:
            merged["capabilities"]["operations"][operation] = (
                value.name.lower() if isinstance(value, Enum) else str(value).lower()
            )
    for diagnostic in diagnostics:
        values = _object_mapping(diagnostic)
        severity = values.get("severity", "warning")
        if isinstance(severity, Enum):
            severity = severity.name
        _diagnostic(
            merged,
            str(severity),
            str(values.get("code", "resource.diagnostic")),
            str(values.get("message", diagnostic)),
            values.get("offset"),
        )
    return _finalize(merged)


def error_codes(summary: Mapping[str, Any]) -> set[str]:
    return {
        diagnostic["code"]
        for diagnostic in summary["diagnostics"]
        if diagnostic["severity"] == "error"
    }


def snapshot_mismatches(expected: Any, actual: Any, path: str = "$") -> list[str]:
    mismatches: list[str] = []
    if type(expected) is not type(actual):
        return [f"{path}: type {type(expected).__name__} != {type(actual).__name__}"]
    if isinstance(expected, Mapping):
        expected_keys = set(expected)
        actual_keys = set(actual)
        for key in sorted(expected_keys - actual_keys):
            mismatches.append(f"{path}.{key}: missing")
        for key in sorted(actual_keys - expected_keys):
            mismatches.append(f"{path}.{key}: unexpected")
        for key in sorted(expected_keys & actual_keys):
            mismatches.extend(snapshot_mismatches(expected[key], actual[key], f"{path}.{key}"))
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            mismatches.append(f"{path}: length {len(expected)} != {len(actual)}")
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            mismatches.extend(
                snapshot_mismatches(expected_item, actual_item, f"{path}[{index}]")
            )
    elif expected != actual:
        mismatches.append(f"{path}: {expected!r} != {actual!r}")
    return mismatches


def assert_snapshot(expected: Any, actual: Any) -> None:
    mismatches = snapshot_mismatches(expected, actual)
    if mismatches:
        details = "\n".join(f"- {mismatch}" for mismatch in mismatches[:100])
        if len(mismatches) > 100:
            details += f"\n- ... and {len(mismatches) - 100} more"
        raise SnapshotMismatch(f"strict snapshot mismatch:\n{details}")


def summarize_fixture_manifest(
    fixture_root: str | Path = DEFAULT_FIXTURES,
) -> dict[str, dict[str, Any]]:
    root = Path(fixture_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="ascii"))
    return {
        record["path"]: summarize_path(root / record["path"])
        for record in manifest["fixtures"]
    }


def self_test(fixture_root: str | Path = DEFAULT_FIXTURES) -> dict[str, Any]:
    root = Path(fixture_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="ascii"))
    valid_count = 0
    malformed_count = 0
    for record in manifest["fixtures"]:
        data = (root / record["path"]).read_bytes()
        if len(data) != record["byte_size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise AssertionError(f"fixture hash mismatch: {record['path']}")
        summary = inspect_resource_bytes(data, record["path"])
        if error_codes(summary):
            raise AssertionError(
                f"valid fixture {record['path']} has errors: {sorted(error_codes(summary))}"
            )
        valid_count += 1
    for record in manifest["malformed"]:
        data = (root / record["path"]).read_bytes()
        if len(data) != record["byte_size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise AssertionError(f"malformed fixture hash mismatch: {record['path']}")
        summary = inspect_resource_bytes(data, record["path"])
        if record["expected_code"] not in error_codes(summary):
            raise AssertionError(
                f"{record['path']} did not produce {record['expected_code']}: "
                f"{sorted(error_codes(summary))}"
            )
        malformed_count += 1
    return {
        "status": "passed",
        "valid_fixtures": valid_count,
        "malformed_fixtures": malformed_count,
    }


def _percentile_95(samples: Sequence[int]) -> int:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def benchmark_paths(
    paths: Sequence[str | Path],
    *,
    iterations: int = 10,
    warmups: int = 2,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    """Measure summaries and return raw samples; callers choose their own budgets."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    if warmups < 0:
        raise ValueError("warmups cannot be negative")
    resources = [(Path(path), Path(path).read_bytes()) for path in paths]
    if not resources:
        raise ValueError("at least one benchmark path is required")

    for _ in range(warmups):
        for path, data in resources:
            inspect_resource_bytes(data, path)

    samples = []
    for _ in range(iterations):
        started = clock()
        for path, data in resources:
            inspect_resource_bytes(data, path)
        samples.append(clock() - started)
    if any(sample < 0 for sample in samples):
        raise ValueError("benchmark clock must be monotonic")

    total_bytes = sum(len(data) for _, data in resources)
    mean_ns = statistics.fmean(samples)
    throughput = 0.0 if mean_ns == 0 else total_bytes / (mean_ns / 1_000_000_000)
    return {
        "schema_version": 1,
        "parser": "source2_conformance.inspect_resource_bytes",
        "resources": [path.name for path, _ in resources],
        "resource_count": len(resources),
        "bytes_per_iteration": total_bytes,
        "iterations": iterations,
        "warmups": warmups,
        "samples_ns": samples,
        "min_ns": min(samples),
        "median_ns": statistics.median(samples),
        "mean_ns": mean_ns,
        "p95_ns": _percentile_95(samples),
        "max_ns": max(samples),
        "throughput_bytes_per_second": throughput,
        "budget_asserted": False,
    }


def differential_report(
    path: str | Path,
    *,
    vrf_cli: str | Path | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    repository_parent = str(REPOSITORY_ROOT.parent)
    if repository_parent not in sys.path:
        sys.path.insert(0, repository_parent)
    from SourceIO.tools.vrf_oracle import compare_with_vrf

    return compare_with_vrf(
        Path(path),
        summarize_path(path),
        explicit_path=vrf_cli,
        expected_sha256=expected_sha256,
    )


def _write_json(value: Any, output: Path | None) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if output is None:
        print(text, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="ascii", newline="\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary", help="Normalize one or more resources.")
    summary_parser.add_argument("paths", nargs="+", type=Path)
    summary_parser.add_argument("--output", type=Path)
    summary_parser.add_argument("--strict", action="store_true")

    snapshot_parser = subparsers.add_parser("snapshot", help="Strictly check fixture snapshots.")
    snapshot_parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    snapshot_parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    snapshot_parser.add_argument(
        "--write",
        action="store_true",
        help="Explicitly replace the snapshot instead of checking it.",
    )

    differential_parser = subparsers.add_parser(
        "differential", help="Compare SourceIO normalization with a configured VRF CLI."
    )
    differential_parser.add_argument("path", type=Path)
    differential_parser.add_argument("--vrf-cli", type=Path)
    differential_parser.add_argument("--vrf-sha256")
    differential_parser.add_argument("--output", type=Path)

    benchmark_parser = subparsers.add_parser(
        "benchmark", help="Emit timing samples without enforcing a wall-clock budget."
    )
    benchmark_parser.add_argument("paths", nargs="+", type=Path)
    benchmark_parser.add_argument("--iterations", type=int, default=10)
    benchmark_parser.add_argument("--warmups", type=int, default=2)
    benchmark_parser.add_argument("--output", type=Path)

    self_test_parser = subparsers.add_parser("self-test", help="Validate generated fixtures.")
    self_test_parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    self_test_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "summary":
        summaries = {str(path): summarize_path(path) for path in args.paths}
        _write_json(summaries, args.output)
        if args.strict and any(error_codes(summary) for summary in summaries.values()):
            return 1
        return 0
    if args.command == "snapshot":
        actual = summarize_fixture_manifest(args.fixtures)
        if args.write:
            _write_json(actual, args.snapshot)
            print(f"wrote strict snapshot for {len(actual)} fixtures")
            return 0
        expected = json.loads(args.snapshot.read_text(encoding="ascii"))
        assert_snapshot(expected, actual)
        print(f"strict snapshot matched {len(actual)} fixtures")
        return 0
    if args.command == "differential":
        report = differential_report(
            args.path,
            vrf_cli=args.vrf_cli,
            expected_sha256=args.vrf_sha256,
        )
        _write_json(report, args.output)
        if report["status"] == "skipped":
            return 3
        return 0 if report["status"] == "matched" else 1
    if args.command == "benchmark":
        report = benchmark_paths(
            args.paths,
            iterations=args.iterations,
            warmups=args.warmups,
        )
        _write_json(report, args.output)
        return 0
    if args.command == "self-test":
        report = self_test(args.fixtures)
        _write_json(report, args.output)
        return 0
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
