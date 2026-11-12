import os

os.environ['NO_BPY'] = '1'

from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.shared.content_manager.providers.source1_gameinfo_provider import Source1GameInfoProvider
from SourceIO.library.utils import TinyPath


GAMEINFO = '''"GameInfo"
{
    game "Test"
    FileSystem
    {
        SteamAppId 420
        SearchPaths
        {
            GaMe_WrItE write
            Game       game
        }
    }
}
'''


def test_game_write_mounts_and_keeps_declared_precedence(tmp_path):
    game = tmp_path / 'game'
    write = tmp_path / 'write'
    (game / 'materials').mkdir(parents=True)
    (write / 'materials').mkdir(parents=True)
    (game / 'materials' / 'shared.vmt').write_bytes(b'game')
    (write / 'materials' / 'shared.vmt').write_bytes(b'write')
    (write / 'materials' / 'write_only.vmt').write_bytes(b'write-only')
    (game / 'gameinfo.txt').write_text(GAMEINFO)

    provider = Source1GameInfoProvider(TinyPath(game / 'gameinfo.txt'))

    assert [(type(mount), mount.filepath) for mount in provider.mount] == [
        (LooseFilesContentProvider, TinyPath(write)),
        (LooseFilesContentProvider, TinyPath(game)),
    ]
    assert provider.find_file(TinyPath('materials/shared.vmt')).read() == b'write'
    assert provider.check(TinyPath('materials/write_only.vmt'))
    assert provider.find_file(TinyPath('materials/write_only.vmt')).read() == b'write-only'
