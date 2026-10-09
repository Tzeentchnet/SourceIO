from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ..datatypes.mesh import Mesh
from ....utils import Buffer


@lump_tag(0x50, 'LUMP_MESHES', bsp_version=29)
class MeshLump(Lump):
    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.meshes: list[Mesh] = []

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        while buffer:
            self.meshes.append(Mesh.from_buffer(buffer, bsp.info.version, bsp))
        return self
