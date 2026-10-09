from dataclasses import dataclass

from ..bsp_file import VBSPFile
from ....utils.file_utils import Buffer


@dataclass(slots=True)
class LightmapHeader:
    count: int
    width: int
    height: int

    @classmethod
    def from_buffer(cls, buffer: Buffer, version: int, bsp: VBSPFile):
        return cls(*buffer.read_fmt('I2H'))
