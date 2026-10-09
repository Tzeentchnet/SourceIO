from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from ...library.shared.content_manager import AssetIndex, AssetIndexEntry

if TYPE_CHECKING:
    import bpy


@dataclass(frozen=True, slots=True)
class BlenderAssetRecord:
    name: str
    relative_path: str
    source: str
    extension: str


class BlenderAssetIndexAdapter:
    """Blender-facing projection of an AssetIndex with no UI registration side effects."""

    def __init__(self, index: AssetIndex):
        self.index = index

    def __iter__(self) -> Iterator[BlenderAssetRecord]:
        for entry in self.index:
            yield self.to_record(entry)

    @staticmethod
    def to_record(entry: AssetIndexEntry) -> BlenderAssetRecord:
        return BlenderAssetRecord(
            entry.name,
            entry.path.as_posix(),
            entry.resource.mount_name,
            entry.extension,
        )

    def search(self, query: str) -> tuple[BlenderAssetRecord, ...]:
        return tuple(self.to_record(entry) for entry in self.index.search(query))

    def mark_asset(
        self,
        data_block: Any,
        entry: AssetIndexEntry,
        *,
        description: str | None = None,
    ) -> Any:
        """Mark an existing Blender ID as an asset without registering browser UI."""

        if not hasattr(data_block, "asset_mark"):
            raise TypeError("data_block must be a Blender ID supporting asset_mark()")
        data_block.asset_mark()
        asset_data = data_block.asset_data
        if description is not None:
            asset_data.description = description
        asset_data["sourceio_path"] = entry.path.as_posix()
        asset_data["sourceio_mount"] = entry.resource.mount_name
        return asset_data
