"""`custom/*` wildcard search paths in Source 1 gameinfo.txt."""
import os
import struct

os.environ['NO_BPY'] = '1'

from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.shared.content_manager.providers.source1_gameinfo_provider import Source1GameInfoProvider
from SourceIO.library.shared.content_manager.providers.vpk_provider import VPKContentProvider
from SourceIO.library.utils import TinyPath

GAMEINFO = '''"GameInfo"
{
    game "Test"
    FileSystem
    {
        SteamAppId 440
        SearchPaths
        {
            game+mod+custom_mod tf/custom/*
            game+game_write     tf
        }
    }
}
'''


def write_vpk(path, files: dict[str, bytes]):
    """Minimal VPK v1 with every file stored after the tree in the same archive."""
    tree = bytearray()
    data = bytearray()
    by_ext: dict[str, dict[str, list[tuple[str, bytes]]]] = {}
    for name, content in files.items():
        directory, _, filename = name.rpartition('/')
        stem, _, ext = filename.rpartition('.')
        by_ext.setdefault(ext, {}).setdefault(directory or ' ', []).append((stem, content))
    for ext, dirs in by_ext.items():
        tree += ext.encode() + b'\0'
        for directory, entries in dirs.items():
            tree += directory.encode() + b'\0'
            for stem, content in entries:
                tree += stem.encode() + b'\0'
                tree += struct.pack('<IHHIIH', 0, 0, 0x7FFF, len(data), len(content), 0xFFFF)
                data += content
            tree += b'\0'
        tree += b'\0'
    tree += b'\0'
    path.write_bytes(struct.pack('<III', 0x55AA1234, 1, len(tree)) + tree + data)


def make_game(tmp_path):
    tf = tmp_path / 'tf'
    custom = tf / 'custom'
    (custom / 'b_folder' / 'materials').mkdir(parents=True)
    (custom / 'b_folder' / 'materials' / 'shared.vmt').write_bytes(b'b_folder')
    (custom / 'b_folder' / 'materials' / 'only_b.vmt').write_bytes(b'only_b')
    write_vpk(custom / 'a_mod.vpk', {'materials/shared.vmt': b'a_mod'})
    write_vpk(custom / 'c_split_dir.vpk', {'materials/only_c.vmt': b'c_split'})
    (custom / 'c_split_000.vpk').write_bytes(b'chunk')
    (custom / 'readme.txt').write_text('not a search path')
    (tf / 'materials').mkdir()
    (tf / 'materials' / 'shared.vmt').write_bytes(b'base')
    (tf / 'materials' / 'only_base.vmt').write_bytes(b'only_base')
    (tf / 'gameinfo.txt').write_text(GAMEINFO)
    return Source1GameInfoProvider(TinyPath(tf / 'gameinfo.txt'))


def test_custom_entries_mount_first_in_alphabetical_order(tmp_path):
    provider = make_game(tmp_path)
    custom = TinyPath(tmp_path / 'tf' / 'custom')
    mounted = [(type(m), m.filepath) for m in provider.mount]
    assert mounted[:3] == [
        (VPKContentProvider, custom / 'a_mod.vpk'),
        (LooseFilesContentProvider, custom / 'b_folder'),
        (VPKContentProvider, custom / 'c_split_dir.vpk'),
    ]
    assert mounted[3] == (LooseFilesContentProvider, TinyPath(tmp_path / 'tf'))
    assert not any(path.name in ('c_split_000.vpk', 'readme.txt') for _, path in mounted)


def test_custom_content_overrides_game(tmp_path):
    provider = make_game(tmp_path)
    assert provider.find_file(TinyPath('materials/shared.vmt')).read() == b'a_mod'
    assert provider.find_file(TinyPath('materials/only_b.vmt')).read() == b'only_b'
    assert provider.find_file(TinyPath('materials/only_c.vmt')).read() == b'c_split'
    assert provider.find_file(TinyPath('materials/only_base.vmt')).read() == b'only_base'
    assert provider.check(TinyPath('materials/only_c.vmt'))


def test_missing_wildcard_folder_is_ignored(tmp_path):
    (tmp_path / 'tf').mkdir()
    (tmp_path / 'tf' / 'gameinfo.txt').write_text(GAMEINFO)
    provider = Source1GameInfoProvider(TinyPath(tmp_path / 'tf' / 'gameinfo.txt'))
    assert [m.filepath for m in provider.mount] == [TinyPath(tmp_path / 'tf')]
