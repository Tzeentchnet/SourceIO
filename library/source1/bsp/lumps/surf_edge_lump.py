import numpy as np

from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ....utils import Buffer


@lump_tag(13, 'LUMP_SURFEDGES')
class SurfEdgeLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.surf_edges = np.array([])

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        self.surf_edges = np.frombuffer(buffer.read(), np.int32)
        return self


@lump_tag(11, 'LUMP_DRAWINDEXES')
class Quake3IndicesLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.indices = np.array([])

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        self.indices = np.frombuffer(buffer.read(), np.int32)
        return self
