import sys
from abc import abstractmethod
from enum import Enum
from typing import Optional, Any

import bpy
import numpy as np

from ..utils.bpy_utils import append_blend
from ...library.utils.tiny_path import TinyPath
from ...logger import SourceLogMan
from .node_arranger import nodes_iterate


ALPHA_CLIP_LABEL = "Alpha clip"
BILINEAR_TAPS_GROUP = "SourceIO Bilinear Taps"


def _bilinear_taps_group() -> bpy.types.ShaderNodeTree:
    """The four texel centres around a UV, and the weights between them, for an image of the given size:
    p = uv * size - 0.5, base = floor(p), weights = p - base, taps at (base + 0.5 + {0, 1}) / size."""
    group = bpy.data.node_groups.get(BILINEAR_TAPS_GROUP)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(BILINEAR_TAPS_GROUP, "ShaderNodeTree")
    interface = group.interface
    for name in ("UV 00", "UV 10", "UV 01", "UV 11", "Weights"):
        interface.new_socket(name, in_out="OUTPUT", socket_type="NodeSocketVector")
    interface.new_socket("UV", in_out="INPUT", socket_type="NodeSocketVector")
    interface.new_socket("Size", in_out="INPUT", socket_type="NodeSocketVector").default_value = (1, 1, 1)
    nodes, links = group.nodes, group.links
    group_input = nodes.new("NodeGroupInput")
    group_output = nodes.new("NodeGroupOutput")

    def vector_math(operation, *inputs):
        node = nodes.new("ShaderNodeVectorMath")
        node.operation = operation
        for index, value in enumerate(inputs):
            if isinstance(value, bpy.types.NodeSocket):
                links.new(value, node.inputs[index])
            else:
                node.inputs[index].default_value = value
        return node.outputs[0]

    size = group_input.outputs["Size"]
    position = vector_math('MULTIPLY_ADD', group_input.outputs["UV"], size, (-0.5, -0.5, 0.0))
    base = vector_math('FLOOR', position)
    links.new(vector_math('SUBTRACT', position, base), group_output.inputs["Weights"])
    for name, offset in (("UV 00", (0.5, 0.5, 0.0)), ("UV 10", (1.5, 0.5, 0.0)),
                         ("UV 01", (0.5, 1.5, 0.0)), ("UV 11", (1.5, 1.5, 0.0))):
        links.new(vector_math('DIVIDE', vector_math('ADD', base, offset), size), group_output.inputs[name])
    return group


def unfilter_alpha_clips(material: bpy.types.Material):
    """Sample the alpha of every alpha clip that reads an image straight at full resolution.

    EEVEE (and the viewport) samples textures through mipmaps it builds by averaging, so a thin alpha-tested
    texture averages below its cutoff a few metres away and vanishes: 19% of a chain-link fence's texels are
    wire, and from 10 m its alpha is mostly under 0.5. Cycles reads the full-resolution image and keeps it. The
    game's own mipmaps are made to keep the coverage, but Blender builds its own. Closest lookups skip the
    mipmaps, so the alpha is four of them blended bilinearly, as Cycles' Linear interpolation does.
    """
    nodes, links = material.node_tree.nodes, material.node_tree.links
    clips = [node for node in nodes if node.bl_idname == Nodes.ShaderNodeMath and node.label == ALPHA_CLIP_LABEL]
    for clip in clips:
        if not clip.inputs[0].links:
            continue
        link = clip.inputs[0].links[0]
        image_node = link.from_node
        if (image_node.bl_idname != Nodes.ShaderNodeTexImage or link.from_socket.name != "Alpha"
                or image_node.image is None or image_node.interpolation == 'Closest'
                or image_node.projection != 'FLAT' or 0 in image_node.image.size):
            continue
        vector_links = image_node.inputs["Vector"].links
        if vector_links:
            uv = vector_links[0].from_socket
        else:
            uv = nodes.new(Nodes.ShaderNodeTexCoord).outputs["UV"]
        taps = nodes.new(Nodes.ShaderNodeGroup)
        taps.node_tree = _bilinear_taps_group()
        taps.label = "Full resolution alpha"
        links.new(uv, taps.inputs["UV"])
        taps.inputs["Size"].default_value = (*image_node.image.size, 1)
        weights = nodes.new(Nodes.ShaderNodeSeparateXYZ)
        links.new(taps.outputs["Weights"], weights.inputs[0])

        alphas = []
        for name in ("UV 00", "UV 10", "UV 01", "UV 11"):
            tap = nodes.new(Nodes.ShaderNodeTexImage)
            tap.image = image_node.image
            tap.interpolation = 'Closest'
            tap.extension = image_node.extension
            tap.hide = True
            links.new(taps.outputs[name], tap.inputs["Vector"])
            alphas.append(tap.outputs["Alpha"])

        def lerp(a, b, factor):
            mix = nodes.new(Nodes.ShaderNodeMix)
            mix.data_type = 'FLOAT'
            links.new(factor, mix.inputs[0])
            links.new(a, mix.inputs[2])
            links.new(b, mix.inputs[3])
            return mix.outputs[0]

        bottom = lerp(alphas[0], alphas[1], weights.outputs["X"])
        top = lerp(alphas[2], alphas[3], weights.outputs["X"])
        links.new(lerp(bottom, top, weights.outputs["Y"]), clip.inputs[0])


class Nodes:
    ShaderNodeAddShader = 'ShaderNodeAddShader'
    ShaderNodeAmbientOcclusion = 'ShaderNodeAmbientOcclusion'
    ShaderNodeAttribute = 'ShaderNodeAttribute'
    ShaderNodeBackground = 'ShaderNodeBackground'
    ShaderNodeBevel = 'ShaderNodeBevel'
    ShaderNodeBlackbody = 'ShaderNodeBlackbody'
    ShaderNodeBrightContrast = 'ShaderNodeBrightContrast'
    ShaderNodeBsdfAnisotropic = 'ShaderNodeBsdfAnisotropic'
    ShaderNodeBsdfDiffuse = 'ShaderNodeBsdfDiffuse'
    ShaderNodeBsdfGlass = 'ShaderNodeBsdfGlass'
    ShaderNodeBsdfHair = 'ShaderNodeBsdfHair'
    ShaderNodeBsdfHairPrincipled = 'ShaderNodeBsdfHairPrincipled'
    ShaderNodeBsdfPrincipled = 'ShaderNodeBsdfPrincipled'
    ShaderNodeBsdfRefraction = 'ShaderNodeBsdfRefraction'
    ShaderNodeBsdfToon = 'ShaderNodeBsdfToon'
    ShaderNodeBsdfTranslucent = 'ShaderNodeBsdfTranslucent'
    ShaderNodeBsdfTransparent = 'ShaderNodeBsdfTransparent'
    ShaderNodeBump = 'ShaderNodeBump'
    ShaderNodeCameraData = 'ShaderNodeCameraData'
    ShaderNodeClamp = 'ShaderNodeClamp'
    ShaderNodeCombineColor = 'ShaderNodeCombineColor'
    ShaderNodeCombineXYZ = 'ShaderNodeCombineXYZ'
    ShaderNodeCustomGroup = 'ShaderNodeCustomGroup'
    ShaderNodeDisplacement = 'ShaderNodeDisplacement'
    ShaderNodeEeveeSpecular = 'ShaderNodeEeveeSpecular'
    ShaderNodeEmission = 'ShaderNodeEmission'
    ShaderNodeFresnel = 'ShaderNodeFresnel'
    ShaderNodeGamma = 'ShaderNodeGamma'
    ShaderNodeGroup = 'ShaderNodeGroup'
    ShaderNodeHairInfo = 'ShaderNodeHairInfo'
    ShaderNodeHoldout = 'ShaderNodeHoldout'
    ShaderNodeHueSaturation = 'ShaderNodeHueSaturation'
    ShaderNodeInvert = 'ShaderNodeInvert'
    ShaderNodeLayerWeight = 'ShaderNodeLayerWeight'
    ShaderNodeLightFalloff = 'ShaderNodeLightFalloff'
    ShaderNodeLightPath = 'ShaderNodeLightPath'
    ShaderNodeMapRange = 'ShaderNodeMapRange'
    ShaderNodeMapping = 'ShaderNodeMapping'
    ShaderNodeMath = 'ShaderNodeMath'
    ShaderNodeMix = 'ShaderNodeMix'
    ShaderNodeMixShader = 'ShaderNodeMixShader'
    ShaderNodeNewGeometry = 'ShaderNodeNewGeometry'
    ShaderNodeNormal = 'ShaderNodeNormal'
    ShaderNodeNormalMap = 'ShaderNodeNormalMap'
    ShaderNodeObjectInfo = 'ShaderNodeObjectInfo'
    ShaderNodeOutputAOV = 'ShaderNodeOutputAOV'
    ShaderNodeOutputLight = 'ShaderNodeOutputLight'
    ShaderNodeOutputLineStyle = 'ShaderNodeOutputLineStyle'
    ShaderNodeOutputMaterial = 'ShaderNodeOutputMaterial'
    ShaderNodeOutputWorld = 'ShaderNodeOutputWorld'
    ShaderNodeParticleInfo = 'ShaderNodeParticleInfo'
    ShaderNodeRGB = 'ShaderNodeRGB'
    ShaderNodeRGBCurve = 'ShaderNodeRGBCurve'
    ShaderNodeRGBToBW = 'ShaderNodeRGBToBW'
    ShaderNodeScript = 'ShaderNodeScript'
    ShaderNodeSeparateColor = 'ShaderNodeSeparateColor'
    ShaderNodeSeparateXYZ = 'ShaderNodeSeparateXYZ'
    ShaderNodeShaderToRGB = 'ShaderNodeShaderToRGB'
    ShaderNodeSqueeze = 'ShaderNodeSqueeze'
    ShaderNodeSubsurfaceScattering = 'ShaderNodeSubsurfaceScattering'
    ShaderNodeTangent = 'ShaderNodeTangent'
    ShaderNodeTexBrick = 'ShaderNodeTexBrick'
    ShaderNodeTexChecker = 'ShaderNodeTexChecker'
    ShaderNodeTexCoord = 'ShaderNodeTexCoord'
    ShaderNodeTexEnvironment = 'ShaderNodeTexEnvironment'
    ShaderNodeTexGradient = 'ShaderNodeTexGradient'
    ShaderNodeTexIES = 'ShaderNodeTexIES'
    ShaderNodeTexImage = 'ShaderNodeTexImage'
    ShaderNodeTexMagic = 'ShaderNodeTexMagic'
    ShaderNodeTexNoise = 'ShaderNodeTexNoise'
    ShaderNodeTexSky = 'ShaderNodeTexSky'
    ShaderNodeTexVoronoi = 'ShaderNodeTexVoronoi'
    ShaderNodeTexWave = 'ShaderNodeTexWave'
    ShaderNodeTexWhiteNoise = 'ShaderNodeTexWhiteNoise'
    ShaderNodeUVAlongStroke = 'ShaderNodeUVAlongStroke'
    ShaderNodeUVMap = 'ShaderNodeUVMap'
    ShaderNodeValToRGB = 'ShaderNodeValToRGB'
    ShaderNodeValue = 'ShaderNodeValue'
    ShaderNodeVectorCurve = 'ShaderNodeVectorCurve'
    ShaderNodeVectorDisplacement = 'ShaderNodeVectorDisplacement'
    ShaderNodeVectorMath = 'ShaderNodeVectorMath'
    ShaderNodeVectorRotate = 'ShaderNodeVectorRotate'
    ShaderNodeVectorTransform = 'ShaderNodeVectorTransform'
    ShaderNodeVertexColor = 'ShaderNodeVertexColor'
    ShaderNodeVolumeAbsorption = 'ShaderNodeVolumeAbsorption'
    ShaderNodeVolumeInfo = 'ShaderNodeVolumeInfo'
    ShaderNodeVolumePrincipled = 'ShaderNodeVolumePrincipled'
    ShaderNodeVolumeScatter = 'ShaderNodeVolumeScatter'
    ShaderNodeWavelength = 'ShaderNodeWavelength'
    ShaderNodeWireframe = 'ShaderNodeWireframe'


# Socket indices of ShaderNodeMix with data_type='RGBA'
MIX_FACTOR = 0
MIX_A = 6
MIX_B = 7
MIX_RESULT = 2

log_manager = SourceLogMan()
logger = log_manager.get_logger('MaterialLoader')


class ExtraMaterialParameters(str, Enum):
    USE_OBJECT_TINT = "UseObjectTint"


class ShaderBase:
    SHADER: str = "Unknown"
    use_bvlg_status = True

    @classmethod
    def use_bvlg(cls, status):
        cls.use_bvlg_status = status

    @staticmethod
    def load_bvlg_nodes():
        if "VertexLitGeneric" not in bpy.data.node_groups:
            current_path = TinyPath(__file__).parent.parent
            asset_path = current_path / 'assets' / "sycreation-s-default.blend"
            append_blend(str(asset_path), "node_groups")

    @staticmethod
    def load_source2_nodes():
        if "csgo_complex.vfx" not in bpy.data.node_groups:
            current_path = TinyPath(__file__).parent.parent
            asset_path = current_path / 'assets' / "source2_materials.blend"
            append_blend(str(asset_path), "node_groups")

    @staticmethod
    def load_source2_nodes_blender5_0():
        if "environment_blend.vfx 5.0" not in bpy.data.node_groups:
            current_path = TinyPath(__file__).parent.parent
            asset_path = current_path / 'assets' / "source2_materials_5_0.blend"
            append_blend(str(asset_path), "node_groups")

    def insert_object_tint(self, color_output_socket, tint_amount=1.0, tint_mask_output: None | object = None):
        color_multiply_node = self.create_mix_color('MULTIPLY')
        if tint_mask_output is None:
            color_multiply_node.inputs[MIX_FACTOR].default_value = tint_amount
        else:
            self.connect_nodes(tint_mask_output, color_multiply_node.inputs[MIX_FACTOR])
        self.connect_nodes(color_output_socket, color_multiply_node.inputs[MIX_A])
        object_color = self.create_node(Nodes.ShaderNodeObjectInfo)
        self.connect_nodes(object_color.outputs["Color"], color_multiply_node.inputs[MIX_B])

        return color_multiply_node.outputs[MIX_RESULT]

    def insert_generic_tint(self, color_output_socket, tint: tuple[float, ...], tint_amount=1.0,
                            tint_mask_output=None | object):
        color_multiply_node = self.create_mix_color('MULTIPLY')
        if tint_mask_output is None:
            color_multiply_node.inputs[MIX_FACTOR].default_value = tint_amount
        else:
            self.connect_nodes(tint_mask_output, color_multiply_node.inputs[MIX_FACTOR])
        self.connect_nodes(color_output_socket, color_multiply_node.inputs[MIX_A])
        color_multiply_node.inputs[MIX_B].default_value = tint

        return color_multiply_node.outputs[MIX_RESULT]

    @staticmethod
    def ensure_length(array: list, length, filler):
        if len(array) < length:
            array.extend([filler] * (length - len(array)))
            return array
        elif len(array) > length:
            return array[:length]
        return array

    @classmethod
    def all_subclasses(cls):
        return set(cls.__subclasses__()).union([s for c in cls.__subclasses__() for s in c.all_subclasses()])

    def __init__(self):
        self.logger = log_manager.get_logger(f'Shaders::{self.SHADER}')
        self.bpy_material: bpy.types.Material = None
        self.do_arrange = True
        self.uv_map = None

    @staticmethod
    def get_missing_texture(texture_name: str, fill_color: tuple = (1.0, 1.0, 1.0, 1.0)):
        assert len(fill_color) == 4, 'Fill color should be in RGBA format'
        if bpy.data.images.get(texture_name, None):
            return bpy.data.images.get(texture_name)
        else:
            image = bpy.data.images.new(texture_name, width=512, height=512, alpha=False)
            image_data = np.full((512 * 512, 4), fill_color, np.float32).flatten()
            image.pixels.foreach_set(image_data.ravel())
            return image

    def load_texture(self, texture_name:TinyPath, texture_path: TinyPath | None = None) -> Optional[bpy.types.Image]:
        pass

    @staticmethod
    def make_texture(texture_name, texture_dimm, texture_data, raw_texture=False):
        image = bpy.data.images.new(texture_name, width=texture_dimm[0], height=texture_dimm[1], alpha=True)
        image.alpha_mode = 'CHANNEL_PACKED'
        image.file_format = 'TARGA'
        image.pixels.foreach_set(texture_data.ravel())
        image.pack()
        if raw_texture:
            image.colorspace_settings.is_data = True
            image.colorspace_settings.name = 'Non-Color'
        return image

    @staticmethod
    def split_to_channels(image):
        buffer = np.zeros(image.size[0] * image.size[1] * 4, np.float32)
        image.pixels.foreach_get(buffer)
        return buffer[0::4], buffer[1::4], buffer[2::4], buffer[3::4],

    def load_texture_or_default(self, file: str, default_color: tuple = (1.0, 1.0, 1.0, 1.0)):
        # Some VMT's in VtMB have a basetexture path starting with a /
        if file.startswith("/"):
            file = file[1:]
        file = TinyPath(file)
        if len(file.parts) == 1:
            texture = self.load_texture(file.stem, None)
        else:
            texture = self.load_texture(file.stem, file.parent)
        return texture or self.get_missing_texture(f'missing_{file.stem}', default_color)

    @staticmethod
    def clamp_value(value, min_value=0.0, max_value=1.0):
        return min(max_value, max(value, min_value))

    @staticmethod
    def new_texture_name_with_suffix(old_name, suffix, ext):
        old_name = TinyPath(old_name)
        return f'{old_name.with_name(old_name.stem)}_{suffix}.{ext}'

    def set_blend_mode(self, mode: str):
        """Configure material transparency.

        ``mode`` is ``'OPAQUE'``, ``'CLIP'``, ``'HASHED'`` or ``'BLEND'``. Only ``'BLEND'`` produces true alpha
        blending, everything else is dithered. Alpha clipping has to be built in nodes, see ``insert_alpha_clip``.
        """
        self.bpy_material.surface_render_method = 'BLENDED' if mode == 'BLEND' else 'DITHERED'

    def insert_alpha_clip(self, alpha_socket, threshold: float):
        clip_node = self.create_node(Nodes.ShaderNodeMath)
        clip_node.operation = 'GREATER_THAN'
        clip_node.label = ALPHA_CLIP_LABEL  # unfilter_alpha_clips finds it once the material is built
        self.connect_nodes(alpha_socket, clip_node.inputs[0])
        clip_node.inputs[1].default_value = threshold
        return clip_node.outputs[0]

    def disable_shadow_casting(self):
        """Make the material invisible to shadow rays. Must be called after the surface shader is connected."""
        nodes = self.bpy_material.node_tree.nodes
        output = next((node for node in nodes if node.bl_idname == Nodes.ShaderNodeOutputMaterial
                       and node.is_active_output), None)
        if output is None:
            return
        surface = output.inputs['Surface']
        light_path = self.create_node(Nodes.ShaderNodeLightPath)
        transparent = self.create_node(Nodes.ShaderNodeBsdfTransparent)
        shadow_mix = self.create_node(Nodes.ShaderNodeMixShader)
        self.connect_nodes(light_path.outputs['Is Shadow Ray'], shadow_mix.inputs[0])
        if surface.links:
            self.connect_nodes(surface.links[0].from_socket, shadow_mix.inputs[1])
        self.connect_nodes(transparent.outputs[0], shadow_mix.inputs[2])
        self.connect_nodes(shadow_mix.outputs[0], surface)

    def create_mix_color(self, blend_type: str = 'MIX', name: str = None):
        node = self.create_node(Nodes.ShaderNodeMix, name)
        node.data_type = 'RGBA'
        node.blend_type = blend_type
        return node

    def clean_nodes(self):
        for node in self.bpy_material.node_tree.nodes:
            self.bpy_material.node_tree.nodes.remove(node)

    def create_node(self, node_type: str, name: str = None, location=None):
        node = self.bpy_material.node_tree.nodes.new(node_type)
        if name:
            node.name = name
            node.label = name
        if location is not None:
            node.location = location
        return node

    def create_node_group(self, group_name, location=None, *, name=None):
        group_node = self.create_node(Nodes.ShaderNodeGroup, name or group_name)
        group_node.node_tree = bpy.data.node_groups.get(group_name)
        group_node.width = 240
        if location is not None:
            group_node.location = location
        return group_node

    def create_texture_node(self, texture, name=None, location=None):
        texture_node = self.create_node(Nodes.ShaderNodeTexImage, name)
        if texture is not None:
            texture_node.image = texture
        if location is not None:
            texture_node.location = location
        return texture_node

    def create_and_connect_texture_node(self, texture, color_out_target=None, alpha_out_target=None, *, name=None,
                                        uv_out=None):
        texture_node = self.create_texture_node(texture, name)
        if color_out_target is not None:
            self.connect_nodes(texture_node.outputs['Color'], color_out_target)
        if alpha_out_target is not None:
            self.connect_nodes(texture_node.outputs['Alpha'], alpha_out_target)
        if uv_out is not None:
            self.connect_nodes(uv_out, texture_node.inputs[0])
        return texture_node

    def get_node(self, name):
        return self.bpy_material.node_tree.nodes.get(name, None)

    def connect_nodes(self, output_socket, input_socket):
        self.bpy_material.node_tree.links.new(output_socket, input_socket)

    def insert_node(self, output_socket, middle_input_socket, middle_output_socket):
        receivers = []
        for link in output_socket.links:
            receivers.append(link.to_socket)
            self.bpy_material.node_tree.links.remove(link)
        self.connect_nodes(output_socket, middle_input_socket)
        for receiver in receivers:
            self.connect_nodes(middle_output_socket, receiver)

    @abstractmethod
    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        raise NotImplementedError(f"create_nodes method should be implemented by {self.__class__.__name__}")

    def align_nodes(self):
        if not self.do_arrange:
            return
        nodes_iterate(self.bpy_material.node_tree)
        self.bpy_material.node_tree.nodes.update()

    def handle_transform(self, transform: tuple, socket: bpy.types.NodeSocket, loc=None, *, uv_node=None,
                         uv_layer_name=None):
        if loc is None:
            loc = socket.node.location
        if uv_node is not None:
            uv_node = uv_node
            uv_node.location = [-300 + loc[0], uv_node.location[1]]
            if self.uv_map is not None:
                self.uv_map.location = [-500 + loc[0], self.uv_map.location[1]]
        else:
            uv_node = self.create_node("ShaderNodeUVMap")
            uv_node.location = [-300 + loc[0], -20 + loc[1]]
        if uv_layer_name is not None:
            uv_node.uv_map = uv_layer_name
        mapping = self.create_node("ShaderNodeMapping")
        mapping.location = [-150 + loc[0], -20 + loc[1]]
        self.connect_nodes(uv_node.outputs[0], mapping.inputs[0])
        mapping.inputs[1].default_value = transform['translate']
        mapping.inputs[2].default_value = transform['rotate']
        mapping.inputs[3].default_value = transform['scale']
        # nodegroup.inputs[4].default_value = transform['center']
        self.connect_nodes(mapping.outputs[0], socket)
        return mapping, uv_node

    def add_uv_mapping(self, scale: tuple[float, float, float], loc=None, uv_layer_name=None):
        if loc is None:
            loc = [0, 0]
        uv_node = self.add_uv_node([-300 + loc[0], -20 + loc[1]], uv_layer_name)
        mapping = self.create_node("ShaderNodeMapping")
        mapping.location = [-150 + loc[0], -20 + loc[1]]
        self.connect_nodes(uv_node.outputs[0], mapping.inputs[0])
        mapping.inputs[3].default_value = scale
        return mapping

    def add_uv_node(self, loc=None, uv_layer_name=None):
        if loc is None:
            loc = [0, 0]
        uv_node = self.create_node("ShaderNodeUVMap")
        uv_node.location = [-300 + loc[0], -20 + loc[1]]
        if uv_layer_name is not None:
            uv_node.uv_map = uv_layer_name
        return uv_node

    # Helper function to get node tree items like sockets by name from node groups with panels,
    # in case there are multiple with the same name.
    # This may still fail if there are multiple items with the same name & type at the same level on the tree
    def get_nodetree_item_from_path(self, root: bpy.types.NodeTreeInterfaceItem | bpy.types.NodeTree, path: list[str],
                                    item_type: type = bpy.types.NodeTreeInterfaceItem, is_output=False,
                                    ignore_level=False):
        check_in_out = isinstance(item_type, bpy.types.NodeTreeInterfaceSocket)
        in_out_str = 'OUTPUT' if is_output else 'INPUT'

        if isinstance(root, bpy.types.NodeTreeInterfaceItem):
            curr_item = root
        else:
            # If the root is the node tree, that has a different variable to access
            # Quickly grab a value just in case this is the right one.
            curr_item = root.interface.items_tree[path[0]]

            # HACK: If we get a child, start going through the whole damn tree until we find the top level item we want
            if curr_item.parent.index != -1 and not ignore_level:
                new_item = None
                for i, item in enumerate(root.interface.items_tree):
                    if item.name == path[0] and item.parent.index == -1:
                        # if path len is 1, we're not looking for the root we're just trying to find the item.
                        if len(path) == 1 and isinstance(item, item_type) and (
                                check_in_out and curr_item.in_out != in_out_str):
                            continue
                        new_item = item
                        break

                if new_item is None:
                    self.logger.error(f'Could not find first item \'{path[0]}\'! Full path: {path}')
                    return None
                else:
                    curr_item = new_item
            # Remove the path segment we just accessed.
            del path[0]

        for path_segment in path:
            if curr_item.item_type == 'SOCKET':
                self.logger.error(
                    f'Current NodeTree item \'{curr_item.name}\' is a socket, but is not the last item in the path! Full path: {path}')

            # .get for some reason works fine in blender's python console, but not for addons?
            # Accessing interface_items by name doesn't work (yet) due to a blender bug (bug report filed after 5.1)
            # curr_item = curr_item.interface_items.get(path_segment)
            # HACK: check if invalid this way since .get doesn't work yet.
            next_index = curr_item.interface_items.find(path_segment)
            if next_index == -1:
                self.logger.error(f'Could not find next item \'{path_segment}\'! Full path: {path}')
                return None
            curr_item = curr_item.interface_items[next_index]

        if isinstance(curr_item, item_type):
            if check_in_out and curr_item.in_out != in_out_str:
                self.logger.error(
                    f'Found a NodeTree socket of name \'{curr_item.name}\' but the socket type is \'{curr_item.in_out}\' instead of \'{in_out_str}\'!')

        return curr_item

    def get_group_socket_index(self, node_tree: bpy.types.NodeTree, socket_path: list[str],
                               root: bpy.types.NodeTreeInterfaceItem = None, is_output=False, ignore_level=False):
        socket = self.get_nodetree_item_from_path(root or node_tree, socket_path, bpy.types.NodeTreeInterfaceSocket,
                                                  is_output, ignore_level)

        # There's already an error from get_nodetree_item_from_path if this happens, so just return.
        if socket is None:
            return None

        if socket.item_type != "SOCKET":
            self.logger.error(f"Attempted to get socket from path, but did not find socket! item found: {socket}")

        # go through all the sockets (input or output), and find the one with a matching unique identifier.
        socket_identifier = socket.identifier
        group_sockets = node_tree.nodes['Group Input'].outputs if not is_output else node_tree.nodes[
            'Group Output'].inputs
        for i, node_socket in enumerate(group_sockets):
            if node_socket.identifier == socket_identifier:
                return i

        self.logger.error(f"Could not find index for socket: {socket.name}")
        return None
