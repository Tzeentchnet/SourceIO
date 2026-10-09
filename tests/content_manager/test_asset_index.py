import os

os.environ["NO_BPY"] = "1"

from SourceIO.blender_bindings.asset_browser import BlenderAssetIndexAdapter
from SourceIO.library.shared.content_manager import AssetIndex, MountLayer, ResolvedResource
from SourceIO.library.utils import MemoryBuffer, TinyPath


class CountingResolver:
    def __init__(self):
        self.calls = 0

    def iter(self, pattern):
        self.calls += 1
        for path in ("z/item.vmdl_c", "a/item.vmat_c", "A/ITEM.vmat_c"):
            tiny_path = TinyPath(path)
            yield ResolvedResource(
                tiny_path,
                tiny_path,
                self,
                MountLayer.LOOSE_FILES,
                "test",
                _opener=lambda: MemoryBuffer(b"asset"),
            )


def test_asset_index_is_lazy_deduplicated_and_stable():
    resolver = CountingResolver()
    index = AssetIndex(resolver)

    assert not index.built
    assert resolver.calls == 0
    assert [entry.path.as_posix() for entry in index] == ["a/item.vmat_c", "z/item.vmdl_c"]
    assert resolver.calls == 1
    assert [entry.path.as_posix() for entry in index] == ["a/item.vmat_c", "z/item.vmdl_c"]
    assert resolver.calls == 1


def test_blender_adapter_has_no_ui_registration_side_effects():
    index = AssetIndex(CountingResolver())
    adapter = BlenderAssetIndexAdapter(index)

    records = list(adapter)

    assert [record.relative_path for record in records] == ["a/item.vmat_c", "z/item.vmdl_c"]
    assert records[0].source == "test"
