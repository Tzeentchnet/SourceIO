from __future__ import annotations

import struct

import pytest

from SourceIO.library.source2.blocks.binary_blob import UnknownBlock
from SourceIO.library.source2.compiled_file_header import CompiledHeader
from SourceIO.library.source2.compiled_resource import CompiledResource, DATA_BLOCK
from SourceIO.library.source2.exceptions import (
    BlockBoundsError,
    BlockIndexError,
    BlockTableError,
    FileSizeError,
    InvalidHeaderError,
    ResourceTruncatedError,
    UnsupportedHeaderVersionError,
    UnsupportedResourceVersionError,
)
from SourceIO.library.utils import MemoryBuffer, TinyPath

from ._builders import build_resource


@pytest.mark.parametrize("size", (0, 1, 4, 15))
def test_truncated_header_raises_typed_error(size: int):
    with pytest.raises(ResourceTruncatedError):
        CompiledHeader.from_buffer(MemoryBuffer(b"\x00" * size))


def test_declared_file_size_must_match_available_bytes():
    data = bytearray(build_resource())
    struct.pack_into("<I", data, 0, len(data) + 1)

    with pytest.raises(FileSizeError) as error:
        CompiledHeader.from_buffer(MemoryBuffer(data))

    assert error.value.declared_size == len(data) + 1
    assert error.value.actual_size == len(data)


def test_unknown_header_version_is_not_accepted_as_latest():
    with pytest.raises(UnsupportedHeaderVersionError) as error:
        CompiledHeader.from_buffer(MemoryBuffer(build_resource(header_version=13)))

    assert error.value.version == 13
    assert error.value.supported == (12,)


def test_block_table_offset_is_validated():
    data = bytearray(build_resource())
    struct.pack_into("<I", data, 8, 0)

    with pytest.raises(BlockTableError):
        CompiledHeader.from_buffer(MemoryBuffer(data))


def test_block_table_count_is_validated_before_iteration():
    data = bytearray(build_resource())
    struct.pack_into("<I", data, 12, 1)

    with pytest.raises(BlockTableError):
        CompiledHeader.from_buffer(MemoryBuffer(data))


def test_block_fourcc_must_be_printable_ascii():
    data = bytearray(build_resource((("DATA", b"x"),)))
    data[16:20] = b"\x00BAD"

    with pytest.raises(InvalidHeaderError):
        CompiledHeader.from_buffer(MemoryBuffer(data))


def test_block_end_must_fit_in_declared_file():
    data = bytearray(build_resource((("DATA", b"1234"),)))
    struct.pack_into("<I", data, 24, 5)

    with pytest.raises(BlockBoundsError):
        CompiledHeader.from_buffer(MemoryBuffer(data))


def test_block_must_not_overlap_block_table():
    data = bytearray(build_resource((("DATA", b"1234"),)))
    struct.pack_into("<I", data, 20, 4)

    with pytest.raises(BlockBoundsError):
        CompiledHeader.from_buffer(MemoryBuffer(data))


def test_blocks_must_not_overlap_each_other():
    data = bytearray(build_resource((("DATA", b"1234"), ("CTRL", b"5678"))))
    struct.pack_into("<I", data, 32, 8)

    with pytest.raises(BlockBoundsError):
        CompiledHeader.from_buffer(MemoryBuffer(data))


def test_resource_version_bounds_are_explicit():
    header = CompiledHeader.from_buffer(
        MemoryBuffer(build_resource(resource_version=7))
    )

    header.validate_resource_version(minimum=5, maximum=7, kind="test")
    with pytest.raises(UnsupportedResourceVersionError):
        header.validate_resource_version(maximum=6, kind="test")
    with pytest.raises(UnsupportedResourceVersionError):
        header.validate_resource_version(supported=(1, 2, 3), kind="test")


def test_compiled_resource_compatibility_and_unknown_block_inspection():
    payload = b"\x10\x20\x30\x40"
    resource = CompiledResource.from_buffer(
        MemoryBuffer(build_resource((("UNKN", payload),))),
        TinyPath("test.vdata_c"),
    )

    block = resource.get_block(block_name="UNKN")

    assert resource.name == "test"
    assert resource.header.header_version == 12
    assert isinstance(block, UnknownBlock)
    assert bytes(block) == payload
    assert block.custom_name == "UNKN"
    assert resource.block_state(0) == "unknown"
    with pytest.raises(BlockIndexError):
        resource.get_block(block_id=2)
    assert resource.get_block(block_id=DATA_BLOCK) is None
