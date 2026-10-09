
from ..base import BaseBlock
from .types import Struct, Enum
from ...exceptions import (
    BlockBoundsError,
    ResourceTruncatedError,
    UnsupportedNTROVersionError,
)
from ...utils.ntro_reader import NTROBuffer, ResourceIntrospectionInfo
from ....utils import Buffer


class ResourceIntrospectionManifest(BaseBlock):
    def __init__(self, info: ResourceIntrospectionInfo):
        self.info = info

    @classmethod
    def from_buffer(cls, buffer: NTROBuffer):
        if buffer.remaining() < 20:
            raise ResourceTruncatedError(
                "NTRO manifest header requires 20 bytes",
                offset=buffer.tell(),
                block_name="NTRO",
                details={"required": 20, "remaining": buffer.remaining()},
            )
        version = buffer.read_uint32()
        if version != 4:
            raise UnsupportedNTROVersionError(
                version,
                offset=0,
                block_name="NTRO",
            )
        struct_offset = buffer.read_relative_offset32()
        struct_count = buffer.read_uint32()
        enum_offset = buffer.read_relative_offset32()
        enum_count = buffer.read_uint32()
        cls._validate_table(
            buffer,
            "structure",
            struct_offset,
            struct_count,
            40,
        )
        cls._validate_table(
            buffer,
            "enum",
            enum_offset,
            enum_count,
            28,
        )

        struct_lookup = {}
        enum_lookup = {}
        structs = []
        enums = []
        with buffer.read_from_offset(struct_offset):
            for i in range(struct_count):
                struct_type = Struct.from_buffer(buffer)
                struct_lookup[struct_type.name] = struct_type
                struct_lookup[struct_type.id] = struct_type
                structs.append(struct_type)
        with buffer.read_from_offset(enum_offset):
            for i in range(enum_count):
                enum_type = Enum.from_buffer(buffer)
                enum_lookup[enum_type.name] = enum_type
                enum_lookup[enum_type.id] = enum_type
                enums.append(enum_type)
        return cls(ResourceIntrospectionInfo(version, structs, enums, struct_lookup, enum_lookup, {}))

    @staticmethod
    def _validate_table(
            buffer: Buffer,
            name: str,
            offset: int,
            count: int,
            minimum_entry_size: int,
    ):
        if offset < 0 or offset > buffer.size():
            raise BlockBoundsError(
                f"NTRO {name} table offset {offset} is outside the block",
                block_name="NTRO",
                details={"offset": offset, "block_size": buffer.size()},
            )
        maximum_count = (buffer.size() - offset) // minimum_entry_size
        if count > maximum_count:
            raise BlockBoundsError(
                f"NTRO {name} table declares {count} entries, but at most "
                f"{maximum_count} fit in the block",
                block_name="NTRO",
                details={
                    "offset": offset,
                    "count": count,
                    "maximum_count": maximum_count,
                },
            )

    def to_buffer(self, buffer: Buffer) -> None:
        raise NotImplementedError("Not implemented")
