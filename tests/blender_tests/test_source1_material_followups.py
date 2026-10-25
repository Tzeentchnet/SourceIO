"""Material/texture lookup indexes, decode-time normal map conversion and the Principled VertexLitGeneric path."""
import unittest
from unittest.mock import patch

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.material_loader import ShaderRegistry
from SourceIO.blender_bindings.material_loader.shaders import source1_shader_base
from SourceIO.blender_bindings.material_loader.shaders.source1_shader_base import Source1ShaderBase
from SourceIO.blender_bindings.source1 import vtf as blender_vtf
from SourceIO.blender_bindings.utils import texture_utils
from SourceIO.blender_bindings.utils.bpy_utils import get_or_create_material
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

    def test_flat_ssbump_becomes_a_flat_normal(self):
        flat = np.full((1, 1, 4), 255, np.uint8)
        flat[..., :3] = round(255 / 3 ** 0.5)
        normal = blender_vtf.ssbump_to_normal(flat)[0, 0]
        np.testing.assert_allclose(normal, [0.5, 0.5, 1.0, 1.0], atol=0.01)


class PrincipledVertexLitGenericTests(_CleanFile):
    def setUp(self):
        super().setUp()
        self._bvlg = Source1ShaderBase.use_bvlg_status
        Source1ShaderBase.use_bvlg(False)
        self._textures = {}

    def tearDown(self):
        Source1ShaderBase.use_bvlg(self._bvlg)

    def _fake_texture(self, name, flags=0):
        image = bpy.data.images.new(name, 4, 4, alpha=True)
        image["sourceio_vtf_flags"] = f"0x{flags:08x}"
        self._textures[name] = image

    def _material(self, text):
        material = bpy.data.materials.new("test")

        def load_texture(shader, texture_name, texture_path=None, **kwargs):
            return self._textures.get(str(texture_name))

        with patch.object(source1_shader_base.Source1ShaderBase, "load_texture", load_texture):
            ShaderRegistry.source1_create_nodes(_NoFiles(), material, _vmt(text), {})
        principled = next((node for node in material.node_tree.nodes
                           if node.bl_idname == 'ShaderNodeBsdfPrincipled'), None)
        return material, principled

    def test_phong_mask_scales_specular_not_roughness(self):
        self._fake_texture("skin")
        self._fake_texture("skin_n")
        _, shader = self._material('"VertexLitGeneric" { "$basetexture" "t/skin" "$bumpmap" "t/skin_n" '
                                   '"$phong" "1" "$phongexponent" "20" }')
        self.assertTrue(shader.inputs['Specular IOR Level'].is_linked)
        self.assertFalse(shader.inputs['Roughness'].is_linked)

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

    def test_vtf_sampler_flags(self):
        self._fake_texture("pixels", flags=0x1)  # POINTSAMPLE
        self._fake_texture("sprite", flags=0xC)  # CLAMPS | CLAMPT
        material, _ = self._material('"UnlitGeneric" { "$basetexture" "t/pixels" }')
        node = next(node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeTexImage')
        self.assertEqual(node.interpolation, 'Closest')
        material, _ = self._material('"UnlitGeneric" { "$basetexture" "t/sprite" }')
        node = next(node for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeTexImage')
        self.assertEqual(node.extension, 'EXTEND')


if __name__ == '__main__':
    unittest.main()
