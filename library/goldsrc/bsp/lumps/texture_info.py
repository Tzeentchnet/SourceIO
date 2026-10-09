from ..bsp_file import BspFile
from ..lump import Lump, LumpInfo, LumpType
from ..structs.texture import TextureInfo
from ....utils import Buffer


class TextureInfoLump(Lump):
    LUMP_TYPE = LumpType.LUMP_TEXTURES_INFO

    def __init__(self, info: LumpInfo):
        super().__init__(info)
        self.values: list[TextureInfo] = []

    def parse(self, buffer: Buffer, bsp: BspFile):
        while buffer:
            texture_info = TextureInfo.from_buffer(buffer)
            self.values.append(texture_info)
