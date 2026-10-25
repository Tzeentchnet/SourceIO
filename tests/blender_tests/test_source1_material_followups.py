"""Material/texture lookup indexes, decode-time normal map conversion and the Principled VertexLitGeneric path."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.material_loader import (
    ShaderRegistry,
    get_or_create_source1_material,
)
from SourceIO.blender_bindings.material_loader.shader_base import ALPHA_CLIP_LABEL, ExtraMaterialParameters
from SourceIO.blender_bindings.material_loader.shaders import source1_shader_base
from SourceIO.blender_bindings.material_loader.shaders.source1_shader_base import Source1ShaderBase
from SourceIO.blender_bindings.material_loader.shaders.source1_shaders.lightmap_generic import LightmapGeneric
from SourceIO.blender_bindings.material_loader.shaders.source1_shaders.vertexlit_generic import (
    VertexLitGeneric,
    phong_exponent_to_roughness,
)
from SourceIO.blender_bindings.preferences import SourceIOPreferences
from SourceIO.blender_bindings.source1 import vtf as blender_vtf
from SourceIO.blender_bindings.utils import bpy_utils, texture_utils
from SourceIO.blender_bindings.utils.bpy_utils import get_or_create_material, pause_view_layer_update
from SourceIO.library.source1.vmt import VMT
from SourceIO.library.utils import MemoryBuffer
from SourceIO.library.utils.tiny_path import TinyPath


class _NoFiles:
    def find_file(self, *args, **kwargs):
        return None


def _vmt(text: str) -> VMT:
    return VMT(MemoryBuffer(text.encode()), 'test.vmt', _NoFiles())


def _pixels(image) -> np.ndarray:
    buffer = np.zeros(image.size[0] * image.size[1] * 4, np.float32)
    image.pixels.foreach_get(buffer)
    return np.rint(buffer * 255).astype(int).reshape(-1, 4)


class _CleanFile(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        bpy.context.scene.TextureCachePath = ""


class MaterialIndexTests(_CleanFile):
    def test_equivalent_paths_share_a_material(self):
        material = get_or_create_material("wall", "materials/brick/wall")
        self.assertIs(get_or_create_material("WALL", "/Materials\\Brick\\Wall/"), material)

    def test_renamed_and_external_materials_are_found(self):
        material = get_or_create_material("wall", "materials/brick/wall")
        material.name = "renamed"
        self.assertIs(get_or_create_material("wall", "materials/brick/wall"), material)
        external = bpy.data.materials.new("external")
        external["full_path"] = "materials/brick/external"
        self.assertIs(get_or_create_material("external", "materials/brick/external"), external)

    def test_material_added_after_a_removal_is_found(self):
        bpy.data.materials.remove(get_or_create_material("a", "materials/a"))
        external = bpy.data.materials.new("b")  # same count as before the removal
        external["full_path"] = "materials/b"
        self.assertIs(get_or_create_material("b", "materials/b"), external)

    def test_material_renamed_and_retargeted_in_place_is_found(self):
        material = get_or_create_material("a", "materials/a")
        material.name = "b"
        material["full_path"] = "materials/b"
        self.assertIs(get_or_create_material("b", "materials/b"), material)

    def test_source1_shader_modes_have_distinct_stable_identities(self):
        principled = get_or_create_source1_material("wall", "materials/brick/wall")
        bvlg = get_or_create_source1_material("wall", "materials/brick/wall", True)
        self.assertIs(get_or_create_source1_material("wall", "materials/brick/wall"), principled)
        self.assertIs(get_or_create_source1_material("wall", "materials/brick/wall", True), bvlg)
        self.assertIsNot(principled, bvlg)
        self.assertEqual(principled["full_path"], "materials/brick/wall")
        self.assertEqual(bvlg["full_path"], "materials/brick/wall?sourceio-shader=bvlg")
        self.assertEqual(bvlg.name, "wall [BVLG]")


class TextureIndexTests(_CleanFile):
    def _image(self, name, path, identity=""):
        image = bpy.data.images.new(name, 1, 1)
        image["full_path"] = path
        image[texture_utils.CACHE_ID_PROPERTY] = identity
        return image

    def test_identities_are_kept_apart_and_removals_noticed(self):
        path = TinyPath("brick/wall_normal")
        plain = self._image("plain", "brick/wall_normal")
        flipped = self._image("flipped", "brick/wall_normal", "invert_y")
        texture_utils._add_texture(path, flipped.name, "invert_y")
        self.assertIs(texture_utils.check_texture_cache(path), plain)
        self.assertIs(texture_utils.check_texture_cache(path, "invert_y"), flipped)
        bpy.data.images.remove(plain)
        self.assertIsNone(texture_utils.check_texture_cache(path))
        flipped.name = "renamed"
        self.assertIs(texture_utils.check_texture_cache(path, "invert_y"), flipped)

    def test_texture_variant_is_cached_without_mutating_the_source(self):
        path = TinyPath('brick/detail')
        plain = self._image('plain', path.as_posix())
        plain.colorspace_settings.is_data = False
        plain.colorspace_settings.name = 'sRGB'
        plain['sourceio_vtf_metadata'] = '{"flags": 0}'

        variant = texture_utils.create_texture_variant(path, plain, 'detail_data')
        variant.colorspace_settings.is_data = True
        variant.colorspace_settings.name = 'Non-Color'

        self.assertIsNot(variant, plain)
        self.assertEqual(variant['sourceio_vtf_metadata'], plain['sourceio_vtf_metadata'])
        self.assertEqual(variant[texture_utils.CACHE_ID_PROPERTY], 'detail_data')
        self.assertEqual(variant.colorspace_settings.name, 'Non-Color')
        self.assertEqual(plain.colorspace_settings.name, 'sRGB')
        self.assertIs(texture_utils.create_texture_variant(path, plain, 'detail_data'), variant)


class CachedVTFMetadataTests(_CleanFile):
    def test_cached_image_recovers_vtf_sampler_flags(self):
        image = bpy.data.images.new("cached", 1, 1)

        class _OneVTF:
            def __init__(self):
                self.calls = 0

            def find_file(self, *args, **kwargs):
                self.calls += 1
                return MemoryBuffer(b"VTF")

        content_manager = _OneVTF()
        shader = Source1ShaderBase(content_manager, _vmt('"VertexLitGeneric" {}'))

        def tag_metadata(cached_image, data):
            self.assertEqual(data, b"VTF")
            cached_image["sourceio_vtf_flags"] = "0x00000001"
            cached_image["sourceio_vtf_metadata"] = "{}"

        with patch.object(source1_shader_base, "check_texture_cache", return_value=image), \
                patch.object(source1_shader_base, "tag_vtf_metadata", side_effect=tag_metadata) as tag:
            self.assertIs(shader.load_texture(TinyPath("cached")), image)
            self.assertIs(shader.load_texture(TinyPath("cached")), image)

        tag.assert_called_once()
        self.assertEqual(content_manager.calls, 1)
        self.assertEqual(image["sourceio_vtf_flags"], "0x00000001")


class DecodeTimeConversionTests(_CleanFile):
    def _import(self, pixels, **kwargs):
        with patch.object(blender_vtf, "load_texture", return_value=(pixels, *pixels.shape[:2])), \
                patch.object(blender_vtf, "tag_vtf_metadata"):
            return blender_vtf.import_texture(TinyPath("t/normal"), MemoryBuffer(b"VTF"), **kwargs)

    def test_normal_map_green_is_flipped_before_encoding(self):
        pixels = np.random.default_rng(0).integers(0, 256, (4, 4, 4), dtype=np.uint8)
        image = self._import(pixels, invert_y=True, cache_identity="invert_y")
        self.assertTrue(image["normalmap_converted"])
        self.assertEqual(image.name, "normal")
        expected = pixels.copy()
        expected[..., 1] = 255 - expected[..., 1]
        # The image's first row is the top one; Blender's pixels start at the bottom.
        np.testing.assert_array_equal(_pixels(image), expected[::-1].reshape(-1, 4))

    def test_float_normal_map_is_not_flipped_twice(self):
        pixels = np.array([[[0.2, 0.25, 0.8, 1.0]]], dtype=np.float32)
        image = self._import(pixels, invert_y=True, cache_identity="invert_y")
        converted = _pixels(image)
        self.assertTrue(image["normalmap_converted"])
        Source1ShaderBase.convert_normalmap(image)
        np.testing.assert_array_equal(_pixels(image), converted)
        self.assertEqual(converted[0, 1], 191)

    def test_flat_ssbump_becomes_a_flat_normal(self):
        flat = np.full((1, 1, 4), 255, np.uint8)
        flat[..., :3] = round(255 / 3 ** 0.5)
        normal = blender_vtf.ssbump_to_normal(flat)[0, 0]
        np.testing.assert_allclose(normal, [0.5, 0.5, 1.0, 1.0], atol=0.01)


class DetailTextureColorspaceTests(_CleanFile):
    def _assert_colorspace(self, shader_type, mode, is_data):
        image = bpy.data.images.new(f'{shader_type.__name__}_{mode}', 1, 1, alpha=True)
        image.colorspace_settings.is_data = False
        image.colorspace_settings.name = 'sRGB'
        suffix = f'{shader_type.__name__.lower()}_{mode}'
        shader = shader_type(
            _NoFiles(),
            _vmt(
                f'"{shader_type.SHADER}" {{ "$detail" "t/detail_{suffix}" '
                f'"$detail2" "t/detail2_{suffix}" '
                f'"$detailblendmode" "{mode}" }}'
            ),
        )
        with patch.object(Source1ShaderBase, 'load_texture_or_default', return_value=image):
            detail = shader.detail
            detail2 = shader.detail2

        self.assertIsNot(detail, image)
        self.assertIsNot(detail2, image)
        self.assertIsNot(detail, detail2)
        for variant in (detail, detail2):
            self.assertEqual(variant.colorspace_settings.is_data, is_data)
            self.assertEqual(variant.colorspace_settings.name, 'Non-Color' if is_data else 'sRGB')
        self.assertFalse(image.colorspace_settings.is_data)
        self.assertEqual(image.colorspace_settings.name, 'sRGB')

    def test_vertexlit_detail_mode_zero_is_data_and_other_modes_are_srgb(self):
        for mode in (0, 1, 2, 5, 7):
            with self.subTest(mode=mode):
                self._assert_colorspace(VertexLitGeneric, mode, mode == 0)

    def test_lightmapped_detail_only_mode_one_is_srgb(self):
        for mode in (0, 1, 2, 5, 7):
            with self.subTest(mode=mode):
                self._assert_colorspace(LightmapGeneric, mode, mode != 1)


class PrincipledVertexLitGenericTests(_CleanFile):
    def setUp(self):
        super().setUp()
        self._textures = {}

    def _fake_texture(self, name, flags=0):
        image = bpy.data.images.new(name, 4, 4, alpha=True)
        image["sourceio_vtf_flags"] = f"0x{flags:08x}"
        self._textures[name] = image

    def _material(self, text, use_bvlg=None, principled_culling=None):
        material = bpy.data.materials.new("test")
        parameters = {}
        if use_bvlg is not None:
            parameters[ExtraMaterialParameters.USE_BVLG] = use_bvlg
        if principled_culling is not None:
            parameters[ExtraMaterialParameters.PRINCIPLED_BACKFACE_CULLING] = principled_culling

        def load_texture(shader, texture_name, texture_path=None, **kwargs):
            return self._textures.get(str(texture_name))

        with patch.object(source1_shader_base.Source1ShaderBase, "load_texture", load_texture):
            ShaderRegistry.source1_create_nodes(_NoFiles(), material, _vmt(text), parameters)
        principled = next((node for node in material.node_tree.nodes
                           if node.bl_idname == 'ShaderNodeBsdfPrincipled'), None)
        return material, principled

    def test_principled_is_default_and_culling_is_opt_in(self):
        self.assertFalse(SourceIOPreferences.bl_rna.properties["principled_backface_culling"].default)
        default, shader = self._material('"VertexLitGeneric" {}')
        self.assertIsNotNone(shader)
        self.assertFalse(default.use_backface_culling)

        culled, _ = self._material('"VertexLitGeneric" {}', principled_culling=True)
        self.assertTrue(culled.use_backface_culling)

        two_sided, _ = self._material(
            '"VertexLitGeneric" { "$nocull" "1" }',
            principled_culling=True,
        )
        self.assertFalse(two_sided.use_backface_culling)

    def test_shader_modes_do_not_leak_between_materials(self):
        _, first = self._material('"VertexLitGeneric" {}', use_bvlg=False)
        bvlg, middle = self._material('"VertexLitGeneric" {}', use_bvlg=True)
        _, last = self._material('"VertexLitGeneric" {}', use_bvlg=False)
        self.assertIsNotNone(first)
        self.assertIsNone(middle)
        self.assertTrue(any(node.bl_idname == 'ShaderNodeGroup' for node in bvlg.node_tree.nodes))
        self.assertIsNotNone(last)

    def test_bvlg_keeps_its_existing_culling_behavior(self):
        culled, _ = self._material('"VertexLitGeneric" {}', use_bvlg=True)
        two_sided, _ = self._material('"VertexLitGeneric" { "$nocull" "1" }', use_bvlg=True)
        self.assertTrue(culled.use_backface_culling)
        self.assertFalse(two_sided.use_backface_culling)

    def test_basealphaenvmapmask_defaults_off(self):
        for body, expected in (("", False), ('"$basealphaenvmapmask" "0"', False),
                               ('"$basealphaenvmapmask" "1"', True)):
            with self.subTest(body=body):
                shader = VertexLitGeneric(_NoFiles(), _vmt(f'"VertexLitGeneric" {{ {body} }}'))
                self.assertEqual(shader.basealphaenvmapmask, expected)

    def test_envmap_controls_principled_specular_strength_and_tint(self):
        _, diffuse_only = self._material('"VertexLitGeneric" {}')
        self.assertEqual(diffuse_only.inputs['Specular IOR Level'].default_value, 0.0)

        _, shader = self._material(
            '"VertexLitGeneric" { "$envmap" "env_cubemap" "$envmaptint" "[0.2 0.4 0.8]" '
            '"$envmapcontrast" "0.5" "$envmapsaturation" "0.5" }'
        )
        luma = 0.2 * 0.299 + 0.4 * 0.587 + 0.8 * 0.114
        contrasted_luma = luma * 0.5 + luma * luma * 0.5
        self.assertAlmostEqual(
            shader.inputs['Specular IOR Level'].default_value,
            0.5 * contrasted_luma,
        )
        saturated_tint = [component * 0.5 + luma * 0.5 for component in (0.2, 0.4, 0.8)]
        peak = max(saturated_tint)
        self.assertSequenceEqual(
            tuple(round(component, 6) for component in shader.inputs['Specular Tint'].default_value),
            tuple(round(component / peak, 6) for component in saturated_tint) + (1.0,),
        )

    def test_envmapmask_scales_principled_specular(self):
        self._fake_texture("mask")
        material, shader = self._material(
            '"VertexLitGeneric" { "$envmap" "env_cubemap" "$envmapmask" "t/mask" }'
        )
        mask_scale = material.node_tree.nodes['envmapmask']
        self.assertEqual(shader.inputs['Specular IOR Level'].links[0].from_node, mask_scale)
        self.assertEqual(mask_scale.inputs['Factor'].links[0].from_node.name, '$envmapmask')
        self.assertEqual(mask_scale.inputs['Factor'].links[0].from_socket.name, 'Color')
        self.assertAlmostEqual(mask_scale.inputs['B'].default_value, 0.5)

    def test_basealphaenvmapmask_uses_inverted_base_alpha(self):
        self._fake_texture("base")
        material, shader = self._material(
            '"VertexLitGeneric" { "$basetexture" "t/base" "$envmap" "env_cubemap" '
            '"$basealphaenvmapmask" "1" }'
        )
        invert = material.node_tree.nodes['basealphaenvmapmask']
        mask_scale = material.node_tree.nodes['envmapmask']
        self.assertEqual(invert.inputs['Color'].links[0].from_node.name, '$basetexture')
        self.assertEqual(invert.inputs['Color'].links[0].from_socket.name, 'Alpha')
        self.assertEqual(mask_scale.inputs['Factor'].links[0].from_node, invert)
        self.assertEqual(shader.inputs['Specular IOR Level'].links[0].from_node, mask_scale)

    def test_normalmapalphaenvmapmask_overrides_other_masks(self):
        self._fake_texture("base")
        self._fake_texture("normal")
        self._fake_texture("mask")
        material, shader = self._material(
            '"VertexLitGeneric" { "$basetexture" "t/base" "$bumpmap" "t/normal" '
            '"$envmap" "env_cubemap" "$envmapmask" "t/mask" "$basealphaenvmapmask" "1" '
            '"$normalmapalphaenvmapmask" "1" }'
        )
        mask_scale = material.node_tree.nodes['envmapmask']
        self.assertNotIn('$envmapmask', material.node_tree.nodes)
        self.assertEqual(mask_scale.inputs['Factor'].links[0].from_node.name, '$bumpmap')
        self.assertEqual(mask_scale.inputs['Factor'].links[0].from_socket.name, 'Alpha')
        self.assertEqual(shader.inputs['Specular IOR Level'].links[0].from_node, mask_scale)

    def test_bumpmap_disables_unsupported_envmap_masks(self):
        self._fake_texture("base")
        self._fake_texture("normal")
        self._fake_texture("mask")
        for mask in ('"$basealphaenvmapmask" "1"', '"$envmapmask" "t/mask"'):
            with self.subTest(mask=mask):
                material, shader = self._material(
                    f'"VertexLitGeneric" {{ "$basetexture" "t/base" "$bumpmap" "t/normal" '
                    f'"$envmap" "env_cubemap" {mask} }}'
                )
                self.assertFalse(shader.inputs['Specular IOR Level'].is_linked)
                self.assertEqual(shader.inputs['Specular IOR Level'].default_value, 0.0)
                self.assertNotIn('envmapmask', material.node_tree.nodes)

    def test_phong_path_does_not_apply_envmap_parameters(self):
        self._fake_texture("base")
        self._fake_texture("mask")
        material, shader = self._material(
            '"VertexLitGeneric" { "$basetexture" "t/base" "$phong" "1" "$phongexponent" "20" '
            '"$envmap" "env_cubemap" "$envmapmask" "t/mask" }'
        )
        self.assertNotIn('$envmapmask', material.node_tree.nodes)
        self.assertNotIn('envmapmask', material.node_tree.nodes)
        self.assertGreater(shader.inputs['Specular IOR Level'].default_value, 0.0)

    def test_phong_mask_scales_specular_not_roughness(self):
        self._fake_texture("skin")
        self._fake_texture("skin_n")
        material, shader = self._material(
            '"VertexLitGeneric" { "$basetexture" "t/skin" "$bumpmap" "t/skin_n" '
            '"$phong" "1" "$phongexponent" "20" }'
        )
        self.assertTrue(shader.inputs['Specular IOR Level'].is_linked)
        self.assertFalse(shader.inputs['Roughness'].is_linked)
        phong_mask = material.node_tree.nodes['phongmask']
        self.assertEqual(shader.inputs['Specular IOR Level'].links[0].from_node, phong_mask)
        self.assertEqual(
            {link.from_node.name for socket in phong_mask.inputs for link in socket.links},
            {'$bumpmap', 'phongfresnel strength'},
        )

    def test_phong_exponent_texture_uses_exact_roughness_curve(self):
        self._fake_texture("exponent")
        material, shader = self._material(
            '"VertexLitGeneric" { "$phong" "1" "$phongexponenttexture" "t/exponent" '
            '"$normalmapalphaphongmask" "0" }'
        )
        roughness = shader.inputs['Roughness'].links[0].from_node
        reciprocal = roughness.inputs[0].links[0].from_node
        denominator = reciprocal.inputs[1].links[0].from_node
        split = denominator.inputs[0].links[0].from_node

        self.assertEqual(roughness.operation, 'SQRT')
        self.assertEqual(reciprocal.operation, 'DIVIDE')
        self.assertEqual(reciprocal.inputs[0].default_value, 2.0)
        self.assertEqual(denominator.operation, 'MULTIPLY_ADD')
        self.assertEqual(denominator.inputs[1].default_value, 149.0)
        self.assertEqual(denominator.inputs[2].default_value, 3.0)
        self.assertEqual(split.name, 'phongexponent split')
        self.assertEqual(denominator.inputs[0].links[0].from_socket.name, 'Red')

        red = 0.5
        node_curve = (reciprocal.inputs[0].default_value
                      / (red * denominator.inputs[1].default_value
                         + denominator.inputs[2].default_value)) ** 0.5
        self.assertAlmostEqual(node_curve, phong_exponent_to_roughness(1.0 + 149.0 * red))
        self.assertIn('phongexponent -> roughness', material.node_tree.nodes)

    def test_phong_exponent_factor_override_including_zero(self):
        self._fake_texture("exponent")
        for factor in (10.0, 0.0):
            with self.subTest(factor=factor):
                material, _ = self._material(
                    f'"VertexLitGeneric" {{ "$phong" "1" "$phongexponenttexture" "t/exponent" '
                    f'"$phongexponentfactor" "{factor}" }}'
                )
                denominator = material.node_tree.nodes['phongexponent denominator']
                self.assertEqual(denominator.inputs[1].default_value, factor)

    def test_phong_fresnel_ranges_use_exact_piecewise_curve(self):
        self._fake_texture("normal")
        material, shader = self._material(
            '"VertexLitGeneric" { "$phong" "1" "$phongexponent" "20" "$bumpmap" "t/normal" '
            '"$normalmapalphaphongmask" "0" "$phongfresnelranges" "[0.1 0.4 0.9]" }'
        )
        strength = shader.inputs['Specular IOR Level'].links[0].from_node
        remap = strength.inputs[0].links[0].from_node
        centered = remap.inputs[0].links[0].from_node
        slope = remap.inputs[1].links[0].from_node
        branch = slope.inputs['Factor'].links[0].from_node
        facing = centered.inputs[0].links[0].from_node

        self.assertEqual(strength.name, 'phongfresnel strength')
        self.assertEqual(strength.operation, 'MULTIPLY')
        self.assertAlmostEqual(strength.inputs[1].default_value, 0.5)
        self.assertEqual(remap.operation, 'MULTIPLY_ADD')
        self.assertAlmostEqual(remap.inputs[2].default_value, 0.4)
        self.assertEqual(centered.operation, 'MULTIPLY_ADD')
        self.assertAlmostEqual(centered.inputs[2].default_value, -0.5)
        self.assertEqual(centered.inputs[0].links[0].from_socket.name, 'Facing')
        self.assertEqual(centered.inputs[1].links[0].from_socket.name, 'Facing')
        self.assertEqual(branch.operation, 'GREATER_THAN')
        self.assertAlmostEqual(slope.inputs['A'].default_value, 0.6)
        self.assertAlmostEqual(slope.inputs['B'].default_value, 1.0)
        self.assertEqual(facing.bl_idname, 'ShaderNodeLayerWeight')
        self.assertEqual(
            facing.inputs['Normal'].links[0].from_socket,
            shader.inputs['Normal'].links[0].from_socket,
        )

        def evaluate(facing_value):
            centered_value = facing_value * facing_value + centered.inputs[2].default_value
            slope_value = (slope.inputs['B'].default_value if centered_value > 0.0
                           else slope.inputs['A'].default_value)
            return centered_value * slope_value + remap.inputs[2].default_value

        self.assertAlmostEqual(evaluate(0.0), 0.1)
        self.assertAlmostEqual(evaluate(0.5 ** 0.5), 0.4)
        self.assertAlmostEqual(evaluate(1.0), 0.9)

    def test_phong_fresnel_ranges_use_sdk_default(self):
        shader = VertexLitGeneric(_NoFiles(), _vmt('"VertexLitGeneric" { "$phong" "1" }'))
        self.assertSequenceEqual(tuple(shader.phongfresnelranges), (0.0, 0.5, 1.0))

    def test_blend_tint_by_base_alpha_masks_the_tint(self):
        self._fake_texture("skin")
        material, shader = self._material('"VertexLitGeneric" { "$basetexture" "t/skin" "$color2" "[1 0 0]" '
                                          '"$blendtintbybasealpha" "1" }')
        blend = material.node_tree.nodes['$blendtintbybasealpha']
        self.assertEqual(blend.inputs[0].links[0].from_socket.name, 'Alpha')
        self.assertEqual(shader.inputs['Base Color'].links[0].from_node, blend)

    def test_additive_adds_onto_the_background(self):
        self._fake_texture("glow")
        material, _ = self._material('"VertexLitGeneric" { "$basetexture" "t/glow" "$additive" "1" }')
        output = next(node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial')
        self.assertEqual(output.inputs['Surface'].links[0].from_node.bl_idname, 'ShaderNodeAddShader')
        self.assertEqual(material.surface_render_method, 'BLENDED')

    def test_alpha_below_one_is_translucent(self):
        self._fake_texture("glass")
        material, shader = self._material('"VertexLitGeneric" { "$basetexture" "t/glass" "$alpha" "0.25" }')
        self.assertEqual(material.surface_render_method, 'BLENDED')
        self.assertAlmostEqual(shader.inputs['Alpha'].links[0].from_socket.default_value, 0.25)

    def test_alpha_is_scaled_before_alpha_test(self):
        self._fake_texture("fence")
        material, shader = self._material('"VertexLitGeneric" { "$basetexture" "t/fence" "$alphatest" "1" '
                                          '"$alphatestreference" "0.5" "$alpha" "0.5" }')
        clip = next(node for node in material.node_tree.nodes if node.label == ALPHA_CLIP_LABEL)
        scaled_alpha = clip.inputs[0].links[0].from_node
        self.assertEqual(scaled_alpha.name, '$alpha')
        self.assertAlmostEqual(scaled_alpha.inputs[1].default_value, 0.5)
        visible_alpha = shader.inputs['Alpha'].links[0].from_node
        self.assertEqual(visible_alpha.name, '$alphatest alpha')
        self.assertEqual({link.from_node.name for input_socket in visible_alpha.inputs for link in input_socket.links},
                         {scaled_alpha.name, clip.name})
        self.assertTrue(any(node.label == "Full resolution alpha" for node in material.node_tree.nodes))

    def test_vtf_sampler_flags(self):
        self._fake_texture("pixels", flags=0x1)  # POINTSAMPLE
        self._fake_texture("sprite", flags=0xC)  # CLAMPS | CLAMPT
        material, _ = self._material('"UnlitGeneric" { "$basetexture" "t/pixels" }')
        node = next(node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeTexImage')
        self.assertEqual(node.interpolation, 'Closest')
        material, _ = self._material('"UnlitGeneric" { "$basetexture" "t/sprite" }')
        node = next(node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeTexImage')
        self.assertEqual(node.extension, 'EXTEND')


class PauseViewLayerUpdateTests(unittest.TestCase):
    def test_restores_private_hook_after_normal_and_exceptional_exit(self):
        class Owner:
            @staticmethod
            def _view_layer_update(context):
                pass

        owner = Owner
        had_own_hook = "_view_layer_update" in vars(owner)
        original = vars(owner).get("_view_layer_update")
        with patch.object(bpy_utils, "_view_layer_update_owner", return_value=owner):
            with pause_view_layer_update():
                self.assertIn("_view_layer_update", vars(owner))
                self.assertIsNot(vars(owner)["_view_layer_update"], original)
            self.assertEqual("_view_layer_update" in vars(owner), had_own_hook)
            if had_own_hook:
                self.assertIs(vars(owner)["_view_layer_update"], original)

            with self.assertRaisesRegex(RuntimeError, "test"):
                with pause_view_layer_update():
                    raise RuntimeError("test")
        self.assertEqual("_view_layer_update" in vars(owner), had_own_hook)
        if had_own_hook:
            self.assertIs(vars(owner)["_view_layer_update"], original)

    def test_missing_private_hook_is_a_noop(self):
        owner = SimpleNamespace()
        with patch.object(bpy_utils, "_view_layer_update_owner", return_value=owner):
            with pause_view_layer_update():
                self.assertFalse(hasattr(owner, "_view_layer_update"))


if __name__ == '__main__':
    unittest.main()
