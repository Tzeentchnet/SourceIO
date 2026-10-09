from dataclasses import dataclass

from ....shared.vector_types import Vector3
from ..bsp_file import VBSPFile
from ....utils.file_utils import Buffer


@dataclass(slots=True)
class Cubemap:
    origin: Vector3[int]
    size: int

    @classmethod
    def from_buffer(cls, buffer: Buffer, version: int, bsp: VBSPFile):
        return cls(buffer.read_fmt("3i"), buffer.read_uint32())
