from .. import Lump, lump_tag
from ..bsp_file import BSPFile, RespawnBSPFile, IBSPFile
from ..datatypes.texture_data import RespawnTextureData, TextureData
from ..datatypes.texture_info import TextureInfo, QuakeTextureInfo
from ..lump import AbstractLump
from ....utils import Buffer


@lump_tag(6, 'LUMP_TEXINFO')
class TextureInfoLump(Lump):

    def __init__(self, lump_info: AbstractLump):
        super().__init__(lump_info)
        self.texture_info: list[TextureInfo] = []

    def parse(self, buffer: Buffer, bsp: BSPFile):
        while buffer:
            self.texture_info.append(TextureInfo.from_buffer(buffer, self.version, bsp))
        return self

@lump_tag(1, 'LUMP_TEXINFO', bsp_ident="IBSP", bsp_version=(46, 0))
@lump_tag(1, 'LUMP_TEXINFO', bsp_ident="RBSP", bsp_version=(1, 0))
class Quake3TextureInfoLump(Lump):

    def __init__(self, lump_info: AbstractLump):
        super().__init__(lump_info)
        self.texture_info: list[QuakeTextureInfo] = []

    def parse(self, buffer: Buffer, bsp: IBSPFile):
        while buffer:
            self.texture_info.append(QuakeTextureInfo.from_buffer(buffer, self.version, bsp))
        return self


@lump_tag(2, 'LUMP_TEXDATA')
class TextureDataLump(Lump):

    def __init__(self, lump_info: AbstractLump):
        super().__init__(lump_info)
        self.texture_data: list[TextureData] = []

    def parse(self, buffer: Buffer, bsp: BSPFile):
        while buffer:
            self.texture_data.append(TextureData.from_buffer(buffer, self.version, bsp))
        return self

@lump_tag(2, 'LUMP_TEXDATA', bsp_ident="rBSP")
class RespawnTextureDataLump(Lump):

    def __init__(self, lump_info: AbstractLump):
        super().__init__(lump_info)
        self.texture_data: list[RespawnTextureData] = []

    def parse(self, buffer: Buffer, bsp: RespawnBSPFile):
        while buffer:
            self.texture_data.append(RespawnTextureData.from_buffer(buffer, self.version, bsp))
        return self
