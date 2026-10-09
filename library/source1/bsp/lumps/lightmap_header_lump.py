from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ..datatypes.lightmap_header import LightmapHeader
from ....utils import Buffer


@lump_tag(0x53, 'LUMP_LIGHTMAP_HEADERS', bsp_version=29)
class LightmapHeadersLump(Lump):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.lightmap_headers: list[LightmapHeader] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.lightmap_headers.append(LightmapHeader.from_buffer(buffer, self.version, bsp))
        return self
