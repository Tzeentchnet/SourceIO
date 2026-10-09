import os

import pytest

os.environ["NO_BPY"] = "1"

from SourceIO.library.shared.content_manager import ContentManager, MountLayer, UnsafeResourcePath
from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.utils import TinyPath


def make_provider(tmp_path, name, payload):
    root = tmp_path / name
    target = root / "materials" / "shared.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    return LooseFilesContentProvider(TinyPath(root))


def test_mount_layer_precedence_is_explicit_and_stable(tmp_path):
    manager = ContentManager()
    manager.clean()
    mounts = [
        (MountLayer.SEARCH_PATHS, b"search"),
        (MountLayer.GAME_PACKAGES, b"game"),
        (MountLayer.WORKSHOP, b"workshop"),
        (MountLayer.ADDONS, b"addon"),
        (MountLayer.LOOSE_FILES, b"loose"),
    ]
    for layer, payload in mounts:
        manager.add_child(make_provider(tmp_path, layer.name.lower(), payload), layer)

    resource = manager.find("materials/shared.bin")

    assert resource.layer == MountLayer.LOOSE_FILES
    assert resource.open().read() == b"loose"
    assert resource.collisions
    assert manager.collision_diagnostics[0].reason == "multiple mounts contain the same resource"


def test_equal_layer_uses_mount_order(tmp_path):
    manager = ContentManager()
    manager.clean()
    first = make_provider(tmp_path, "first", b"first")
    second = make_provider(tmp_path, "second", b"second")
    manager.add_child(first, MountLayer.LOOSE_FILES)
    manager.add_child(second, MountLayer.LOOSE_FILES)

    assert manager.open("materials/shared.bin").read() == b"first"
    assert manager.open("materials/shared.bin").read() == b"first"


def test_explicit_current_package_layer_zero_overrides_inferred_loose_layer(tmp_path):
    manager = ContentManager()
    manager.clean()
    unrelated = make_provider(tmp_path, "unrelated", b"loose")
    selected_package = make_provider(tmp_path, "selected", b"selected")
    manager.add_child(unrelated, MountLayer.LOOSE_FILES)
    manager.add_child(selected_package)
    manager.add_child(selected_package, MountLayer.CURRENT_PACKAGE)

    resource = manager.find("materials/shared.bin")

    assert resource.provider is selected_package
    assert resource.layer is MountLayer.CURRENT_PACKAGE
    assert resource.open().read() == b"selected"


def test_safe_normalization_preserves_legacy_absolute_find_file(tmp_path):
    manager = ContentManager()
    manager.clean()
    provider = make_provider(tmp_path, "root", b"data")
    manager.add_child(provider)

    assert manager.open("materials\\shared.bin").read() == b"data"
    assert manager.open("root/materials/shared.bin").read() == b"data"
    with pytest.raises(UnsafeResourcePath):
        manager.open("../materials/shared.bin")
    with pytest.raises(UnsafeResourcePath):
        manager.open(TinyPath(tmp_path / "root" / "materials" / "shared.bin"))
    absolute_path = TinyPath(tmp_path / "root" / "materials" / "shared.bin")
    assert manager.find_file(absolute_path).read() == b"data"
    assert manager.get_provider_from_path(absolute_path) is provider
    assert manager.get_steamid_from_asset(absolute_path) == provider.steam_id


def test_iter_is_deduplicated_and_stable(tmp_path):
    manager = ContentManager()
    manager.clean()
    root = tmp_path / "assets"
    (root / "z").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"a")
    (root / "z" / "b.bin").write_bytes(b"b")
    manager.add_child(LooseFilesContentProvider(TinyPath(root)))

    first = [resource.path.as_posix() for resource in manager.iter("*")]
    second = [resource.path.as_posix() for resource in manager.iter("*")]

    assert first == ["a.bin", "z/b.bin"]
    assert second == first
