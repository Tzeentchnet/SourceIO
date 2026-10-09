import numpy as np

from ....shared.app_id import SteamAppId
from ....utils import Buffer
from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile


@lump_tag(12, 'LUMP_EDGES')
class EdgeLump(Lump):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.edges = np.array([])

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        self.edges = np.frombuffer(buffer.read(), np.uint16 if self.version == 0 else np.uint32)
        self.edges = self.edges.reshape((-1, 2))
        return self


@lump_tag(12, 'LUMP_EDGES', steam_id=SteamAppId.VINDICTUS)
class VEdgeLump(Lump):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.edges = np.array([])

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        self.edges = np.frombuffer(buffer.read(), np.uint32)
        self.edges = self.edges.reshape((-1, 2))
        return self
