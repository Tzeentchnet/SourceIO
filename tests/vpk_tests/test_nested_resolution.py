import os

import pytest

os.environ["NO_BPY"] = "1"

from SourceIO.library.shared.content_manager import (
    ContentManager,
    MountLayer,
    NestedArchiveLimitError,
    UnsafeResourcePath,
)
from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.shared.content_manager.providers.vpk_provider import VPKContentProvider
from SourceIO.library.source2.interfaces import ResourceRef as InterfaceResourceRef
from SourceIO.library.source2.interfaces import ResourceResolver
from SourceIO.library.utils import TinyPath

from .helpers import build_vpk, write_vpk


def make_nested_packages(tmp_path):
    sky_vpk, _ = build_vpk({"maps/sky.vmap_c": b"sky-map"})
    map_vpk, _ = build_vpk(
        {
            "maps/main.vmap_c": b"main-map",
            "maps/sky.vpk": sky_vpk,
            "materials/dependency.bin": b"package-dependency",
        }
    )
    outer = write_vpk(
        tmp_path / "pak01_dir.vpk",
        {
            "maps/main.vpk": map_vpk,
            "materials/dependency.bin": b"game-dependency",
        },
    )
    return outer


def test_map_and_skybox_packages_resolve_without_loader_mounts(tmp_path):
    outer = make_nested_packages(tmp_path)
    manager = ContentManager()
    manager.clean()
    manager.add_child(LooseFilesContentProvider(TinyPath(tmp_path / "loose")), MountLayer.LOOSE_FILES)
    manager.add_child(VPKContentProvider(TinyPath(outer)), MountLayer.GAME_PACKAGES)

    main = manager.find(TinyPath("maps/main.vmap_c"))
    assert main is not None
    assert main.layer == MountLayer.CURRENT_PACKAGE
    assert main.open().read() == b"main-map"

    sky = manager.find(TinyPath("maps/sky.vmap_c"))
    assert sky is not None
    assert sky.layer == MountLayer.CURRENT_PACKAGE
    assert sky.open().read() == b"sky-map"
    assert [path.name for path in sky.archive_chain[-2:]] == ["main.vpk", "sky.vpk"]


def test_resource_ref_prefers_its_package_for_dependencies(tmp_path):
    outer = make_nested_packages(tmp_path)
    loose = tmp_path / "loose" / "materials"
    loose.mkdir(parents=True)
    (loose / "dependency.bin").write_bytes(b"loose-dependency")
    manager = ContentManager()
    manager.clean()
    manager.add_child(LooseFilesContentProvider(TinyPath(tmp_path / "loose")), MountLayer.LOOSE_FILES)
    manager.add_child(VPKContentProvider(TinyPath(outer)), MountLayer.GAME_PACKAGES)

    main = manager.find(TinyPath("maps/main.vmap_c"))
    dependency = manager.open(main.dependency(TinyPath("materials/dependency.bin")))

    assert dependency.read() == b"package-dependency"
    assert manager.find("materials/dependency.bin").layer == MountLayer.CURRENT_PACKAGE


def test_source2_resolver_protocol_uses_source_path_context(tmp_path):
    outer = make_nested_packages(tmp_path)
    manager = ContentManager()
    manager.clean()
    manager.add_child(VPKContentProvider(TinyPath(outer)), MountLayer.GAME_PACKAGES)
    assert manager.find(TinyPath("maps/main.vmap_c")) is not None

    dependency = manager.resolve(
        InterfaceResourceRef(
            path="materials/dependency.bin",
            source_path="maps/main.vmap_c",
        )
    )

    assert isinstance(manager, ResourceResolver)
    assert dependency.read() == b"package-dependency"


def test_explicit_nested_locator_and_limits(tmp_path):
    outer = make_nested_packages(tmp_path)
    manager = ContentManager()
    manager.clean()
    manager.add_child(VPKContentProvider(TinyPath(outer)), MountLayer.GAME_PACKAGES)

    assert manager.open("pak01_dir.vpk::maps/main.vpk::maps/main.vmap_c").read() == b"main-map"

    manager.clean()
    manager.configure_nested_archives(max_depth=0)
    manager.add_child(VPKContentProvider(TinyPath(outer)), MountLayer.GAME_PACKAGES)
    with pytest.raises(NestedArchiveLimitError):
        manager.open("pak01_dir.vpk::maps/main.vpk::maps/main.vmap_c")

    manager.clean()
    manager.configure_nested_archives(max_size=32)
    manager.add_child(VPKContentProvider(TinyPath(outer)), MountLayer.GAME_PACKAGES)
    with pytest.raises(NestedArchiveLimitError):
        manager.open("pak01_dir.vpk::maps/main.vpk::maps/main.vmap_c")


def test_nested_archive_boundaries_reject_traversal(tmp_path):
    outer = make_nested_packages(tmp_path)
    manager = ContentManager()
    manager.clean()
    manager.add_child(VPKContentProvider(TinyPath(outer)))

    with pytest.raises(UnsafeResourcePath):
        manager.open("pak01_dir.vpk::../maps/main.vmap_c")
