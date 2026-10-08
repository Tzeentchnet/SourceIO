from typing import Optional

from SourceIO.blender_bindings.material_loader.shader_base import Nodes, MIX_FACTOR, MIX_A, MIX_B, MIX_RESULT
from SourceIO.blender_bindings.material_loader.shaders.goldsrc_shader_base import GoldSrcShaderBase
from SourceIO.library.models.mdl.v10.structs.texture import MdlTextureFlag


class GoldSrcShaderMode1(GoldSrcShaderBase):
    SHADER: str = 'goldsrc_shader_mode1'

    def create_nodes(self, material, rad_info=None, model_name: Optional[str] = None):
        if super().create_nodes(material, {}) in ['UNKNOWN', 'LOADED']:
            return

        basetexture = self.load_texture(material.name, model_name)
        basetexture_node = self.create_node(Nodes.ShaderNodeTexImage, '$basetexture')
        basetexture_node.image = basetexture
        basetexture_node.id_data.nodes.active = basetexture_node

        if rad_info is not None:
            self._emit_surface(basetexture_node, rad_info)
            return

        vertex_color_color = self.create_node(Nodes.ShaderNodeVertexColor)
        vertex_color_color.layer_name = "RENDER_COLOR"
        vertex_color_alpha = self.create_node(Nodes.ShaderNodeVertexColor)
        vertex_color_alpha.layer_name = "RENDER_AMOUNT"

        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node(Nodes.ShaderNodeBsdfPrincipled, self.SHADER)
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])

        mixer = self.create_mix_color('MIX')
        mixer.inputs[MIX_FACTOR].default_value = 1.0
        self.connect_nodes(basetexture_node.outputs['Color'], mixer.inputs[MIX_A])
        self.connect_nodes(vertex_color_color.outputs['Color'], mixer.inputs[MIX_B])

        self.connect_nodes(mixer.outputs[MIX_RESULT], shader.inputs['Base Color'])
        self.connect_nodes(vertex_color_alpha.outputs['Color'], shader.inputs['Alpha'])

        if self._valve_material.flags & MdlTextureFlag.CHROME:
            shader.inputs['Specular IOR Level'].default_value = 0.5
            shader.inputs['Metallic'].default_value = 1
            uvs_node = self.create_node(Nodes.ShaderNodeTexCoord)
            self.connect_nodes(uvs_node.outputs['Reflection'], basetexture_node.inputs['Vector'])
        if self._valve_material.flags & MdlTextureFlag.FULL_BRIGHT:
            shader.inputs['Emission Strength'].default_value = 1
            self.connect_nodes(basetexture_node.outputs['Color'], shader.inputs['Emission Color'])
        else:
            shader.inputs['Specular IOR Level'].default_value = 0
