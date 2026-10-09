from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ..datatypes.world_light import WorldLight
from ....utils import Buffer


@lump_tag(15, 'LUMP_WORLDLIGHTS')
class WorldLightLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.lights: list[WorldLight] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.lights.append(WorldLight.from_buffer(buffer, self.version, bsp))
        return self
