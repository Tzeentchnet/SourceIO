"""Build deterministic, locally-authored Source 2 conformance fixtures.

The generated files are intentionally tiny.  They contain only structures
needed by the conformance harness and are not derived from game files or from
ValveResourceFormat's test corpus.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "tests" / "fixtures" / "source2_generated"
BUILDER_VERSION = 1
MAX_BLOCK_COUNT = 4_096
MAX_BLOCK_SIZE = 64 * 1024 * 1024
MAX_DEPENDENCY_COUNT = 16_384
MAX_TEXTURE_DIMENSION = 16_384
MAX_VALUE_DEPTH = 64


@dataclass(frozen=True)
class Block:
    name: str
    payload: bytes


@dataclass(frozen=True)
class Fixture:
    filename: str
    data: bytes
    description: str
    expected: Mapping[str, Any]


@dataclass(frozen=True)
class MalformedCase:
    name: str
    data: bytes
    expected_code: str
    description: str


def _align(value: int, alignment: int = 16) -> int:
    return value + (-value % alignment)


def build_compiled_resource(
    blocks: Sequence[Block],
    *,
    resource_version: int = 1,
    trailing_data: bytes = b"",
) -> bytes:
    """Build the common Source 2 compiled-resource header and block table."""
    if not blocks:
        raise ValueError("at least one block is required")
    if len(blocks) > MAX_BLOCK_COUNT:
        raise ValueError(f"too many blocks: {len(blocks)}")

    table_offset = 16
    cursor = _align(table_offset + 12 * len(blocks))
    block_layout: list[tuple[Block, int]] = []
    for index, block in enumerate(blocks):
        encoded_name = block.name.encode("ascii")
        if len(encoded_name) != 4:
            raise ValueError(f"block name must be four ASCII bytes: {block.name!r}")
        if len(block.payload) > MAX_BLOCK_SIZE:
            raise ValueError(f"block {block.name} is too large")
        block_layout.append((block, cursor))
        cursor += len(block.payload)
        if index + 1 < len(blocks):
            cursor = _align(cursor)

    output = bytearray(cursor + len(trailing_data))
    struct.pack_into("<IHHII", output, 0, len(output), 12, resource_version, 8, len(blocks))
    for index, (block, absolute_offset) in enumerate(block_layout):
        entry_offset = table_offset + 12 * index
        output[entry_offset : entry_offset + 4] = block.name.encode("ascii")
        struct.pack_into(
            "<II",
            output,
            entry_offset + 4,
            absolute_offset - (entry_offset + 4),
            len(block.payload),
        )
        output[absolute_offset : absolute_offset + len(block.payload)] = block.payload

    if trailing_data:
        output[-len(trailing_data) :] = trailing_data
    return bytes(output)


def build_rerl(dependencies: Sequence[str]) -> bytes:
    """Build a Resource External Reference List (RERL) block."""
    encoded = [dependency.replace("\\", "/").encode("utf-8") + b"\0" for dependency in dependencies]
    entries_start = 8
    strings_start = entries_start + 16 * len(encoded)
    output = bytearray(strings_start + sum(map(len, encoded)))
    struct.pack_into("<II", output, 0, entries_start, len(encoded))
    string_cursor = strings_start
    for index, (dependency, dependency_bytes) in enumerate(zip(dependencies, encoded)):
        entry_offset = entries_start + 16 * index
        name_field_offset = entry_offset + 8
        resource_hash = zlib.crc32(dependency.lower().encode("utf-8")) & 0xFFFF_FFFF
        struct.pack_into(
            "<IIII",
            output,
            entry_offset,
            resource_hash,
            index,
            string_cursor - name_field_offset,
            0,
        )
        output[string_cursor : string_cursor + len(dependency_bytes)] = dependency_bytes
        string_cursor += len(dependency_bytes)
    return bytes(output)


def _sourceio_kv3_imports():
    repository_parent = str(REPOSITORY_ROOT.parent)
    if repository_parent not in sys.path:
        sys.path.insert(0, repository_parent)

    with contextlib.redirect_stdout(sys.stderr):
        from SourceIO.library.source2.keyvalues3.binary_keyvalues import write_valve_keyvalue3
        from SourceIO.library.source2.keyvalues3.enums import (
            KV3CompressionMethod,
            KV3Format,
            KV3Signature,
        )
        from SourceIO.library.source2.keyvalues3.types import Object
        from SourceIO.library.utils import WritableMemoryBuffer

    return (
        write_valve_keyvalue3,
        KV3CompressionMethod,
        KV3Format,
        KV3Signature,
        Object,
        WritableMemoryBuffer,
    )


def build_kv3(value: Mapping[str, Any], *, compression: str = "uncompressed") -> bytes:
    """Serialize Python values with SourceIO's writer, imported only on use."""
    (
        write_valve_keyvalue3,
        compression_type,
        format_type,
        signature_type,
        object_type,
        buffer_type,
    ) = _sourceio_kv3_imports()
    compression_value = {
        "uncompressed": compression_type.UNCOMPRESSED,
        "lz4": compression_type.LZ4,
        "zstd": compression_type.ZSTD,
    }.get(compression)
    if compression_value is None:
        raise ValueError(f"unsupported KV3 compression: {compression}")

    buffer = buffer_type()
    write_valve_keyvalue3(
        buffer,
        object_type.from_python(dict(value)),
        format_type.generic,
        signature_type.KV3_V3,
        compression_value,
    )
    return bytes(buffer.data)


def build_texture_data(
    *,
    width: int,
    height: int,
    depth: int = 1,
    pixel_format: int = 4,
    mip_count: int = 1,
) -> bytes:
    """Build a version-one TextureData header with no extra-data records."""
    if not all(0 < dimension <= 0xFFFF for dimension in (width, height, depth)):
        raise ValueError("texture dimensions must fit unsigned 16-bit values")
    return struct.pack(
        "<HH4f3HBBIII",
        1,
        0,
        0.25,
        0.5,
        0.75,
        1.0,
        width,
        height,
        depth,
        pixel_format,
        mip_count,
        0,
        8,
        0,
    )


def build_sound_data_v4(
    *,
    sample_rate: int,
    channels: int,
    sample_count: int,
) -> tuple[bytes, bytes]:
    """Build a tiny VSND v4 PCM16 DATA block and streaming payload."""
    if not 0 < sample_rate <= 0xFFFF:
        raise ValueError("sample_rate must fit unsigned 16-bit values")
    if channels not in (1, 2):
        raise ValueError("channels must be one or two")
    if sample_count < 0:
        raise ValueError("sample_count cannot be negative")
    streaming_data = bytes(sample_count * channels * 2)
    duration = sample_count / sample_rate
    data_block = struct.pack(
        "<HBBiIfIIiIiiii",
        sample_rate,
        0,
        channels,
        -1,
        sample_count,
        duration,
        0,
        0,
        0,
        len(streaming_data),
        0,
        0,
        0,
        0,
    )
    return data_block, streaming_data


def build_vbib() -> bytes:
    """Build one uncompressed triangle vertex buffer and index buffer."""
    vertex_data = struct.pack(
        "<9f",
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
    )
    index_data = struct.pack("<3H", 0, 1, 2)

    vertex_descriptor = 16
    vertex_payload = vertex_descriptor + 24
    index_descriptor = _align(vertex_payload + len(vertex_data))
    index_payload = index_descriptor + 24
    output = bytearray(index_payload + len(index_data))

    struct.pack_into(
        "<IIII",
        output,
        0,
        vertex_descriptor,
        1,
        index_descriptor - 8,
        1,
    )
    struct.pack_into(
        "<IIIIII",
        output,
        vertex_descriptor,
        3,
        12,
        vertex_payload - (vertex_descriptor + 8),
        0,
        vertex_payload - (vertex_descriptor + 16),
        len(vertex_data),
    )
    output[vertex_payload : vertex_payload + len(vertex_data)] = vertex_data
    struct.pack_into(
        "<IIIIII",
        output,
        index_descriptor,
        3,
        2,
        0,
        0,
        index_payload - (index_descriptor + 16),
        len(index_data),
    )
    output[index_payload : index_payload + len(index_data)] = index_data
    return bytes(output)


def _resource(
    filename: str,
    description: str,
    data: Mapping[str, Any],
    *,
    dependencies: Sequence[str] = (),
    extra_blocks: Sequence[Block] = (),
    compression: str = "uncompressed",
    expected: Mapping[str, Any],
) -> Fixture:
    blocks = [
        Block("RERL", build_rerl(dependencies)),
        *extra_blocks,
        Block("DATA", build_kv3(data, compression=compression)),
    ]
    return Fixture(
        filename,
        build_compiled_resource(blocks),
        description,
        expected,
    )


def build_valid_fixtures() -> tuple[Fixture, ...]:
    """Return the complete deterministic valid-fixture corpus."""
    material_dependency = "materials/conformance/triangle.vmat"
    mesh_dependency = "models/conformance/triangle.vmesh"
    animation_dependency = "animations/conformance/idle.vanim"

    texture_pixels = bytes(
        (
            255,
            0,
            0,
            255,
            0,
            255,
            0,
            255,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
            255,
        )
    )
    texture = Fixture(
        "minimal_texture.vtex_c",
        build_compiled_resource(
            (
                Block("RERL", build_rerl(())),
                Block("DATA", build_texture_data(width=2, height=2)),
            ),
            trailing_data=texture_pixels,
        ),
        "Two-by-two RGBA texture with one mip.",
        {
            "resource_type": "texture",
            "dimensions": {"width": 2, "height": 2, "depth": 1, "mip_count": 1},
            "channels": {"count": 4},
        },
    )
    material = _resource(
        "minimal_material.vmat_c",
        "Material with one texture dependency.",
        {
            "m_shaderName": "vr_standard.vfx",
            "m_textureParams": [
                {
                    "m_name": "g_tColor",
                    "m_pValue": "textures/conformance/checker.vtex",
                }
            ],
            "m_intParams": [],
            "m_floatParams": [],
            "m_vectorParams": [],
            "m_dynamicParams": [],
            "m_dynamicTextureParams": [],
        },
        dependencies=("textures/conformance/checker.vtex",),
        expected={"resource_type": "material", "dependency_count": 1},
    )
    mesh = _resource(
        "minimal_mesh.vmesh_c",
        "Single triangle mesh with an uncompressed VBIB block.",
        {
            "m_name": "conformance_triangle",
            "m_meshCount": 1,
            "m_vertexCount": 3,
            "m_indexCount": 3,
            "m_primitiveCount": 1,
        },
        dependencies=(material_dependency,),
        extra_blocks=(Block("VBIB", build_vbib()),),
        expected={
            "resource_type": "mesh",
            "dependency_count": 1,
            "mesh": {
                "mesh_count": 1,
                "vertex_count": 3,
                "index_count": 3,
                "primitive_count": 1,
            },
        },
    )
    model = _resource(
        "minimal_model.vmdl_c",
        "Model with two bones and references to one mesh and animation.",
        {
            "m_name": "conformance_model",
            "m_modelSkeleton": {
                "m_boneName": ["root", "child"],
                "m_nParent": [-1, 0],
                "m_nFlag": [0, 0],
            },
            "m_refMeshes": [mesh_dependency],
            "m_refAnimGroups": [animation_dependency],
        },
        dependencies=(mesh_dependency, animation_dependency),
        expected={
            "resource_type": "model",
            "dependency_count": 2,
            "skeleton": {"bone_count": 2},
        },
    )
    animation = _resource(
        "minimal_animation.vanim_c",
        "Animation metadata for a one-second, thirty-frame clip.",
        {
            "m_name": "idle",
            "m_nFrames": 30,
            "m_fps": 30.0,
            "m_flDuration": 1.0,
            "m_nClipCount": 1,
        },
        dependencies=("models/conformance/rig.vmdl",),
        expected={
            "resource_type": "animation",
            "animation": {
                "clip_count": 1,
                "frame_count": 30,
                "fps": 30.0,
                "duration_seconds": 1.0,
            },
        },
    )
    sound_data, sound_stream = build_sound_data_v4(
        sample_rate=48_000,
        channels=2,
        sample_count=48,
    )
    sound_metadata = build_compiled_resource(
        (
            Block("RERL", build_rerl(())),
            Block("DATA", sound_data),
        ),
        resource_version=4,
    )
    sound = Fixture(
        "minimal_sound.vsnd_c",
        sound_metadata + sound_stream,
        "VSND v4 PCM16 metadata with a 48-sample silent stream.",
        {
            "resource_type": "sound",
            "sound": {
                "sample_rate": 48_000,
                "channel_count": 2,
                "sample_count": 48,
                "duration_seconds": 0.001,
                "bits_per_sample": 16,
                "encoding": "pcm16",
            },
        },
    )
    compressed = _resource(
        "compressed_metadata.vdata_c",
        "LZ4-compressed KV3 metadata used for boundary tests.",
        {
            "m_name": "compressed_metadata",
            "m_values": list(range(32)),
            "m_nested": {"enabled": True, "label": "sourceio-conformance"},
        },
        compression="lz4",
        expected={"resource_type": "keyvalues3"},
    )
    return texture, material, mesh, model, animation, sound, compressed


def _block_entries(data: bytes) -> list[tuple[int, str, int, int]]:
    if len(data) < 16:
        raise ValueError("resource is too short")
    table_offset = 8 + struct.unpack_from("<I", data, 8)[0]
    count = struct.unpack_from("<I", data, 12)[0]
    entries = []
    for index in range(count):
        entry_offset = table_offset + 12 * index
        name = data[entry_offset : entry_offset + 4].decode("ascii")
        relative_offset, size = struct.unpack_from("<II", data, entry_offset + 4)
        entries.append((entry_offset, name, entry_offset + 4 + relative_offset, size))
    return entries


def _patched(data: bytes, offset: int, fmt: str, value: int) -> bytes:
    output = bytearray(data)
    struct.pack_into(fmt, output, offset, value)
    return bytes(output)


def _truncate_block(data: bytes, block_name: str, payload_size: int) -> bytes:
    entries = _block_entries(data)
    entry_offset, _, block_offset, original_size = next(
        entry for entry in entries if entry[1] == block_name
    )
    if payload_size < 0 or payload_size >= original_size:
        raise ValueError("payload_size must truncate the selected block")
    output = bytearray(data[: block_offset + payload_size])
    struct.pack_into("<I", output, entry_offset + 8, payload_size)
    struct.pack_into("<I", output, 0, len(output))
    return bytes(output)


def _deep_mapping(depth: int) -> Mapping[str, Any]:
    value: Mapping[str, Any] = {"leaf": 1}
    for index in range(depth):
        value = {f"level_{index:02d}": value}
    return value


def build_malformed_cases(
    valid_fixtures: Iterable[Fixture] | None = None,
) -> tuple[MalformedCase, ...]:
    """Derive a systematic malformed corpus from valid local fixtures."""
    fixtures = {fixture.filename: fixture for fixture in valid_fixtures or build_valid_fixtures()}
    texture = fixtures["minimal_texture.vtex_c"].data
    compressed = fixtures["compressed_metadata.vdata_c"].data
    texture_entries = _block_entries(texture)
    rerl_entry = next(entry for entry in texture_entries if entry[1] == "RERL")
    data_entry = next(entry for entry in texture_entries if entry[1] == "DATA")
    table_offset = 8 + struct.unpack_from("<I", texture, 8)[0]

    cases = [
        MalformedCase(
            f"header_truncated_{length:02d}",
            texture[:length],
            "header.truncated",
            f"Compiled header truncated at byte {length}.",
        )
        for length in (0, 3, 7, 11, 15)
    ]
    cases.extend(
        (
            MalformedCase(
                "header_version_invalid",
                _patched(texture, 4, "<H", 11),
                "header.version",
                "Unsupported compiled-header version.",
            ),
            MalformedCase(
                "declared_file_size_mismatch",
                _patched(texture, 0, "<I", len(texture) + 1),
                "header.file_size",
                "Declared file size differs from the available bytes.",
            ),
            MalformedCase(
                "block_table_before_header",
                _patched(texture, 8, "<I", 0),
                "block_table.offset",
                "Block table points into the fixed header.",
            ),
            MalformedCase(
                "block_table_beyond_eof",
                _patched(texture, 8, "<I", 0xFFFF_FFF0),
                "block_table.range",
                "Block table offset is beyond the file.",
            ),
            MalformedCase(
                "block_count_excessive",
                _patched(texture, 12, "<I", MAX_BLOCK_COUNT + 1),
                "limit.block_count",
                "Block count exceeds the conformance safety limit.",
            ),
            MalformedCase(
                "block_table_truncated",
                _patched(texture[: table_offset + 12], 0, "<I", table_offset + 12),
                "block_table.range",
                "The second declared block-table entry is absent.",
            ),
            MalformedCase(
                "block_offset_into_table",
                _patched(
                    texture,
                    rerl_entry[0] + 4,
                    "<I",
                    0,
                ),
                "block.overlap_header",
                "RERL points into the block table.",
            ),
            MalformedCase(
                "block_offset_beyond_eof",
                _patched(texture, data_entry[0] + 4, "<I", 0x7FFF_FFFF),
                "block.range",
                "DATA starts beyond the file.",
            ),
            MalformedCase(
                "block_size_excessive",
                _patched(texture, data_entry[0] + 8, "<I", MAX_BLOCK_SIZE + 1),
                "limit.block_size",
                "DATA size exceeds the conformance safety limit.",
            ),
            MalformedCase(
                "dependency_count_excessive",
                _patched(
                    texture,
                    rerl_entry[2] + 4,
                    "<I",
                    MAX_DEPENDENCY_COUNT + 1,
                ),
                "limit.dependency_count",
                "RERL dependency count exceeds the safety limit.",
            ),
            MalformedCase(
                "texture_depth_excessive",
                _patched(
                    texture,
                    data_entry[2] + 24,
                    "<H",
                    MAX_TEXTURE_DIMENSION + 1,
                ),
                "limit.dimension",
                "Texture depth exceeds the safety limit.",
            ),
        )
    )

    compressed_size = next(entry[3] for entry in _block_entries(compressed) if entry[1] == "DATA")
    boundaries = sorted({0, 3, 4, 19, 31, compressed_size - 1})
    for payload_size in boundaries:
        if 0 <= payload_size < compressed_size:
            cases.append(
                MalformedCase(
                    f"compressed_payload_truncated_{payload_size:04d}",
                    _truncate_block(compressed, "DATA", payload_size),
                    "payload.kv3_invalid",
                    f"Compressed KV3 DATA ends at payload byte {payload_size}.",
                )
            )

    deep_data = build_compiled_resource(
        (
            Block("RERL", build_rerl(())),
            Block("DATA", build_kv3(_deep_mapping(MAX_VALUE_DEPTH + 2))),
        )
    )
    cases.append(
        MalformedCase(
            "structured_value_depth_excessive",
            deep_data,
            "limit.value_depth",
            "Decoded KV3 nesting exceeds the normalization depth limit.",
        )
    )
    return tuple(cases)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _malformed_path(case: MalformedCase) -> str:
    extension = (
        ".vdata_c"
        if case.name.startswith("compressed_")
        or case.name.startswith("structured_value_")
        else ".vtex_c"
    )
    return f"malformed/{case.name}{extension}"


def generated_files() -> dict[str, bytes]:
    valid = build_valid_fixtures()
    malformed = build_malformed_cases(valid)
    files = {fixture.filename: fixture.data for fixture in valid}
    files.update({_malformed_path(case): case.data for case in malformed})

    manifest = {
        "schema_version": 1,
        "builder_version": BUILDER_VERSION,
        "provenance": {
            "kind": "generated",
            "generator": "tools/build_source2_fixtures.py",
            "upstream_fixture_content": False,
            "network_required": False,
        },
        "fixtures": [
            {
                "path": fixture.filename,
                "description": fixture.description,
                "byte_size": len(fixture.data),
                "sha256": _sha256(fixture.data),
                "expected": fixture.expected,
            }
            for fixture in valid
        ],
        "malformed": [
            {
                "path": _malformed_path(case),
                "description": case.description,
                "byte_size": len(case.data),
                "sha256": _sha256(case.data),
                "expected_code": case.expected_code,
            }
            for case in malformed
        ],
    }
    files["manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("ascii")
    return files


def write_generated_fixtures(output: Path = DEFAULT_OUTPUT, *, check: bool = False) -> list[str]:
    """Write generated files or return drift paths in check mode."""
    expected_files = generated_files()
    drift: list[str] = []
    for relative_path, expected_data in expected_files.items():
        target = output / Path(relative_path)
        actual_data = target.read_bytes() if target.is_file() else None
        if actual_data != expected_data:
            drift.append(relative_path)
            if not check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(expected_data)

    if output.is_dir():
        expected_paths = {Path(path) for path in expected_files}
        for target in output.rglob("*"):
            if target.is_file() and target.relative_to(output) not in expected_paths:
                drift.append(target.relative_to(output).as_posix())
    return sorted(set(drift))


def self_test() -> None:
    first = generated_files()
    second = generated_files()
    if first != second:
        raise AssertionError("fixture generation is not deterministic")
    manifest = json.loads(first["manifest.json"])
    for record in (*manifest["fixtures"], *manifest["malformed"]):
        data = first[record["path"]]
        if len(data) != record["byte_size"] or _sha256(data) != record["sha256"]:
            raise AssertionError(f"manifest mismatch for {record['path']}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Generated fixture directory.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without writing files.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify deterministic generation and manifest hashes.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_test:
        self_test()
    drift = write_generated_fixtures(args.output, check=args.check)
    if args.check and drift:
        for relative_path in drift:
            print(f"fixture drift: {relative_path}", file=sys.stderr)
        return 1
    if not args.check:
        print(f"wrote {len(generated_files())} deterministic files to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
