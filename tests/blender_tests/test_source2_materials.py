import os
import tempfile
import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.material_loader import ShaderRegistry
from SourceIO.blender_bindings.material_loader.shader_base import (MIX_A, MIX_B, MIX_FACTOR, ALPHA_CLIP_LABEL,
                                                                  unfilter_alpha_clips)
from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import Source2ShaderBase
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
            material = build('csgo_complex.vfx', textures, ints={'F_DETAIL_TEXTURE': 1, **ints}, vectors=vectors)
            transform = linked_node(material.node_tree.nodes['g_tDetail'].inputs['Vector'])
            self.assertEqual(linked_node(transform.inputs[0]).uv_map, uv_map, ints)

    @staticmethod
    def bake_uv(build_uv, size=8, secondary_uv=None):
        """Bake the vector that build_uv(builder) returns on a unit quad (TEXCOORD spans 0..1) into a float
        image, one value per texel."""
        mesh = bpy.data.meshes.new('uv_quad')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        mesh.uv_layers.new(name='TEXCOORD').data.foreach_set('uv', (0, 0, 1, 0, 1, 1, 0, 1))
        if secondary_uv is not None:
            mesh.uv_layers.new(name='TEXCOORD_1').data.foreach_set('uv', secondary_uv)
        obj = bpy.data.objects.new('uv_quad', mesh)
        bpy.context.scene.collection.objects.link(obj)
        material = bpy.data.materials.new('uv_transform')
        mesh.materials.append(material)
        builder = Source2ShaderBase.__new__(Source2ShaderBase)
        builder.bpy_material = material
        material.node_tree.nodes.clear()
        uv_output = build_uv(builder)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        emission = nodes.new('ShaderNodeEmission')
        output = nodes.new('ShaderNodeOutputMaterial')
        links.new(uv_output, emission.inputs['Color'])
        links.new(emission.outputs[0], output.inputs['Surface'])
        image = bpy.data.images.new('bake', size, size, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        target = nodes.new('ShaderNodeTexImage')
        target.image = image
        nodes.active = target
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 1
        scene.cycles.device = 'CPU'
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.bake(type='EMIT', margin=0)
        pixels = np.array(image.pixels[:], dtype=np.float32).reshape(size, size, 4)
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        return pixels

    def test_uv_transform_matches_shader(self):
        # In Source's UV space (V down): uv' = R(rotation) * (S * (uv - P) + P - C) + C + offset, P = 0 or C.
        size = 8
        for scale_about_center in (False, True):
            scale, offset, center, rotation = (2.0, 0.5), (0.25, 0.125), (0.25, 0.75), 30.0
            pixels = self.bake_uv(lambda builder: builder.create_transform(
                'TEXCOORD', (*scale, 0.0), (*offset, 0.0), (*center, 0.0), rotation, scale_about_center).outputs[0],
                size)
            texel = (np.arange(size) + 0.5) / size
            u, v = np.meshgrid(texel, texel)  # rows are V in Blender's image
            source_uv = np.stack((u, 1 - v), -1)
            pivot = np.array(center) if scale_about_center else np.zeros(2)
            angle = np.radians(rotation)
            rotation_matrix = np.array(((np.cos(angle), -np.sin(angle)), (np.sin(angle), np.cos(angle))))
            scaled = np.array(scale) * (source_uv - pivot) + pivot - np.array(center)
            expected = scaled @ rotation_matrix.T + np.array(center) + np.array(offset)
            expected[..., 1] = 1 - expected[..., 1]
            np.testing.assert_allclose(pixels[..., :2], expected, atol=2e-3, err_msg=str(scale_about_center))

    def test_alpha_clip_reads_full_resolution(self):
        # EEVEE's mipmaps average a thin alpha-tested texture below its cutoff; Closest lookups skip them.
        material = build('csgo_vertexlitgeneric.vfx', ('g_tColor',), ints={'F_ALPHA_TEST': 1},
                         floats={'g_flAlphaTestReference': 0.4})
        clip = next(node for node in material.node_tree.nodes if node.label == ALPHA_CLIP_LABEL)
        self.assertAlmostEqual(clip.inputs[1].default_value, 0.4, places=6)
        color = material.node_tree.nodes['g_tColor']
        taps = [node for node in material.node_tree.nodes
                if node.bl_idname == 'ShaderNodeTexImage' and node.interpolation == 'Closest']
        self.assertEqual(len(taps), 4)
        self.assertTrue(all(tap.image == color.image for tap in taps))
        self.assertNotEqual(linked_node(clip.inputs[0]), color)
        # The color keeps its filtered lookup and the shared UV transform
        transform = linked_node(color.inputs['Vector'])
        self.assertTrue(all(linked_node(linked_node(tap.inputs['Vector']).inputs['UV']) == transform
                            for tap in taps))

    def test_full_resolution_alpha_matches_linear_sampling(self):
        size = 16
        rng = np.random.default_rng(1)
        image = bpy.data.images.new('alpha', 4, 4, alpha=True, float_buffer=True)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels = rng.random(4 * 4 * 4).astype(np.float32)

        def build_alpha(builder, unfilter):
            texture = builder.create_node('ShaderNodeTexImage')
            texture.image = image
            clip = builder.insert_alpha_clip(texture.outputs['Alpha'], 0.5)
            if unfilter:
                unfilter_alpha_clips(builder.bpy_material)
            return clip.node.inputs[0].links[0].from_socket

        linear = self.bake_uv(lambda builder: build_alpha(builder, False), size)
        unfiltered = self.bake_uv(lambda builder: build_alpha(builder, True), size)
        np.testing.assert_allclose(unfiltered[..., 0], linear[..., 0], atol=2e-3)

    def test_secondary_uv_fallback(self):
        size = 4
        texel = (np.arange(size) + 0.5) / size
        primary = np.stack(np.meshgrid(texel, texel), -1)
        pixels = self.bake_uv(lambda builder: builder._secondary_uv_or_primary(), size)
        np.testing.assert_allclose(pixels[..., :2], primary, atol=2e-3)
        # A mesh that has the secondary set reads it.
        pixels = self.bake_uv(lambda builder: builder._secondary_uv_or_primary(), size,
                              secondary_uv=(0.5, 0.5, 0.75, 0.5, 0.75, 0.75, 0.5, 0.75))
        np.testing.assert_allclose(pixels[..., :2], 0.5 + primary / 4, atol=2e-3)

    def test_lightmappedgeneric_layer_uv_transforms(self):
        textures = ('g_tColor', 'g_tLayer1NormalRoughness', 'g_tLayer2Color', 'g_tBlendModulation')
        material = build('csgo_lightmappedgeneric.vfx', textures, ints={'F_FANCY_BLENDING': 2},
                         floats={'g_flLayer1NormalTexCoordRotation': 90.0},
                         vectors={'g_vLayer2TexCoordScale': (2.0, 2.0, 0.0, 0.0),
                                  'g_vLayer2TexCoordCenter': (0.0, 0.0, 0.0, 0.0),
                                  'g_vBlendModulateTexCoordScale': (0.65, 0.65, 0.0, 0.0)})
        nodes = material.node_tree.nodes

        def transform(slot):
            node = linked_node(nodes[slot].inputs['Vector'])
            self.assertEqual(node.node_tree.name, 'SourceIO UV Transform')
            self.assertTrue(node.inputs['Scale About Center'].default_value)
            return node

        self.assertEqual(tuple(transform('g_tColor').inputs['g_vTexCoordScale'].default_value), (1.0, 1.0, 0.0))
        self.assertEqual(transform('g_tLayer1NormalRoughness').inputs['g_flTexCoordRotation'].default_value, 90.0)
        layer2 = transform('g_tLayer2Color')
        self.assertEqual(tuple(layer2.inputs['g_vTexCoordScale'].default_value), (2.0, 2.0, 0.0))
        self.assertEqual(tuple(layer2.inputs['g_vTexCoordCenter'].default_value), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(transform('g_tBlendModulation').inputs['g_vTexCoordScale'].default_value[0], 0.65,
                               places=6)

    def test_vertexlitgeneric_uv_sets_and_transforms(self):
        textures = ('g_tColor', 'g_tNormal', 'g_tDetail', 'g_tDetailMask', 'g_tMetalness')
        vectors = {'g_vTexCoordScale': (3.0, 3.0, 0.0, 0.0), 'g_vNormalTexCoordScale': (2.0, 2.0, 0.0, 0.0)}
        material = build('csgo_vertexlitgeneric.vfx', textures, ints={'F_DETAIL_TEXTURE': 1}, vectors=vectors)
        nodes = material.node_tree.nodes
        color = linked_node(nodes['g_tColor'].inputs['Vector'])
        self.assertEqual(tuple(color.inputs['g_vTexCoordScale'].default_value), (3.0, 3.0, 0.0))
        self.assertFalse(color.inputs['Scale About Center'].default_value)
        self.assertEqual(linked_node(nodes['g_tMetalness'].inputs['Vector']), color)
        normal = linked_node(nodes['g_tNormal'].inputs['Vector'])
        self.assertEqual(tuple(normal.inputs['g_vTexCoordScale'].default_value), (2.0, 2.0, 0.0))
        self.assertTrue(normal.inputs['Scale About Center'].default_value)
        # The secondary UV set, or the primary one on meshes without it (CS2's map meshes).
        for fallback in (linked_node(linked_node(nodes['g_tDetail'].inputs['Vector']).inputs[0]),
                         linked_node(nodes['g_tDetailMask'].inputs['Vector'])):
            self.assertEqual(fallback.data_type, 'VECTOR')
            self.assertEqual(linked_node(fallback.inputs[4]).uv_map, 'TEXCOORD')
            self.assertEqual(linked_node(fallback.inputs[5]).uv_map, 'TEXCOORD_1')
            has_secondary = linked_node(fallback.inputs[0])
            self.assertEqual(has_secondary.operation, 'GREATER_THAN')
            self.assertEqual(linked_node(linked_node(has_secondary.inputs[0]).inputs[0]).uv_map, 'TEXCOORD_1')

        material = build('csgo_vertexlitgeneric.vfx', textures,
                         ints={'F_DETAIL_TEXTURE': 1, 'g_bUseSecondaryUvForDetailTexture': 0,
                               'g_bUseSecondaryUvForDetailMask': 0})
        nodes = material.node_tree.nodes
        self.assertEqual(linked_node(linked_node(nodes['g_tDetail'].inputs['Vector']).inputs[0]).uv_map, 'TEXCOORD')
        self.assertEqual(linked_node(nodes['g_tDetailMask'].inputs['Vector']).uv_map, 'TEXCOORD')

    def test_generic_specular_textures_and_ranges(self):
        material = build('generic.vfx', ('g_tColor', 'g_tNormal', 'g_tRoughness', 'g_tMetalnessReflectanceFresnel'),
                         ints={'F_SPECULAR': 1},
                         vectors={'g_vGlossinessRange': (0.2, 0.6, 0.0, 0.0),
                                  'g_vMetalnessRange': (1.0, 1.0, 0.0, 0.0),
                                  'g_vReflectanceRange': (0.0, 0.1, 0.0, 0.0)})
        shader = shader_node(material, 'generic.vfx')
        # Roughness: the average of blue and alpha, mapped into 1 - glossiness range = 0.4..0.8.
        roughness = linked_node(shader.inputs['Roughness'])
        self.assertEqual(roughness.operation, 'MULTIPLY_ADD')
        self.assertAlmostEqual(roughness.inputs[1].default_value, 0.2, places=6)
        self.assertAlmostEqual(roughness.inputs[2].default_value, 0.4, places=6)
        average = linked_node(roughness.inputs[0])
        self.assertEqual({source(average.inputs[0]), source(average.inputs[1])},
                         {('g_tRoughness', 'Blue'), ('g_tRoughness', 'Alpha')})
        metallic = linked_node(shader.inputs['Metallic'])
        self.assertEqual(source(metallic.inputs[0]), ('g_tMetalnessReflectanceFresnel', 'Red'))
        self.assertEqual((metallic.inputs[1].default_value, metallic.inputs[2].default_value), (0.0, 1.0))
        # Reflectance is F0; Specular IOR Level 0.5 is F0 0.04.
        specular = linked_node(shader.inputs['Specular IOR Level'])
        self.assertEqual(source(specular.inputs[0]), ('g_tMetalnessReflectanceFresnel', 'Green'))
        self.assertAlmostEqual(specular.inputs[1].default_value, 1.25, places=6)
        self.assertAlmostEqual(specular.inputs[2].default_value, 0.0, places=6)
        self.assertTrue(material.node_tree.nodes['g_tRoughness'].image.colorspace_settings.is_data)

    def test_generic_without_specular_is_diffuse(self):
        material = build('generic.vfx', ('g_tColor', 'g_tRoughness'))
        shader = shader_node(material, 'generic.vfx')
        self.assertEqual(shader.inputs['Specular IOR Level'].default_value, 0.0)
        self.assertEqual(shader.inputs['Roughness'].default_value, 1.0)
        self.assertEqual(texture_nodes(material), {'g_tColor'})

    def test_generic_self_illum_and_tint(self):
        material = build('generic.vfx', ('g_tColor', 'g_tSelfIllumMask'), ints={'F_SELF_ILLUM': 1},
                         floats={'g_flSelfIllumScale': 3.0},
                         vectors={'g_vColorTint': (1.0, 0.5, 1.0, 0.0), 'g_vSelfIllumTint': (1.0, 0.0, 0.0, 0.0)})
        shader = shader_node(material, 'generic.vfx')
        tint = linked_node(shader.inputs['Base Color'])
        self.assertEqual(source(tint.inputs[MIX_A]), ('g_tColor', 'Color'))
        self.assertEqual(tuple(tint.inputs[MIX_B].default_value), (1.0, 0.5, 1.0, 0.0))
        self.assertEqual(shader.inputs['Emission Strength'].default_value, 3.0)
        emission_tint = linked_node(shader.inputs['Emission Color'])
        self.assertEqual(tuple(emission_tint.inputs[MIX_A].default_value), (1.0, 0.0, 0.0, 0.0))
        mask = linked_node(emission_tint.inputs[MIX_B])
        self.assertEqual(source(mask.inputs[MIX_A]), ('g_tSelfIllumMask', 'Color'))
        self.assertEqual(linked_node(mask.inputs[MIX_B]), tint)

    @staticmethod
    def render_over_background(material, background, size=4, vertex_color=None, uv_layers=None):
        """Render a quad filling the camera with this material in front of a uniform world color; the mean
        pixel. The file doesn't hold the radiance as rendered, so compare with ``render_constant``. uv_layers
        adds UV maps with a constant value, {name: (u, v)}."""
        mesh = bpy.data.meshes.new('unlit_quad')
        mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
        mesh.uv_layers.new(name='TEXCOORD')
        for name, uv in (uv_layers or {}).items():
            mesh.uv_layers.new(name=name).data.foreach_set('uv', np.tile(np.asarray(uv, np.float32), 4))
        if vertex_color is not None:
            colors = mesh.color_attributes.new('COLOR', 'FLOAT_COLOR', 'CORNER')
            colors.data.foreach_set('color', np.tile(np.asarray(vertex_color, np.float32), len(colors.data)))
        mesh.materials.append(material)
        obj = bpy.data.objects.new('unlit_quad', mesh)
        camera = bpy.data.objects.new('camera', bpy.data.cameras.new('camera'))
        camera.data.type = 'ORTHO'
        camera.data.ortho_scale = 1.0
        camera.location = (0, 0, 2)
        scene = bpy.context.scene
        hidden = [other for other in scene.objects if not other.hide_render]  # the factory scene's cube
        for other in hidden:
            other.hide_render = True
        for new in (obj, camera):
            scene.collection.objects.link(new)
        scene.camera = camera
        world = bpy.data.worlds.new('background')
        world.node_tree.nodes['Background'].inputs['Color'].default_value = (*background, 1.0)
        scene.world = world
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = 64
        scene.cycles.use_denoising = False
        scene.cycles.device = 'CPU'
        scene.view_settings.view_transform = 'Standard'
        scene.render.resolution_x = scene.render.resolution_y = size
        scene.render.image_settings.file_format = 'OPEN_EXR'
        with tempfile.TemporaryDirectory() as directory:
            scene.render.filepath = os.path.join(directory, 'render.exr')
            bpy.ops.render.render(write_still=True)
            image = bpy.data.images.load(scene.render.filepath)
            pixels = np.array(image.pixels[:], dtype=np.float32).reshape(-1, 4)
            bpy.data.images.remove(image)
        for removed in (obj, camera):
            bpy.data.objects.remove(removed)
        for other in hidden:
            other.hide_render = False
        bpy.data.meshes.remove(mesh)
        bpy.data.worlds.remove(world)
        return pixels[:, :3].mean(0)

    @classmethod
    def render_constant(cls, value, background):
        """What render_over_background gives for a surface that emits value."""
        material = bpy.data.materials.new('constant')
        nodes = material.node_tree.nodes
        nodes.clear()
        emission = nodes.new('ShaderNodeEmission')
        emission.inputs['Color'].default_value = (*value, 1.0)
        material.node_tree.links.new(emission.outputs[0], nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
        return cls.render_over_background(material, background)

    def assert_renders_as(self, material, background, value, message='', vertex_color=None):
        np.testing.assert_allclose(self.render_over_background(material, background, vertex_color=vertex_color),
                                   self.render_constant(value, background), atol=2e-3, err_msg=message)

    @staticmethod
    def set_color(material, slot, rgba):
        image = bpy.data.images.new(f'{material.name}_{slot}', 1, 1, alpha=True, float_buffer=True)
        image.pixels = rgba
        image.alpha_mode = 'CHANNEL_PACKED'  # as imported textures are
        old_image = material.node_tree.nodes[slot].image  # the alpha clip's taps share it
        for node in material.node_tree.nodes:
            if node.bl_idname == 'ShaderNodeTexImage' and node.image == old_image:
                node.image = image

    def test_unlitgeneric_blend_modes(self):
        # Over a background B, a color C with alpha A and tint T: Opaque C·T, Translucent lerp(B, C·T, A),
        # Alpha Test C·T where A > reference, else B, Additive B + C·T·A, Multiply B·C·T,
        # Mod2x 2·B·lerp(0.5, C·T, A), ModThenAdd B·lerp(1, C·T, A).
        background, color, alpha, tint = np.array((0.2, 0.4, 0.1)), np.array((0.5, 0.25, 1.0)), 0.6, 0.8
        drawn = color * tint
        expected = {0: drawn, 1: background * (1 - alpha) + drawn * alpha, 2: drawn, 4: background + drawn * alpha,
                    5: background * drawn, 3: 2 * background * (0.5 + (drawn - 0.5) * alpha),
                    6: background * (1 + (drawn - 1) * alpha)}
        for blend_mode, value in expected.items():
            material = build('csgo_unlitgeneric.vfx', ('g_tColor',), ints={'F_BLEND_MODE': blend_mode},
                             floats={'g_flAlphaTestReference': 0.7 if blend_mode == 2 else 0.5},
                             vectors={'g_vColorTint': (tint, tint, tint, 0.0)})
            self.set_color(material, 'g_tColor', (*color, alpha))
            if blend_mode == 2:
                value = background  # alpha 0.6 is under the 0.7 reference
            self.assert_renders_as(material, background, value, f'F_BLEND_MODE {blend_mode}')
        material = build('csgo_unlitgeneric.vfx', ('g_tColor',), ints={'F_BLEND_MODE': 2},
                         floats={'g_flAlphaTestReference': 0.5})
        self.set_color(material, 'g_tColor', (*color, alpha))
        self.assert_renders_as(material, background, color)

    def test_unlitgeneric_two_textures(self):
        # nuke_clouds_002: two white textures with the clouds in alpha, drawn additively.
        background = np.array((0.2, 0.4, 0.1))
        material = build('csgo_unlitgeneric.vfx', ('g_tColor', 'g_tColor2'),
                         ints={'F_BLEND_MODE': 4, 'F_TWOTEXTURE': 1},
                         vectors={'g_vTex2CoordScale': (1.5, 1.5, 0.0, 0.0), 'g_vTex2CoordOffset': (0.25, 0.0, 0.0, 0.0)})
        self.set_color(material, 'g_tColor', (1.0, 0.5, 1.0, 0.5))
        self.set_color(material, 'g_tColor2', (0.5, 1.0, 1.0, 0.4))
        self.assert_renders_as(material, background, background + np.array((0.5, 0.5, 1.0)) * 0.2)
        # The second texture's transform scales about its center, the first's about the origin.
        nodes = material.node_tree.nodes
        first = linked_node(nodes['g_tColor'].inputs['Vector'])
        second = linked_node(nodes['g_tColor2'].inputs['Vector'])
        self.assertFalse(first.inputs['Scale About Center'].default_value)
        self.assertTrue(second.inputs['Scale About Center'].default_value)
        self.assertEqual(tuple(second.inputs['g_vTexCoordScale'].default_value), (1.5, 1.5, 0.0))
        self.assertEqual(tuple(second.inputs['g_vTexCoordOffset'].default_value), (0.25, 0.0, 0.0))
        # Without F_TWOTEXTURE the second texture isn't loaded.
        material = build('csgo_unlitgeneric.vfx', ('g_tColor', 'g_tColor2'))
        self.assertEqual(texture_nodes(material), {'g_tColor'})

    @staticmethod
    def srgb(linear):
        linear = np.asarray(linear, dtype=np.float64)
        return np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)

    def test_static_overlay_blend_modes(self):
        # Unlit overlays (F_LIT 0), a color C with alpha A, opacity scale s over a background B: Opaque C,
        # Translucent lerp(B, C, A·s), Alpha Test C above the reference, Mod2x 2·B·lerp(0.5, sRGB(C), A·s) (the
        # color in gamma space; 0.002 is on the curve's linear segment), Additive B + C·A·s, Multiply B·C,
        # ModThenAdd B·lerp(1, C, A·s).
        background, color, alpha, scale = np.array((0.2, 0.4, 0.1)), np.array((0.5, 0.25, 0.002)), 0.6, 0.75
        faded = alpha * scale
        expected = {0: color, 1: background * (1 - faded) + color * faded, 2: color,
                    3: 2 * background * (0.5 + (self.srgb(color) - 0.5) * faded), 4: background + color * faded,
                    5: background * color, 6: background * (1 + (color - 1) * faded)}
        for blend_mode, value in expected.items():
            material = build('csgo_static_overlay.vfx', ('g_tColor', 'g_tNormal', 'g_tMetalness'),
                             ints={'F_BLEND_MODE': blend_mode},
                             floats={'g_flOpacityScale': scale, 'g_flAlphaTestReference': 0.5})
            self.set_color(material, 'g_tColor', (*color, alpha))
            self.assert_renders_as(material, background, value, f'F_BLEND_MODE {blend_mode}')
            self.assertFalse(texture_nodes(material) & {'g_tNormal', 'g_tMetalness'})
        material = build('csgo_static_overlay.vfx', ('g_tColor',), ints={'F_BLEND_MODE': 2},
                         floats={'g_flAlphaTestReference': 0.7})
        self.set_color(material, 'g_tColor', (*color, alpha))
        self.assert_renders_as(material, background, background, 'alpha under the reference')

    def test_static_overlay_lit(self):
        # Lit overlays draw the csgo_complex group, metalness from g_tMetalness green, blended by the scaled alpha.
        material = build('csgo_static_overlay.vfx', ('g_tColor', 'g_tNormal', 'g_tMetalness', 'g_tAmbientOcclusion',
                                                     'g_tSelfIllumMask'),
                         ints={'F_BLEND_MODE': 1, 'F_LIT': 1}, floats={'g_flOpacityScale': 0.9})
        group = shader_node(material, 'csgo_static_overlay.vfx')
        self.assertEqual(source(group.inputs['TextureColor']), ('g_tColor', 'Color'))
        self.assertEqual(source(group.inputs['TextureMetalness']), ('g_tMetalness', 'Green'))
        self.assertEqual(source(group.inputs['TextureNormal']), ('g_tNormal', 'Color'))
        mix = linked_node(output_node(material).inputs['Surface'])
        self.assertEqual(mix.bl_idname, 'ShaderNodeMixShader')
        self.assertEqual(linked_node(mix.inputs[2]), group)
        opacity = linked_node(mix.inputs[0])
        self.assertAlmostEqual(opacity.inputs[1].default_value, 0.9, places=6)
        self.assertEqual(source(opacity.inputs[0]), ('g_tColor', 'Alpha'))
        self.assertEqual(texture_nodes(material), {'g_tColor', 'g_tNormal', 'g_tMetalness'})

    def test_static_overlay_color_adjust(self):
        # Contrast about the texture's average color a, then brightness, then saturation about the grey of
        # SATURATION_WEIGHTS: c' = B·((c − a)·C + a); c'' = Y + S·(c' − Y).
        from unittest import mock
        from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import SATURATION_WEIGHTS
        background, color, average = np.array((0.2, 0.4, 0.1)), np.array((0.5, 0.25, 0.75)), (0.4, 0.3, 0.2)
        contrast, saturation, brightness = 1.5, 0.5, 1.2
        adjusted = brightness * ((color - average) * contrast + average)
        grey = np.dot(adjusted, SATURATION_WEIGHTS)
        adjusted = grey + saturation * (adjusted - grey)
        with mock.patch.object(Source2ShaderBase, '_texture_average_color', return_value=average):
            material = build('csgo_static_overlay.vfx', ('g_tColor',),
                             floats={'g_fTextureColorContrast': contrast, 'g_fTextureColorSaturation': saturation,
                                     'g_fTextureColorBrightness': brightness})
        self.set_color(material, 'g_tColor', (*color, 1.0))
        self.assert_renders_as(material, background, adjusted)
        self.assertAlmostEqual(sum(SATURATION_WEIGHTS), 1.0)

    def test_static_overlay_vertex_colors(self):
        # F_PAINT_VERTEX_COLORS multiplies the color by the vertex color and the alpha by its alpha.
        background, color, alpha = np.array((0.2, 0.4, 0.1)), np.array((0.5, 0.25, 1.0)), 0.8
        vertex_color = np.array((0.5, 1.0, 0.25, 0.5))
        material = build('csgo_static_overlay.vfx', ('g_tColor',),
                         ints={'F_BLEND_MODE': 1, 'F_PAINT_VERTEX_COLORS': 1})
        self.set_color(material, 'g_tColor', (*color, alpha))
        faded = alpha * vertex_color[3]
        self.assert_renders_as(material, background, background * (1 - faded) + color * vertex_color[:3] * faded,
                               vertex_color=vertex_color)
        # Without the flag the vertex colors are ignored.
        material = build('csgo_static_overlay.vfx', ('g_tColor',), ints={'F_BLEND_MODE': 1})
        self.set_color(material, 'g_tColor', (*color, alpha))
        self.assert_renders_as(material, background, background * (1 - alpha) + color * alpha,
                               vertex_color=vertex_color)

    def render_input(self, material, socket_name, shader, uv_layers=None):
        """What the material feeds into its node group's input, rendered as an Emission (RGB)."""
        nodes, links = material.node_tree.nodes, material.node_tree.links
        source_socket = nodes[shader].inputs[socket_name].links[0].from_socket
        emission = nodes.new('ShaderNodeEmission')
        links.new(source_socket, emission.inputs['Color'])
        links.new(emission.outputs[0], output_node(material).inputs['Surface'])
        return self.render_over_background(material, (0.0, 0.0, 0.0), uv_layers=uv_layers)

    def assert_input_is(self, material, socket_name, value, shader, message=''):
        np.testing.assert_allclose(self.render_input(material, socket_name, shader),
                                   self.render_constant(value, (0.0, 0.0, 0.0)), atol=2e-3, err_msg=message)

    def test_complex_detail_color_modes(self):
        # F_DETAIL_TEXTURE 1 Mod2X: C·lerp(1, 1.9922·D, f); 2 Overlay (and 4): lerp(C, linear(overlay(sRGB(C),
        # 0.9961·D)), f), with f = g_flDetailBlendFactor (default 1) × max(mask, g_flDetailBlendToFull).
        def overlay(a, b):
            return np.where(a < 0.5, 2 * a * b, 1 - 2 * (1 - a) * (1 - b))

        def linear(c):
            return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

        color, detail, mask = np.array((0.05, 0.4, 0.8)), np.array((0.3, 0.5, 0.9)), 0.6
        cases = (({'F_DETAIL_TEXTURE': 1}, {}, color * (1 + (1.9922 * detail - 1) * mask)),
                 ({'F_DETAIL_TEXTURE': 1}, {'g_flDetailBlendFactor': 0.5, 'g_flDetailBlendToFull': 0.8},
                  color * (1 + (1.9922 * detail - 1) * 0.4)),
                 ({'F_DETAIL_TEXTURE': 2}, {'g_flDetailBlendFactor': 0.5},
                  color + (linear(overlay(self.srgb(color), 0.9961 * detail)) - color) * 0.3),
                 ({'F_DETAIL_TEXTURE': 4}, {}, color + (linear(overlay(self.srgb(color), 0.9961 * detail)) - color) * mask),
                 ({}, {}, color))
        for ints, floats, value in cases:
            material = build('csgo_complex.vfx', ('g_tColor', 'g_tDetail', 'g_tDetailMask'), ints=ints, floats=floats)
            if ints:
                self.assertTrue(material.node_tree.nodes['g_tDetail'].image.colorspace_settings.is_data)
                self.assertTrue(material.node_tree.nodes['g_tDetailMask'].image.colorspace_settings.is_data)
                self.set_color(material, 'g_tDetail', (*detail, 1.0))
                self.set_color(material, 'g_tDetailMask', (mask, 0.0, 0.0, 1.0))
            else:
                self.assertEqual(texture_nodes(material), {'g_tColor'})
            self.set_color(material, 'g_tColor', (*color, 1.0))
            self.assert_input_is(material, 'TextureColor', value, 'csgo_complex.vfx', f'{ints} {floats}')

    def test_complex_detail_normals(self):
        # F_DETAIL_TEXTURE 3: D = lerp((0, 0, 1), detail, f · g_flDetailNormalStrength),
        # n' = n·D.z + (n.z·D.z·D.xy, 0), in the normal map's [0, 1] encoding; the color is left alone.
        normal, detail, mask, strength = np.array((0.1, -0.2, 0.97)), np.array((0.3, 0.4, 0.87)), 0.5, 0.8
        faded = np.array((0.0, 0.0, 1.0)) + (detail - (0.0, 0.0, 1.0)) * mask * strength
        blended = normal * faded[2] + np.array((*(normal[2] * faded[2] * faded[:2]), 0.0))
        for mode in (3, 4):
            material = build('csgo_complex.vfx', ('g_tColor', 'g_tNormal', 'g_tDetail', 'g_tNormalDetail',
                                                  'g_tDetailMask'),
                             ints={'F_DETAIL_TEXTURE': mode}, floats={'g_flDetailNormalStrength': strength})
            self.assertEqual('g_tDetail' in texture_nodes(material), mode == 4)
            self.set_color(material, 'g_tNormal', (*(normal * 0.5 + 0.5), 1.0))
            self.set_color(material, 'g_tNormalDetail', (*(detail * 0.5 + 0.5), 1.0))
            self.set_color(material, 'g_tDetailMask', (mask, 0.0, 0.0, 1.0))
            self.assert_input_is(material, 'TextureNormal', blended * 0.5 + 0.5, 'csgo_complex.vfx', f'mode {mode}')
        # Mod2X leaves the normal map alone.
        material = build('csgo_complex.vfx', ('g_tColor', 'g_tNormal', 'g_tDetail', 'g_tNormalDetail'),
                         ints={'F_DETAIL_TEXTURE': 1})
        self.assertEqual(source(shader_node(material, 'csgo_complex.vfx').inputs['TextureNormal']),
                         ('g_tNormal', 'Color'))
        self.assertNotIn('g_tNormalDetail', texture_nodes(material))

    def test_vertexlitgeneric_detail_mod2x(self):
        color, detail, mask = np.array((0.5, 0.25, 0.75)), np.array((0.5, 0.25, 0.75)), 0.5
        material = build('csgo_vertexlitgeneric.vfx', ('g_tColor', 'g_tDetail', 'g_tDetailMask'),
                         ints={'F_DETAIL_TEXTURE': 1}, floats={'g_flDetailBlendFactor': 0.8})
        self.assertTrue(material.node_tree.nodes['g_tDetail'].image.colorspace_settings.is_data)
        for slot, rgba in (('g_tColor', (*color, 1.0)), ('g_tDetail', (*detail, 1.0)),
                           ('g_tDetailMask', (mask, 0.0, 0.0, 1.0))):
            self.set_color(material, slot, rgba)
        self.assert_input_is(material, 'TextureColor', color * (1 + (1.9922 * detail - 1) * 0.4),
                             'csgo_vertexlitgeneric.vfx')

    def test_character_and_weapon_detail(self):
        # F_DETAIL_TEXTURE 0 Multiply, 1 Replace, faded by g_fDetailBlendFactor (times the tint mask, or its
        # inverse, by g_nMaskDetailTextureByTintMask / g_bMaskDetailTextureByTintMask). The detail is sRGB.
        color, detail, tint_mask = np.array((0.5, 0.25, 0.75)), np.array((0.4, 0.8, 0.2)), 0.25
        cases = (('csgo_character.vfx', {}, {}, color * detail),
                 ('csgo_character.vfx', {'F_DETAIL_TEXTURE': 1}, {'g_fDetailBlendFactor': 0.5},
                  color + (detail - color) * 0.5),
                 ('csgo_character.vfx', {'F_TINT_MASK': 1, 'g_nMaskDetailTextureByTintMask': 2}, {},
                  color + (color * detail - color) * (1 - tint_mask)),
                 ('csgo_character.vfx', {'g_nMaskDetailTextureByTintMask': 2}, {}, color),
                 ('csgo_weapon.vfx', {'F_TINT_MASK': 1, 'g_bMaskDetailTextureByTintMask': 1},
                  {'g_fDetailBlendFactor': 0.5}, color + (color * detail - color) * tint_mask * 0.5))
        for shader, ints, floats, value in cases:
            material = build(shader, ('g_tColor', 'g_tDetail', 'g_tTintMask'), ints=ints, floats=floats)
            self.assertFalse(material.node_tree.nodes['g_tDetail'].image.colorspace_settings.is_data)
            for slot, rgba in (('g_tColor', (*color, 1.0)), ('g_tDetail', (*detail, 1.0)),
                               ('g_tTintMask', (tint_mask, 0.0, 0.0, 1.0))):
                if slot in material.node_tree.nodes:
                    self.set_color(material, slot, rgba)
            self.assert_input_is(material, 'TextureColor', value, shader, f'{shader} {ints} {floats}')

    def lit_layers(self, ints=None, floats=None, modulation=None, weight=0.0, grey=None):
        """Render csgo_lightmappedgeneric lit by a uniform white world: a black layer 1 and a white layer 2 at the
        painted weight, or (grey) one layer of that grey."""
        if grey is not None:
            material = build('csgo_lightmappedgeneric.vfx', ('g_tColor',))
            self.set_color(material, 'g_tColor', (grey, grey, grey, 1.0))
        else:
            textures = ('g_tColor', 'g_tLayer2Color') + (('g_tBlendModulation',) if modulation else ())
            material = build('csgo_lightmappedgeneric.vfx', textures, ints=ints, floats=floats)
            self.set_color(material, 'g_tColor', (0.0, 0.0, 0.0, 1.0))
            self.set_color(material, 'g_tLayer2Color', (1.0, 1.0, 1.0, 1.0))
            if modulation:
                self.assertTrue(material.node_tree.nodes['g_tBlendModulation'].image.colorspace_settings.is_data)
                self.set_color(material, 'g_tBlendModulation', modulation)
        return self.render_over_background(material, (1.0, 1.0, 1.0), uv_layers={'TEXCOORD_4': (weight, 0.0)})

    def test_lightmappedgeneric_layer_blend(self):
        # Layer 2's share: the painted weight w (TEXCOORD_4.x) with F_FANCY_BLENDING 0, else
        # smoothstep(max(0, m - s), min(1, m + s), w) with the modulation's green (1, 2) or alpha (3) as m and
        # its red (1) or g_flBlendSoftness (2, 3) as s. A black and a white layer render as that grey.
        def smoothstep(edge0, edge1, x):
            t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
            return t * t * (3 - 2 * t)

        weight, modulation, softness = 0.55, (0.2, 0.5, 0.0, 0.7), 0.15
        cases = ((0, 0.0), (0, 0.3), (0, 1.0), (0, weight), (1, smoothstep(0.3, 0.7, weight)),
                 (2, smoothstep(0.35, 0.65, weight)), (3, smoothstep(0.55, 0.85, weight)))
        for mode, factor in cases:
            rendered = self.lit_layers({'F_FANCY_BLENDING': mode}, {'g_flBlendSoftness': softness},
                                       modulation if mode else None, factor if mode == 0 else weight)
            np.testing.assert_allclose(rendered, self.lit_layers(grey=factor), atol=5e-3,
                                       err_msg=f'F_FANCY_BLENDING {mode}, factor {factor}')
        material = build('csgo_lightmappedgeneric.vfx', ('g_tColor', 'g_tLayer2Color', 'g_tBlendModulation'))
        self.assertNotIn('g_tBlendModulation', texture_nodes(material))  # VertexBlend doesn't modulate
        # One layer: the group gets a factor of 0.
        material = build('csgo_lightmappedgeneric.vfx', ('g_tColor', 'g_tBlendModulation'))
        group = shader_node(material, 'csgo_lightmappedgeneric.vfx')
        self.assertFalse(group.inputs['BlendModulate'].is_linked)
        self.assertEqual(tuple(group.inputs['BlendModulate'].default_value)[:3], (0.0, 0.0, 0.0))

if __name__ == '__main__':
    unittest.main()
