import math
from typing import Any

import bpy
import numpy as np

from ..source2_shader_base import Source2ShaderBase
from ...shader_base import Nodes, ExtraMaterialParameters
from .....library.shared.content_manager import ContentManager
from .....library.source2 import CompiledTextureResource
from .....library.source2.blocks.texture_data import VTexFormat
from .....library.utils.path_utilities import path_stem
from .....library.utils.thirdparty.equilib.cube2equi_numpy import run as convert_to_eq
from .....logger import SourceLogMan

log_manager = SourceLogMan()


class Skybox(Source2ShaderBase):
    SHADER: str = 'sky.vfx'

    def __init__(self, content_manager: ContentManager, source2_material, yaw: float = 0.0,
                 brightness: float = 1.0, tint: tuple[float, float, float] = (1.0, 1.0, 1.0)):
        """``yaw`` (degrees), ``brightness`` and ``tint`` come from the ``env_sky`` that shows the sky."""
        super().__init__(content_manager, source2_material)
        self.content_manager = content_manager
        self.logger = log_manager.get_logger(f'Shaders::{self.SHADER}')
        self.do_arrange = True
        self.yaw = yaw
        self.brightness = brightness
        self.tint = tint

    @property
    def sky_texture(self):

        texture_path = self._material_resource.get_texture_property('g_tSkyTexture', None)
        if texture_path:
            texture_resource = self._material_resource.get_child_resource(texture_path, self.content_manager,
                                                                          CompiledTextureResource)
            (width, height) = texture_resource.get_resolution(0)
            faces = {}
            for i, k in enumerate("FBLRUD"):
                data, _ = texture_resource.get_cubemap_face(i, 0)
                side = data.reshape((width, height, 4))
                if k == 'B':
                    side = np.rot90(side, 2)
                if k == 'L':
                    side = np.rot90(side, 3)
                if k == 'R':
                    side = np.rot90(side, 1)
                faces[k] = side.T

            pixel_data = convert_to_eq(faces, "dict", 2048, 1024, 'default', 'bilinear').T
            pixel_data = np.rot90(pixel_data, 1)
            # pixel_data = np.flipud(pixel_data)
            name = path_stem(texture_path)
            image = bpy.data.images.new(
                name + '.tga',
                width=2048,
                height=1024,
                alpha=True
            )
            image.alpha_mode = 'CHANNEL_PACKED'
            if pixel_data.shape[0] == 0:
                return None

            pixel_format = texture_resource.get_texture_format()
            if pixel_format in (VTexFormat.RGBA16161616F, VTexFormat.BC6H):
                image.use_generated_float = True
                image.file_format = 'HDR'
                image.pixels.foreach_set(pixel_data.astype(np.float32).ravel())
            else:
                image.file_format = 'PNG'
                image.pixels.foreach_set(pixel_data.ravel())

            image.pack()
            return image
        return None

    def create_nodes(self, material:bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        self.logger.info(f'Creating material {repr(material.name)}')
        self.bpy_material = material

        if self.bpy_material is None:
            self.logger.error('Failed to get or create material')
            return 'UNKNOWN'

        if self.bpy_material.get('source_loaded'):
            return 'LOADED'

        self.clean_nodes()
        self.bpy_material['source_loaded'] = True

        material_output = self.create_node(Nodes.ShaderNodeOutputWorld)
        shader = self.create_node(Nodes.ShaderNodeBackground, self.SHADER)
        self.connect_nodes(shader.outputs['Background'], material_output.inputs['Surface'])

        # The equirectangular image has the cubemap's +X (Source's forward) at its center, which is Blender's +X.
        # env_sky turns the sky by its yaw, counterclockwise seen from above: look the texture up at -yaw.
        texture = self.create_node(Nodes.ShaderNodeTexEnvironment)
        texture.image = self.sky_texture
        if self.yaw % 360:
            coordinates = self.create_node(Nodes.ShaderNodeTexCoord)
            mapping = self.create_node(Nodes.ShaderNodeMapping)
            mapping.inputs['Rotation'].default_value = (0.0, 0.0, math.radians(-self.yaw))
            self.connect_nodes(coordinates.outputs['Generated'], mapping.inputs['Vector'])
            self.connect_nodes(mapping.outputs['Vector'], texture.inputs['Vector'])

        color_output = texture.outputs['Color']
        if tuple(self.tint) != (1.0, 1.0, 1.0):
            color_output = self.insert_generic_tint(color_output, (*self.tint, 1.0))
        self.connect_nodes(color_output, shader.inputs['Color'])

        # Exposure biases are in stops; the render-only one applies to the visible sky, not to the light it gives.
        material = self._material_resource
        strength = self.brightness * 2.0 ** material.get_float_property('g_flBrightnessExposureBias', 0.0)
        render_only = material.get_float_property('g_flRenderOnlyExposureBias', 0.0)
        shader.inputs['Strength'].default_value = strength
        if render_only:
            light_path = self.create_node(Nodes.ShaderNodeLightPath)
            camera_strength = self.create_node(Nodes.ShaderNodeMapRange, 'render only exposure')
            camera_strength.inputs['To Min'].default_value = strength
            camera_strength.inputs['To Max'].default_value = strength * 2.0 ** render_only
            self.connect_nodes(light_path.outputs['Is Camera Ray'], camera_strength.inputs['Value'])
            self.connect_nodes(camera_strength.outputs['Result'], shader.inputs['Strength'])
