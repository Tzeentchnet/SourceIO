from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ..datatypes.brush import Quake3Brush, RavenBrushSide, Quake3BrushSide
from ....utils import Buffer


@lump_tag(8, 'LUMP_BRUSHES', bsp_ident="IBSP", bsp_version=(46, 0))
@lump_tag(8, 'LUMP_BRUSHES', bsp_ident="RBSP", bsp_version=(1, 0))
class Quake3BrushLump(Lump):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.brushes: list[Quake3Brush] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.brushes.append(Quake3Brush.from_buffer(buffer, self.version, bsp))
        return self


@lump_tag(9, 'LUMP_BRUSHSIDES', bsp_ident="IBSP", bsp_version=(46, 0))
class Quake3BrushSidesLump(Lump):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.brush_sides: list[Quake3BrushSide] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.brush_sides.append(Quake3BrushSide.from_buffer(buffer, self.version, bsp))
        return self

@lump_tag(9, 'LUMP_BRUSHSIDES', bsp_ident="RBSP", bsp_version=(1, 0))
class RavenBrushSidesLump(Lump):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.brush_sides: list[RavenBrushSide] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.brush_sides.append(RavenBrushSide.from_buffer(buffer, self.version, bsp))
        return self
