from pprint import pformat
from typing import Any

import bpy

from ...shader_base import Nodes, ExtraMaterialParameters
from ..node_math import NodeMath
from ..source2_shader_base import Source2ShaderBase, OPAQUE, ALPHA_TEST
from .....library.source2.blocks.kv3_block import KVBlock


class CSGOUnlitGeneric(Source2ShaderBase):
    """Unlit: the color is drawn as it is (an Emission shader), blended by F_BLEND_MODE.

    The color is g_tColor (times g_tColor2 with F_TWOTEXTURE, alpha too), gradient modulation, vertex color,
    the color tint and the model tint. The blend modes are ``Source2ShaderBase._blended_surface``'s. Additive
    multiplies the color by its alpha: nuke_clouds_002 (the de_dust2 skybox's cloud cards) is white in both
    textures, with the clouds in alpha.

    The primary UV is transformed, moved into its texture-animation atlas cell, then scrolled in UV units per
    second. The second texture keeps its independent transform. Compiled material expressions that dynamically
    drive these follow-up parameters are not evaluated; the affected feature is explicitly left disabled rather
    than approximated from a stale static value.
    """
    SHADER: str = 'csgo_unlitgeneric.vfx'

    def _has_dynamic_parameter(self, *names: str) -> bool:
        return bool(self._dynamic_parameters.intersection(names))

    def _time_seconds(self):
        time = self.get_node("Source 2 Time")
        if time is not None:
            return time.outputs[0]

        time = self.create_node(Nodes.ShaderNodeValue, "Source 2 Time")
        driver = time.outputs[0].driver_add("default_value").driver
        driver.type = 'SCRIPTED'
        driver.expression = "frame * fps_base / fps"
        scene = bpy.context.scene
        for name, data_path in (
                ("fps", "render.fps"),
                ("fps_base", "render.fps_base")):
            variable = driver.variables.new()
            variable.name = name
            variable.type = 'SINGLE_PROP'
            variable.targets[0].id_type = 'SCENE'
            variable.targets[0].id = scene
            variable.targets[0].data_path = data_path
        return time.outputs[0]

    def _animated_uv(self, uv_output, m: NodeMath):
        if not self._check_flag("F_TEXTURE_ANIMATION"):
            return uv_output
        parameter_names = (
            "g_vAnimationGrid",
            "g_nNumAnimationCells",
            "g_flAnimationTimePerFrame",
            "g_flAnimationTimeOffset",
            "g_flAnimationFrame",
        )
        if self._has_dynamic_parameter(*parameter_names):
            dynamic = sorted(self._dynamic_parameters.intersection(parameter_names))
            self.logger.warn(
                f"Dynamic texture-animation parameters are unsupported ({', '.join(dynamic)}); "
                "texture animation is disabled")
            return uv_output

        material_data = self._material_resource
        grid = material_data.get_vector_property("g_vAnimationGrid", (1.0, 1.0, 0.0, 0.0))
        grid_x, grid_y = int(grid[0]), int(grid[1])
        cell_count = material_data.get_int_property("g_nNumAnimationCells", 1)
        if grid_x < 1 or grid_y < 1 or cell_count < 1:
            self.logger.warn(
                f"Invalid texture-animation grid {grid_x}x{grid_y} with {cell_count} cells; "
                "texture animation is disabled")
            return uv_output

        mode = material_data.get_int_property("F_TEXTURE_ANIMATION_MODE", 0)
        if mode == 0:
            seconds_per_frame = material_data.get_float_property("g_flAnimationTimePerFrame", 0.1)
            if seconds_per_frame <= 0.0:
                self.logger.warn(
                    f"Invalid g_flAnimationTimePerFrame {seconds_per_frame}; texture animation is disabled")
                return uv_output
            frame = m.math(
                'FLOOR',
                (m.scalar(self._time_seconds())
                 + material_data.get_float_property("g_flAnimationTimeOffset", 0.0)) / seconds_per_frame)
            frame = m.math('MODULO', frame, cell_count)
        elif mode == 1:
            seed = m.math('FLOOR', material_data.get_float_property("g_flAnimationFrame", 0.0))
            random_value = m.sin(seed * (12.9898 + 78.233)) * 43758.546875
            frame = m.math('FLOOR', m.math('FRACT', random_value) * cell_count)
        elif mode == 2:
            frame = m.math('FLOOR', material_data.get_float_property("g_flAnimationFrame", 0.0))
        else:
            self.logger.warn(f"Unknown F_TEXTURE_ANIMATION_MODE {mode}; texture animation is disabled")
            return uv_output

        column = m.math('MODULO', frame, grid_x)
        row = m.math('FLOOR', frame / grid_x)
        # Imported UVs are V-flipped, so Source's top-to-bottom row becomes this bottom-to-top Blender row.
        offset = m.combine(column, grid_y - 1.0 - row, 0.0)
        animated = (m.vector(uv_output) + offset) * (1.0 / grid_x, 1.0 / grid_y, 1.0)
        animated.socket.node.name = animated.socket.node.label = "Texture Animation UV"
        return animated.socket

    def _scrolled_uv(self, uv_output, m: NodeMath):
        scroll = self._material_resource.get_vector_property("g_vTexCoordScrollSpeed", (0.0, 0.0, 0.0, 0.0))
        if not any(scroll[:2]):
            return uv_output
        if self._has_dynamic_parameter("g_vTexCoordScrollSpeed"):
            self.logger.warn(
                "Dynamic g_vTexCoordScrollSpeed is unsupported; texture-coordinate scrolling is disabled")
            return uv_output

        # U is unchanged by import; V is flipped between Source and Blender.
        scrolled = m.vector(uv_output) + m.vector((scroll[0], -scroll[1], 0.0)) * m.scalar(self._time_seconds())
        scrolled.socket.node.name = scrolled.socket.node.label = "Texture Coordinate Scroll"
        return scrolled.socket

    def _gradient_modulated(self, color_output, uv_output, m: NodeMath):
        if not self._check_flag("F_GRADIENTMODULATION"):
            return color_output
        parameter_names = (
            "g_vGradientColorStop0",
            "g_vGradientColorStop1",
            "g_vGradientColorStop2",
            "g_vGradientModulation_StopPositions",
        )
        if self._has_dynamic_parameter(*parameter_names):
            dynamic = sorted(self._dynamic_parameters.intersection(parameter_names))
            self.logger.warn(
                f"Dynamic gradient parameters are unsupported ({', '.join(dynamic)}); "
                "gradient modulation is disabled")
            return color_output

        stops = []
        for index in range(3):
            value = self._material_resource.get_vector_property(
                f"g_vGradientColorStop{index}", (1.0, 1.0, 1.0, 0.0))
            stop = self.create_node(Nodes.ShaderNodeRGB, f"g_vGradientColorStop{index}")
            stop.outputs["Color"].default_value = (*value[:3], 1.0)
            stops.append(m.vector(self._srgb_to_linear(stop.outputs["Color"])))

        positions = self._material_resource.get_vector_property(
            "g_vGradientModulation_StopPositions", (1.0, 1.0, 1.0, 0.0))
        source_v = m.saturate(1.0 - m.vector(uv_output).y)
        first_weight = m.smoothstep(positions[0], positions[1], source_v)
        second_weight = m.smoothstep(positions[1], positions[2], source_v)
        gradient = m.lerp(m.lerp(stops[0], stops[1], first_weight), stops[2], second_weight * second_weight)
        gradient.socket.node.name = gradient.socket.node.label = "Gradient Modulation"
        return self._multiply(color_output, gradient.socket)

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        material_output = self.create_node(Nodes.ShaderNodeOutputMaterial)
        material_data = self._material_resource
        data = self._material_resource.get_block(KVBlock, block_name='DATA')
        self._dynamic_parameters = {parameter["m_name"] for parameter in data.get("m_dynamicParams", ())}
        self.logger.info(pformat(dict(data)))
        m = NodeMath(material.node_tree, self.create_node)

        primary_uv = self._animated_uv(self._texcoord_transform().outputs[0], m)
        primary_uv = self._scrolled_uv(primary_uv, m)
        color_texture = self._get_texture("g_tColor", (1, 1, 1, 1))
        self.connect_nodes(primary_uv, color_texture.inputs[0])
        color_output, alpha_output = color_texture.outputs[0], color_texture.outputs[1]

        if self._check_flag("F_TWOTEXTURE"):
            color2_texture = self._get_texture("g_tColor2", (1, 1, 1, 1))
            # Unlike the first texture's, this transform scales about the center.
            transform = self.create_transform("TEXCOORD",
                                              material_data.get_vector_property("g_vTex2CoordScale", (1.0, 1.0, 0.0)),
                                              material_data.get_vector_property("g_vTex2CoordOffset", (0.0, 0.0, 0.0)),
                                              material_data.get_vector_property("g_vTex2CoordCenter", (0.5, 0.5, 0.0)),
                                              material_data.get_float_property("g_flTex2CoordRotation", 0.0),
                                              scale_about_center=True)
            self.connect_nodes(transform.outputs[0], color2_texture.inputs[0])
            color_output = self._multiply(color_output, color2_texture.outputs[0])
            alpha_multiply = self.create_node(Nodes.ShaderNodeMath)
            alpha_multiply.operation = 'MULTIPLY'
            self.connect_nodes(alpha_output, alpha_multiply.inputs[0])
            self.connect_nodes(color2_texture.outputs[1], alpha_multiply.inputs[1])
            alpha_output = alpha_multiply.outputs[0]
        else:
            self._skip_texture("g_tColor2")

        color_output = self._gradient_modulated(color_output, primary_uv, m)
        if self._check_flag("F_VERTEX_COLOR"):
            vertex_color = self._vertex_color()
            color_output = self._multiply(color_output, self._srgb_to_linear(vertex_color.outputs["Color"]))

            alpha_vector = self.create_node(Nodes.ShaderNodeCombineXYZ, "Vertex Color Alpha")
            self.connect_nodes(vertex_color.outputs["Alpha"], alpha_vector.inputs["X"])
            alpha_linear = self._srgb_to_linear(alpha_vector.outputs["Vector"])
            alpha_channel = self.create_node(Nodes.ShaderNodeSeparateXYZ, "Linear Vertex Color Alpha")
            self.connect_nodes(alpha_linear, alpha_channel.inputs["Vector"])
            vertex_alpha = m.scalar(alpha_output) * m.scalar(alpha_channel.outputs["X"])
            vertex_alpha.socket.node.name = vertex_alpha.socket.node.label = "Vertex Color Alpha"
            alpha_output = vertex_alpha.socket

        color_output = self._tinted(color_output)
        blend_mode = material_data.get_int_property("F_BLEND_MODE", OPAQUE)
        if blend_mode not in (OPAQUE, ALPHA_TEST):
            alpha_output = self._opacity_scaled(alpha_output)
        surface = self._blended_surface(color_output, alpha_output, blend_mode, alpha_test_reference=
                                        material_data.get_float_property("g_flAlphaTestReference", 0.5))
        self.connect_nodes(surface, material_output.inputs['Surface'])
