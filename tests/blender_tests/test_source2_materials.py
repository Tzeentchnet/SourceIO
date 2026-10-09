import unittest

import bpy

from SourceIO.blender_bindings.material_loader.material_loader import ShaderRegistry
from SourceIO.blender_bindings.material_loader.shader_base import MIX_A, MIX_B, MIX_FACTOR
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


def linked_node(socket):
    return socket.links[0].from_node


def output_node(material):
    return next(n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')


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

    def test_vertexlitgeneric_decal(self):
        textures = ('g_tColor', 'g_tDecal')
        material = build('csgo_vertexlitgeneric.vfx', textures, ints={'F_DECAL_TEXTURE': 1})
        blend = linked_node(shader_node(material, 'csgo_vertexlitgeneric.vfx').inputs['TextureColor'])
        self.assertEqual(blend.blend_type, 'MIX')
        self.assertEqual(source(blend.inputs[MIX_FACTOR]), ('g_tDecal', 'Alpha'))
        self.assertEqual(source(blend.inputs[MIX_A]), ('g_tColor', 'Color'))
        self.assertEqual(source(blend.inputs[MIX_B]), ('g_tDecal', 'Color'))
        decal = material.node_tree.nodes['g_tDecal']
        self.assertEqual(linked_node(decal.inputs['Vector']).uv_map, 'TEXCOORD_1')

        material = build('csgo_vertexlitgeneric.vfx', textures,
                         ints={'F_DECAL_TEXTURE': 1, 'F_DECAL_BLEND_MODE': 1, 'g_bUseSecondaryUvForDecal': 0})
        blend = linked_node(shader_node(material, 'csgo_vertexlitgeneric.vfx').inputs['TextureColor'])
        self.assertEqual(blend.blend_type, 'MULTIPLY')
        decal = material.node_tree.nodes['g_tDecal']
        self.assertEqual(linked_node(decal.inputs['Vector']).uv_map, 'TEXCOORD')

    def test_transmission_texture(self):
        material = build('csgo_foliage.vfx', ('g_tColor', 'g_tNormal', 'g_tTransmissiveColor'),
                         ints={'F_TRANSMISSIVE_BACKFACE_NDOTL': 1})
        add_shader = linked_node(output_node(material).inputs['Surface'])
        self.assertEqual(add_shader.bl_idname, 'ShaderNodeAddShader')
        self.assertEqual(source(add_shader.inputs[0]), ('csgo_foliage.vfx', 'BSDF'))
        translucent = linked_node(add_shader.inputs[1])
        self.assertEqual(source(translucent.inputs['Color']), ('g_tTransmissiveColor', 'Color'))

        # Alpha-tested parts don't transmit: the color goes through the clipped alpha.
        material = build('csgo_foliage.vfx', ('g_tColor', 'g_tNormal', 'g_tTransmissiveColor'),
                         ints={'F_TRANSMISSIVE_BACKFACE_NDOTL': 1, 'F_ALPHA_TEST': 1})
        translucent = linked_node(linked_node(output_node(material).inputs['Surface']).inputs[1])
        mask = linked_node(translucent.inputs['Color'])
        self.assertEqual(source(mask.inputs[MIX_A]), ('g_tTransmissiveColor', 'Color'))
        self.assertEqual(linked_node(mask.inputs[MIX_B]).operation, 'GREATER_THAN')

    def test_transmission_from_albedo(self):
        material = build('csgo_complex.vfx', ('g_tColor', 'g_tNormal', 'g_tTransmissiveColor'),
                         ints={'F_TRANSMISSIVE_BACKFACE_NDOTL': 1, 'F_USE_ALBEDO_FOR_TRANSMISSIVE': 1})
        add_shader = linked_node(output_node(material).inputs['Surface'])
        self.assertEqual(source(add_shader.inputs[0]), ('csgo_complex.vfx', 'BSDF'))
        self.assertEqual(source(linked_node(add_shader.inputs[1]).inputs['Color']), ('g_tColor', 'Color'))
        self.assertNotIn('g_tTransmissiveColor', texture_nodes(material))

    def test_no_transmission(self):
        material = build('csgo_complex.vfx', ('g_tColor', 'g_tNormal'))
        self.assertEqual(source(output_node(material).inputs['Surface']), ('csgo_complex.vfx', 'BSDF'))

    def test_lightmappedgeneric_detail(self):
        textures = ('g_tColor', 'g_tLayer1Detail', 'g_tLayer2Color', 'g_tLayer2Detail')
        vectors = {'g_vLayer1DetailScale': (8.0, 2.0, 0.0, 0.0), 'g_vLayer1DetailTintAndBlend': (1.0, 1.0, 1.0, 0.4)}
        material = build('csgo_lightmappedgeneric.vfx', textures, ints={'F_DETAILTEXTURE': 1}, vectors=vectors)
        shader = shader_node(material, 'csgo_lightmappedgeneric.vfx')
        blend = linked_node(shader.inputs['TextureColor0'])
        self.assertEqual(blend.blend_type, 'MULTIPLY')
        self.assertAlmostEqual(blend.inputs[MIX_FACTOR].default_value, 0.4, places=6)
        self.assertEqual(source(blend.inputs[MIX_A]), ('g_tColor', 'Color'))
        mod2x = linked_node(blend.inputs[MIX_B])
        self.assertEqual(source(mod2x.inputs[MIX_A]), ('g_tLayer1Detail', 'Color'))
        self.assertEqual(tuple(mod2x.inputs[MIX_B].default_value), (2.0, 2.0, 2.0, 1.0))
        detail = material.node_tree.nodes['g_tLayer1Detail']
        self.assertTrue(detail.image.colorspace_settings.is_data)
        transform = linked_node(detail.inputs['Vector'])
        self.assertEqual(tuple(transform.inputs['g_vTexCoordScale'].default_value), (8.0, 2.0, 0.0))
        # F_DETAILTEXTURE 1 details layer 1 only.
        self.assertEqual(source(shader.inputs['TextureColor1']), ('g_tLayer2Color', 'Color'))
        self.assertNotIn('g_tLayer2Detail', texture_nodes(material))

        material = build('csgo_lightmappedgeneric.vfx', textures, ints={'F_DETAILTEXTURE': 2})
        shader = shader_node(material, 'csgo_lightmappedgeneric.vfx')
        self.assertEqual(source(linked_node(linked_node(shader.inputs['TextureColor1']).inputs[MIX_B]).inputs[MIX_A]),
                         ('g_tLayer2Detail', 'Color'))

    def test_anisotropic_gloss_roughness(self):
        textures = ('g_tColor', 'g_tNormal', 'g_tAnisoGloss')
        material = build('csgo_character.vfx', textures, ints={'F_ANISOTROPIC_GLOSS': 1})
        average = linked_node(shader_node(material, 'csgo_character.vfx').inputs['TextureRoughness'])
        self.assertEqual(average.operation, 'DOT_PRODUCT')
        self.assertEqual(source(average.inputs[0]), ('g_tAnisoGloss', 'Color'))
        self.assertEqual(tuple(average.inputs[1].default_value), (0.5, 0.5, 0.0))

        material = build('csgo_complex.vfx', textures)
        self.assertEqual(source(shader_node(material, 'csgo_complex.vfx').inputs['TextureRoughness']),
                         ('g_tNormal', 'Alpha'))
        self.assertNotIn('g_tAnisoGloss', texture_nodes(material))

    def test_complex_detail_secondary_uv(self):
        textures = ('g_tColor', 'g_tDetail')
        vectors = {'g_vDetailTexCoordScale': (1.0, 1.0, 0.0, 0.0), 'g_vDetailTexCoordOffset': (0.0, 0.0, 0.0, 0.0)}
        for ints, uv_map in (({}, 'TEXCOORD'), ({'F_SECONDARY_UV': 1}, 'TEXCOORD_1'),
                             ({'F_SECONDARY_UV': 1, 'g_bUseSecondaryUvForDetailTexture': 0}, 'TEXCOORD')):
            material = build('csgo_complex.vfx', textures, ints=ints, vectors=vectors)
            transform = linked_node(material.node_tree.nodes['g_tDetail'].inputs['Vector'])
            self.assertEqual(linked_node(transform.inputs[0]).uv_map, uv_map, ints)


if __name__ == '__main__':
    unittest.main()
