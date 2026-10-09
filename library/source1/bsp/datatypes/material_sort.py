from dataclasses import dataclass

from ..bsp_file import VBSPFile
from ....utils.file_utils import Buffer


@dataclass(slots=True)
class MaterialSort:
    texdata_index: int
    lightmap_header_index: int
    unk_1: int
    vertex_offset: int

    @classmethod
    def from_buffer(cls, buffer: Buffer, version: int, bsp: VBSPFile):
        return cls(*buffer.read_fmt('Hh2I'))
