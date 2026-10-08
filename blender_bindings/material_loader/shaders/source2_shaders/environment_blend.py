from pprint import pformat
from typing import Any
from enum import StrEnum

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import Nodes, ExtraMaterialParameters
from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import Source2ShaderBase
from SourceIO.library.source2.blocks.kv3_block import KVBlock
from SourceIO.library.utils.math_utilities import srgb_to_linear, SOURCE2_HAMMER_UNIT_TO_METERS
from SourceIO.blender_bindings.operators.import_settings_base import SharedOptions


# Todo List
# - Static Overlay
#   The material layers themselves can determine if static overlays can appear on them (and maybe their alpha via the tint mask?)
#   This might not be possible in blender, or it might need geo nodes, haven't looked into it.
# - ContrastSaturationBrightness
#   Scrapped for now, without built in matrix math in shader nodes it is an absolute NIGHTMARE.
#   So I say just wait for blender to support that, geo nodes already do but shader nodes don't yet.
#   it also doesn't help that shader nodes also don't really support vectors of sizes bigger than 3.
# - Preserve Luminance Color Tint Mode
#   Also requires matrix math.
class EnvironmentBlend(Source2ShaderBase):
    SHADER: str = 'environment_blend.vfx'

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        shader = self.create_node_group('environment_blend.vfx 5.0', name=self.SHADER)
        mapping = self.create_node_group('environment_blend.vfx Mapping')
        self.connect_nodes(shader.outputs['BSDF'], material_output.inputs['Surface'])
        material_data = self._material_resource
        data = material_data.get_block(KVBlock, block_name='DATA')
        self.logger.info(pformat(dict(data)))

        shader_tree = shader.node_tree
        mapping_tree = mapping.node_tree

        def get_input_socket(root: bpy.types.NodeTreeInterfacePanel, node_group=shader, tree=shader_tree):
            return lambda path: node_group.inputs[self.get_group_socket_index(tree, path, root)]

        # unfortunately, blender's menu switches only allow inputs as strings, instead of as integer, so string enums need to be defined for them :/
        class color_tint_mode(StrEnum):
            MULTIPLY = 'Multiply'
            PRESERVE_LUMINANCE = 'Preserve Luminance'
            MOD2X = 'Mod2X'

        class blend_mode(StrEnum):
            MULTIPLY = 'Multiply'
            MOD2X = 'Mod2x' # different caps intentional.
            BLEND = 'Blend'

        class tint_mask_mode(StrEnum):
            UNMASKED = 'Unmasked'
            MASKED = 'Masked'
            INVERTED = 'Inverted'

        class texcoord_scale_axis(StrEnum):
            NONE = 'None'
            X = 'X'
            Y = 'Y'
            Z = 'Z'

        class texcoord_u_scale_origin(StrEnum):
            LEFT = 'Left'
            CENTER = 'Center'
            RIGHT = 'Right'

        class texcoord_v_scale_origin(StrEnum):
            TOP = 'Top'
            CENTER = 'Center'
            BOTTOM = 'Bottom'

        for i in range(2):
            affix = '' if i == 0 else '2'
            texcoord_scale_panel = get_input_socket(self.get_nodetree_item_from_path(mapping_tree, [f'TexCoord{affix} Scale By Model Scale'], bpy.types.NodeTreeInterfacePanel), mapping, mapping_tree)
            texcoord_scale_panel(['U Scale Axis']).default_value = list(texcoord_scale_axis)[material_data.get_int_property(f'g_nScaleTexCoord{affix}UByModelScaleAxis', 0)].value
            texcoord_scale_panel(['V Scale Axis']).default_value = list(texcoord_scale_axis)[material_data.get_int_property(f'g_nScaleTexCoord{affix}VByModelScaleAxis', 0)].value
            texcoord_scale_panel(['U Scale Origin']).default_value = list(texcoord_u_scale_origin)[material_data.get_int_property(f'g_nScaleTexCoord{affix}UByModelScaleOrigin', 0)].value
            texcoord_scale_panel(['V Scale Origin']).default_value = list(texcoord_v_scale_origin)[material_data.get_int_property(f'g_nScaleTexCoord{affix}VByModelScaleOrigin', 2)].value

        # TODO: Replace this with an input of the ACTUAL import scale the user has set
        mapping.inputs['Import Scale'].default_value = SOURCE2_HAMMER_UNIT_TO_METERS

        # F_LAYER_COUNT is essentially a boolean, 1 means 3 layers.
        shader.inputs['Use 3 Layers'].default_value = self._check_flag('F_LAYER_COUNT')

        # Ideally there probably should be an extra parameter for if this is being used
        objcolor_node = self.create_node(Nodes.ShaderNodeObjectInfo)
        self.connect_nodes(objcolor_node.outputs['Color'], shader.inputs[self.get_group_socket_index(shader_tree, ['Environment Layers', 'Model Tint'])])

        for i in range(1, 4):
            layer = shader_tree.interface.items_tree[f'Layer {i}']
            layer_mapping = get_input_socket(self.get_nodetree_item_from_path(mapping_tree, [f'Layer {i}'], bpy.types.NodeTreeInterfacePanel), mapping, mapping_tree)

            is_biplanar = self._check_flag(f'F_LAYER{i}_BIPLANAR')

            layer_mapping(['Biplanar Mapping']).default_value = is_biplanar
            layer_mapping(['Biplanar Tiling']).default_value = material_data.get_float_property(f'g_flBiPlanarTiling{i}', 196)
            layer_transform = material_data.get_vector_property(f'g_vScaleRotationTranslation{i}', (1, 0, 0, 0))
            layer_mapping(['Scale']).default_value = layer_transform[0]
            layer_mapping(['Rotation']).default_value = layer_transform[1]
            layer_mapping(['Translation']).default_value = (layer_transform[2], layer_transform[3])

            layer_uv_socket = mapping.outputs[f'Layer {i}']

            # COLOR / TINT MASK
            if self._have_texture(f'g_tColor{i}'):
                color_texture = self._get_texture(f'g_tColor{i}', (0.5, 0.5, 0.5, 1))
                # Biplanar mapping from what I can tell is close enough to triplanar/box mapping that we might as well just use that.
                if is_biplanar:
                    color_texture.projection = 'BOX'
                self.connect_nodes(layer_uv_socket, color_texture.inputs['Vector'])
                self.connect_nodes(color_texture.outputs['Color'], shader.inputs[f'TextureColor{i}'])
                self.connect_nodes(color_texture.outputs['Alpha'], shader.inputs[f'TextureTintMask{i}'])

                # TODO: Support ContrastSaturationBrightness
                # the math for it requires the average color of the texture, which reflectiveness is (or at least this is how Source2Viewer handles is)
                # But the color correction also heavily relies on matrix math and shader nodes do not support that yet.
                # Maybe a much more simple color adjustment could be done, with the input being the result of matrix math done in code, not sure how feasible that would be.
                #texture_resource = self._material_resource.get_child_resource(texture_path, self.content_manager,
                #                                                          CompiledTextureResource)
                #data_block = self.get_block(TextureData, block_name='DATA')
                #reflectivity = data_block.texture_info.reflectivity

            layer_color = get_input_socket(self.get_nodetree_item_from_path(layer, ['Color'], bpy.types.NodeTreeInterfacePanel))

            layer_color(['Color Tint']).default_value = srgb_to_linear(material_data.get_vector_property(f'g_vColorTint{i}', (1, 1, 1, 1)), 1)
            layer_color(['Color Tint Mode']).default_value = list(color_tint_mode)[material_data.get_int_property(f'g_nTextureColorTintMode{i}', 0)].value
            layer_color(['Mask Color Tint']).default_value = self._check_flag(f'g_bMaskColorTint{i}', 1)
            layer_color(['Mask Vertex Color Tint']).default_value = self._check_flag(f'g_bMaskVertexColorTint{i}', 1)
            layer_color(['Model Tint']).default_value = self._check_flag(f'g_bModelTint{i}', 1)
            layer_color(['Vertex Color Strength']).default_value = material_data.get_float_property(f'g_fVertexColorStrength{i}', 1.0)

            layer_tintmask = get_input_socket(self.get_nodetree_item_from_path(layer, ['Tint Mask'], bpy.types.NodeTreeInterfacePanel))
            tintmask_contrastbrightness = material_data.get_vector_property(f'g_vLayerTintMaskContrastBrightness{i}', (1, 0))
            layer_tintmask(['Contrast']).default_value = tintmask_contrastbrightness[0]
            layer_tintmask(['Brightness']).default_value = tintmask_contrastbrightness[1]

            # NORMAL / ROUGHNESS
            if self._have_texture(f'g_tNormalRoughness{i}'):
                normal_texture = self._get_texture(f'g_tNormalRoughness{i}', (0.5, 0.5, 0.5, 0.5), True)
                if is_biplanar:
                    normal_texture.projection = 'BOX'
                self.connect_nodes(layer_uv_socket, normal_texture.inputs['Vector'])
                self.connect_nodes(normal_texture.outputs['Color'], shader.inputs[f'TextureNormal{i}'])
                self.connect_nodes(normal_texture.outputs['Alpha'], shader.inputs[f'TextureRoughness{i}'])

            shader.inputs[self.get_group_socket_index(shader_tree, ['Normal', 'Strength'], layer)].default_value = material_data.get_float_property(f'g_fLayerNormalStrength{i}', 1.0)

            layer_roughness = get_input_socket(self.get_nodetree_item_from_path(layer, ['Roughness'], bpy.types.NodeTreeInterfacePanel))
            roughness_contrastbrightness = material_data.get_vector_property(f'g_vLayerRoughnessContrastBrightness{i}', (1, 0))
            layer_roughness(['Contrast']).default_value = roughness_contrastbrightness[0]
            layer_roughness(['Brightness']).default_value = roughness_contrastbrightness[1]

            if self._have_texture(f'g_tPacked{i}'):
                packed_texture = self._get_texture(f'g_tPacked{i}', (0, 0, 0, 1), True)
                if is_biplanar:
                    packed_texture.projection = 'BOX'
                self.connect_nodes(layer_uv_socket, packed_texture.inputs['Vector'])
                split_rgb_node = self.create_node(Nodes.ShaderNodeSeparateColor)
                split_rgb_node.mode = 'RGB'
                self.connect_nodes(packed_texture.outputs['Color'], split_rgb_node.inputs['Color'])

                # SELFILLUM
                layer_selfillum = get_input_socket(self.get_nodetree_item_from_path(layer, ['SelfIllum'], bpy.types.NodeTreeInterfacePanel))
                self.connect_nodes(split_rgb_node.outputs['Red'], layer_selfillum(['Strength']))
                layer_selfillum(['Scale']).default_value = material_data.get_float_property(f'g_fLayerSelfIllumScale{i}', 1)
                layer_selfillum(['Override Bloom Amount']).default_value = material_data.get_float_property(f'g_flOverrideBloomAmount{i}', 0)
                layer_selfillum(['Tint']).default_value = srgb_to_linear(material_data.get_vector_property(f'g_vLayerSelfIllumTint{i}', (1, 1, 1, 1)), 1)

                # METALNESS
                # I've decided to split this out before the node group although it could go either way
                layer_metalness = get_input_socket(self.get_nodetree_item_from_path(layer, ['Metalness'], bpy.types.NodeTreeInterfacePanel))
                self.connect_nodes(split_rgb_node.outputs['Green'], layer_metalness(['Metalness']))
                metalness_contrastbrightness = material_data.get_vector_property(f'g_vLayerMetalnessContrastBrightness{i}', (1, 0))
                layer_metalness(['Contrast']).default_value = metalness_contrastbrightness[0]
                layer_metalness(['Brightness']).default_value = metalness_contrastbrightness[1]

            # UV OVERLAY
            layer_uvoverlay = get_input_socket(self.get_nodetree_item_from_path(layer, ['UV Overlay'], bpy.types.NodeTreeInterfacePanel))
            layer_uvoverlay(['Overlay Strength']).default_value = material_data.get_float_property(f'g_flOverlayStrength{i}', 1)
            layer_uvoverlay(['Overlay Normal Strength']).default_value = material_data.get_float_property(f'g_flOverlayNormalStrength{i}', 1)
            layer_uvoverlay(['Overlay Roughness Strength']).default_value = material_data.get_float_property(f'g_flOverlayRoughnessStrength{i}', 0)
            layer_uvoverlay(['Overlay Tint Mask']).default_value = list(tint_mask_mode)[material_data.get_int_property(f'g_nOverlayTintMask{i}', 0)].value

            # WORLD OVERLAY
            # World overlay strength is only stored as a vec2 if there are 2 world overlays, otherwise its stored as a float.
            if material_data.get_int_property('F_WORLD_OVERLAY', 0) == 1:
                worldoverlay_strength = (material_data.get_float_property(f'g_flWorldOverlayStrength{i}', 1), 0)
            else:
                worldoverlay_strength = material_data.get_vector_property(f'g_vWorldOverlayStrengths{i}', (1, 0))
            layer_worldoverlay = get_input_socket(self.get_nodetree_item_from_path(layer, ['World Overlay'], bpy.types.NodeTreeInterfacePanel))
            layer_worldoverlay(['World Overlay Strengths']).default_value = worldoverlay_strength[:2]
            layer_worldoverlay(['World Overlay Tint Mask']).default_value = list(tint_mask_mode)[material_data.get_int_property(f'g_nWorldOverlayTintMask{i}', 1)].value

            # Layers 2 & 3 only
            if i > 1:
                # REVEAL
                if self._have_texture(f'g_tRevealMask{i}'):
                    layer_mapping([f'Layer {i} Reveal Mask', 'Use Secondary UV']).default_value = self._check_flag(f'g_bRevealUseSecondaryUv{i}')
                    reveal_transform = material_data.get_vector_property(f'g_vRevealScaleRotationTranslation{i}', (1, 0, 0, 0))
                    layer_mapping([f'Layer {i} Reveal Mask', 'Scale']).default_value = reveal_transform[0]
                    layer_mapping([f'Layer {i} Reveal Mask', 'Rotation']).default_value = reveal_transform[1]
                    layer_mapping([f'Layer {i} Reveal Mask', 'Translation']).default_value = (reveal_transform[2], reveal_transform[3])

                    revealmask_texture = self._get_texture(f'g_tRevealMask{i}', (1, 1, 1, 1), True)
                    self.connect_nodes(mapping.outputs[f'Layer {i} Reveal Mask'], revealmask_texture.inputs['Vector'])
                    self.connect_nodes(revealmask_texture.outputs['Color'], shader.inputs[f'RevealMask{i}'])

                layer_reveal = get_input_socket(self.get_nodetree_item_from_path(layer, ['Reveal'], bpy.types.NodeTreeInterfacePanel))

                layer_reveal(['Invert']).default_value = self._check_flag(f'g_bRevealInvert{i}')
                # Offset here refers to where the reveal shows up on the texture, not the reveal's UVs.
                layer_reveal(['Offset']).default_value = material_data.get_float_property(f'g_flRevealOffset{i}', 0)
                layer_reveal(['Softness']).default_value = material_data.get_float_property(f'g_flRevealSoftness{i}', 0.125)

                # BORDER EFFECTS
                layer_bordereffects = get_input_socket(self.get_nodetree_item_from_path(layer, ['Border Effects'], bpy.types.NodeTreeInterfacePanel))
                # Note: Border Layer Amount is a vec2 for layer 2, but a vec3 for layer 3
                # the index for the vector appears to correspond to each layer. So LayerAmount[0] is how much the border effect shows up on layer 1, and so on.
                # For the other border effects, index 0 is how much that variable applies to other layers, and index 1 is how much it applies to itself.
                layer_bordereffects(['Border Effects']).default_value = self._check_flag(f'g_bBorderEffects{i}')
                layer_bordereffects(['Width']).default_value = material_data.get_vector_property(f'g_vBorderWidth{i}', (0.125, 0.125))[:2]
                layer_bordereffects(['Softness']).default_value = material_data.get_vector_property(f'g_vBorderSoftness{i}', (0.5, 0.5))[:2]
                layer_bordereffects(['Layer Amount']).default_value = material_data.get_vector_property(f'g_vBorderLayerAmount{i}', (1, 1, 1))[:i]
                layer_bordereffects(['Blend Mode']).default_value = list(blend_mode)[material_data.get_int_property(f'g_nBorderBlendMode{i}', 0)].value
                border_tint = srgb_to_linear(material_data.get_vector_property(f'g_vBorderTint{i}', (0.5, 0.5, 0.5, 1)), 1)
                layer_bordereffects(['Tint']).default_value = border_tint
                # Shader nodes still offer no way to actually separate the alpha from a color socket.
                layer_bordereffects(['Tint Alpha']).default_value = border_tint[3]
                layer_bordereffects(['Roughness Strength']).default_value = material_data.get_float_property(f'g_flBorderRoughnessStrength{i}', 0)
                layer_bordereffects(['Roughness']).default_value = material_data.get_float_property(f'g_flBorderRoughness{i}', 0.5)

        # UV OVERLAY
        panel_uv_overlay = get_input_socket(self.get_nodetree_item_from_path(shader_tree, ['UV Overlay'], bpy.types.NodeTreeInterfacePanel))
        panel_uv_overlay(['UV Overlay']).default_value = self._check_flag('F_UV_OVERLAY')

        panel_uv_overlay_mapping = get_input_socket(self.get_nodetree_item_from_path(mapping_tree, ['Overlay'], bpy.types.NodeTreeInterfacePanel), mapping, mapping_tree)
        panel_uv_overlay_mapping(['Use Secondary UV']).default_value = self._check_flag(f'g_bUseSecondaryUvForOverlay', 1)
        overlay_transform = material_data.get_vector_property('g_vOverlayScaleRotationTranslation', (1, 0, 0, 0))
        panel_uv_overlay_mapping(['Scale']).default_value = overlay_transform[0]
        panel_uv_overlay_mapping(['Rotation']).default_value = overlay_transform[1]
        panel_uv_overlay_mapping(['Translation']).default_value = (overlay_transform[2], overlay_transform[3])

        if self._have_texture('g_tColorOverlay'):
            overlay_color_texture = self._get_texture('g_tColorOverlay', (0.5, 0.5, 0.5, 1))
            self.connect_nodes(mapping.outputs['Overlay'], overlay_color_texture.inputs['Vector'])
            self.connect_nodes(overlay_color_texture.outputs['Color'], shader.inputs['ColorOverlay'])

        if self._have_texture('g_tNormalRoughnessOverlay'):
            overlay_normalroughness_texture = self._get_texture('g_tNormalRoughnessOverlay', (0.5, 0.5, 0.5, 0.5), True)
            self.connect_nodes(mapping.outputs['Overlay'], overlay_normalroughness_texture.inputs['Vector'])
            self.connect_nodes(overlay_normalroughness_texture.outputs['Color'], shader.inputs['NormalOverlay'])
            self.connect_nodes(overlay_normalroughness_texture.outputs['Alpha'], shader.inputs['RoughnessOverlay'])

        panel_uv_overlay(['Darkness Contrast']).default_value = material_data.get_float_property('g_flOverlayDarknessContrast', 1)
        panel_uv_overlay(['Brightness Contrast']).default_value = material_data.get_float_property('g_flOverlayBrightnessContrast', 1)

        # UV REVEAL OVERLAY
        # Even though Valve categorizes this under layer 2, its handled with all the other overlays.
        layer_uvrevealoverlay = get_input_socket(self.get_nodetree_item_from_path(shader_tree, ['UV Reveal Overlay'], bpy.types.NodeTreeInterfacePanel, ignore_level=True))
        if self._have_texture('g_tRevealOverlay'):
            overlay_reveal_texture = self._get_texture('g_tRevealOverlay', (0, 0, 0, 1), True)
            self.connect_nodes(mapping.outputs['Overlay'], overlay_reveal_texture.inputs['Vector'])
            self.connect_nodes(overlay_reveal_texture.outputs['Color'], layer_uvrevealoverlay(['Reveal Overlay']))

            layer_uvrevealoverlay(['Invert']).default_value = self._check_flag('g_bRevealOverlayInvert2')
            layer_uvrevealoverlay(['Strength']).default_value = material_data.get_float_property('g_flRevealOverlayStrength2', 1)

        # WORLD OVERLAY
        world_overlay_count = material_data.get_int_property('F_WORLD_OVERLAY', 0)
        if world_overlay_count > 0:
            world_overlay_panel = get_input_socket(self.get_nodetree_item_from_path(shader_tree, ['World Overlay'], bpy.types.NodeTreeInterfacePanel))
            if world_overlay_count == 1:
                world_overlay_panel(['World Overlays']).default_value = '1'
                # If there's only one world overlay, they store values as float, otherwise they store them as vec2.
                world_overlay_panel(['Darkness Contrast']).default_value = (material_data.get_float_property('g_flWorldOverlayDarknessContrast', 1), 1)
                world_overlay_panel(['Brightness Contrast']).default_value = (material_data.get_float_property('g_flWorldOverlayBrightnessContrast', 1), 1)
                tiling_values = (material_data.get_float_property('g_flWorldOverlayTiling', 512), 512)
            else:
                world_overlay_panel(['World Overlays']).default_value = '2'
                world_overlay_panel(['Darkness Contrast']).default_value = material_data.get_vector_property('g_vWorldOverlayDarknessContrast', (1, 1))[:2]
                world_overlay_panel(['Brightness Contrast']).default_value = material_data.get_vector_property('g_vWorldOverlayBrightnessContrast', (1, 1))[:2]
                tiling_values = material_data.get_vector_property('g_vWorldOverlayTiling', (512, 512))

            mapping.inputs['Use Object Space'].default_value = self._check_flag('F_WORLD_OVERLAY_USE_OBJECT_SPACE')
            mapping.inputs['Tiling'].default_value = tiling_values

            if self._have_texture('g_tColorWorldOverlay'):
                world_overlay1_texture = self._get_texture('g_tColorWorldOverlay', (0.5, 0.5, 0.5, 1))
                # World overlays are always biplanar projection
                world_overlay1_texture.projection = 'BOX'
                self.connect_nodes(world_overlay1_texture.outputs['Color'], shader.inputs['WorldOverlay1'])
                self.connect_nodes(mapping.outputs['WorldOverlay 1'], world_overlay1_texture.inputs['Vector'])

            if self._have_texture('g_tColorWorldOverlay2'):
                world_overlay2_texture = self._get_texture('g_tColorWorldOverlay2', (0.5, 0.5, 0.5, 1))
                world_overlay2_texture.projection = 'BOX'
                self.connect_nodes(world_overlay2_texture.outputs['Color'], shader.inputs['WorldOverlay2'])
                self.connect_nodes(mapping.outputs['WorldOverlay 2'], world_overlay2_texture.inputs['Vector'])
