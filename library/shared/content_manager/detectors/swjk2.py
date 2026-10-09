from typing import Collection

from ...app_id import SteamAppId
from . import QuakeIDTech3Detector
from ..provider import ContentProvider
from ..providers.loose_files import LooseFilesContentProvider
from ..providers.zip_content_provider import ZIPContentProvider
from ....utils import backwalk_file_resolver, TinyPath


class StarWarsJediKnights2Detector(QuakeIDTech3Detector):

    @classmethod
    def game(cls) -> str:
        return 'Star Wars: Jedi Knight II - Jedi Outcast'

    @classmethod
    def find_game_root(cls, path: TinyPath) -> TinyPath | None:
        game_dll = backwalk_file_resolver(path, 'jk2gamex86.dll')
        if game_dll is None:
            return None
        return game_dll.parent / "base"

    @classmethod
    def scan(cls, path: TinyPath) -> tuple[Collection[ContentProvider] | None, TinyPath | None]:
        base_dir = cls.find_game_root(path)
        if base_dir is None:
            return None, None
        providers = set()
        cls.add_provider(LooseFilesContentProvider(base_dir, SteamAppId.STAR_WARS_JEDI_KNIGHTS2), providers)
        for pk3_file in base_dir.glob('*.pk3'):
            cls.add_provider(ZIPContentProvider(pk3_file, SteamAppId.STAR_WARS_JEDI_KNIGHTS2), providers)

        return providers, base_dir
