from typing import Optional

import bpy

from ...shader_base import (Nodes, ShaderBase, MIX_FACTOR, MIX_A, MIX_B,
                                                                    MIX_RESULT)
from ....utils.texture_utils import check_texture_cache
from .....library.shared.content_manager import ContentManager
from .....library.utils.tiny_path import TinyPath

class IdTech3Shader(ShaderBase):
    SHADER: str = 'idtech3_shader'

    def __init__(self, content_manager: ContentManager):
        super().__init__()
        self.content_manager = content_manager

    def _initial_setup(self):
        material = self.bpy_material
        if material.get('source_loaded', False):
            return False

        material['source_loaded'] = True
        self.clean_nodes()
        material.surface_render_method = 'DITHERED'
        material.use_raytrace_refraction = False
        material.refraction_depth = 0.2
        return True

    def create_nodes(self, material, material_data: dict):
        self.bpy_material = material
        self._initial_setup()

        self.set_blend_mode('HASHED')
        self.bpy_material.use_transparent_shadow = True

        if "fogparams" in material_data:
            material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
            shader = self.create_node(Nodes.ShaderNodeBsdfTransparent, self.SHADER)
            self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
            return

        if not material_data["textures"]:
            return

        # return build_q3_material_two_pipelines(material, material_data["textures"],
        #                                      lambda n: self.load_texture(TinyPath(n).with_suffix("")), uv_name="UVMap")

        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        shader.inputs['Specular IOR Level'].default_value = 0

        textures = material_data["textures"]

        def math_op(op, a=None, b=None, val_a=None, val_b=None):
            n = self.create_node(Nodes.ShaderNodeMath)
            n.operation = op
            n.use_clamp = True
            if a is not None:
                self.connect_nodes(a, n.inputs[0])
            if b is not None:
                self.connect_nodes(b, n.inputs[1])
            if val_a is not None:
                n.inputs[0].default_value = val_a
            if val_b is not None:
                n.inputs[1].default_value = val_b
            return n.outputs[0]

        def color_math_op(op, a=None, b=None, val_a=None, val_b=None):
            n = self.create_mix_color(op)
            n.inputs[MIX_FACTOR].default_value = 1.0
            if a is not None:
                self.connect_nodes(a, n.inputs[MIX_A])
            if b is not None:
                self.connect_nodes(b, n.inputs[MIX_B])
            if val_a is not None:
                n.inputs[MIX_A].default_value = val_a
            if val_b is not None:
                n.inputs[MIX_B].default_value = val_b
            return n.outputs[MIX_RESULT]

        def _add_shader(acc_socket, add_socket):
            """Return a new shader socket that adds add_socket to acc_socket."""
            n = self.create_node(Nodes.ShaderNodeAddShader)
            self.connect_nodes(acc_socket, n.inputs[0])
            self.connect_nodes(add_socket, n.inputs[1])
            return n.outputs[0]

        def _mix_shader(acc_socket, top_socket, fac_socket_or_value):
            """Return a new shader socket that mixes acc_socket with top_socket by fac."""
            n = self.create_node(Nodes.ShaderNodeMixShader)
            if isinstance(fac_socket_or_value, float):
                n.inputs[0].default_value = fac_socket_or_value
            else:
                self.connect_nodes(fac_socket_or_value, n.inputs[0])
            self.connect_nodes(acc_socket, n.inputs[1])
            self.connect_nodes(top_socket, n.inputs[2])
            return n.outputs[0]

        def _rgb_const(r, g, b):
            """Return a color socket of a constant RGB."""
            n = self.create_node(Nodes.ShaderNodeRGB)
            n.outputs[0].default_value = (r, g, b, 1.0)
            return n.outputs[0]

        def _value_const(v):
            """Return a scalar socket of a constant value."""
            n = self.create_node(Nodes.ShaderNodeValue)
            n.outputs[0].default_value = v
            return n.outputs[0]

        def _mul_color(a, b):
            """Return socket of a * b (color multiply)."""
            return color_math_op('MULTIPLY', a, b)

        def _mix_color(a, b, fac):
            """Return socket of mix(a, b, fac) in color space."""
            n = self.create_mix_color('MIX')
            if isinstance(fac, float):
                n.inputs[MIX_FACTOR].default_value = fac
            else:
                self.connect_nodes(fac, n.inputs[MIX_FACTOR])
            self.connect_nodes(a, n.inputs[MIX_A])
            self.connect_nodes(b, n.inputs[MIX_B])
            return n.outputs[MIX_RESULT]

        def _scale_color(c, s):
            """Return socket of c * s, where s is scalar."""
            gs = self.create_node(Nodes.ShaderNodeRGB)
            if isinstance(s, float):
                gs.outputs[0].default_value = (s, s, s, 1.0)
            else:
                sep = self.create_node(Nodes.ShaderNodeCombineColor)
                sep.mode = "RGB"
                self.connect_nodes(s, sep.inputs['R'])
                self.connect_nodes(s, sep.inputs['G'])
                self.connect_nodes(s, sep.inputs['B'])
                gs = sep
            return color_math_op('MULTIPLY', c, gs.outputs[0] if hasattr(gs, 'outputs') else gs)

        def _parse_blend(blend_str):
            """Return (src, dst) tokens uppercased and normalized."""
            s = blend_str.strip()
            if not s:
                return 'GL_ONE', 'GL_ZERO'
            lowered = s.lower()
            if lowered == 'add':
                return 'GL_ONE', 'GL_ONE'
            if lowered == 'filter':
                return 'GL_DST_COLOR', 'GL_ZERO'
            if lowered == 'blend':
                return 'GL_SRC_ALPHA', 'GL_ONE_MINUS_SRC_ALPHA'
            parts = s.split()
            if len(parts) == 2:
                return parts[0].upper(), parts[1].upper()
            return 'GL_ONE', 'GL_ONE'

        def _cmp_alpha(op, a, threshold):
            """Return 0/1 mask from comparing a with threshold by op."""
            n = self.create_node(Nodes.ShaderNodeMath)
            n.operation = op
            self.connect_nodes(a, n.inputs[0])
            n.inputs[1].default_value = threshold
            n.use_clamp = True
            return n.outputs[0]

        acc_color = _rgb_const(0.0, 0.0, 0.0)
        acc_alpha = _value_const(1.0)
        self.connect_nodes(acc_color, shader.inputs['Base Color'])
        if 'Alpha' in shader.inputs:
            self.connect_nodes(acc_alpha, shader.inputs['Alpha'])
        accum_shader = shader.outputs['BSDF']
        has_base = False

        for layer in textures:
            texture_path = None
            for k, v in layer.items():
                if "map" in k and k != "animMap":
                    texture_path = TinyPath(v).with_suffix("")
                    break

            if texture_path is not None:
                if texture_path == "$lightmap":
                    acc_color.default_value = (1.0, 1.0, 1.0, 1.0)
                    has_base = True

                if texture_path.startswith("$"):
                    continue

                basetexture = self.load_texture(texture_path)
                if basetexture is None:
                    continue

                basetexture_node = self.create_node(Nodes.ShaderNodeTexImage, '$basetexture')
                basetexture_node.image = basetexture
                basetexture_node.id_data.nodes.active = basetexture_node

                col = basetexture_node.outputs['Color']
                alp = basetexture_node.outputs['Alpha']

                alpha_mode = layer.get("alphafunc", "")
                if alpha_mode == 'GE128':
                    alp = _cmp_alpha('GREATER_THAN', alp, 0.5)
                elif alpha_mode == 'LT128':
                    alp = _cmp_alpha('LESS_THAN', alp, 0.5)

                if layer.get("alphagen", "").lower() == "lightingspecular":
                    self.connect_nodes(alp, shader.inputs['Specular IOR Level'])
                    alp = _value_const(1.0)

                blend_src, blend_dst = "GL_ONE", "GL_ZERO"

                if layer.get("blendfunc", ""):
                    blend_src, blend_dst = _parse_blend(layer.get("blendfunc", ""))

                if (blend_src, blend_dst) in {('GL_ONE', 'GL_ZERO')}:
                    acc_color = col if not has_base else col
                    has_base = True
                    self.connect_nodes(acc_color, shader.inputs['Base Color'])
                    if layer.get("alphafunc", ""):
                        acc_alpha = alp if alp is not None else acc_alpha

                elif (blend_src, blend_dst) in {('GL_DST_COLOR', 'GL_ZERO'), ('GL_ZERO', 'GL_SRC_COLOR')}:
                    acc_color = _mul_color(acc_color, col)
                    self.connect_nodes(acc_color, shader.inputs['Base Color'])

                elif (blend_src, blend_dst) in {('GL_SRC_ALPHA', 'GL_ONE_MINUS_SRC_ALPHA')}:
                    acc_color = _mix_color(acc_color, col, alp)
                    self.connect_nodes(acc_color, shader.inputs['Base Color'])
                    if 'Alpha' in shader.inputs:
                        acc_alpha = _mix_color(acc_alpha, _value_const(1.0), alp)
                        self.connect_nodes(acc_alpha, shader.inputs['Alpha'])

                elif (blend_src, blend_dst) in {('GL_ONE', 'GL_ONE')}:
                    if has_base:
                        em = self.create_node(Nodes.ShaderNodeEmission)
                        self.connect_nodes(col, em.inputs['Color'])
                        accum_shader = _add_shader(accum_shader, em.outputs['Emission'])
                    else:
                        invert_node = self.create_node(Nodes.ShaderNodeInvert)
                        self.connect_nodes(col, invert_node.inputs['Color'])
                        trans = self.create_node(Nodes.ShaderNodeBsdfTransparent)
                        self.connect_nodes(invert_node.outputs['Color'], trans.inputs['Color'])
                        accum_shader = _add_shader(accum_shader, trans.outputs[0])
                        em = self.create_node(Nodes.ShaderNodeEmission)
                        self.connect_nodes(col, em.inputs['Color'])
                        accum_shader = _add_shader(accum_shader, em.outputs['Emission'])

                elif (blend_src, blend_dst) in {('GL_SRC_ALPHA', 'GL_ONE')}:
                    em = self.create_node(Nodes.ShaderNodeEmission)
                    self.connect_nodes(col, em.inputs['Color'])
                    self.connect_nodes(alp, em.inputs['Strength'])
                    accum_shader = _add_shader(accum_shader, em.outputs['Emission'])

                elif (blend_src, blend_dst) in {('GL_ONE', 'GL_ONE_MINUS_SRC_ALPHA')}:
                    em = self.create_node(Nodes.ShaderNodeEmission)
                    self.connect_nodes(col, em.inputs['Color'])
                    accum_shader = _add_shader(accum_shader, em.outputs['Emission'])
                    if 'Alpha' in shader.inputs:
                        inv = math_op('SUBTRACT', _value_const(1.0), alp)
                        acc_alpha = math_op('MULTIPLY', acc_alpha, inv)
                else:
                    acc_color = _mix_color(acc_color, col, alp)
                    self.connect_nodes(acc_color, shader.inputs['Base Color'])
                    if 'Alpha' in shader.inputs:
                        acc_alpha = _mix_color(acc_alpha, _value_const(1.0), alp)

        self.connect_nodes(accum_shader, material_output.inputs['Surface'])
        self.connect_nodes(acc_alpha, shader.inputs['Alpha'])

    def load_texture(self, texture_name) -> Optional[bpy.types.Image]:
        texture_name = TinyPath(texture_name)
        image = check_texture_cache(texture_name)
        if image is not None:
            return image
        model_texture = bpy.data.images.get(texture_name, None)
        if model_texture is None:
            texture_buffer = (self.content_manager.find_file(texture_name + ".png") or
                              self.content_manager.find_file(texture_name + ".jpg") or
                              self.content_manager.find_file(texture_name + ".jpeg") or
                              self.content_manager.find_file(texture_name + ".tga"))

            if texture_buffer:
                model_texture = bpy.data.images.new(
                    texture_name,
                    width=8,
                    height=8,
                    alpha=True
                )
                model_texture.source = "FILE"
                model_texture.file_format = "PNG"
                texture_data = texture_buffer.read(-1)
                model_texture.pack(data=texture_data, data_len=len(texture_data))
                model_texture.alpha_mode = "CHANNEL_PACKED"
                model_texture.reload()
                return model_texture
            return None
        return model_texture
