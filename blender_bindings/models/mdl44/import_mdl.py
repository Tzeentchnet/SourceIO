from ..materials import get_model_material_names
import math
from collections import defaultdict
from typing import Union

import bpy
import numpy as np
from mathutils import Euler, Matrix, Quaternion, Vector

from ..common import assign_bone_collections, merge_meshes, create_eyeballs, generate_wrinkle_map_node_group, make_bodygroup_selectors, create_flex_drivers
from ...shared.model_container import ModelContainer
from ...operators.import_settings_base import ModelOptions
from ..import_animations import set_pose
from ...utils.bpy_utils import add_material, edit_armature, get_or_create_material
from ...utils.fast_mesh import FastMesh, set_vertex_weights
from ....library.models.mdl.structs.header import StudioHDRFlags
from ....library.models.mdl.structs.local_animation import AnimDescFlags
from ....library.models.mdl.v44.mdl_file import MdlV44
from ....library.models.mdl.v44.vertex_animation_cache import preprocess_vertex_animation
from ....library.models.mdl.v49.flex_expressions import *
from ....library.models.vtx.v7.vtx import Vtx
from ....library.models.vvd import Vvd
from ....library.shared.content_manager import ContentManager
from ....library.utils.common import get_slice
from ....library.utils.path_utilities import path_stem
from ....logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('Source1::ModelLoader')


def create_armature(mdl: MdlV44, scale=1.0, load_refpose=False):
    if mdl.header.flags & StudioHDRFlags.STATIC_PROP != 0:
        return
    model_name = path_stem(mdl.header.name)
    armature = bpy.data.armatures.new(f"{model_name}_ARM_DATA")
    armature_obj = bpy.data.objects.new(f"{model_name}_ARM", armature)
    armature_obj['MODE'] = 'SourceIO'
    armature_obj.show_in_front = True

    with edit_armature(armature_obj) as edit_bones:
        bl_bones = []
        for bone in mdl.bones:
            bl_bone = edit_bones.new(bone.name[:63])
            bl_bones.append(bl_bone)
            bl_bone.tail = Vector((0, 0, scale))
            x, y, z, w = bone.quat
            mat = Matrix.LocRotScale(Vector(bone.position) * scale, Quaternion((w, x, y, z)), (1, 1, 1))
            if bone.parent_id == -1:
                bl_bone.matrix = mat
            else:
                bl_bone.parent = bl_bones[bone.parent_id]
                bl_bone.matrix = bl_bones[bone.parent_id].matrix @ mat
        assign_bone_collections(armature, mdl.bones, bl_bones)

    if load_refpose:
        apply_reference_pose(armature_obj, mdl, scale)
    return armature_obj


def apply_reference_pose(armature_obj: bpy.types.Object, mdl: MdlV44, scale: float):
    """Pose the armature at the first frame of the model's first animation, unless it is a delta."""
    if not mdl.animations or mdl.animations[0] is None or mdl.anim_descs[0].flags & AnimDescFlags.DELTA:
        return
    set_pose(armature_obj, {name: track[0] for name, track in mdl.animations[0].items() if len(track)}, scale)


def import_model(content_manager: ContentManager, mdl: MdlV44, vtx: Vtx, vvd: Vvd,
                options: ModelOptions):
    full_material_names = get_model_material_names(content_manager, mdl)
    [setattr(mat, 'bpy_material', get_or_create_material(mat.name, full_material_names[mat.name])) for mat in mdl.materials if mat.bpy_material is None]
    # ensure all MaterialV49 has its bpy_material counterpart

    objects = []
    bodygroups = defaultdict(list)
    attachments = []
    extra_stuff = []
    desired_lod = 0
    all_vertices = vvd.lod_data[desired_lod]

    static_prop = mdl.header.flags & StudioHDRFlags.STATIC_PROP != 0
    armature = None
    vertex_anim_cache = preprocess_vertex_animation(mdl, vvd)
    vert_anim_fixed_point_scale = mdl.header.vert_anim_fixed_point_scale if (mdl.header.flags & StudioHDRFlags.VERT_ANIM_FIXED_POINT_SCALE !=0 ) else 1/4096

    scale = options.scale
    create_drivers = options.create_flex_drivers
    debug_stereo_balance = options.debug_stereo_balance

    if not static_prop:
        armature = create_armature(mdl, scale)

    for vtx_body_part, body_part in zip(vtx.body_parts, mdl.body_parts):
        for vtx_model, model in zip(vtx_body_part.models, body_part.models):

            if model.vertex_count == 0:
                continue
            mesh_name = f'{body_part.name}_{model.name}'

            mesh_data = FastMesh.new(f'{mesh_name}_MESH')
            mesh_obj = bpy.data.objects.new(mesh_name, mesh_data)
            default_skin_groups = {str(n): list(map(lambda a: a.bpy_material.name, group)) for (n, group) in enumerate(mdl.skin_groups)}
            mesh_obj['active_skin'] = '0'
            mesh_obj['model_type'] = 's1'
            objects.append(mesh_obj)
            bodygroups[body_part.name].append(mesh_obj)
            mesh_obj['prop_path'] = path_stem(mdl.header.name)

            model_vertices = get_slice(all_vertices, model.vertex_offset, model.vertex_count)
            vtx_vertices, indices_array, material_indices_array = merge_meshes(model, vtx_model.model_lods[desired_lod])

            indices_array = np.array(indices_array, dtype=np.uint32)
            vertices = model_vertices[vtx_vertices]

            mesh_data.from_pydata(vertices['vertex'] * scale, [], np.flip(indices_array).reshape((-1, 3)), shade_flat=False)
            mesh_data.update()

            mesh_data.set_custom_normals(vertices['normal'])

            material_remapper = np.zeros((material_indices_array.max() + 1,), dtype=np.uint32)
            for mat_id in np.unique(material_indices_array):
                mat_name = mdl.materials[mat_id].name
                material = get_or_create_material(mat_name, full_material_names[mat_name])
                material_remapper[mat_id] = add_material(material, mesh_obj)

            skin_groups = {str(n): list(map(lambda a: a.bpy_material, group)) for (n, group) in enumerate(mdl.skin_groups)}
            try:
                mesh_obj['skin_groups'] = skin_groups
            except:
                mesh_obj['skin_groups'] = default_skin_groups

            mesh_data.polygons.foreach_set('material_index', material_remapper[material_indices_array[::-1]])

            uv_data = mesh_data.uv_layers.new()

            vertex_indices = np.zeros((len(mesh_data.loops, )), dtype=np.uint32)
            mesh_data.loops.foreach_get('vertex_index', vertex_indices)
            uvs = vertices['uv']
            uvs[:, 1] = 1 - uvs[:, 1]
            uv_data.data.foreach_set('uv', uvs[vertex_indices].flatten())

            if not static_prop:
                modifier = mesh_obj.modifiers.new(
                    type="ARMATURE", name="Armature")
                modifier.object = armature
                mesh_obj.parent = armature

                weight_groups = {bone.name: mesh_obj.vertex_groups.new(name=bone.name) for bone in mdl.bones}

                set_vertex_weights([weight_groups[bone.name] for bone in mdl.bones], vertices["bone_id"], vertices["weight"])

                flexes = []
                for mesh in model.meshes:
                    if mesh.flexes:
                        flexes.extend([(mdl.flex_names[flex.flex_desc_index], flex) for flex in mesh.flexes])

                if flexes:
                    mesh_obj.shape_key_add(name='base')

                    if debug_stereo_balance:
                        # debug tool to get stereo flex balances. i'll leave it here just in case
                        side_right = mesh_obj.vertex_groups.new(name='blendright')
                        side_left = mesh_obj.vertex_groups.new(name='blendleft')
                        side_all = np.zeros(model.vertex_count, dtype=np.float32)

                        for flex_name, flex_desc in flexes:
                            vertex_animation = vertex_anim_cache[flex_name]
                            side = get_slice(vertex_animation['side'], model.vertex_offset, model.vertex_count).ravel()
                            side_all = np.maximum(side_all, side)
                        side_all = side_all[vtx_vertices] + 0.0

                        for n, vert in enumerate(vtx_vertices):
                            side_right.add([n], side_all[n], 'REPLACE')
                            side_left.add([n], 1-side_all[n], 'REPLACE')

                    for flex_name, flex_desc in flexes:
                        vertex_animation = vertex_anim_cache[flex_name]
                        flex_delta = get_slice(vertex_animation["pos"], model.vertex_offset, model.vertex_count)
                        flex_delta = flex_delta[vtx_vertices] * scale

                        side = get_slice(vertex_animation["side"], model.vertex_offset, model.vertex_count)
                        side = side[vtx_vertices] + 0.0
                        wrinkle = get_slice(vertex_animation["wrinkle"], model.vertex_offset, model.vertex_count)
                        wrinkle = wrinkle[vtx_vertices] + 0.0 # this will have to be explained to me :P
                        # model.vertex_count and vtx_vertices can differ in size, so doing something like this just makes it work?
                        # apparently vtx_vertices has duplicate indicies, which can be observed by turning it into a set.
                        # i'm just following here
                        # -hisanimations
                        
                        model_vertices = get_slice(all_vertices['vertex'], model.vertex_offset, model.vertex_count)
                        model_vertices = model_vertices[vtx_vertices] * scale

                        if flex_desc.partner_index:
                            partner_name = mdl.flex_names[flex_desc.partner_index]
                            flexes, sides = [flex_name, partner_name], [1-side, side] if not debug_stereo_balance else [1.0, 1.0]
                        else:
                            flexes, sides = [flex_name], [1.0]

                        for flex_name, side in zip(flexes, sides):
                            shape_key = mesh_data.shape_keys.key_blocks.get(flex_name, None) or mesh_obj.shape_key_add(
                                name=flex_name)
                            shape_key.data.foreach_set("co", (flex_delta*side + model_vertices).ravel())
                            shape_key.value = 0.0

                            if flex_desc.vertex_anim_type == 1:
                                mesh_data: bpy.types.Mesh
                                if wrinkle.max() > 0:
                                    wrinkle_name = f'WR.{flex_name}.S'
                                if wrinkle.min() < 0:
                                    wrinkle_name = f'WR.{flex_name}.C'
                                attr: bpy.types.Attribute = mesh_data.attributes.get(wrinkle_name, None) or mesh_data.attributes.new(wrinkle_name, 'FLOAT', 'POINT')
                                wrinkle_data = (abs(wrinkle) * vert_anim_fixed_point_scale) * side
                                attr.data.foreach_set('value', wrinkle_data.ravel())

                    if create_drivers:
                        create_flex_drivers(mesh_obj, mdl)

                    if options.generate_wrinkle_map_node_group:
                        generate_wrinkle_map_node_group(mesh_obj)

            mesh_data.validate()
                
            if model.has_eyeballs:
                create_eyeballs(mdl, armature, mesh_obj, model, scale, extra_stuff)

    if mdl.attachments:
        attachments = create_attachments(mdl, armature if not static_prop else objects[0], scale)
    attachments.extend(extra_stuff)

    if not static_prop:
        if options.bodygroup_vis_switches:
            make_bodygroup_selectors(mdl, armature, bodygroups)

    return ModelContainer(objects, bodygroups, [], attachments, armature, None)


def create_attachments(mdl: MdlV44, armature: bpy.types.Object, scale):
    attachments = []
    for attachment in mdl.attachments:
        empty = bpy.data.objects.new(attachment.name, None)
        pos = Vector(attachment.pos) * scale
        rot = Euler(attachment.rot)

        empty.matrix_basis.identity()
        empty.scale *= scale
        empty.location = pos
        empty.rotation_euler = rot

        if armature.type == 'ARMATURE':
            modifier = empty.constraints.new(type="CHILD_OF")
            modifier.target = armature
            modifier.subtarget = mdl.bones[attachment.parent_bone].name
            modifier.inverse_matrix.identity()

        attachments.append(empty)

    return attachments
