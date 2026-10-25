from __future__ import annotations

import struct

import pytest

from SourceIO.library.source2.blocks.kv3_block import KVBlock
from SourceIO.library.source2.blocks.manifest import ManifestBlock
from SourceIO.library.source2.blocks.resource_introspection_manifest.manifest import (
    ResourceIntrospectionManifest,
)
from SourceIO.library.source2.compiled_resource import CompiledResource
from SourceIO.library.source2.exceptions import (
    KV3UnsupportedVersion,
    KV3ValidationError,
    UnsupportedNTROVersionError,
    UnsupportedResourceVersionError,
)
from SourceIO.library.source2.keyvalues3.binary_keyvalues import (
    KV3Buffers,
    KV3Context,
    _read_value_legacy,
    read_valve_keyvalue3,
    write_valve_keyvalue3,
)
from SourceIO.library.source2.keyvalues3.enums import (
    KV3CompressionMethod,
    KV3Format,
    KV3Signature,
    KV3Type,
    Specifier,
)
from SourceIO.library.source2.keyvalues3.types import Object, String, UInt32
from SourceIO.library.source2.utils.ntro_reader import NTROBuffer
from SourceIO.library.utils import MemoryBuffer, WritableMemoryBuffer

from ._builders import build_resource


@pytest.mark.parametrize("signature", (
    KV3Signature.KV3_V0,
    KV3Signature.KV3_V1,
    KV3Signature.KV3_V2,
    KV3Signature.KV3_V3,
    KV3Signature.KV3_V4,
    KV3Signature.KV3_V5,
))
def test_kv3_versions_zero_through_five_round_trip(signature: KV3Signature):
    output = WritableMemoryBuffer()
    write_valve_keyvalue3(
        output,
        Object({"value": String("test")}),
        KV3Format.generic,
        signature,
        KV3CompressionMethod.UNCOMPRESSED,
    )

    parsed = read_valve_keyvalue3(MemoryBuffer(output.data))

    assert parsed["value"] == "test"


@pytest.mark.parametrize(
    ("value_type", "expected"),
    (
        (KV3Type.UINT32_ZERO, 0),
        (KV3Type.UINT32_ONE, 1),
    ),
)
def test_kv3_uint32_constants_use_no_payload(
        value_type: KV3Type,
        expected: int,
):
    empty = MemoryBuffer(b"")
    buffers = KV3Buffers(empty, empty, empty, empty)
    context = KV3Context(
        strings=[],
        buffer0=buffers,
        buffer1=buffers,
        types_buffer=[(value_type, Specifier.UNSPECIFIED)],
        object_member_count_buffer=empty,
        binary_blob_sizes=None,
        binary_blob_buffer=None,
        read_type=lambda current: current.types_buffer.pop(),
        read_value=_read_value_legacy,
        active_buffer=buffers,
    )

    value = context.read_value(context)

    assert type(value) is UInt32
    assert value == expected
    assert all(buffer.tell() == 0 for buffer in (
        buffers.byte_buffer,
        buffers.short_buffer,
        buffers.int_buffer,
        buffers.double_buffer,
    ))


def test_future_kv3_version_raises_typed_error():
    with pytest.raises(KV3UnsupportedVersion):
        read_valve_keyvalue3(MemoryBuffer(b"\x063VK"))
    with pytest.raises(KV3UnsupportedVersion):
        KVBlock.from_buffer(NTROBuffer(b"\x063VK", None, None))
    resource = CompiledResource.from_buffer(
        build_resource((("DATA", b"\x063VK"),)),
        "future.vdata_c",
    )
    with pytest.raises(KV3UnsupportedVersion):
        resource.get_block(block_name="DATA")


@pytest.mark.parametrize("data", (b"", b"V", b"BAD!", b"\x013V"))
def test_malformed_kv3_signatures_raise_validation_error(data: bytes):
    with pytest.raises(KV3ValidationError):
        read_valve_keyvalue3(MemoryBuffer(data))


def test_kv3_block_writer_preserves_selected_version():
    output = WritableMemoryBuffer()
    block = KVBlock(
        {"value": String("test")},
        version=KV3Signature.KV3_V5,
        format_=KV3Format.generic,
    )

    block.to_buffer(output)

    assert bytes(output.data[:4]) == KV3Signature.KV3_V5.value


def test_malformed_kv3_trailer_raises_validation_error():
    output = WritableMemoryBuffer()
    write_valve_keyvalue3(
        output,
        Object({"value": String("test")}),
        KV3Format.generic,
        KV3Signature.KV3_V1,
        KV3CompressionMethod.UNCOMPRESSED,
    )
    data = bytearray(output.data)
    data[-4:] = b"\x00\x00\x00\x00"

    with pytest.raises(KV3ValidationError):
        read_valve_keyvalue3(MemoryBuffer(data))


def test_unknown_ntro_version_raises_typed_error():
    data = struct.pack("<IIIII", 5, 0, 0, 0, 0)

    with pytest.raises(UnsupportedNTROVersionError):
        ResourceIntrospectionManifest.from_buffer(
            NTROBuffer(data, None, None)
        )


def test_unknown_manifest_version_raises_typed_error():
    data = struct.pack("<II", 9, 0)

    with pytest.raises(UnsupportedResourceVersionError):
        ManifestBlock.from_buffer(NTROBuffer(data, None, None))
