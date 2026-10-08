import math
from collections import defaultdict
from typing import Optional

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

from SourceIO.blender_bindings.material_loader.shaders.goldsrc_shaders.goldsrc_shader import GoldSrcShader
from SourceIO.blender_bindings.operators.import_settings_base import ModelOptions
from SourceIO.blender_bindings.shared.model_container import ModelContainer
from SourceIO.blender_bindings.utils.bpy_utils import add_material, get_or_create_material, ActionCurveFactory
from SourceIO.blender_bindings.utils.fast_mesh import FastMesh
from SourceIO.library.models.mdl.v10.mdl_file import Mdl, Channels
from SourceIO.library.models.mdl.v10.structs.texture import StudioTexture
from SourceIO.library.models.mdl.v10.structs.sequence import StudioSequence
from SourceIO.library.utils import Buffer
from SourceIO.library.utils.path_utilities import path_stem


def create_armature(mdl: Mdl, scale):
    model_name = path_stem(mdl.header.name)
    armature = bpy.data.armatures.new(f"{model_name}_ARM_DATA")
    armature_obj = bpy.data.objects.new(f"{model_name}_ARM", armature)
    armature_obj['MODE'] = 'SourceIO'
    armature_obj.show_in_front = True
    bpy.context.scene.collection.objects.link(armature_obj)

    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')

    bone_length = 0.25 * scale
    edit_bones = []
    mdl_bone_transforms = []

    # Create bones and calculate their armature-space transforms.
    for index, mdl_bone_info in enumerate(mdl.bones):
        if not mdl_bone_info.name:
            mdl_bone_info.name = f"Bone_{index}"

        edit_bone = armature.edit_bones.new(mdl_bone_info.name)

        mdl_bone_info.name = edit_bone.name

        edit_bone.head = Vector((0.0, 0.0, 0.0))
        edit_bone.tail = Vector((0.0, bone_length, 0.0))

        local_position = Vector(mdl_bone_info.pos) * scale
        local_rotation = Euler(mdl_bone_info.rot).to_matrix().to_4x4()
        local_matrix = Matrix.Translation(local_position) @ local_rotation

        if mdl_bone_info.parent != -1:
            armature_matrix = mdl_bone_transforms[mdl_bone_info.parent] @ local_matrix
        else:
            armature_matrix = local_matrix

        edit_bones.append(edit_bone)
        mdl_bone_transforms.append(armature_matrix)

    for index, mdl_bone_info in enumerate(mdl.bones):
        edit_bone = edit_bones[index]

        if mdl_bone_info.parent != -1:
            edit_bone.parent = edit_bones[mdl_bone_info.parent]
            edit_bone.use_connect = False

        edit_bone.matrix = mdl_bone_transforms[index]

        edit_bone.length = bone_length

    bpy.ops.object.mode_set(mode='OBJECT')
    return armature_obj, mdl_bone_transforms


def import_model(mdl_file: Buffer, mdl_texture_file: Optional[Buffer], options: ModelOptions):
    mdl = Mdl.from_buffer(mdl_file)
    mdl_file_textures = mdl.textures
    if not mdl_file_textures and mdl_texture_file is not None:
        mdl_filet = Mdl.from_buffer(mdl_texture_file)
        mdl_file_textures = mdl_filet.textures

    objects = []
    bodygroups = defaultdict(list)
    armature, bone_transforms = create_armature(mdl, options.scale)

    for body_part in mdl.bodyparts:
        for body_part_model in body_part.models:
            model_name = body_part_model.name

            model_mesh = FastMesh.new(f'{model_name}_mesh')
            model_object = bpy.data.objects.new(f'{model_name}', model_mesh)

            if body_part_model.vertices.size == 0:
                continue

            objects.append(model_object)
            bodygroups[body_part.name].append(model_object)

            modifier = model_object.modifiers.new(name='Skeleton', type='ARMATURE')
            modifier.object = armature
            model_object.parent = armature

            model_vertices = body_part_model.vertices * options.scale
            model_normals = []
            model_indices = []
            model_materials = []

            uv_per_mesh = []
            # transformed_normals = []

            for model_index, body_part_model_mesh in enumerate(body_part_model.meshes):
                mesh_texture = mdl_file_textures[body_part_model_mesh.skin_ref]
                model_materials.extend(np.full(body_part_model_mesh.triangle_count, body_part_model_mesh.skin_ref))

                for mesh_triverts, mesh_triverts_fan in body_part_model_mesh.triangles:
                    def process(v0, v1, v2):
                        model_indices.append([v0.vertex_index, v1.vertex_index, v2.vertex_index])
                        model_normals.extend((body_part_model.normals[v0.normal_index],
                                              body_part_model.normals[v1.normal_index],
                                              body_part_model.normals[v2.normal_index]))
                        uv_per_mesh.append({
                            v0.vertex_index: (v0.uv[0] / mesh_texture.width, 1 - v0.uv[1] / mesh_texture.height),
                            v1.vertex_index: (v1.uv[0] / mesh_texture.width, 1 - v1.uv[1] / mesh_texture.height),
                            v2.vertex_index: (v2.uv[0] / mesh_texture.width, 1 - v2.uv[1] / mesh_texture.height)
                        })
                        # transform = bone_transforms[body_part_model.bone_normal_info[v0.vertex_index]].to_3x3()
                        # n0 = Vector(body_part_model.normals[v0.normal_index])
                        # n1 = Vector(body_part_model.normals[v1.normal_index])
                        # n2 = Vector(body_part_model.normals[v2.normal_index])
                        # n0 = n0 @ transform
                        # n1 = n1 @ transform
                        # n2 = n2 @ transform
                        # transformed_normals.append(n0.normalized())
                        # transformed_normals.append(n1.normalized())
                        # transformed_normals.append(n2.normalized())

                    if mesh_triverts_fan:
                        for index in range(1, len(mesh_triverts) - 1):
                            process(mesh_triverts[0],
                                    mesh_triverts[index + 1],
                                    mesh_triverts[index])

                    else:
                        for index in range(len(mesh_triverts) - 2):
                            process(mesh_triverts[index],
                                    mesh_triverts[index + 2 - (index & 1)],
                                    mesh_triverts[index + 1 + (index & 1)])
            remap = {}
            for model_material_index in np.unique(model_materials):
                model_texture_info = mdl_file_textures[model_material_index]
                remap[model_material_index] = load_material(path_stem(mdl.header.name), model_texture_info,
                                                            model_object)

            model_mesh.from_pydata(model_vertices, [], np.asarray(model_indices, np.uint32), shade_flat=False)
            model_mesh.update()
            model_mesh.polygons.foreach_set('material_index', [remap[a] for a in model_materials])

            vertex_indices = np.zeros((len(model_mesh.loops, )), dtype=np.uint32)
            model_mesh.loops.foreach_get('vertex_index', vertex_indices)

            model_mesh.uv_layers.new()
            model_mesh_uv = model_mesh.uv_layers[0].data
            for poly in model_mesh.polygons:
                for loop_index in range(poly.loop_start, poly.loop_start + poly.loop_total):
                    model_mesh_uv[loop_index].uv = uv_per_mesh[poly.index][model_mesh.loops[loop_index].vertex_index]

            mdl_vertex_groups = {}
            for vertex_index, vertex_info in enumerate(body_part_model.bone_vertex_info):
                mdl_vertex_group = mdl_vertex_groups.setdefault(vertex_info, [])
                mdl_vertex_group.append(vertex_index)

            for vertex_bone_index, vertex_bone_vertices in mdl_vertex_groups.items():
                vertex_group_bone = mdl.bones[vertex_bone_index]
                vertex_group = model_object.vertex_groups.new(name=vertex_group_bone.name)
                vertex_group.add(vertex_bone_vertices, 1.0, 'ADD')
                vertex_group_transform = bone_transforms[vertex_bone_index]
                for vertex in vertex_bone_vertices:
                    model_mesh.vertices[vertex].co = vertex_group_transform @ model_mesh.vertices[vertex].co
                    # model_mesh.vertices[vertex].normal = vertex_group_transform @ model_mesh.vertices[vertex].normal
            model_mesh.validate()

    if options.import_animations:
        load_animations(mdl, armature, path_stem(mdl.header.name), options.scale)
    bpy.context.scene.collection.objects.unlink(armature)

    return ModelContainer(objects, bodygroups, [], [], armature)


def load_material(model_name: str, model_texture_info: StudioTexture, model_object):
    material_name = f"{model_name}_{model_texture_info.name}"
    material = get_or_create_material(material_name, material_name)
    mat_id = add_material(material, model_object)
    bpy_material = GoldSrcShader(model_texture_info)
    bpy_material.create_nodes(material, model_name=model_name)
    bpy_material.align_nodes()
    return mat_id


def write_smd(mdl: Mdl, sequence: StudioSequence, animation: list[Channels]):
    with open(sequence.name + ".smd", "w") as f:
        f.write("version 1\n")
        f.write("nodes\n")
        for i, bone in enumerate(mdl.bones):
            f.write(f"  {i} \"{bone.name}\" {bone.parent}\n")
        f.write("end\n")
        f.write("skeleton\n")
        for frame in range(sequence.frame_count):
            f.write(f"  time {frame}\n")
            for i, bone in enumerate(mdl.bones):
                animation_channels = animation[i]

                pos = Vector((bone.pos[0], bone.pos[1], bone.pos[2]))
                rot = Vector((bone.rot[0], bone.rot[1], bone.rot[2]))

                if animation_channels.pos_x is not None:
                    pos.x = animation_channels.pos_x[frame] * bone.pos_scale[0] + bone.pos[0]

                if animation_channels.pos_y is not None:
                    pos.y = animation_channels.pos_y[frame] * bone.pos_scale[1] + bone.pos[1]

                if animation_channels.pos_z is not None:
                    pos.z = animation_channels.pos_z[frame] * bone.pos_scale[2] + bone.pos[2]

                if animation_channels.rot_x is not None:
                    rot.x = animation_channels.rot_x[frame] * bone.rot_scale[0] + bone.rot[0]

                if animation_channels.rot_y is not None:
                    rot.y = animation_channels.rot_y[frame] * bone.rot_scale[1] + bone.rot[1]

                if animation_channels.rot_z is not None:
                    rot.z = animation_channels.rot_z[frame] * bone.rot_scale[2] + bone.rot[2]

                if bone.parent == -1:
                    tmp = pos[0]
                    pos[0] = pos[1]
                    pos[1] = -tmp

                    rot[2] += math.radians(-90)

                f.write(f"    {i} ")
                f.write(f"{0 + pos[0]:.06f} ")
                f.write(f"{0 + pos[1]:.06f} ")
                f.write(f"{0 + pos[2]:.06f} ")
                f.write(f"{0 + rot[0]:.06f} ")
                f.write(f"{0 + rot[1]:.06f} ")
                f.write(f"{0 + rot[2]:.06f}")

                f.write("\n")
        f.write("end\n")


def load_animations(mdl: Mdl, armature, model_name, scale):
    """Import every sequence stored in the model as an action on ``armature``.

    Each frame's bone transform is ``T(pos + delta_pos) @ R(rot + delta_rot)`` in parent space, exactly
    how ``create_armature`` builds the rest pose from ``pos``/``rot``, so the pose-bone value is the
    rest-relative ``rest_local^-1 @ frame_local``. Only the first blend of each sequence is used, and
    sequences stored in external ``*NN.mdl`` sequence-group files are skipped.
    """
    bones = mdl.bones
    rest_inverse = [(Matrix.Translation(Vector(bone.pos) * scale) @ Euler(bone.rot).to_matrix().to_4x4()).inverted()
                    for bone in bones]
    for pose_bone in armature.pose.bones:
        pose_bone.rotation_mode = 'QUATERNION'

    factory = ActionCurveFactory(armature.name, armature, legacy_behavior=True)
    for sequence_id, sequence in enumerate(mdl.sequences):
        if sequence.group_index != 0 or sequence_id not in mdl.animations or sequence.frame_count == 0:
            continue
        frame_count = sequence.frame_count
        blend = mdl.animations[sequence_id][0]
        factory.new_action(f'{model_name}_{sequence.name}')
        frames = np.arange(frame_count, dtype=np.float32)

        for bone_id, bone in enumerate(bones):
            channels: Channels = blend[bone_id]
            deltas = np.zeros((6, frame_count), np.float32)
            for axis, values in enumerate((channels.pos_x, channels.pos_y, channels.pos_z,
                                           channels.rot_x, channels.rot_y, channels.rot_z)):
                if values is not None and len(values):
                    padded = np.empty(frame_count, np.float32)
                    count = min(len(values), frame_count)
                    padded[:count] = values[:count]
                    padded[count:] = values[count - 1]
                    deltas[axis] = padded
            positions = (np.asarray(bone.pos, np.float32)[:, None] + deltas[:3] * np.asarray(bone.pos_scale, np.float32)[:, None]) * scale
            rotations = np.asarray(bone.rot, np.float32)[:, None] + deltas[3:] * np.asarray(bone.rot_scale, np.float32)[:, None]

            locations = np.empty((frame_count, 3), np.float32)
            quaternions = np.empty((frame_count, 4), np.float32)
            previous = None
            for frame in range(frame_count):
                frame_local = Matrix.Translation(Vector(positions[:, frame])) @ Euler(rotations[:, frame]).to_matrix().to_4x4()
                location, rotation, _ = (rest_inverse[bone_id] @ frame_local).decompose()
                if previous is not None and rotation.dot(previous) < 0:
                    rotation.negate()
                previous = rotation
                locations[frame] = location
                quaternions[frame] = rotation

            group = factory.new_group(bone.name)
            for data_path, values in (("location", locations), ("rotation_quaternion", quaternions)):
                for index in range(values.shape[1]):
                    curve = factory.new_fcurve(f'pose.bones["{bone.name}"].{data_path}', index, group)
                    curve.keyframe_points.add(frame_count)
                    curve.keyframe_points.foreach_set("co_ui", np.column_stack((frames, values[:, index])).ravel())
                    curve.keyframe_points.foreach_set("interpolation", np.ones(frame_count, np.int32))
                    curve.update()
