"""Game detectors must not claim another Source 2 game's install."""
import os

os.environ['NO_BPY'] = '1'

from SourceIO.library.shared.content_manager.detectors.cs2 import CS2Detector
from SourceIO.library.shared.content_manager.detectors.dota2 import Dota2Detector
from SourceIO.library.utils import TinyPath


def make_install(root, files):
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'')


def test_dota2_detector_ignores_cs2(tmp_path):
    make_install(tmp_path, ['game/bin/win64/cs2.exe', 'game/csgo/gameinfo.gi', 'game/csgo/pak01_dir.vpk',
                            'game/csgo/maps/de_dust2.vpk'])
    map_path = TinyPath(tmp_path / 'game/csgo/maps/de_dust2.vpk')
    assert Dota2Detector.find_game_root(map_path) is None
    assert CS2Detector.find_game_root(map_path) == TinyPath(tmp_path / 'game')


def test_dota2_detector_finds_dota(tmp_path):
    make_install(tmp_path, ['game/dota/gameinfo.gi', 'game/dota/pak01_dir.vpk', 'game/dota/maps/dota.vpk'])
    root = Dota2Detector.find_game_root(TinyPath(tmp_path / 'game/dota/maps/dota.vpk'))
    assert root == TinyPath(tmp_path / 'game')
