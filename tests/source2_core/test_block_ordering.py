from __future__ import annotations

from SourceIO.library.source2.blocks.all_blocks import (
    guess_block_type,
    register_block_type,
)
from SourceIO.library.source2.blocks.base import BaseBlock
from SourceIO.library.source2.compiled_resource import CompiledResource, DATA_BLOCK
from SourceIO.library.utils import Buffer, MemoryBuffer, TinyPath

from ._builders import build_resource


def test_data_parser_loads_prerequisite_metadata_first():
    events: list[str] = []

    class ControlBlock(BaseBlock):
        @classmethod
        def from_buffer(cls, buffer):
            events.append("CTRL")
            buffer.read()
            return cls()

        def to_buffer(self, buffer: Buffer) -> None:
            raise NotImplementedError

    class DataBlock(BaseBlock):
        @classmethod
        def from_buffer(cls, buffer):
            events.append("DATA")
            buffer.read()
            return cls()

        def to_buffer(self, buffer: Buffer) -> None:
            raise NotImplementedError

    class OrderedResource(CompiledResource):
        def get_data_block_type(self):
            return DataBlock

    previous_control_type = guess_block_type("CTRL")
    register_block_type("CTRL", ControlBlock, replace=True)
    try:
        resource = OrderedResource.from_buffer(
            MemoryBuffer(build_resource((
                ("DATA", b"data"),
                ("CTRL", b"control"),
            ))),
            TinyPath("ordered.vtest_c"),
        )

        resource.get_block(block_id=DATA_BLOCK)

        assert events == ["CTRL", "DATA"]
        assert resource.block_state(0) == "parsed"
        assert resource.block_state(1) == "parsed"
    finally:
        register_block_type("CTRL", previous_control_type, replace=True)
