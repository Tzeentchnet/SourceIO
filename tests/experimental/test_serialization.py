import os
import struct

import pytest

from SourceIO.library.source2.blocks.base import BaseBlock
from SourceIO.library.source2.compiled_file_header import CompiledHeader
from SourceIO.library.source2.compiled_resource import CompiledResource
from SourceIO.library.source2.interfaces import (
    Maturity,
    ResourceCapabilities,
)
from SourceIO.library.source2.keyvalues3.binary_keyvalues import write_valve_keyvalue3
from SourceIO.library.source2.keyvalues3.enums import (
    KV3CompressionMethod,
    KV3Format,
    KV3Signature,
)
from SourceIO.library.source2.keyvalues3.types import Int64, Object, String
from SourceIO.library.source2.provenance import ResourceProvenance
from SourceIO.library.source2.serialization import (
    BlockSerializationRule,
    ResourceSerializationRule,
    SemanticValidation,
    SerializationBlockedError,
    SerializationRegistry,
    create_default_serialization_registry,
    preflight_resource_serialization,
    serialize_resource_to_bytes,
    validate_compiled_resource_structure,
    write_resource_atomic,
)
from SourceIO.library.source2.utils.ntro_reader import NTROBuffer
from SourceIO.library.utils import Buffer, MemoryBuffer, TinyPath, WritableMemoryBuffer


def _compiled_resource_bytes(blocks: list[tuple[str, bytes]]) -> bytes:
    header_size = 16 + 12 * len(blocks)
    output = bytearray(header_size + sum(len(data) for _, data in blocks))
    struct.pack_into("<IHHII", output, 0, len(output), 12, 1, 8, len(blocks))
    data_offset = header_size
    for index, (name, data) in enumerate(blocks):
        entry_offset = 16 + index * 12
        struct.pack_into(
            "<4sII",
            output,
            entry_offset,
            name.encode("ascii"),
            data_offset - (entry_offset + 4),
            len(data),
        )
        output[data_offset:data_offset + len(data)] = data
        data_offset += len(data)
    return bytes(output)


def _resource(blocks: list[tuple[str, bytes]]) -> CompiledResource:
    return CompiledResource.from_buffer(
        MemoryBuffer(_compiled_resource_bytes(blocks)),
        TinyPath("test.bin_c"),
    )


def _provenance(resource: CompiledResource) -> ResourceProvenance:
    return ResourceProvenance(str(resource.path))


def _kv3_payload(data: Object) -> bytes:
    output = WritableMemoryBuffer()
    write_valve_keyvalue3(
        output,
        data,
        KV3Format.generic,
        KV3Signature.KV3_V1,
        KV3CompressionMethod.UNCOMPRESSED,
    )
    return bytes(output.data)


def test_preflight_enumerates_every_unsupported_block():
    resource = _resource([
        ("AAAA", b"one"),
        ("BBBB", b"two"),
        ("CCCC", b"three"),
    ])

    report = preflight_resource_serialization(resource, provenance=_provenance(resource))

    assert not report.supported
    assert len(report.blocks) == 3
    assert len(report.unsupported_blocks) == 3
    assert [block.block_name for block in report.unsupported_blocks] == ["AAAA", "BBBB", "CCCC"]


def test_missing_provenance_blocks_resource_after_all_blocks_are_preflighted():
    resource = _resource([("MVTX", b"supported")])

    report = preflight_resource_serialization(resource)

    assert not report.resource.supported
    assert "ResourceProvenance" in report.resource.reason
    assert len(report.blocks) == 1
    assert report.blocks[0].supported


def test_shared_write_capability_gate_blocks_resource():
    resource = _resource([("MVTX", b"supported")])
    read_only = ResourceCapabilities(read=Maturity.STABLE)

    report = preflight_resource_serialization(
        resource,
        provenance=_provenance(resource),
        resource_capabilities=read_only,
    )

    assert not report.resource.supported
    assert "write capability" in report.resource.reason
    assert report.blocks[0].supported


def test_mismatched_provenance_root_blocks_resource():
    resource = _resource([("MVTX", b"supported")])

    report = preflight_resource_serialization(
        resource,
        provenance=ResourceProvenance("another/resource.bin_c"),
    )

    assert not report.resource.supported
    assert "does not match" in report.resource.reason
    assert report.blocks[0].supported


def test_preflight_continues_after_registered_serializer_failure():
    class FailingBlock(BaseBlock):
        @classmethod
        def from_buffer(cls, buffer: NTROBuffer):
            return cls()

        def to_buffer(self, buffer: Buffer) -> None:
            raise RuntimeError("expected failure")

    resource = _resource([("AAAA", b"one"), ("BBBB", b"two")])
    resource._blocks[0] = FailingBlock()
    registry = SerializationRegistry()
    registry.register_resource(ResourceSerializationRule(
        CompiledResource,
        validate_compiled_resource_structure,
        ("test",),
    ))
    registry.register_block(BlockSerializationRule(
        FailingBlock,
        lambda context: context.block.to_buffer(None),
        lambda context, data: SemanticValidation.success(),
        ("test",),
    ))

    report = preflight_resource_serialization(
        resource,
        registry=registry,
        provenance=_provenance(resource),
    )

    assert len(report.blocks) == 2
    assert len(report.unsupported_blocks) == 2
    assert report.blocks[0].reason == "Block serializer failed during preflight"
    assert "No block serialization rule" in report.blocks[1].reason


def test_preflight_continues_after_core_block_parse_failure():
    resource = _resource([
        ("DATA", b"not-kv3"),
        ("AAAA", b"unsupported"),
    ])

    report = preflight_resource_serialization(
        resource,
        provenance=_provenance(resource),
    )

    assert len(report.blocks) == 2
    assert len(report.unsupported_blocks) == 2
    assert report.blocks[0].reason == "Block parsing failed"
    assert "No block serialization rule" in report.blocks[1].reason


def test_verified_binary_blocks_serialize_with_semantic_validation():
    original = _compiled_resource_bytes([
        ("MVTX", b"\x01\x02\x03"),
        ("MIDX", b"\x04\x05"),
    ])
    resource = CompiledResource.from_buffer(
        MemoryBuffer(original),
        TinyPath("test.bin_c"),
    )

    serialized = serialize_resource_to_bytes(resource, provenance=_provenance(resource))
    header = CompiledHeader.from_buffer(MemoryBuffer(serialized))

    assert header.file_size == len(serialized)
    assert [block.name for block in header.blocks] == ["MVTX", "MIDX"]
    assert [block.size for block in header.blocks] == [3, 2]


def test_verified_kv3_block_uses_semantic_tree_validation_without_mutating_source():
    resource = _resource([(
        "DATA",
        _kv3_payload(Object({
            "name": String("particle"),
            "count": Int64(3),
        })),
    )])
    source_block = resource.get_block(block_name="DATA")
    source_version = source_block._version

    serialized = serialize_resource_to_bytes(
        resource,
        provenance=_provenance(resource),
    )

    assert source_block._version is source_version
    reparsed = CompiledResource.from_buffer(serialized, "roundtrip.bin_c")
    reparsed_data = reparsed.get_block(block_name="DATA")
    assert reparsed_data["name"] == "particle"
    assert reparsed_data["count"] == 3


def test_atomic_preflight_failure_preserves_existing_file(tmp_path, monkeypatch):
    destination = tmp_path / "resource.bin_c"
    destination.write_bytes(b"existing")
    resource = _resource([("AAAA", b"unsupported")])
    temporary_opened = False

    def reject_temp_open(*args, **kwargs):
        nonlocal temporary_opened
        temporary_opened = True
        raise AssertionError("temporary output opened before preflight completed")

    monkeypatch.setattr(
        "SourceIO.library.source2.serialization.resource_serializer.tempfile.mkstemp",
        reject_temp_open,
    )

    with pytest.raises(SerializationBlockedError) as error:
        write_resource_atomic(resource, destination, provenance=_provenance(resource))

    assert len(error.value.report.unsupported_blocks) == 1
    assert not temporary_opened
    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_atomic_replace_failure_preserves_existing_file_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "resource.bin_c"
    destination.write_bytes(b"existing")
    resource = _resource([("MVTX", b"\x01\x02\x03")])

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_resource_atomic(resource, destination, provenance=_provenance(resource))

    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))


def test_atomic_success_replaces_destination_only_after_preflight(tmp_path):
    destination = tmp_path / "resource.bin_c"
    destination.write_bytes(b"existing")
    resource = _resource([("MVTX", b"\x01\x02\x03")])

    result = write_resource_atomic(resource, destination, provenance=_provenance(resource))

    assert result.report.supported
    assert result.bytes_written == len(destination.read_bytes())
    assert destination.read_bytes() != b"existing"
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))
