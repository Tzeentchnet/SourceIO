import math
import re
from typing import Any, Iterator, Type

import bpy
from mathutils import Euler, Matrix, Vector

from ...shared.exceptions import RequiredFileNotFound
from ...utils.bpy_utils import (find_layer_collection, get_or_create_child_collection, get_or_create_collection,
                                pause_view_layer_update)
from ....library.shared.app_id import SteamAppId
from ....library.shared.content_manager import ContentManager
from ....library.shared.content_manager.providers.vpk_provider import VPKContentProvider
from ....library.source2 import CompiledWorldResource, CompiledResource
from ....library.source2.keyvalues3.types import Object, NullObject
from ....library.source2.resource_types import CompiledManifestResource
from ....library.source2.resource_types.compiled_world_resource import CompiledEntityLumpResource, \
    CompiledMapResource
from ....library.utils import FileBuffer
from ....library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS
from ....library.utils.tiny_path import TinyPath
from ....logger import SourceLogMan

from .entities.abstract_entity_handlers import get_angles, get_origin, parse_float_vector
from .entities.base_entity_handlers import BaseEntityHandler
from .entities.cs2_entity_handlers import CS2EntityHandler
from .entities.deadlock_entity_handlers import DeadlockEntityHandler
from .entities.hlvr_entity_handlers import HLVREntityHandler
from .entities.sbox_entity_handlers import SBoxEntityHandler

log_manager = SourceLogMan()

logger = log_manager.get_logger("VWRLD")

# A 3D skybox map has its own sun and sky, which the map that references it already has, and a sky_camera
# that only says where the skybox goes.
SKYBOX_SKIPPED_CLASSES = frozenset({"light_environment", "env_sky", "sky_camera", "skybox_reference"})

# Meshes compiled from toolsblocklight/toolssolidblocklight brushes (n0_lr0_c1_s_cb_bl_mesh_blocklight1_shadow):
# the game only renders them into shadow maps. Their materials aren't shipped.
LIGHT_BLOCKER = re.compile(r"_blocklight\d+_(?:no)?shadow$")
SHADOW_CASTERS_COLLECTION = "shadow_casters"

# The map compiler names the models it generates for a world node after the node, layer, cluster and
# batching flags (n0_lr0_c0_s_cb_b_), then what the model is: nomerge6_steam_001, agg_merge_agave_plant_01_0,
# mesh_overlay12, bl_mesh_blocklight3_shadow, ending in render flags (_nsh, _nzp). Hammer's own names are gone.
_COMPILER_PREFIX = re.compile(r"(?:n|node)\d+_+(?:world_)?lr\d+_(?:c\d+_)?(?:s_)?(?:cb_)?(?:b_)?(?:bl_)?(?:nv_)?")
_RENDER_FLAGS = re.compile(r"(?:_(?:nzp|nsh|nz))+$")
_MODEL_KINDS = (re.compile(r"agg_(?:merge|prop|nomerge)_(.+)_\d+"), re.compile(r"agg\d+_\d+_(.+)"),
                re.compile(r"nomerge\d+_(.+)"), re.compile(r"mesh_mat\d+_(.+)"),
                re.compile(r"mesh_(overlay\d+|blocklight\d+_(?:no)?shadow)"))


def world_node_model_name(model_path: str) -> str:
    """A readable name for a model the map compiler generated: the material (or overlay, light blocker) it was
    made for, without the compiler's bookkeeping. Other models keep their file name."""
    path = TinyPath(model_path)
    stem = path.stem
    if "worldnodes" not in path.as_posix().split("/") or (prefix := _COMPILER_PREFIX.match(stem)) is None:
        return stem
    name = _RENDER_FLAGS.sub("", stem[prefix.end():])
    for kind in _MODEL_KINDS:
        if match := kind.fullmatch(name):
            return match[1]
    return name or stem


def get_entity_name(entity_data: dict[str, Any]):
    return f'{entity_data.get("targetname", entity_data.get("hammeruniqueid", "missing_hammer_id"))}'


def load_map(map_resource: CompiledMapResource, cm: ContentManager, scale: float = SOURCE2_HAMMER_UNIT_TO_METERS):
    world_resource = find_world(map_resource, cm)
    if world_resource is None:
        return None
    return import_world(world_resource, map_resource, cm, scale)


def find_world(map_resource: CompiledMapResource, cm: ContentManager) -> CompiledWorldResource | None:
    manifest_resource_path = next(filter(lambda a: a.endswith(".vrman"), map_resource.get_child_resources()), None)
    if manifest_resource_path is not None:
        manifest_resource = map_resource.get_child_resource(manifest_resource_path, cm, CompiledManifestResource)
        world_resource_path = next(
            filter(lambda a: isinstance(a, str) and a.endswith(".vwrld"), manifest_resource.get_child_resources()),
            None)
        if world_resource_path is not None:
            return manifest_resource.get_child_resource(world_resource_path, cm, CompiledWorldResource)

    world_resource_path = next(filter(lambda a: a.endswith(".vwrld"), map_resource.get_child_resources()), None)
    if world_resource_path is not None:
        return map_resource.get_child_resource(world_resource_path, cm, CompiledWorldResource)
    return None


def cheap_path_check(resource_id: str | int, content_manager: ContentManager, resource: CompiledResource):
    if isinstance(resource_id, str):
        res_path = TinyPath(resource_id + "_c")
        if content_manager.check(res_path):
            return TinyPath(resource_id + "_c")
    return resource.get_child_resource_path(resource_id)


def import_world(world_resource: CompiledWorldResource, map_resource: CompiledMapResource,
                 content_manager: ContentManager, scale=SOURCE2_HAMMER_UNIT_TO_METERS):
    map_name = map_resource.name
    master_collection = get_or_create_collection(map_name, bpy.context.scene.collection)
    with pause_view_layer_update():
        load_world_nodes(world_resource, map_resource, content_manager, master_collection, scale)
        load_entities(world_resource, master_collection, scale, content_manager)
        load_skyboxes(world_resource, master_collection, scale, content_manager)


def load_world_nodes(world_resource: CompiledWorldResource, map_resource: CompiledMapResource,
                     content_manager: ContentManager, master_collection: bpy.types.Collection, scale: float):
    data_block = world_resource.data_block
    uv_scale:list[float]|None = None
    if data_block:
        if "m_worldLightingInfo" in data_block:
            uv_scale_prop = data_block["m_worldLightingInfo"].get("m_vLightmapUvScale", None)
            if uv_scale_prop is not None:
                uv_scale = uv_scale_prop.tolist()
    if uv_scale is None:
        uv_scale = [1., 1.]

    for node_prefix in world_resource.get_worldnode_prefixes():
        node_resource = map_resource.get_worldnode(node_prefix, content_manager)
        if node_resource is None:
            raise RequiredFileNotFound("Failed to find WorldNode resource")
        collection = get_or_create_child_collection(f"static_props_{TinyPath(node_prefix).name}",
                                                    master_collection)
        for scene_object in node_resource.get_scene_objects():
            renderable_model = scene_object["m_renderableModel"]
            proper_path = cheap_path_check(renderable_model, content_manager, node_resource)
            if (transform := scene_object.get('m_vTransform', None)) is not None:
                matrix = Matrix(transform).to_4x4()
            else:
                matrix = Matrix.Identity(4)
            if proper_path and LIGHT_BLOCKER.search(TinyPath(proper_path).stem):
                shadow_casters = get_or_create_child_collection(SHADOW_CASTERS_COLLECTION, master_collection)
                create_static_prop_placeholder(scene_object, proper_path, matrix, shadow_casters, scale, uv_scale,
                                               shadow_only=True)
            else:
                create_static_prop_placeholder(scene_object, proper_path, matrix, collection, scale, uv_scale)
        for scene_object in node_resource.get_aggregate_scene_objects():
            renderable_model = scene_object["m_renderableModel"]
            proper_path = cheap_path_check(renderable_model, content_manager, node_resource)
            if scene_object["m_fragmentTransforms"] or scene_object["m_aggregateMeshes"]:
                fragments = []
                transforms: list | None = scene_object.get("m_fragmentTransforms", None)
                for i, draw_info in enumerate(scene_object["m_aggregateMeshes"]):
                    if draw_info.get("m_bHasTransform", False) and transforms is not None:
                        matrix = Matrix(transforms[i].reshape(3, 4)).to_4x4()
                    else:
                        matrix = Matrix.Identity(4)

                    transform_mat = matrix.to_4x4()
                    loc, rot, scl = transform_mat.decompose()
                    loc *= scale
                    matrix = Matrix.LocRotScale(loc, rot, scl)

                    fragment = {
                        "draw_call": draw_info["m_nDrawCallIndex"],
                        "tint_color": draw_info.get('m_vTintColor', [255, 255, 255]),
                        "matrix": list(matrix)
                    }
                    fragments.append(fragment)
                create_aggregate_prop_placeholder(scene_object, proper_path, fragments, collection, scale, uv_scale)
            else:
                create_static_prop_placeholder(scene_object, proper_path, None, collection, scale, uv_scale)
    hide_shadow_casters(master_collection)


def hide_shadow_casters(master_collection: bpy.types.Collection):
    """Light blockers are hidden in the viewport (the collection's eye shows them); once loaded they cast shadows
    in renders and are otherwise invisible, see ``make_shadow_only``."""
    for child in master_collection.children:
        if child.name.split(".")[0] != SHADOW_CASTERS_COLLECTION:
            continue
        layer_collection = find_layer_collection(bpy.context.view_layer.layer_collection, child.name)
        if layer_collection is not None:
            layer_collection.hide_viewport = True


def create_static_prop_placeholder(scene_object: Object, proper_path: TinyPath | None, matrix: Matrix | None,
                                   collection: bpy.types.Collection, scale: float, uv_scale: list[float],
                                   shadow_only: bool = False):
    if not proper_path:
        return

    custom_data = {'prop_path': str(proper_path),
                   'type': 'static_prop',
                   'scale': scale,
                   'uv_scale': uv_scale,
                   'entity': {k: str(v) for (k, v) in scene_object.to_dict().items()},
                   'tint_color': scene_object.get('m_vTintColor', [1.0, 1.0, 1.0, 1.0]),
                   'skin': scene_object.get('skin', 'default') or 'default'}
    if shadow_only:
        custom_data['shadow_only'] = True
    empty = create_empty(world_node_model_name(proper_path), scale, custom_data=custom_data)
    if matrix is not None:
        transform_mat = matrix.to_4x4()
        loc, rot, scl = transform_mat.decompose()
        loc *= scale
        empty.matrix_world = Matrix.LocRotScale(loc, rot, scl)
    collection.objects.link(empty)


def create_aggregate_prop_placeholder(scene_object: Object, proper_path: TinyPath | None,
                                      fragments: list[dict[str, int | Matrix]],
                                      collection: bpy.types.Collection, scale: float,
                                      uv_scale: list[float]):
    if not proper_path:
        return

    custom_data = {'prop_path': str(proper_path),
                   'type': 'aggregate_static_prop',
                   'scale': scale,
                   'uv_scale': uv_scale,
                   'entity': {k: str(v) for (k, v) in scene_object.items() if
                              k not in ["m_fragmentTransforms", "m_aggregateMeshes"]},
                   'fragments': fragments,
                   'skin': scene_object.get('skin', 'default') or 'default'}
    empty = create_empty(world_node_model_name(proper_path), scale, custom_data=custom_data)
    collection.objects.link(empty)


def create_empty(name: str, scale: float, custom_data=None):
    placeholder = bpy.data.objects.new(name, None)
    placeholder.empty_display_size = 16 * scale
    placeholder['entity_data'] = custom_data
    return placeholder


def get_entity_handler(cm: ContentManager) -> Type[BaseEntityHandler]:
    if cm.steam_id == SteamAppId.HALF_LIFE_ALYX:
        return HLVREntityHandler
    elif cm.steam_id == SteamAppId.SBOX_STEAM_ID:
        return SBoxEntityHandler
    # elif cm.steam_id == 890 and 'steampal' in cm.content_providers:
    #     return SteamPalEntityHandler
    elif cm.steam_id == SteamAppId.COUNTER_STRIKE_GO:
        return CS2EntityHandler
    elif cm.steam_id == SteamAppId.DEADLOCK:
        return DeadlockEntityHandler
    return BaseEntityHandler


def entity_values(entity: dict) -> dict:
    # Newer lumps wrap each entity's keys in {"version": 1, "values": {...}, "attributes": {...}}
    return entity["values"] if "values" in entity else entity


def iter_entity_lumps(world_resource: CompiledWorldResource,
                      cm: ContentManager) -> Iterator[CompiledEntityLumpResource]:
    def walk(lump: CompiledEntityLumpResource):
        yield lump
        for child in lump.get_child_lumps(cm):
            yield from walk(child)

    for entity_lump in world_resource.data_block["m_entityLumps"]:
        if isinstance(entity_lump, NullObject):
            continue
        yield from walk(world_resource.get_child_resource(entity_lump, cm, CompiledEntityLumpResource))


def load_entities(world_resource: CompiledWorldResource, collection: bpy.types.Collection,
                  scale: float, cm: ContentManager, skipped_classes: frozenset[str] = frozenset()):
    handler_class = get_entity_handler(cm)
    for entity_resource in iter_entity_lumps(world_resource, cm):
        entities = [entity for entity in entity_resource.get_entities()
                    if entity_values(entity).get("classname") not in skipped_classes]
        handler_class(entities, collection, cm, scale).load_entities()


def load_skyboxes(world_resource: CompiledWorldResource, collection: bpy.types.Collection,
                  scale: float, cm: ContentManager):
    """Import the 3D skybox of every ``skybox_reference``.

    The skybox is a map of its own (CS2 ships ``maps/prefabs/de_dust2/de_dust2_skybox.vmap`` as
    ``maps/prefabs/de_dust2/de_dust2_skybox.vpk``), built at ``1 / scale`` of its ``sky_camera``, which stands
    for the referencing map's origin, as in Source 1.
    """
    references = [entity_values(entity) for lump in iter_entity_lumps(world_resource, cm)
                  for entity in lump.get_entities()]
    for reference in references:
        if reference.get("classname") != "skybox_reference" or not reference.get("targetMapName"):
            continue
        target = TinyPath(reference["targetMapName"])
        sky_map = open_skybox_map(target, cm)
        sky_world = find_world(sky_map, cm) if sky_map is not None else None
        if sky_world is None:
            logger.warn(f"3D skybox {target.as_posix()} not found")
            continue
        sky_camera = next((values for lump in iter_entity_lumps(sky_world, cm) for entity in lump.get_entities()
                           if (values := entity_values(entity)).get("classname") == "sky_camera"), None)
        matrix = skybox_matrix(reference, sky_camera, scale)

        sky_collection = get_or_create_child_collection(target.stem, collection)
        load_world_nodes(sky_world, sky_map, cm, sky_collection, scale)
        load_entities(sky_world, sky_collection, scale, cm, SKYBOX_SKIPPED_CLASSES)
        for obj in sky_collection.all_objects:
            if obj.parent is None:
                obj.matrix_basis = matrix @ obj.matrix_basis


def open_skybox_map(target: TinyPath, cm: ContentManager) -> CompiledMapResource | None:
    """Mount the skybox's ``.vpk``, a loose file next to the map ones, and open its compiled map."""
    stem = target.with_suffix("").as_posix()
    map_path = TinyPath(stem + ".vmap_c")
    if not cm.check(map_path):
        vpk_buffer = cm.find_file(TinyPath(stem + ".vpk"), do_not_cache=True)
        if vpk_buffer is None:
            return None
        if not isinstance(vpk_buffer, FileBuffer):
            # A VPK packed in another archive; VPKContentProvider reads from disk.
            logger.warn(f"{stem}.vpk is inside an archive, which can't be mounted")
            return None
        vpk_path = TinyPath(vpk_buffer.name)
        vpk_buffer.close()
        cm.add_child(VPKContentProvider(vpk_path))
    buffer = cm.find_file(map_path)
    if buffer is None:
        return None
    return CompiledMapResource.from_buffer(buffer, map_path)


def skybox_matrix(reference: dict, sky_camera: dict | None, scale: float) -> Matrix:
    """Where a skybox map's contents go: ``T(reference) R(reference) S(reference) S(sky scale) T(-sky_camera)``.

    Translations are in Blender units (Hammer units times ``scale``). Without a ``sky_camera`` the map is placed
    as it is, at the reference.
    """
    angles = get_angles(reference)
    rotation = Euler((math.radians(angles[2]), math.radians(angles[0]), math.radians(angles[1])))
    reference_scale = parse_float_vector(reference.get("scales", "1 1 1"))
    matrix = Matrix.LocRotScale(Vector(get_origin(reference)) * scale, rotation, Vector(reference_scale))
    if sky_camera is not None:
        sky_scale = float(sky_camera.get("scale", 16))
        matrix = matrix @ Matrix.Scale(sky_scale, 4) @ Matrix.Translation(-Vector(get_origin(sky_camera)) * scale)
    return matrix
