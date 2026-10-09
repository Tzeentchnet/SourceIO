from __future__ import annotations

from dataclasses import dataclass, field
from typing import Collection

from ..utils import Buffer
from .exceptions import (
    BlockBoundsError,
    BlockTableError,
    FileSizeError,
    InvalidHeaderError,
    ResourceTruncatedError,
    UnsupportedHeaderVersionError,
    UnsupportedResourceVersionError,
)


COMPILED_HEADER_SIZE = 16
BLOCK_INFO_SIZE = 12
SUPPORTED_HEADER_VERSIONS = (12,)


def _require_bytes(buffer: Buffer, size: int, structure: str):
    remaining = buffer.size() - buffer.tell()
    if remaining < size:
        raise ResourceTruncatedError(
            f"Truncated {structure}: requires {size} bytes, only {remaining} remain",
            offset=buffer.tell(),
            details={"required": size, "remaining": remaining, "structure": structure},
        )


@dataclass(slots=True)
class BlockInfo:
    name: str
    size: int
    absolute_offset: int
    relative_offset: int = field(default=0, repr=False, compare=False)
    table_offset: int | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if len(self.name) != 4 or not self.name.isascii() or not self.name.isprintable():
            raise InvalidHeaderError(f"Invalid block FourCC {self.name!r}")
        if self.size < 0:
            raise BlockBoundsError(f"Block {self.name!r} has a negative size")
        if self.absolute_offset < 0:
            raise BlockBoundsError(f"Block {self.name!r} has a negative offset")

    def __repr__(self):
        return (
            f"<InfoBlock:{self.name} absolute offset:{self.absolute_offset} "
            f"size:{self.size}>"
        )

    @property
    def end_offset(self) -> int:
        return self.absolute_offset + self.size

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        entry_offset = buffer.tell()
        _require_bytes(buffer, BLOCK_INFO_SIZE, "block-table entry")
        raw_name = buffer.read(4)
        if len(raw_name) != 4:
            raise ResourceTruncatedError(
                "Truncated block FourCC",
                offset=entry_offset,
                details={"required": 4, "actual": len(raw_name)},
            )
        try:
            block_name = raw_name.decode("ascii")
        except UnicodeDecodeError as exc:
            raise InvalidHeaderError(
                f"Block FourCC at offset {entry_offset} is not ASCII",
                offset=entry_offset,
                details={"fourcc": raw_name.hex()},
            ) from exc
        if not block_name.isprintable():
            raise InvalidHeaderError(
                f"Block FourCC at offset {entry_offset} is not printable",
                offset=entry_offset,
                details={"fourcc": raw_name.hex()},
            )

        offset_field = buffer.tell()
        block_offset = buffer.read_uint32()
        block_size = buffer.read_uint32()
        absolute_offset = offset_field + block_offset
        return cls(
            block_name,
            block_size,
            absolute_offset,
            relative_offset=block_offset,
            table_offset=entry_offset,
        )


@dataclass(slots=True)
class CompiledHeader:
    file_size: int
    header_version: int
    resource_version: int
    blocks: list[BlockInfo]
    block_table_offset: int = field(default=COMPILED_HEADER_SIZE, repr=False, compare=False)
    file_offset: int = field(default=0, repr=False, compare=False)

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        file_offset = buffer.tell()
        actual_size = buffer.size() - file_offset
        if actual_size < COMPILED_HEADER_SIZE:
            raise ResourceTruncatedError(
                f"Compiled resource header requires {COMPILED_HEADER_SIZE} bytes, "
                f"only {actual_size} are available",
                offset=file_offset,
                details={"required": COMPILED_HEADER_SIZE, "actual": actual_size},
            )

        file_size = buffer.read_uint32()
        header_version = buffer.read_uint16()
        resource_version = buffer.read_uint16()

        if file_size != actual_size:
            raise FileSizeError(file_size, actual_size, offset=file_offset)
        if header_version not in SUPPORTED_HEADER_VERSIONS:
            raise UnsupportedHeaderVersionError(
                header_version,
                SUPPORTED_HEADER_VERSIONS,
                offset=file_offset + 4,
            )

        block_offset_field = buffer.tell()
        relative_block_offset = buffer.read_uint32()
        block_table_offset = block_offset_field + relative_block_offset
        block_count = buffer.read_uint32()
        file_end = file_offset + file_size

        if not file_offset + COMPILED_HEADER_SIZE <= block_table_offset <= file_end:
            raise BlockTableError(
                f"Block table offset {block_table_offset} is outside the resource",
                offset=block_offset_field,
                details={
                    "block_table_offset": block_table_offset,
                    "file_start": file_offset,
                    "file_end": file_end,
                },
            )

        available_table_bytes = file_end - block_table_offset
        maximum_block_count = available_table_bytes // BLOCK_INFO_SIZE
        if block_count > maximum_block_count:
            raise BlockTableError(
                f"Block table declares {block_count} entries, but only "
                f"{maximum_block_count} fit in the resource",
                offset=file_offset + 12,
                details={
                    "block_count": block_count,
                    "maximum_block_count": maximum_block_count,
                    "block_table_offset": block_table_offset,
                },
            )

        table_end = block_table_offset + block_count * BLOCK_INFO_SIZE
        info_blocks: list[BlockInfo] = []
        buffer.seek(block_table_offset)
        for _ in range(block_count):
            info_blocks.append(BlockInfo.from_buffer(buffer))

        cls._validate_blocks(
            info_blocks,
            file_offset=file_offset,
            file_end=file_end,
            table_offset=block_table_offset,
            table_end=table_end,
        )
        return cls(
            file_size,
            header_version,
            resource_version,
            info_blocks,
            block_table_offset,
            file_offset,
        )

    @staticmethod
    def _validate_blocks(
            blocks: list[BlockInfo],
            *,
            file_offset: int,
            file_end: int,
            table_offset: int,
            table_end: int,
    ):
        header_end = file_offset + COMPILED_HEADER_SIZE
        occupied_ranges: list[tuple[int, int, str]] = []

        for block in blocks:
            block_start = block.absolute_offset
            block_end = block.end_offset
            if block_start < header_end or block_start > file_end or block_end > file_end:
                raise BlockBoundsError(
                    f"Block {block.name!r} range [{block_start}, {block_end}) "
                    f"is outside resource range [{file_offset}, {file_end})",
                    offset=block.table_offset,
                    block_name=block.name,
                    details={
                        "block_start": block_start,
                        "block_end": block_end,
                        "file_start": file_offset,
                        "file_end": file_end,
                    },
                )

            if block.size and block_start < table_end and block_end > table_offset:
                raise BlockBoundsError(
                    f"Block {block.name!r} overlaps the block table",
                    offset=block.table_offset,
                    block_name=block.name,
                    details={
                        "block_start": block_start,
                        "block_end": block_end,
                        "table_start": table_offset,
                        "table_end": table_end,
                    },
                )

            if block.size:
                occupied_ranges.append((block_start, block_end, block.name))

        occupied_ranges.sort()
        for previous, current in zip(occupied_ranges, occupied_ranges[1:]):
            previous_start, previous_end, previous_name = previous
            current_start, current_end, current_name = current
            if current_start < previous_end:
                raise BlockBoundsError(
                    f"Blocks {previous_name!r} and {current_name!r} overlap",
                    block_name=current_name,
                    details={
                        "first": (previous_start, previous_end, previous_name),
                        "second": (current_start, current_end, current_name),
                    },
                )

    def validate_resource_version(
            self,
            *,
            minimum: int | None = None,
            maximum: int | None = None,
            supported: Collection[int] | None = None,
            kind: str | None = None,
    ):
        if minimum is not None and not 0 <= minimum <= 0xFFFF:
            raise ValueError("minimum resource version must fit in uint16")
        if maximum is not None and not 0 <= maximum <= 0xFFFF:
            raise ValueError("maximum resource version must fit in uint16")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum resource version must not exceed maximum")

        if supported is not None:
            supported_versions = tuple(sorted(set(supported)))
            if self.resource_version not in supported_versions:
                raise UnsupportedResourceVersionError(
                    self.resource_version,
                    kind=kind,
                    supported=supported_versions,
                    offset=self.file_offset + 6,
                )
            return

        below_minimum = minimum is not None and self.resource_version < minimum
        above_maximum = maximum is not None and self.resource_version > maximum
        if below_minimum or above_maximum:
            bounds = tuple(
                bound for bound in (minimum, maximum)
                if bound is not None
            )
            raise UnsupportedResourceVersionError(
                self.resource_version,
                kind=kind,
                supported=bounds or None,
                offset=self.file_offset + 6,
            )

    def to_buffer(self, buffer: Buffer):
        if self.header_version not in SUPPORTED_HEADER_VERSIONS:
            raise UnsupportedHeaderVersionError(
                self.header_version,
                SUPPORTED_HEADER_VERSIONS,
                offset=buffer.tell() + 4,
            )
        if not 0 <= self.resource_version <= 0xFFFF:
            raise UnsupportedResourceVersionError(self.resource_version)

        header_start = buffer.tell()
        buffer.new_label(
            "file_size",
            4,
            lambda target, label: (
                target.seek(label.offset),
                target.write_uint32(target.size() - header_start),
            ),
        )
        buffer.write_uint16(self.header_version)
        buffer.write_uint16(self.resource_version)
        block_offset_field = buffer.tell()
        buffer.write_uint32(header_start + COMPILED_HEADER_SIZE - block_offset_field)
