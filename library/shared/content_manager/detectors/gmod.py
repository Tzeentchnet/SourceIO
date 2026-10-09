from typing import Collection
from ....archives.gma import check_gma
from ...app_id import SteamAppId
from .source1 import Source1Detector
from ..provider import ContentProvider
from ..providers.gma_provider import GMAContentProvider
from ..providers.loose_files import LooseFilesContentProvider
from ..providers.source1_gameinfo_provider import Source1GameInfoProvider
from ....utils import backwalk_file_resolver, TinyPath


class GModDetector(Source1Detector):

    @classmethod
    def game(cls) -> str:
        return "Garry's Mod"

    #: Files that identify a Garry's Mod install, relative to the game root.
    #:
    #: ``garrysmod/dupes`` used to be the only marker, but it is user data -- the
    #: game creates it the first time a duplicator file is saved, so a fresh or
    #: never-played install was not detected at all and fell back to the generic
    #: Source 1 handling. These all ship with the game.
    GAME_MARKERS = ('garrysmod/garrysmod.ver', 'garrysmod/garrysmod_dir.vpk', 'garrysmod/dupes')

    @classmethod
    def find_game_root(cls, path: TinyPath) -> TinyPath | None:
        for marker in cls.GAME_MARKERS:
            found = backwalk_file_resolver(path, marker)
            if found is not None:
                # `backwalk_file_resolver` also matches a leading sub-path, so make
                # sure the whole marker resolved rather than just `garrysmod/`.
                if found.name != TinyPath(marker).name:
                    continue
                return found.parent.parent
        return None

    @classmethod
    def scan(cls, path: TinyPath) -> tuple[Collection[ContentProvider] | None, TinyPath | None]:
        gmod_root = cls.find_game_root(path)
        if gmod_root is None:
            return None, None
        gmod_dir = gmod_root / 'garrysmod'

        providers = set()
        initial_mod_gi_path = backwalk_file_resolver(path, "gameinfo.txt")
        if initial_mod_gi_path is not None:
            cls.add_provider(Source1GameInfoProvider(initial_mod_gi_path), providers)

        # gameinfo.txt lives in the mod directory, not the game root.
        garrysmod_mod_gi_path = gmod_dir / "gameinfo.txt"
        if initial_mod_gi_path != garrysmod_mod_gi_path and garrysmod_mod_gi_path.exists():
            cls.add_provider(Source1GameInfoProvider(garrysmod_mod_gi_path), providers)

        cls.register_common(gmod_root, providers)
        addons_dir = gmod_dir / "addons"
        # `addons` only exists once the user installs one.
        if addons_dir.exists():
            for addon in addons_dir.iterdir():
                if addon.suffix == ".gma":
                    if not check_gma(addon):
                        continue
                    provider = GMAContentProvider(addon)
                else:
                    provider = LooseFilesContentProvider(addon, SteamAppId.GARRYS_MOD)
                cls.add_provider(provider, providers)
        return providers, gmod_root
