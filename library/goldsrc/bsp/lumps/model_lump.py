from ..bsp_file import BspFile
from ..lump import Lump, LumpInfo, LumpType
from ..structs.model import Model
from ....utils import Buffer


class ModelLump(Lump):
    LUMP_TYPE = LumpType.LUMP_MODELS

    def __init__(self, info: LumpInfo):
        super().__init__(info)
        self.values: list[Model] = []

    def parse(self, buffer: Buffer, bsp: BspFile):
        while buffer:
            self.values.append(Model.from_buffer(buffer))
