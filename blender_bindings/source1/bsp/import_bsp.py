import json
import re
from typing import Any, Optional, Type

import bpy
import numpy as np

from .entities.abstract_entity_handlers import AbstractEntityHandler
from .entities.quake3.quake3_entity_handler import QuakeEntityHandler
from .entities.quake3.sof_entity_handler import RavenQ3EntityHandler
from ...material_loader.shaders.idtech3.idtech3 import IdTech3Shader
from ...operators.import_settings_base import Source1BSPSettings
from .entities.quake3.swjk2 import StarWarsJediKnights2
from ...utils.fast_mesh import FastMesh
from ....library.shared.app_id import SteamAppId
from ....library.shared.content_manager import ContentManager
from ....library.source1.bsp.bsp_file import open_bsp, VBSPFile
from ....library.source1.bsp.datatypes.static_prop_lump import StaticPropLump
from ....library.source1.bsp.datatypes.face import Face
from ....library.source1.bsp.datatypes.texture_data import TextureData
from ....library.source1.bsp.datatypes.texture_info import TextureInfo
from ....library.source1.bsp.geometry import OverlayBuilder, displacement_mesh
from ....library.source1.bsp.lumps import *
from ....library.source1.bsp.lumps.texture_lump import Quake3TextureInfoLump
from ....library.source1.vmt import VMT
from ....library.utils import Buffer, TinyPath, path_stem, strip_vmt_extension, SOURCE1_HAMMER_UNIT_TO_METERS
from ....library.utils.idtech3_shader_parser import parse_shader_materials
from ....library.utils.math_utilities import convert_rotation_source1_to_blender
from ....logger import SourceLogMan, SLogger
from ...material_loader.material_loader import ShaderRegistry
from ...material_loader.shaders.source1_shader_base import Source1ShaderBase
from ...utils.bpy_utils import add_material, get_or_create_collection, get_or_create_material

from .entities.base_entity_handler import BaseEntityHandler
from .entities.bms_entity_handlers import BlackMesaEntityHandler
from .entities.csgo_entity_handlers import CSGOEntityHandler
from .entities.halflife2_entity_handler import HalfLifeEntityHandler
from .entities.left4dead2_entity_handlers import Left4dead2EntityHandler
from .entities.portal2_entity_handlers import Portal2EntityHandler
from .entities.p2ce_entity_handlers import Portal2CEEntityHandler
from .entities.portal_entity_handlers import PortalEntityHandler
from .entities.tf2_entity_handler import TF2EntityHandler
from .entities.titanfall_entity_handler import TitanfallEntityHandler
from .entities.vindictus_entity_handler import VindictusEntityHandler
from .entities.vampire_entity_handler import VampireEntityHandler

strip_patch_coordinates = re.compile(r"_-?\d+_-?\d+_-?\d+.*$")
log_manager = SourceLogMan()


def get_entity_name(entity_data: dict[str, Any]):
    return f'{entity_data.get("targetname", entity_data.get("hammerid", "missing_hammer_id"))}'


def import_bsp(map_path: TinyPath, buffer: Buffer, content_manager: ContentManager, settings: Source1BSPSettings,
               override_steamappid: Optional[SteamAppId] = None):
    logger = log_manager.get_logger(map_path.name)
    logger.info(f'Loading map "{map_path}"')
    bsp = open_bsp(map_path, buffer, content_manager, override_steamappid)
    if bsp is None:
        raise Exception("Could not open map file. This function can only load Source1 BSP files.")

    pak_lump: Optional[PakLump] = bsp.get_lump('LUMP_PAK')
    if pak_lump:
        content_manager.add_child(pak_lump)

    master_collection = bpy.data.collections.new(map_path.name)
    bpy.context.scene.collection.children.link(master_collection)
    import_entities(bsp, content_manager, settings, master_collection, logger)
    import_cubemaps(bsp, settings, master_collection, logger)
    import_static_props(bsp, settings, master_collection, logger)
    import_materials(bsp, content_manager, settings, logger)
    import_disp(bsp, settings, master_collection, logger)
    import_overlays(bsp, settings, master_collection, logger)


def import_entities(bsp: VBSPFile, content_manager: ContentManager, settings: Source1BSPSettings,
                    master_collection: bpy.types.Collection, logger: SLogger):
    info = bsp.info
    steam_id = info.steam_app_id

    handler_class: Type[AbstractEntityHandler]
    if steam_id == SteamAppId.TEAM_FORTRESS_2:
        handler_class = TF2EntityHandler
    elif steam_id == SteamAppId.SOURCE_FILMMAKER:  # SFM
        handler_class = TF2EntityHandler
    elif steam_id == SteamAppId.BLACK_MESA:  # BlackMesa
        handler_class = BlackMesaEntityHandler
    elif steam_id == SteamAppId.COUNTER_STRIKE_GO:  # CS:GO
        handler_class = CSGOEntityHandler
    elif steam_id == SteamAppId.LEFT_4_DEAD_2:
        handler_class = Left4dead2EntityHandler
    elif steam_id == SteamAppId.VAMPIRE_THE_MASQUERADE_BLOODLINES or info.version == 17:
        handler_class = VampireEntityHandler
    elif steam_id == SteamAppId.PORTAL_2 and info.version == 29:  # Titanfall
        handler_class = TitanfallEntityHandler
    elif steam_id == SteamAppId.PORTAL:
        handler_class = PortalEntityHandler
    elif (steam_id in [SteamAppId.PORTAL_2, SteamAppId.THINKING_WITH_TIME_MACHINE, SteamAppId.PORTAL_STORIES_MEL]
          and info.version != 29):  # Portal 2
        handler_class = Portal2EntityHandler
    elif steam_id == SteamAppId.PORTAL_2_CE:
        handler_class = Portal2CEEntityHandler
    elif steam_id in (SteamAppId.HALF_LIFE_2, SteamAppId.HALF_LIFE_2_EP_1, SteamAppId.HALF_LIFE_2_EP_2,
                      SteamAppId.HALF_LIFE_2_LOST_COAST, SteamAppId.HALF_LIFE_2_DEATHMATCH,
                      SteamAppId.COUNTER_STRIKE_SOURCE, SteamAppId.GARRYS_MOD):
        # HL2 and everything built directly on it: Lost Coast, HL2:DM, CS:S and
        # Garry's Mod all ship HL2's entities and previously fell through to
        # BaseEntityHandler with an "unrecognized game" warning.
        handler_class = HalfLifeEntityHandler
    elif steam_id == SteamAppId.VINDICTUS:
        handler_class = VindictusEntityHandler
    elif steam_id == SteamAppId.QUAKE3:
        handler_class = QuakeEntityHandler
    elif steam_id == SteamAppId.STAR_WARS_JEDI_KNIGHTS2:
        handler_class = StarWarsJediKnights2
    elif steam_id == SteamAppId.RAVEN_Q3_ENGINE:
        handler_class = RavenQ3EntityHandler
    else:
        logger.warn("Unrecognized game! Using default behaviour for handing entities, this may not work!")
        handler_class = BaseEntityHandler
    logger.info(f"Using {handler_class.__name__} entity handler")
    entity_handler = handler_class(bsp, content_manager, master_collection, settings.scale, settings.light_scale)

    entity_lump: Optional[EntityLump] = bsp.get_lump('LUMP_ENTITIES')
    if entity_lump:
        entities_json = bpy.data.texts.new(f'{bsp.filepath.stem}_entities.json')
        json.dump(entity_lump.entities, entities_json, indent=1)
    entity_handler.load_entities(settings)


def import_cubemaps(bsp: VBSPFile, settings: Source1BSPSettings, master_collection: bpy.types.Collection,
                    logger: SLogger):
    if not settings.import_cubemaps:
        return
    cubemap_lump: Optional[CubemapLump] = bsp.get_lump('LUMP_CUBEMAPS')
    if not cubemap_lump:
        return
    parent_collection = get_or_create_collection('cubemaps', master_collection)
    for n, cubemap in enumerate(cubemap_lump.cubemaps):
        refl_probe = bpy.data.lightprobes.new(f"CUBEMAP_{n}_PROBE", 'SPHERE')
        obj = bpy.data.objects.new(f"CUBEMAP_{n}", refl_probe)
        obj.location = cubemap.origin
        obj.location *= settings.scale
        refl_probe.influence_distance = (cubemap.size or 1) * SOURCE1_HAMMER_UNIT_TO_METERS * settings.scale * 10000
        parent_collection.objects.link(obj)


def import_static_props(bsp: VBSPFile, settings: Source1BSPSettings, master_collection: bpy.types.Collection,
                        logger: SLogger):
    gamelump: Optional[GameLump] = bsp.get_lump('LUMP_GAME_LUMP')
    if gamelump and settings.load_static_props:
        static_prop_lump: StaticPropLump = gamelump.game_lumps.get('sprp', None)
        if static_prop_lump:
            parent_collection = get_or_create_collection('static_props', master_collection)
            for n, prop in enumerate(static_prop_lump.static_props):
                model_name = static_prop_lump.model_names[prop.prop_type]
                placeholder = bpy.data.objects.new(f'static_prop_{n}', None)
                placeholder.location = np.multiply(prop.origin, settings.scale)
                placeholder.rotation_euler = convert_rotation_source1_to_blender(prop.rotation)
                placeholder.scale = prop.scaling

                placeholder.scale *= settings.scale
                placeholder.empty_display_size = 16

                entity = {
                    'type': 'static_prop',
                    'origin': '{} {} {}'.format(*prop.origin),
                    'angles': '{} {} {}'.format(*prop.rotation),
                    'scale': '{} {} {}'.format(*prop.scaling),
                    'skin': str(prop.skin),
                }

                if prop.diffuse_modulation:
                    tint = [a / 255.0 for a in prop.diffuse_modulation]
                    entity['tint'] = f'{tint[0]} {tint[1]} {tint[2]} {1.0}'

                placeholder['entity_data'] = {'parent_path': str(bsp.filepath.parent),
                                              'prop_path': model_name,
                                              'scale': settings.scale,
                                              'type': 'static_props',
                                              'skin': str(prop.skin),
                                              'entity': entity
                                              }
                parent_collection.objects.link(placeholder)


def import_materials(bsp: VBSPFile, content_manager: ContentManager, settings: Source1BSPSettings, logger: SLogger):
    if not settings.import_textures:
        return
    Source1ShaderBase.use_bvlg(settings.use_bvlg)

    strings_lump: Optional[StringsLump] = bsp.get_lump('LUMP_TEXDATA_STRING_TABLE')
    texture_data_lump: Optional[TextureDataLump] = bsp.get_lump('LUMP_TEXDATA')
    texture_info_lump: Optional[Quake3TextureInfoLump | TextureInfoLump] = bsp.get_lump('LUMP_TEXINFO')
    shaders_lump: Optional[ShadersLump] = bsp.get_lump('LUMP_SHADERS')

    def import_source1_materials():
        pak_lump: Optional[PakLump] = bsp.get_lump('LUMP_PAK')
        if pak_lump:
            content_manager.add_child(pak_lump)
        for texture_data in texture_data_lump.texture_data:
            material_name = strings_lump.strings[texture_data.name_id] or "NO_NAME"
            material_name = strip_vmt_extension(material_name.lstrip("/\\"))
            tmp = strip_patch_coordinates.sub("", material_name)

            mat = get_or_create_material(path_stem(tmp), tmp)

            if mat.get('source1_loaded'):
                logger.debug(
                    f'Skipping loading of {tmp} as it already loaded')
                continue
            logger.info(f"Loading {material_name} material")
            material_path = TinyPath("materials") / (material_name + ".vmt")
            material_file = content_manager.find_file(material_path)

            if material_file:
                try:
                    Source1ShaderBase.use_bvlg(settings.use_bvlg)
                    vmt = VMT(material_file, material_path, content_manager)
                    ShaderRegistry.source1_create_nodes(content_manager, mat, vmt, {})
                except Exception as e:
                    logger.exception("Failed to load material due to exception:", e)
            else:
                logger.error(f'Failed to find {material_name} material')

    def import_idtech3_materials():
        material_definitions = {}
        for _, buffer in content_manager.glob("*.shader"):
            materials = parse_shader_materials(buffer.read(-1).decode("utf-8"))
            material_definitions.update(materials)

        for shaders in shaders_lump.shaders:
            material_name = shaders.name
            mat = get_or_create_material(path_stem(material_name), material_name)

            if mat.get('source1_loaded'):
                logger.debug(
                    f'Skipping loading of {material_name} as it already loaded')
                continue
            logger.info(f"Loading {material_name} material")

            if material_name in material_definitions:
                material_params = material_definitions[material_name]
            else:
                material_params = {'textures': [{"map": material_name}]}
            if mat.get('source1_loaded'):
                logger.debug(
                    f'Skipping loading of {material_name} as it already loaded')
                continue
            logger.info(f"Loading {material_name} material")

            loader = IdTech3Shader(content_manager)
            loader.create_nodes(mat, material_params)

    def import_quake3_materials():
        material_definitions = {}
        for _, buffer in content_manager.glob("*.shader"):
            materials = parse_shader_materials(buffer.read(-1).decode("utf-8"))
            material_definitions.update(materials)

        for texture in texture_info_lump.texture_info:
            material_name = texture.name
            mat = get_or_create_material(path_stem(material_name), material_name)
            if material_name in material_definitions:
                material_params = material_definitions[material_name]
            else:
                material_params = {'textures': [{"map": material_name}]}
            if mat.get('source1_loaded'):
                logger.debug(
                    f'Skipping loading of {material_name} as it already loaded')
                continue
            logger.info(f"Loading {material_name} material")

            loader = IdTech3Shader(content_manager)
            loader.create_nodes(mat, material_params)

    if strings_lump and texture_data_lump:
        import_source1_materials()
    elif shaders_lump:
        import_idtech3_materials()
    elif texture_info_lump and isinstance(texture_info_lump, Quake3TextureInfoLump):
        import_quake3_materials()


def get_tex_info(face: Face, bsp: VBSPFile):
    tex_info_lump: TextureInfoLump = bsp.get_lump('LUMP_TEXINFO')
    if tex_info_lump:
        return tex_info_lump.texture_info[face.tex_info_id]
    return None


def get_texture_data(tex_info: TextureInfo, bsp: VBSPFile) -> Optional[TextureData]:
    tex_data_lump: TextureDataLump = bsp.get_lump('LUMP_TEXDATA')
    if tex_data_lump:
        tex_datas = tex_data_lump.texture_data
        return tex_datas[tex_info.texture_data_id]
    return None


def import_disp(bsp: VBSPFile, settings: Source1BSPSettings,
                master_collection: bpy.types.Collection, logger: SLogger):
    disp_info_lump: Optional[DispInfoLump] = bsp.get_lump('LUMP_DISPINFO')
    if not disp_info_lump or not disp_info_lump.infos:
        return

    disp_multiblend: Optional[DispMultiblendLump] = bsp.get_lump('LUMP_DISP_MULTIBLEND')
    strings_lump: Optional[StringsLump] = bsp.get_lump('LUMP_TEXDATA_STRING_TABLE')
    disp_verts_lump: Optional[DispVertLump] = bsp.get_lump('LUMP_DISP_VERTS')

    parent_collection = get_or_create_collection('displacements', master_collection)
    info_count = len(disp_info_lump.infos)
    multiblend_offset = 0
    for n, disp_info in enumerate(disp_info_lump.infos):
        logger.info(f'Processing {n + 1}/{info_count} displacement face')
        final_vertex_colors = {}
        src_face = disp_info.get_source_face(bsp)

        texture_info = get_tex_info(src_face, bsp)
        texture_data = get_texture_data(texture_info, bsp)
        tv1, tv2 = texture_info.texture_vectors

        disp_mesh = displacement_mesh(bsp, disp_info)
        disp_indices = disp_mesh.disp_vertex_ids
        subdiv_vert_count = len(disp_indices)
        disp_uv = np.zeros((subdiv_vert_count, 2), dtype=np.float32)
        disp_uv[:, 0] = (np.dot(disp_mesh.flat_positions, tv1[:3]) + tv1[3]) / texture_data.view_width
        disp_uv[:, 1] = 1 - ((np.dot(disp_mesh.flat_positions, tv2[:3]) + tv2[3]) / texture_data.view_height)

        disp_vertices_alpha = disp_verts_lump.vertices['alpha'][disp_indices] / 255
        final_vertex_colors['vertex_alpha'] = np.ones((disp_vertices_alpha.shape[0],4))
        final_vertex_colors['vertex_alpha'][:, 3:] = disp_vertices_alpha.reshape((disp_vertices_alpha.shape[0], 1))

        if disp_multiblend and disp_info.has_multiblend:
            multiblend_layers = disp_multiblend.blends[multiblend_offset:multiblend_offset + subdiv_vert_count]
            # m_vMultiBlend is (w1, w2, w3, w4) and maps straight onto RGBA. CS:GO's
            # lightmapped_4wayblend_ps20b.fxc reads only .g/.b/.a for layers 2/3/4:
            #     blendfactor1 = i.vertexBlend.g * lum + i.vertexBlend.g;   // layer 2
            #     blendfactor2 = i.vertexBlend.b * lum + i.vertexBlend.b;   // layer 3
            #     blendfactor3 = i.vertexBlend.a * lum + i.vertexBlend.a;   // layer 4
            # .r (layer 1) is never sampled -- layer 1 is the base that the lerp
            # chain starts from. Swapping R and A here used to overwrite the layer-4
            # weight with the unused layer-1 one, so layer 4 never blended in.
            final_vertex_colors['multiblend'] = multiblend_layers['multiblend'].copy()

            final_vertex_colors['alphablend'] = multiblend_layers['alphablend']
            miltiblend_color_layer = multiblend_layers['multiblend_colors']
            shape_ = multiblend_layers.shape[0]
            final_vertex_colors['multiblend_color0'] = np.concatenate((miltiblend_color_layer[:, 0, :],
                                                                       np.ones((shape_, 1))),
                                                                      axis=1)
            final_vertex_colors['multiblend_color1'] = np.concatenate((miltiblend_color_layer[:, 1, :],
                                                                       np.ones((shape_, 1))),
                                                                      axis=1)
            final_vertex_colors['multiblend_color2'] = np.concatenate((miltiblend_color_layer[:, 2, :],
                                                                       np.ones((shape_, 1))),
                                                                      axis=1)
            final_vertex_colors['multiblend_color3'] = np.concatenate((miltiblend_color_layer[:, 3, :],
                                                                       np.ones((shape_, 1))),
                                                                      axis=1)
            multiblend_offset += subdiv_vert_count
        mesh_data = FastMesh.new(f"{bsp.filepath.stem}_disp_{disp_info.map_face}_MESH")
        mesh_obj = bpy.data.objects.new(f"{bsp.filepath.stem}_disp_{disp_info.map_face}", mesh_data)
        if parent_collection is not None:
            parent_collection.objects.link(mesh_obj)
        else:
            master_collection.objects.link(mesh_obj)
        mesh_data.from_pydata((disp_mesh.positions * settings.scale).astype(np.float32), [],
                              disp_mesh.triangles)

        uv_data = mesh_data.uv_layers.new().data
        vertex_indices = np.zeros((len(mesh_data.loops, )), dtype=np.uint32)
        mesh_data.loops.foreach_get('vertex_index', vertex_indices)
        uv_data.foreach_set('uv', disp_uv[vertex_indices].flatten())

        for name, vertex_color_layer in final_vertex_colors.items():
            vertex_colors = mesh_data.vertex_colors.get(name, False) or mesh_data.vertex_colors.new(name=name)
            vertex_colors_data = vertex_colors.data
            vertex_colors_data.foreach_set('color', vertex_color_layer[vertex_indices].flatten())

        material_name = strings_lump.strings[texture_data.name_id] or "NO_NAME"
        material_name = strip_patch_coordinates.sub("", material_name)
        add_material(get_or_create_material(path_stem(material_name), material_name), mesh_obj)
        mesh_data.validate(clean_customdata=False)


def import_overlays(bsp: VBSPFile, settings: Source1BSPSettings,
                    master_collection: bpy.types.Collection, logger: SLogger):
    """info_overlay entities: VBSP compiles them into LUMP_OVERLAYS and drops the entities."""
    if not getattr(settings, 'load_overlays', True):
        return
    overlay_lump: Optional[OverlayLump] = bsp.get_lump('LUMP_OVERLAYS')
    strings_lump: Optional[StringsLump] = bsp.get_lump('LUMP_TEXDATA_STRING_TABLE')
    if not overlay_lump or not overlay_lump.overlays or not strings_lump:
        return
    texture_info_lump: TextureInfoLump = bsp.get_lump('LUMP_TEXINFO')
    texture_data_lump: TextureDataLump = bsp.get_lump('LUMP_TEXDATA')

    builder = OverlayBuilder(bsp)
    parent_collection = get_or_create_collection('overlays', master_collection)
    created = 0
    for overlay in overlay_lump.overlays:
        mesh = builder.build(overlay)
        if not len(mesh.polygon_sizes):
            logger.debug(f'Overlay {overlay.id} does not touch any of its faces')
            continue
        texture_data = texture_data_lump.texture_data[texture_info_lump.texture_info[overlay.tex_info].texture_data_id]
        material_name = strings_lump.strings[texture_data.name_id] or "NO_NAME"
        material_name = strip_patch_coordinates.sub("", strip_vmt_extension(material_name.lstrip("/\\")))

        loop_starts = np.concatenate([[0], np.cumsum(mesh.polygon_sizes)[:-1]])
        polygons = [range(start, start + size) for start, size in zip(loop_starts, mesh.polygon_sizes)]
        mesh_data = bpy.data.meshes.new(f"overlay_{overlay.id}_MESH")
        mesh_data.from_pydata((mesh.positions * settings.scale).tolist(), [], polygons)
        # Loops follow the vertex order, so the texture coordinates line up one-to-one.
        uvs = mesh.texcoords.copy()
        uvs[:, 1] = 1.0 - uvs[:, 1]
        mesh_data.uv_layers.new().data.foreach_set('uv', uvs.astype(np.float32).ravel())
        mesh_data.validate()

        mesh_obj = bpy.data.objects.new(f"overlay_{overlay.id}_{path_stem(material_name)}", mesh_data)
        mesh_obj['overlay_id'] = overlay.id
        mesh_obj['render_order'] = overlay.render_order
        # A decal lifted a fraction of a unit off the wall would only shade the wall under it.
        mesh_obj.visible_shadow = False
        add_material(get_or_create_material(path_stem(material_name), material_name), mesh_obj)
        parent_collection.objects.link(mesh_obj)
        created += 1
    logger.info(f'Imported {created}/{len(overlay_lump.overlays)} overlays')
    # def load_physics(self):
    #     physics_lump: PhysicsLump = self.map_file.get_lump('LUMP_PHYSICS')
    #     if not physics_lump or not physics_lump.solid_blocks:
    #         return
    #     parent_collection = get_or_create_collection('physics', self.main_collection)
    #     solid_blocks = physics_lump.solid_blocks
    #     for sb_id, solid_block in solid_blocks.items():
    #         for s_id, solid in enumerate(solid_block.solids):
    #             mesh_obj = bpy.data.objects.new(f"physics_{sb_id}_{s_id}",
    #                                             bpy.data.meshes.new(f"physics_{sb_id}_{s_id}_MESH"))
    #             mesh_data = mesh_obj.data
