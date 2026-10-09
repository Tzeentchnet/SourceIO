from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ..datatypes.cubemap import Cubemap
from ....utils import Buffer


@lump_tag(42, 'LUMP_CUBEMAPS')
class CubemapLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.cubemaps: list[Cubemap] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.cubemaps.append(Cubemap.from_buffer(buffer, self.version, bsp))
        return self
