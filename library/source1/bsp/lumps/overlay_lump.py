from ....shared.app_id import SteamAppId
from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ..datatypes.overlay import Overlay, VOverlay
from ....utils import Buffer


@lump_tag(45, 'LUMP_OVERLAYS')
class OverlayLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.overlays = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.overlays.append(Overlay.from_buffer(buffer, self.version, bsp))
        return self


@lump_tag(45, 'LUMP_OVERLAYS', steam_id=SteamAppId.VINDICTUS)
class VOverlayLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.overlays = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.overlays.append(VOverlay.from_buffer(buffer, self.version, bsp))
        return self
