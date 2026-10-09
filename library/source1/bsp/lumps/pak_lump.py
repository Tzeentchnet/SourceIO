import zipfile
from io import BytesIO

from ....shared.app_id import SteamAppId
from ....shared.content_manager.providers.zip_content_provider import ZIPContentProvider
from .. import Lump, ValveLumpInfo, lump_tag
from ..bsp_file import VBSPFile
from ....utils import Buffer
from ....utils.tiny_path import TinyPath


@lump_tag(40, 'LUMP_PAK')
class PakLump(Lump, ZIPContentProvider):

    def __init__(self, lump_info: ValveLumpInfo):
        super().__init__(lump_info)
        self.filepath = None
        self._steamapp_id = SteamAppId.UNKNOWN
        self._zip_file = None
        self._cache = {}

    def parse(self, buffer: Buffer, bsp: VBSPFile):
        self.filepath = bsp.filepath
        if self._zip_file is None:
            zip_data = BytesIO(buffer.read())
            self._zip_file = zipfile.ZipFile(zip_data)
            self._cache = {TinyPath(a.lower()).as_posix(): a for a in self._zip_file.NameToInfo}
        return self
