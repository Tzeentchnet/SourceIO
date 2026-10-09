import bpy

from ...library.shared.content_manager import AssetIndex, ContentManager


def serialize_mounted_content(cm: ContentManager):
    data = cm.serialize()
    resources = bpy.context.scene.mounted_resources
    for item_hash, item in data.items():
        if (resource := resources.get(item['name'])) != None:
            if resource.path == item['path']: continue
        new_resource = resources.add()
        new_resource.path = item["path"]
        new_resource.name = item["name"]
        new_resource.hash = item_hash


def deserialize_mounted_content(cm: ContentManager):
    data = {}
    resources = bpy.context.scene.mounted_resources
    for resource in resources:
        item = {"path": resource.path, "name": resource.name}
        data[resource.hash] = item
    cm.deserialize(data)


def get_asset_index(cm: ContentManager, patterns: tuple[str, ...] = ("*",)) -> AssetIndex:
    cached = getattr(cm, "_blender_asset_index", None)
    if cached is None or cached.patterns != patterns:
        cached = AssetIndex(cm, patterns)
        cm._blender_asset_index = cached
    return cached


def invalidate_asset_index(cm: ContentManager) -> None:
    cached = getattr(cm, "_blender_asset_index", None)
    if cached is not None:
        cached.invalidate()
