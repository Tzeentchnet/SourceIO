from typing import Collection

from .source1 import Source1Detector
from ..provider import ContentProvider
from ..providers.hfs_provider import HFS1ContentProvider, HFS2ContentProvider
from ....utils import backwalk_file_resolver, TinyPath


class VindictusDetector(Source1Detector):

    @classmethod
    def game(cls) -> str:
        return 'Vindictus'

    @classmethod
    def find_game_root(cls, path: TinyPath) -> TinyPath | None:
        game_exe = backwalk_file_resolver(path, 'Vindictus.exe')
        if game_exe is not None:
            return game_exe.parent
        return None

    @classmethod
    def scan(cls, path: TinyPath) -> tuple[Collection[ContentProvider] | None, TinyPath | None]:
        game_root = cls.find_game_root(path)
        if game_root is None:
            return None, None
        hfs_provider = HFS2ContentProvider(game_root / 'hfs')
        content_providers = {hfs_provider}
        for file in game_root.glob('*.hfs'):
            cls.add_provider(HFS1ContentProvider(file), content_providers)
        return content_providers, game_root
