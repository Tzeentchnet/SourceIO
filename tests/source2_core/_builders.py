from __future__ import annotations

import struct
from collections.abc import Sequence


def build_resource(
        blocks: Sequence[tuple[str, bytes]] = (),
        *,
        header_version: int = 12,
        resource_version: int = 1,
) -> bytes:
    table_end = 16 + len(blocks) * 12
    total_size = table_end + sum(len(data) for _, data in blocks)
    output = bytearray(total_size)
    struct.pack_into(
        "<IHHII",
        output,
        0,
        total_size,
        header_version,
        resource_version,
        8,
        len(blocks),
    )

    data_offset = table_end
    for index, (name, data) in enumerate(blocks):
        if len(name) != 4 or not name.isascii():
            raise ValueError(f"Invalid test FourCC: {name!r}")
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


def build_ntro_with_struct(struct_name: str) -> bytes:
    name = struct_name.encode("ascii") + b"\x00"
    structure_offset = 20
    structure_size = 40
    name_offset = structure_offset + structure_size
    total_size = name_offset + len(name)
    output = bytearray(total_size)

    struct.pack_into("<I", output, 0, 4)
    struct.pack_into("<I", output, 4, structure_offset - 4)
    struct.pack_into("<I", output, 8, 1)
    struct.pack_into("<I", output, 12, total_size - 12)
    struct.pack_into("<I", output, 16, 0)

    struct.pack_into("<II", output, structure_offset, 4, 1)
    struct.pack_into(
        "<I",
        output,
        structure_offset + 8,
        name_offset - (structure_offset + 8),
    )
    struct.pack_into("<IIhhI", output, structure_offset + 12, 0, 0, 0, 1, 0)
    struct.pack_into(
        "<I",
        output,
        structure_offset + 28,
        name_offset - (structure_offset + 28),
    )
    struct.pack_into("<II", output, structure_offset + 32, 0, 0)
    output[name_offset:] = name
    return bytes(output)
