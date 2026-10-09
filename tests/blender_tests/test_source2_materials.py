import os
import tempfile
import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.material_loader import ShaderRegistry
from SourceIO.blender_bindings.material_loader.shader_base import (MIX_A, MIX_B, MIX_FACTOR, ALPHA_CLIP_LABEL,
                                                                  unfilter_alpha_clips)
from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import Source2ShaderBase
from SourceIO.blender_bindings.source2.vmat_loader import load_material
from SourceIO.library.source2.blocks.texture_data import TextureImportSettings
from SourceIO.library.source2.resource_types import CompiledMaterialResource
from SourceIO.library.utils import TinyPath


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

    def test_vmat_texture_settings_are_scoped_and_isolate_material_cache(self):
        resource = FakeMaterial('csgo_complex.vfx', ('g_tColor',))
        settings = TextureImportSettings(mip_level=2, decode_packed_channels=False)
        material = load_material(
            None,
            resource,
            TinyPath("materials/test/settings.vmat"),
            texture_settings=settings,
        )
        default_material = load_material(
            None,
            resource,
            TinyPath("materials/test/settings.vmat"),
        )

        self.assertIsNot(material, default_material)
        self.assertEqual(material["sourceio_texture_settings"], settings.cache_identity())
        self.assertEqual(resource.texture_import_settings, TextureImportSettings())

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
    def render_over_background(material, background, size=4, vertex_color=None, uv_layers=None, object_color=None):
        """Render a quad filling the camera with this material in front of a uniform world color; the mean
        pixel. The file doesn't hold the radiance as rendered, so compare with ``render_constant``. uv_layers
        adds UV maps with a constant value, {name: (u, v)}; object_color sets the quad's object color."""
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
        if object_color is not None:
            obj.color = object_color
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

    # csgo_environment and csgo_environment_blend, against Python ports of VRF's csgo_environment.frag.

    def render_link(self, material, input_socket, vertex_color=(0.0, 0.0, 0.0, 0.0), uv_layers=None,
                    object_color=None):
        """What feeds an input socket, rendered as an Emission (a float as grey). Imported meshes always have a
        COLOR attribute (zeros where the model has none), so the quad gets one."""
        emission = material.node_tree.nodes.new('ShaderNodeEmission')
        material.node_tree.links.new(input_socket.links[0].from_socket, emission.inputs['Color'])
        material.node_tree.links.new(emission.outputs[0], output_node(material).inputs['Surface'])
        return self.render_over_background(material, (0.0, 0.0, 0.0), vertex_color=vertex_color,
                                           uv_layers=uv_layers, object_color=object_color)

    def assert_rendered(self, rendered, value, message=''):
        """rendered (from render_link) is what a surface emitting value renders as."""
        value = tuple(float(v) for v in np.broadcast_to(np.asarray(value, np.float64), 3))
        np.testing.assert_allclose(rendered, self.render_constant(value, (0.0, 0.0, 0.0)), atol=3e-3,
                                   err_msg=message)

    def assert_link_is(self, material, input_socket, value, message='', **kwargs):
        self.assert_rendered(self.render_link(material, input_socket, **kwargs), value, message)

    @staticmethod
    def environment(shader, layers=1, textures=(), ints=None, floats=None, vectors=None, colors=None, heights=None,
                    normals=None, average=(1.0, 1.0, 1.0)):
        """An environment material with g_tColor<n>, g_tHeight<n>, g_tNormal<n> per layer, set to colors[n - 1]
        (RGB), heights[n - 1] (height, tint mask, AO, metalness) and normals[n - 1] (normal, roughness)."""
        from unittest import mock
        slots = [f'{kind}{n}' for n in range(1, layers + 1) for kind in ('g_tColor', 'g_tHeight', 'g_tNormal')]
        with mock.patch.object(Source2ShaderBase, '_texture_average_color', return_value=average):
            material = build(shader, tuple(slots) + tuple(textures), ints=ints, floats=floats, vectors=vectors)
        for n in range(1, layers + 1):
            for slot, values, default in ((f'g_tColor{n}', colors, (1.0, 1.0, 1.0)),
                                          (f'g_tHeight{n}', heights, (0.5, 1.0, 1.0, 0.0)),
                                          (f'g_tNormal{n}', normals, (0.5, 0.5, 1.0, 0.5))):
                value = tuple(values[n - 1]) if values else default
                Source2MaterialTests.set_color(material, slot, value if len(value) == 4 else (*value, 1.0))
        return material

    @staticmethod
    def bsdf(material, shader):
        return material.node_tree.nodes[shader]

    def test_environment_color_matrix(self):
        # Untinted, g_mTextureAdjust is round 23's contrast, brightness and saturation; a grey tint does nothing,
        # a white one is the identity.
        from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import SATURATION_WEIGHTS
        from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.csgo_environment import color_matrix
        color, average, contrast, saturation, brightness = np.array((0.5, 0.25, 0.75)), (0.4, 0.3, 0.2), 1.5, 0.5, 1.2
        adjusted = brightness * ((color - average) * contrast + average)
        grey = np.dot(adjusted, SATURATION_WEIGHTS)
        np.testing.assert_allclose((np.append(color, 1.0) @ color_matrix(contrast, saturation, brightness, average))[:3],
                                   grey + saturation * (adjusted - grey), atol=1e-9)
        np.testing.assert_allclose(color_matrix(1, 1, 1, average, (0.3, 0.3, 0.3)), np.identity(4), atol=1e-9)
        np.testing.assert_allclose(color_matrix(1, 1, 1, average), np.identity(4), atol=1e-9)

    def test_environment_layer(self):
        # The tint mask (height green through its contrast and brightness) blends in the color adjusted with the
        # tint, over the color (mode 0) or the color adjusted without it (mode 1). Metalness is height alpha,
        # roughness the normal map's alpha through its contrast and brightness.
        from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.csgo_environment import color_matrix
        color, average, tint = np.array((0.5, 0.25, 0.75)), (0.4, 0.3, 0.2), (0.7, 0.5, 0.3)
        raw_mask, rough = 0.6, 0.4
        mask = np.clip(((raw_mask - 0.5) * 1.5 + 0.5) * 0.8, 0, 1)
        floats = {'g_fTextureColorContrast1': 1.5, 'g_fTextureColorSaturation1': 0.5,
                  'g_fTextureColorBrightness1': 1.2, 'g_fTintMaskContrast1': 1.5, 'g_fTintMaskBrightness1': 0.8,
                  'g_fTextureRoughnessContrast1': 2.0, 'g_fTextureRoughnessBrightness1': 1.25}
        tinted = (np.append(color, 1.0) @ color_matrix(1.5, 0.5, 1.2, average, tint))[:3]
        untinted = (np.append(color, 1.0) @ color_matrix(1.5, 0.5, 1.2, average))[:3]
        for mode, base in ((0, color), (1, untinted)):
            material = self.environment('csgo_environment.vfx', ints={'g_nColorCorrectionMode1': mode}, floats=floats,
                                        vectors={'g_vTextureColorTint1': (*tint, 0.0)}, colors=[color],
                                        heights=[(0.5, raw_mask, 1.0, 0.3)], normals=[(0.5, 0.5, 1.0, rough)],
                                        average=average)
            bsdf = self.bsdf(material, 'csgo_environment.vfx')
            self.assert_link_is(material, bsdf.inputs['Base Color'],
                                np.clip(base + (tinted - base) * mask, 0, 1), f'mode {mode}')
        self.assert_link_is(material, bsdf.inputs['Metallic'], 0.3)
        self.assert_link_is(material, bsdf.inputs['Roughness'], np.clip(((rough - 0.5) * 2.0 + 0.5) * 1.25, 0, 1))
        material = self.environment('csgo_environment.vfx', ints={'g_bMetalness1': 0}, heights=[(0.5, 1, 1, 0.3)])
        self.assertFalse(self.bsdf(material, 'csgo_environment.vfx').inputs['Metallic'].is_linked)

    def test_environment_vertex_color(self):
        # The painted color times g_vColorTint (sRGB), faded by the paint's alpha, masked by the raw tint mask.
        color, raw_mask, paint, tint = np.array((0.5, 0.25, 0.75)), 0.6, np.array((0.2, 0.4, 0.8, 0.5)), 0.8
        linear_tint = ((tint + 0.055) / 1.055) ** 2.4
        material = self.environment('csgo_environment.vfx', floats={'g_fTintMaskContrast1': 2.0},
                                    vectors={'g_vColorTint': (tint, tint, tint, 0.0)}, colors=[color],
                                    heights=[(0.5, raw_mask, 1.0, 0.0)])
        vertex = linear_tint * (1 + (paint[:3] - 1) * paint[3])
        self.assert_link_is(material, self.bsdf(material, 'csgo_environment.vfx').inputs['Base Color'],
                            color * (1 + (vertex - 1) * raw_mask), vertex_color=paint)

    def test_environment_model_tint(self):
        # The model tint (the object color, stored sRGB) is colorized into the tint mask: VRF's ColorizeTint by
        # g_flModelTintAmount × (1 - min(tint)) × tint mask; off with g_bModelTint1 0. The color ends clamped.
        color, raw_mask, tint = np.array((0.5, 0.25, 0.75)), 0.7, np.array((0.9, 0.5, 0.3))
        linear_tint = ((tint + 0.055) / 1.055) ** 2.4
        for ints, amount in (({}, 0.8 * (1 - linear_tint.min()) * raw_mask), ({'g_bModelTint1': 0}, 0.0)):
            material = self.environment('csgo_environment.vfx', ints=ints, floats={'g_flModelTintAmount': 0.8},
                                        colors=[color], heights=[(0.5, raw_mask, 1.0, 0.0)])
            self.assert_link_is(material, self.bsdf(material, 'csgo_environment.vfx').inputs['Base Color'],
                                self.vrf_colorize(color, linear_tint, amount), f'{ints}',
                                object_color=(*tint, 1.0))

    @staticmethod
    def rotate_and_contrast(normal, rotation, contrast):
        angle = np.radians(rotation)
        turned = np.array((np.cos(angle) * normal[0] - np.sin(angle) * normal[1],
                           np.sin(angle) * normal[0] + np.cos(angle) * normal[1], normal[2]))
        result = np.array((0.0, 0.0, 1.0)) + (turned - (0.0, 0.0, 1.0)) * contrast
        return result / np.linalg.norm(result)

    def test_environment_normal(self):
        # The normal turns with the layer's UV rotation, then contrast: normalize(lerp(up, n, contrast)); a detail
        # normal (its own rotation and contrast) folds in as normalize(n + detail - up).
        normal, detail = np.array((-0.4, 0.2, 0.894)), np.array((0.3, -0.1, 0.949))
        base = self.rotate_and_contrast(normal, 30.0, 1.5)
        folded = base + self.rotate_and_contrast(detail, 10.0, 0.5) - (0.0, 0.0, 1.0)
        for ints, expected in (({}, base), ({'F_DETAIL_NORMAL': 1}, folded / np.linalg.norm(folded))):
            material = self.environment('csgo_environment.vfx', textures=('g_tNormalDetail1',), ints=ints,
                                        floats={'g_flTexCoordRotation1': 30.0, 'g_fTextureNormalContrast1': 1.5,
                                                'g_flDetailTexCoordRotation1': 10.0,
                                                'g_fDetailTextureNormalContrast1': 0.5},
                                        normals=[(*(normal * 0.5 + 0.5), 0.5)])
            self.assertEqual('g_tNormalDetail1' in texture_nodes(material), bool(ints))
            if ints:
                self.set_color(material, 'g_tNormalDetail1', (*(detail * 0.5 + 0.5), 1.0))
            normal_map = linked_node(self.bsdf(material, 'csgo_environment.vfx').inputs['Normal'])
            self.assert_link_is(material, normal_map.inputs['Color'], expected * 0.5 + 0.5, f'{ints}')

    def test_environment_normal_turns_with_image(self):
        # A constant tangent-space slope through the layer's rotation tilts the shading normal the way a Bump of a
        # height rising along the transformed U does (the image as the UV transform lays it on the surface).
        from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import _uv_transform_group
        from SourceIO.blender_bindings.material_loader.shaders.source2_shaders.csgo_environment import (
            _layer_normal_group)

        def tilt(rotation, bump):
            material = bpy.data.materials.new('tilt')
            nodes, links = material.node_tree.nodes, material.node_tree.links
            nodes.clear()
            emission = nodes.new('ShaderNodeEmission')
            links.new(emission.outputs[0], nodes.new('ShaderNodeOutputMaterial').inputs['Surface'])
            if bump:
                uv = nodes.new('ShaderNodeUVMap')
                uv.uv_map = 'TEXCOORD'
                transform = nodes.new('ShaderNodeGroup')
                transform.node_tree = _uv_transform_group()
                transform.inputs['g_flTexCoordRotation'].default_value = rotation
                links.new(uv.outputs[0], transform.inputs[0])
                height = nodes.new('ShaderNodeSeparateXYZ')
                links.new(transform.outputs[0], height.inputs[0])
                bump_node = nodes.new('ShaderNodeBump')
                bump_node.inputs['Distance'].default_value = 0.2
                links.new(height.outputs['X'], bump_node.inputs['Height'])
                normal = bump_node.outputs[0]
            else:
                layer = nodes.new('ShaderNodeGroup')
                layer.node_tree = _layer_normal_group()
                layer.inputs['Normal'].default_value = (0.3, 0.5, 0.958, 1.0)  # (-0.4, 0, 0.917): uphill along +U
                layer.inputs['Rotation'].default_value = rotation
                encode = nodes.new('ShaderNodeVectorMath')
                encode.operation = 'MULTIPLY_ADD'
                links.new(layer.outputs[0], encode.inputs[0])
                encode.inputs[1].default_value = encode.inputs[2].default_value = (0.5, 0.5, 0.5)
                normal_map = nodes.new('ShaderNodeNormalMap')
                links.new(encode.outputs[0], normal_map.inputs['Color'])
                normal = normal_map.outputs[0]
            shown = nodes.new('ShaderNodeVectorMath')
            shown.operation = 'MULTIPLY_ADD'
            links.new(normal, shown.inputs[0])
            shown.inputs[1].default_value = shown.inputs[2].default_value = (0.5, 0.5, 0.5)
            links.new(shown.outputs[0], emission.inputs['Color'])
            xy = self.render_over_background(material, (0.0, 0.0, 0.0))[:2] - self.render_constant(
                (0.5, 0.5, 0.5), (0.0, 0.0, 0.0))[:2]
            return np.degrees(np.arctan2(xy[1], xy[0]))

        for rotation in (0.0, 90.0, 33.0, -60.0):
            self.assertAlmostEqual(tilt(rotation, False), tilt(rotation, True), delta=1.0, msg=f'{rotation}')

    @staticmethod
    def vrf_band_weight(difference, factor, height, softness, mask_with_height):
        softness = max(softness, 1e-4)
        quarter, saturate = softness * 0.25, lambda v: min(max(v, 0.0), 1.0)
        edge = 0.02 + quarter
        crossfade = saturate(0.5 + difference / (2.0 * softness))
        edge_high = saturate((saturate(factor + difference * 0.1 + 0.2) - (0.98 - quarter)) / edge)
        crossfade += (1.0 - crossfade) * edge_high
        edge_low = saturate((edge - saturate(factor + difference * 0.05 - 0.05)) / edge)
        crossfade -= crossfade * edge_low
        return saturate(crossfade * (0.5 + (height - 0.5) * mask_with_height) * 2.0)

    @classmethod
    def vrf_blend_layer(cls, below, factor, height, zero, scale, softness, influence=1.0, mask_with_height=0.0):
        """VRF's BlendLayer; below and the result are (weight, difference, signed raw, signed carry, under height)."""
        out = {'weight': 0.0, 'difference': 0.0, 'signed_raw': 0.0, 'signed_carry': 0.0,
               'under': below['under']}
        if factor <= 0.0:
            return out
        signed = 2.0 * factor - 1.0
        plus_signed = (height - zero) * scale + signed + max(below['signed_raw'], 0.0)
        difference = plus_signed - (below['signed_carry'] + (below['under'] - below['signed_carry']) * influence)
        out.update(weight=cls.vrf_band_weight(difference, factor, height, softness, mask_with_height),
                   difference=difference, signed_raw=signed)
        if out['weight'] > 0.05:
            out['signed_carry'] = signed * out['weight']
            out['under'] = below['under'] + (max(below['under'], plus_signed) - below['under']) * out['weight']
        return out

    @staticmethod
    def vrf_blend_weights(height1, height2, scale1, scale2, zero1, zero2, factor, softness):
        """VRF's GetBlendWeights (heights are already minus their zero points)."""
        h1 = scale1 + softness
        top1 = height1 * h1
        h2 = scale2 + softness
        blend1 = (-zero1 * h1 - (1.0 - zero2) * h2) - softness
        blend2 = (1.0 - zero1) * h1 + zero2 * h2
        top2 = height2 * (scale2 - softness) + blend1 + (blend2 - blend1) * factor
        top = max(top1, top2) - softness
        weights = np.array((max(top1 - top, 0.0) + 0.001, max(top2 - top, 0.0)))
        return weights / weights.sum()

    def blend_layers(self, layers=2, ints=None, floats=None, colors=None, heights=None, paint=(0.0, 0.0),
                     softness=0.0, socket='Base Color', vertex_color=(0.0, 0.0, 0.0, 0.0), **kwargs):
        material = self.environment('csgo_environment_blend.vfx', layers, ints=ints, floats=floats, colors=colors,
                                    heights=heights, **kwargs)
        return self.render_link(material, self.bsdf(material, 'csgo_environment_blend.vfx').inputs[socket],
                                vertex_color=vertex_color,
                                uv_layers={'TEXCOORD_4': paint, 'TEXCOORD_4_2': (0.0, softness)})

    def test_environment_blend_legacy_weights(self):
        # Without F_USE_NEW_BLENDING: GetBlendWeights over the heights (minus their zero points), with the painted
        # weight (TEXCOORD_4.x) as is and softness w + g_flBlendSoftness2 (TEXCOORD_4_2.y is w). A black and a
        # white layer render as layer 2's weight.
        black_white = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
        cases = ((0.6, 0.4, 0.5, {}, 0.1), (0.3, 0.7, 0.4, {'g_flHeightMapScale2': 2.0}, 0.0),
                 (0.5, 0.5, 0.8, {'g_flHeightMapZeroPoint1': 0.2, 'g_flBlendSoftness2': 0.3}, 0.2),
                 (0.5, 0.5, 0.0, {}, 0.0), (0.5, 0.5, 1.0, {}, 0.0))
        for height1, height2, paint, floats, stream_softness in cases:
            softness = min(max(stream_softness + floats.get('g_flBlendSoftness2', 0.01), 0.001), 1.0)
            zero1 = floats.get('g_flHeightMapZeroPoint1', 0.5)
            weights = self.vrf_blend_weights(height1 - zero1, height2 - 0.5, 1.0, floats.get('g_flHeightMapScale2', 1.0),
                                             zero1, 0.5, paint, softness)
            rendered = self.blend_layers(floats=floats, colors=black_white, paint=(paint, 0.0), softness=stream_softness,
                                         heights=[(height1, 1, 1, 0), (height2, 1, 1, 0)])
            self.assert_rendered(rendered, weights[1], f'{height1} {height2} {paint} {floats}')

    def test_environment_blend_height_band(self):
        # F_USE_NEW_BLENDING: BlendLayer over layer 1's scaled height, the paint remapped by 1.1x - 0.05; layer 3
        # measured against what layer 2 carries up. Layers black, red and blue render as (w2 (1 - w3), 0, w3).
        colors = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
        cases = ((0.6, 0.5, 0.4, 0.5, 0.0, {}), (0.3, 0.7, 0.5, 0.6, 0.0, {'g_flMaskWithHeight2': 0.8}),
                 (0.5, 0.4, 0.6, 0.9, 0.7, {'g_flUnderlyingHeightMapInfluence3': 0.5, 'g_flHeightMapScale3': 2.0}),
                 (0.5, 0.5, 0.5, 0.0, 0.5, {}), (0.6, 0.5, 0.5, 0.55, 0.45, {'g_flBlendSoftness3': 0.2}))
        saturate = lambda v: min(max(v, 0.0), 1.0)
        for height1, height2, height3, paint2, paint3, floats in cases:
            floats = {'g_flBlendSoftness2': 0.3, **floats}
            softness3 = floats.get('g_flBlendSoftness3', 0.01)
            softness = floats['g_flBlendSoftness2'] if paint2 >= 0.001 else softness3
            softness = saturate(softness + (softness3 - softness) * paint3)
            seed = {'signed_raw': 0.0, 'signed_carry': 0.0, 'under': (height1 - 0.5) * 1.0}
            carry2 = self.vrf_blend_layer(seed, saturate(paint2 * 1.1 - 0.05), height2, 0.5, 1.0, softness,
                                          mask_with_height=floats.get('g_flMaskWithHeight2', 0.0))
            carry3 = self.vrf_blend_layer(carry2, saturate(paint3 * 1.1 - 0.05), height3, 0.5,
                                          floats.get('g_flHeightMapScale3', 1.0), softness,
                                          floats.get('g_flUnderlyingHeightMapInfluence3', 1.0))
            w2, w3 = carry2['weight'], carry3['weight']
            rendered = self.blend_layers(3, ints={'F_USE_NEW_BLENDING': 1, 'F_ENABLE_LAYER_3': 1}, floats=floats,
                                         colors=colors, paint=(paint2, paint3),
                                         heights=[(h, 1, 1, 0) for h in (height1, height2, height3)])
            self.assert_rendered(rendered, (w2 * (1 - w3), 0.0, w3),
                                 f'{height1} {height2} {height3} {paint2} {paint3} {floats}')
        # Two layers: layer 3's textures aren't loaded.
        material = self.environment('csgo_environment_blend.vfx', 2, textures=('g_tColor3', 'g_tHeight3'))
        self.assertFalse(texture_nodes(material) & {'g_tColor3', 'g_tHeight3'})

    @staticmethod
    def vrf_colorize(albedo, tint, amount):
        luma_weights = np.array((0.2125, 0.7154, 0.0721))
        direction = np.maximum(tint, 0.001) / np.linalg.norm(np.maximum(tint, 0.001))
        luma = np.dot(albedo, luma_weights)
        tinted = min(luma / np.dot(direction, luma_weights), 3.0 * luma * max(tint))
        return np.clip(albedo + (direction * tinted - albedo) * amount, 0, 1)

    def test_environment_blend_border(self):
        # F_BLEND_EFFECTS_2 with the height-band blend: VRF's ApplyBlendBorder recolors both layers (and sets their
        # roughness) in a band around the seam, before they are combined by layer 2's weight.
        def smoothstep(edge0, edge1, x):
            t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
            return t * t * (3 - 2 * t)

        lower, upper = np.array((0.5, 0.25, 0.75)), np.array((0.2, 0.6, 0.4))
        height1, height2, paint, softness = 0.55, 0.5, 0.5, 0.3
        tint_srgb, spread, border_softness, offset, layer_amount = 0.6, 0.3, 0.1, 0.05, (1.0, 0.5)
        tint = np.array((tint_srgb, 0.4, 0.2))
        tint_linear = np.where(tint <= 0.04045, tint / 12.92, ((tint + 0.055) / 1.055) ** 2.4)
        tint_masks = (0.8, 0.4)
        factor = np.clip(paint * 1.1 - 0.05, 0, 1)
        carry = self.vrf_blend_layer({'signed_raw': 0.0, 'signed_carry': 0.0, 'under': height1 - 0.5}, factor,
                                     height2, 0.5, 1.0, softness)
        weight = carry['weight']
        paint_mask = np.clip(factor * 5, 0, 1) * np.clip((1 - factor) * 5, 0, 1)
        band_spread = spread * (0.5 + 0.5 * 1.0) * paint_mask
        band_softness = softness + border_softness
        band = ((1 - smoothstep(band_spread - band_softness, band_spread + band_softness,
                                abs(carry['difference'] + offset))) * (1 - np.clip(band_softness / spread * 0.01, 0, 1)))
        sides = np.clip(np.array(layer_amount) * 0.5 * 2, 0, 1)
        amount = (band * np.clip(paint_mask * (4 + (1 - 4) * np.clip(band_softness * 0.5, 0, 1)), 0, 1)
                  * (sides[0] + (sides[1] - sides[0]) * weight))
        mask = tint_masks[0] + (tint_masks[1] - tint_masks[0]) * weight
        for mode in range(4):
            def recolor(color):
                if mode == 3:
                    return color + (self.vrf_colorize(color, tint_linear, mask) - color) * amount
                if mode == 1:
                    return color + (tint_linear - color) * amount * mask
                return color * (1 + ((tint_linear * (2 if mode == 2 else 1)) - 1) * amount * mask)

            expected = recolor(lower) + (recolor(upper) - recolor(lower)) * weight
            ints = {'F_USE_NEW_BLENDING': 1, 'F_BLEND_EFFECTS_2': 1, 'F_BORDER_BLEND_MODE_2': mode,
                    'g_bBorderTintMask2': 1, 'F_BORDER_ROUGHNESS_2': 1}
            floats = {'g_flBlendSoftness2': softness, 'g_flBorderSpread2': spread, 'g_flBorderOffset2': offset,
                      'g_flBorderSoftness2': border_softness, 'g_fBorderRoughness2': 0.9}
            vectors = {'g_vBorderTint2': (*tint, 0.0), 'g_vBorderLayerAmount2': (*layer_amount, 0.0, 0.0)}
            heights = [(height1, tint_masks[0], 1, 0), (height2, tint_masks[1], 1, 0)]
            rendered = self.blend_layers(ints=ints, floats=floats, vectors=vectors, colors=[lower, upper],
                                         heights=heights, paint=(paint, 0.0))
            self.assert_rendered(rendered, expected, f'mode {mode}')
            self.assertGreater(amount, 0.2)
        # Roughness 0.5 on both layers, pulled to 0.9 by the border.
        rendered = self.blend_layers(ints=ints, floats=floats, vectors=vectors, colors=[lower, upper],
                                     heights=heights, paint=(paint, 0.0), socket='Roughness')
        self.assert_rendered(rendered, 0.5 + 0.4 * amount * mask)
        # The legacy blend has no seam to decorate.
        ints['F_USE_NEW_BLENDING'] = 0
        material = self.environment('csgo_environment_blend.vfx', 2, ints=ints)
        self.assertFalse(any(node.bl_idname == 'ShaderNodeGroup' and 'Border' in node.node_tree.name
                             for node in material.node_tree.nodes))

    def test_environment_blend_selective(self):
        # g_flColorOverlay2 multiplies by lerp(1, 2·sRGB(upper), w·overlay) before replacing by w·replace;
        # roughness lerp(lower·(1 + (upper - lower)·2·w·combine), upper, w·replace); normals fold by w·combine.
        lower, upper = np.array((0.5, 0.25, 0.75)), np.array((0.2, 0.6, 0.002))
        weight = self.vrf_blend_weights(0.0, 0.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.01)[1]
        floats = {'g_flColorOverlay2': 0.7, 'g_flColorReplace2': 0.4, 'g_flRoughnessCombine2': 0.8,
                  'g_flRoughnessReplace2': 0.3, 'g_flNormalCombine2': 0.6, 'g_flNormalReplace2': 0.2}
        overlaid = lower * (1 + (2 * self.srgb(upper) - 1) * weight * 0.7)
        rendered = self.blend_layers(floats=floats, colors=[lower, upper], paint=(0.5, 0.0),
                                     normals=[(0.5, 0.5, 1.0, 0.3), (0.5, 0.5, 1.0, 0.7)])
        self.assert_rendered(rendered, overlaid + (upper - overlaid) * weight * 0.4)
        combined = 0.3 * (1 + (0.7 - 0.3) * 2 * weight * 0.8)
        rendered = self.blend_layers(floats=floats, colors=[lower, upper], paint=(0.5, 0.0), socket='Roughness',
                                     normals=[(0.5, 0.5, 1.0, 0.3), (0.5, 0.5, 1.0, 0.7)])
        self.assert_rendered(rendered, combined + (0.7 - combined) * weight * 0.3)
        self.assertGreater(weight, 0.2)
        self.assertLess(weight, 0.8)

    def test_environment_blend_shared_color_overlay(self):
        # F_SHARED_COLOR_OVERLAY: o = 2·overlay - 1 scales the color by max(0, 1 + (1 - (1 - max(0, o))^B)·B +
        # ((1 + min(0, o))^D - 1)·D), by Σ lerp(1, mask or 1 - mask, |mask strength|)·layer strength·share.
        lower, upper, overlay = np.array((0.5, 0.25, 0.75)), np.array((0.2, 0.6, 0.4)), np.array((0.8, 0.3, 0.5))
        bright, dark, tint_masks = 1.5, 0.75, (0.6, 0.3)
        weight = self.vrf_blend_weights(0.0, 0.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.01)[1]
        o = overlay * 2 - 1
        factor = np.maximum(0, 1 + (1 - (1 - np.maximum(0, o)) ** bright) * bright
                            + ((1 + np.minimum(0, o)) ** dark - 1) * dark)
        amount = ((1 + (tint_masks[0] - 1) * 0.5) * 0.8 * (1 - weight)
                  + (1 + ((1 - tint_masks[1]) - 1) * 1.0) * 1.0 * weight)
        material = self.environment('csgo_environment_blend.vfx', 2, textures=('g_tSharedColorOverlay',),
                                    ints={'F_SHARED_COLOR_OVERLAY': 1},
                                    floats={'g_flOverlayBrightnessContrast': bright,
                                            'g_flOverlayDarknessContrast': dark},
                                    vectors={'g_vColorOverlayLayerStrengths': (0.8, 1.0, 1.0, 0.0),
                                             'g_vColorOverlayTintMaskStrengths': (0.5, -1.0, 0.0, 0.0)},
                                    colors=[lower, upper], heights=[(0.5, tint_masks[0], 1, 0),
                                                                    (0.5, tint_masks[1], 1, 0)])
        self.assertFalse(material.node_tree.nodes['g_tSharedColorOverlay'].image.colorspace_settings.is_data)
        self.set_color(material, 'g_tSharedColorOverlay', (*overlay, 1.0))
        rendered = self.render_link(material, self.bsdf(material, 'csgo_environment_blend.vfx').inputs['Base Color'],
                                    uv_layers={'TEXCOORD_4': (0.5, 0.0), 'TEXCOORD_4_2': (0.0, 0.0)})
        blended = lower + (upper - lower) * weight
        self.assert_rendered(rendered, blended * (1 + (factor - 1) * amount))

    def test_environment_blend_vertex_color(self):
        # g_nVertexColorMode<n>: 0 masked by the tint mask, 1 unmasked, 2 disabled, weighed by each layer's share:
        # color · lerp(1, paint, saturate(Σ masked + Σ unmasked) · Σ enabled).
        lower, upper, paint = np.array((0.5, 0.25, 0.75)), np.array((0.2, 0.6, 0.4)), np.array((0.2, 0.4, 0.8, 0.5))
        tint_masks = (0.6, 0.3)
        weight = self.vrf_blend_weights(0.0, 0.0, 1.0, 1.0, 0.5, 0.5, 0.5, 0.01)[1]
        shares = (1 - weight, weight)
        vertex = 1 + (paint[:3] - 1) * paint[3]
        for modes in ((0, 0), (1, 0), (0, 2), (2, 1)):
            masked = sum(mask * share for mask, share in zip(tint_masks, shares))
            unmasked = sum(float(mode != 0) * share for mode, share in zip(modes, shares))
            enabled = sum(float(mode != 2) * share for mode, share in zip(modes, shares))
            vertex_mask = np.clip(unmasked + masked, 0, 1) * enabled
            rendered = self.blend_layers(ints={f'g_nVertexColorMode{n}': mode for n, mode in zip((1, 2), modes)},
                                         colors=[lower, upper], paint=(0.5, 0.0), vertex_color=paint,
                                         heights=[(0.5, tint_masks[0], 1, 0), (0.5, tint_masks[1], 1, 0)])
            blended = lower + (upper - lower) * weight
            self.assert_rendered(rendered, blended * (1 + (vertex - 1) * vertex_mask), f'{modes}')

    def test_environment_blend_facing_direction(self):
        # F_BLEND_BY_FACING_DIRECTION_2 scales the paint by smoothstep(max(0, 1 - spread - softness),
        # min(1, 1 - spread + 0.001 + softness), dot(direction, normal)·0.5 + 0.5); the test quad faces +Z.
        def smoothstep(edge0, edge1, x):
            t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
            return t * t * (3 - 2 * t)

        black_white = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
        for direction, spread, softness in (((1.0, 0.0, 1.0), 0.2, 0.15), ((0.0, 0.0, -1.0), 0.5, 0.1),
                                            ((0.0, 1.0, 0.0), 0.5, 0.3)):
            unit = np.array(direction) / np.linalg.norm(direction)
            facing = smoothstep(max(0, 1 - spread - softness), min(1, 1 - spread + 0.001 + softness),
                                unit[2] * 0.5 + 0.5)
            weight = self.vrf_blend_weights(0.0, 0.0, 1.0, 1.0, 0.5, 0.5, 0.8 * facing, 0.01)[1]
            rendered = self.blend_layers(ints={'F_BLEND_BY_FACING_DIRECTION_2': 1}, colors=black_white,
                                         floats={'g_flFacingDirectionMaskSpread2': spread,
                                                 'g_vFacingDirectionMaskSoftness2': softness},
                                         vectors={'g_vFacingDirection2': (*direction, 0.0)}, paint=(0.8, 0.0))
            self.assert_rendered(rendered, weight, f'{direction}')

    def test_environment_alpha_test(self):
        material = self.environment('csgo_environment.vfx', ints={'F_ALPHA_TEST': 1},
                                    floats={'g_flAlphaTestReference': 0.3})
        self.assertEqual(material.surface_render_method, 'DITHERED')
        clip = linked_node(self.bsdf(material, 'csgo_environment.vfx').inputs['Alpha'])
        self.assertEqual(clip.label, ALPHA_CLIP_LABEL)
        self.assertAlmostEqual(clip.inputs[1].default_value, 0.3, places=6)


if __name__ == '__main__':
    unittest.main()
