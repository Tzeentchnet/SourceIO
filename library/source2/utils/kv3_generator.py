import uuid

from ..keyvalues3.enums import KV3Format
from ...utils.s2_keyvalues import KeyValues


class KV3mdl:
    def __init__(self, *, include_default_animation: bool = True, modeldoc_version: int = 28):
        self.modeldoc_version = modeldoc_version
        self.storage = {'rootNode': {'_class': 'RootNode',
                                     'children': [],
                                     'model_archetype': '',
                                     'primary_associated_entity': '',
                                     'anim_graph_name': '',
                                     }}

        self.render_mesh_list = {'_class': 'RenderMeshList', 'children': []}

        self.animation_list = {'_class': 'AnimationList', 'children': []}

        self.bodygroup_list = {'_class': 'BodyGroupList', 'children': []}

        self.jigglebone_list = {'_class': 'JiggleBoneList', 'children': []}

        self.skin_group_list = {'_class': 'MaterialGroupList', 'children': []}

        self.morph_control_list = {'_class': 'MorphControlList', 'children': []}

        self.morph_rule_list = {'_class': 'MorphRuleList', 'children': []}
        self.lod_group_list = {'_class': 'LODGroupList', 'children': []}
        self.attachment_list = {'_class': 'AttachmentList', 'children': []}
        self.physics_shape_list = {'_class': 'PhysicsShapeList', 'children': []}
        self.hitbox_set_list = {'_class': 'HitboxSetList', 'children': []}
        # self.command_list = {'_class': 'CommandList', 'command_buffer': "\n"}

        self.storage['rootNode']['children'].append(self.render_mesh_list)
        self.storage['rootNode']['children'].append(self.animation_list)
        self.storage['rootNode']['children'].append(self.bodygroup_list)
        self.storage['rootNode']['children'].append(self.jigglebone_list)
        self.storage['rootNode']['children'].append(self.skin_group_list)
        self.storage['rootNode']['children'].append(self.morph_control_list)
        self.storage['rootNode']['children'].append(self.morph_rule_list)
        self.storage['rootNode']['children'].append(self.lod_group_list)
        self.storage['rootNode']['children'].append(self.attachment_list)
        self.storage['rootNode']['children'].append(self.physics_shape_list)
        self.storage['rootNode']['children'].append(self.hitbox_set_list)
        # self.storage['rootNode']['children'].append(self.command_list)
        self._add_bone_markup()
        if include_default_animation:
            self._add_empty_anim()

    # def add_anim(self):
    def _add_bone_markup(self):
        markup = {
            "_class": "BoneMarkupList",
            "bone_cull_type": "None",
        }

        self.storage['rootNode']['children'].append(markup)

    def _add_empty_anim(self):
        anim = {'_class': 'EmptyAnim',
                'activity_name': '',
                'activity_weight': 1,
                'anim_markup_ordered': False,
                'delta': False,
                'disable_compression': False,
                'fade_in_time': 0.2,
                'fade_out_time': 0.2,
                'frame_count': 1,
                'frame_rate': 30,
                'hidden': False,
                'looping': False,
                'name': 'ref',
                'weight_list_name': '',
                'worldSpace': False}
        self.animation_list['children'].append(anim)

    def add_render_mesh(self, name, path, mesh_class='RenderMeshFile', import_filter=None):
        render_mesh = {'_class': mesh_class,
                       'name': name,
                       'filename': path,
                       'import_scale': 1.0
                       }
        if import_filter is not None:
            render_mesh['import_filter'] = {
                'exclude_by_default': True,
                'exception_list': list(import_filter),
            }

        self.render_mesh_list['children'].append(render_mesh)

    def add_bodygroup(self, name):
        bodygroup = {'_class': 'BodyGroup',
                     'children': [],
                     'hidden_in_tools': False,
                     'name': name}
        self.bodygroup_list['children'].append(bodygroup)
        return bodygroup

    def add_jiggle_bone(self, data):
        jiggle_bone = {'_class': 'JiggleBone'}
        jiggle_bone.update(data)
        self.jigglebone_list['children'].append(jiggle_bone)

    def add_skin(self, skin_name, skin_class='MaterialGroup'):
        skin = {
            '_class': skin_class,
            'name': skin_name,
            'remaps': []
        }
        self.skin_group_list['children'].append(skin)

        return skin

    def add_morph_control(self, name, stereo=False, min_value=0.0, max_value=1.0):
        morph_control = {'_class': 'MorphControl',
                         'name': name,
                         'stereo': stereo,
                         'min_value': min_value,
                         'max_value': max_value,
                         }
        self.morph_control_list['children'].append(morph_control)
        return morph_control

    def add_morph_rule(self, name, target, expression):
        morph_rule = {'_class': 'MorphRule',
                      'name': name,
                      'target': target,
                      'expression': expression,
                      }
        self.morph_rule_list['children'].append(morph_rule)
        return morph_rule

    def add_lod(self, name, switch_threshold, meshes):
        lod = {
            '_class': 'LODGroup',
            'name': name,
            'switch_threshold': float(switch_threshold),
            'meshes': list(meshes),
        }
        self.lod_group_list['children'].append(lod)
        return lod

    def add_attachment(self, name, parent_bone, origin, angles, weight=1.0, ignore_rotation=False):
        attachment = {
            '_class': 'Attachment',
            'name': name,
            'parent_bone': parent_bone,
            'relative_origin': list(origin),
            'relative_angles': list(angles),
            'weight': float(weight),
            'ignore_rotation': bool(ignore_rotation),
        }
        self.attachment_list['children'].append(attachment)
        return attachment

    def add_physics_shape(self, shape_class, **properties):
        shape = {'_class': shape_class}
        shape.update(properties)
        self.physics_shape_list['children'].append(shape)
        return shape

    def add_hitbox(self, set_name, name, parent_bone, minimum, maximum, group_id=0, surface_property=''):
        hitbox_set = next(
            (item for item in self.hitbox_set_list['children'] if item['name'] == set_name),
            None,
        )
        if hitbox_set is None:
            hitbox_set = {'_class': 'HitboxSet', 'name': set_name, 'children': []}
            self.hitbox_set_list['children'].append(hitbox_set)
        hitbox = {
            '_class': 'Hitbox',
            'name': name,
            'parent_bone': parent_bone,
            'surface_property': surface_property,
            'translation_only': False,
            'group_id': int(group_id),
            'hitbox_mins': list(minimum),
            'hitbox_maxs': list(maximum),
        }
        hitbox_set['children'].append(hitbox)
        return hitbox

    def add_animation_file(self, name, source_filename):
        animation = {
            '_class': 'AnimFile',
            'name': name,
            'activity_name': '',
            'activity_weight': 1,
            'weight_list_name': '',
            'fade_in_time': 0.2,
            'fade_out_time': 0.2,
            'looping': False,
            'delta': False,
            'worldSpace': False,
            'hidden': False,
            'anim_markup_ordered': False,
            'disable_compression': False,
            'animgraph_additive': False,
            'source_filename': source_filename,
            'import_bone_scales': False,
            'start_frame': -1,
            'end_frame': -1,
            'framerate': 30.0,
            'reverse': False,
        }
        self.animation_list['children'].append(animation)
        return animation

    def add_copy_node(self, source, name):
        self.command_list['command_buffer'] += f'CopyNode( SourceNode = "{source}", Name = "{name}" );\n'

    @staticmethod
    def add_skin_remap(skin, remap_from, remap_to):
        remap = {'from': remap_from, 'to': remap_to}
        skin['remaps'].append(remap)

    @staticmethod
    def add_bodygroup_choice(bodygroup, meshes_name):
        if isinstance(meshes_name, str):
            meshes_name = [meshes_name]
        choice = {'_class': 'BodyGroupChoice', 'meshes': meshes_name}
        bodygroup['children'].append(choice)

    def dump(self):
        try:
            format_value = KV3Format[f'modeldoc{self.modeldoc_version}'].value
        except KeyError as error:
            raise ValueError(f'Unsupported ModelDoc version {self.modeldoc_version}') from error
        format_uuid = str(uuid.UUID(bytes_le=format_value))
        return KeyValues.dump_str('KV3',
                                  ('text', 'e21c7f3c-8a33-41c5-9977-a76d3a32aa0d'),
                                  (f'modeldoc{self.modeldoc_version}', format_uuid),
                                  self.storage)
