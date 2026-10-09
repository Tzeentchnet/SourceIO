from dataclasses import dataclass

from .base import BaseBlock
from ..utils.ntro_reader import NTROBuffer
from ...utils import MemoryBuffer, Buffer


@dataclass
class BinaryBlob(BaseBlock):
    data: MemoryBuffer

    @classmethod
    def from_buffer(cls, buffer: NTROBuffer) -> 'BaseBlock':
        data = buffer.read(buffer.remaining())
        return cls(MemoryBuffer(data))

    def to_buffer(self, buffer: Buffer) -> None:
        buffer.write(self.data.data)

    def __bytes__(self) -> bytes:
        return bytes(self.data.data)


@dataclass
class UnknownBlock(BinaryBlob):
    """Raw bytes for a FourCC that has no registered decoder."""

    reason: str = "No block decoder is registered"
