"""Game detectors must not claim another Source 2 game's install."""
import os

os.environ['NO_BPY'] = '1'

from SourceIO.library.shared.content_manager.detectors.cs2 import CS2Detector
from SourceIO.library.shared.content_manager.detectors.dota2 import Dota2Detector
from SourceIO.library.shared.app_id import SteamAppId
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.utils import TinyPath


def make_install(root, files):
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.name == 'gameinfo.gi':
            path.write_text(
                '"GameInfo"\n'
                '{\n'
                '    "FileSystem"\n'
                '    {\n'
                '        "SearchPaths"\n'
                '        {\n'
                '            "Game" "|gameinfo_path|."\n'
                '        }\n'
                '    }\n'
                '}\n',
                encoding='utf-8',
            )
        else:
            path.write_bytes(b'')


def test_dota2_detector_ignores_cs2(tmp_path):
    make_install(tmp_path, ['game/bin/win64/cs2.exe', 'game/csgo/gameinfo.gi', 'game/csgo/pak01_dir.vpk',
                            'game/csgo/maps/de_dust2.vpk'])
    map_path = TinyPath(tmp_path / 'game/csgo/maps/de_dust2.vpk')
    assert Dota2Detector.find_game_root(map_path) is None
    assert CS2Detector.find_game_root(map_path) == TinyPath(tmp_path / 'game')


def test_cs2_detector_mounts_the_scanned_game_addon(tmp_path):
    make_install(tmp_path, [
        'game/bin/win64/cs2.exe',
        'game/csgo/gameinfo.gi',
        'game/csgo/pak01_dir.vpk',
        'game/csgo_addons/sourceio/materials/sourceio/probe.vmat_c',
    ])
    asset_path = TinyPath(
        tmp_path
        / 'game/csgo_addons/sourceio/materials/sourceio/probe.vmat_c'
    )
    content_manager = ContentManager()
    content_manager.clean()
    try:
        content_manager.scan_for_content(asset_path)
        material = content_manager.find_file(
            TinyPath('materials/sourceio/probe.vmat_c')
        )
        assert material is not None
        material.close()
        assert content_manager.steam_id == SteamAppId.COUNTER_STRIKE_GO
    finally:
        content_manager.clean()


def test_dota2_detector_finds_dota(tmp_path):
    make_install(tmp_path, ['game/dota/gameinfo.gi', 'game/dota/pak01_dir.vpk', 'game/dota/maps/dota.vpk'])
    map_path = TinyPath(tmp_path / 'game/dota/maps/dota.vpk')
    root = Dota2Detector.find_game_root(map_path)
    providers, scanned_root = Dota2Detector.scan(map_path)

    assert root == TinyPath(tmp_path / 'game')
    assert scanned_root == root
    assert {provider.steam_id for provider in providers} == {SteamAppId.DOTA_2}


def test_dota2_detector_finds_content_and_game_addons(tmp_path):
    make_install(tmp_path, [
        'game/dota/gameinfo.gi',
        'game/dota/pak01_dir.vpk',
        'content/dota_addons/sourceio/models/probe.vmdl',
        'game/dota_addons/sourceio/models/probe.vmdl_c',
    ])

    for relative_path in (
            'content/dota_addons/sourceio/models/probe.vmdl',
            'game/dota_addons/sourceio/models/probe.vmdl_c',
    ):
        assert Dota2Detector.find_game_root(TinyPath(tmp_path / relative_path)) == TinyPath(tmp_path / 'game')


def test_content_manager_keeps_dota_identity_when_generic_detector_also_matches(tmp_path):
    make_install(tmp_path, ['game/dota/gameinfo.gi', 'game/dota/pak01_dir.vpk'])
    content_manager = ContentManager()
    content_manager.clean()
    try:
        content_manager.scan_for_content(TinyPath(tmp_path / 'game/dota/gameinfo.gi'))
        assert content_manager.steam_id == SteamAppId.DOTA_2
        assert {provider.steam_id for provider in content_manager.children} == {SteamAppId.DOTA_2}
    finally:
        content_manager.clean()
