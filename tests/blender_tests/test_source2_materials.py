import unittest

import bpy

from SourceIO.blender_bindings.material_loader.material_loader import ShaderRegistry
from SourceIO.library.source2.resource_types import CompiledMaterialResource


class FakeMaterial(CompiledMaterialResource):
    """A compiled material with only a DATA block; every texture resolves to a missing-texture image."""

    def __init__(self, shader, textures=(), ints=None, floats=None, vectors=None):
        self._data = {
            'm_shaderName': shader,
            'm_textureParams': [{'m_name': name, 'm_pValue': f'materials/test/{name}.vtex'} for name in textures],
            'm_intParams': [{'m_name': k, 'm_nValue': v} for k, v in (ints or {}).items()],
            'm_floatParams': [{'m_name': k, 'm_flValue': v} for k, v in (floats or {}).items()],
            'm_vectorParams': [{'m_name': k, 'm_value': v} for k, v in (vectors or {}).items()],
            'm_dynamicParams': [],
            'm_dynamicTextureParams': [],
        }

    def get_block(self, block_class, *, block_id=None, block_name=None):
        return self._data if block_name == 'DATA' else None

    def has_child_resource(self, name_or_id, cm):
        return True

    def get_child_resource(self, name_or_id, cm, resource_class):
        return None


def build(shader, textures=(), **params):
    material = bpy.data.materials.new(shader)
    ShaderRegistry.source2_create_nodes(None, material, FakeMaterial(shader, textures, **params), {})
    return material


def source(socket):
    """(node name, output name) feeding a socket, looking through Separate Color to the texture."""
    if not socket.is_linked:
        return None
    link = socket.links[0]
    node, output = link.from_node, link.from_socket.name
    if node.bl_idname == 'ShaderNodeSeparateColor':
        return source(node.inputs[0])[0], output
    return node.name, output


def shader_node(material, shader):
    return material.node_tree.nodes[shader]


def texture_nodes(material):
    return {n.name for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeTexImage'}


class Source2MaterialTests(unittest.TestCase):
    def tearDown(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    def test_complex_metalness_texture_is_green_channel(self):
        material = build('csgo_complex.vfx', ('g_tColor', 'g_tNormal', 'g_tMetalness', 'g_tAmbientOcclusion'),
                         ints={'F_METALNESS_TEXTURE': 1})
        shader = shader_node(material, 'csgo_complex.vfx')
        self.assertEqual(source(shader.inputs['TextureMetalness']), ('g_tMetalness', 'Green'))
        self.assertEqual(source(shader.inputs['TextureRoughness']), ('g_tNormal', 'Alpha'))
        self.assertNotIn('g_tAmbientOcclusion', texture_nodes(material))

    def test_complex_metalness_flag_without_texture_uses_color_alpha(self):
        material = build('csgo_complex.vfx', ('g_tColor', 'g_tNormal'), ints={'F_METALNESS_TEXTURE': 1})
        shader = shader_node(material, 'csgo_complex.vfx')
        self.assertEqual(source(shader.inputs['TextureMetalness']), ('g_tColor', 'Alpha'))

    def test_complex_color_tint(self):
        material = build('csgo_complex.vfx', ('g_tColor',), vectors={'g_vColorTint': (0.5, 0.25, 1.0, 0.0)})
        tint = shader_node(material, 'csgo_complex.vfx').inputs['g_vColorTint'].default_value
        self.assertEqual(tuple(tint), (0.5, 0.25, 1.0, 1.0))

    def test_translucent_flag(self):
        material = build('csgo_complex.vfx', ('g_tColor',), ints={'F_TRANSLUCENT': 1})
        self.assertEqual(source(shader_node(material, 'csgo_complex.vfx').inputs['Alpha']), ('g_tColor', 'Alpha'))
        material = build('csgo_vertexlitgeneric.vfx', ('g_tColor',), ints={'F_TRANSLUCENT': 1})
        self.assertEqual(source(shader_node(material, 'csgo_vertexlitgeneric.vfx').inputs['Alpha']),
                         ('g_tColor', 'Alpha'))
        material = build('csgo_lightmappedgeneric.vfx', ('g_tColor',), ints={'F_TRANSLUCENT': 1})
        self.assertEqual(source(shader_node(material, 'csgo_lightmappedgeneric.vfx').inputs['TextureAlpha0']),
                         ('g_tColor', 'Alpha'))

    def test_vertexlitgeneric_metalness_texture(self):
        material = build('csgo_vertexlitgeneric.vfx',
                         ('g_tColor', 'g_tNormal', 'g_tMetalness', 'g_tAmbientOcclusion'))
        shader = shader_node(material, 'csgo_vertexlitgeneric.vfx')
        self.assertEqual(source(shader.inputs['TextureMetalness']), ('g_tMetalness', 'Green'))
        self.assertNotIn('g_tAmbientOcclusion', texture_nodes(material))

    def test_character(self):
        material = build('csgo_character.vfx', ('g_tColor', 'g_tNormal', 'g_tMetalness', 'g_tAmbientOcclusion',
                                                'g_tBloodMask', 'g_tPatch0', 'g_tPatch0Backing'))
        shader = shader_node(material, 'csgo_character.vfx')
        self.assertEqual(source(shader.inputs['TextureColor']), ('g_tColor', 'Color'))
        self.assertEqual(source(shader.inputs['TextureMetalness']), ('g_tMetalness', 'Green'))
        self.assertEqual(source(shader.inputs['TextureRoughness']), ('g_tNormal', 'Alpha'))
        self.assertEqual(texture_nodes(material), {'g_tColor', 'g_tNormal', 'g_tMetalness'})

    def test_weapon_roughness_from_metalness_red(self):
        material = build('csgo_weapon.vfx', ('g_tColor', 'g_tNormal', 'g_tMetalness', 'g_tAmbientOcclusion',
                                             'g_tSticker0', 'g_tNormalRoughnessSticker0', 'g_tStickerScratches'))
        shader = shader_node(material, 'csgo_weapon.vfx')
        self.assertEqual(source(shader.inputs['TextureMetalness']), ('g_tMetalness', 'Green'))
        self.assertEqual(source(shader.inputs['TextureRoughness']), ('g_tMetalness', 'Red'))
        self.assertEqual(texture_nodes(material), {'g_tColor', 'g_tNormal', 'g_tMetalness'})

    def test_foliage_without_alpha_test_is_opaque(self):
        material = build('csgo_foliage.vfx', ('g_tColor', 'g_tNormal', 'g_tAmbientOcclusion', 'g_tNoiseMap'))
        shader = shader_node(material, 'csgo_foliage.vfx')
        self.assertFalse(shader.inputs['Alpha'].is_linked)
        self.assertEqual(texture_nodes(material), {'g_tColor', 'g_tNormal'})
        material = build('csgo_foliage.vfx', ('g_tColor', 'g_tNormal'), ints={'F_ALPHA_TEST': 1})
        self.assertTrue(shader_node(material, 'csgo_foliage.vfx').inputs['Alpha'].is_linked)


if __name__ == '__main__':
    unittest.main()
