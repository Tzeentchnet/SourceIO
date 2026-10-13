import json
import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import (
    ALPHA_CLIP_LABEL,
    MIX_A,
    MIX_B,
    MIX_FACTOR,
)
from SourceIO.tests.blender_tests.test_source2_materials import (
    build,
    linked_node,
    output_node,
    shader_node,
    source,
    texture_nodes,
)


class Source2DotaMaterialTests(unittest.TestCase):
    def tearDown(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)

    def test_hero_packed_masks_drive_supported_principled_inputs(self):
        material = build(
            "hero.vfx",
            ("g_tColor", "g_tNormal", "g_tMasks1", "g_tMasks2"),
            ints={"F_MASKS_1": 1, "F_MASKS_2": 1, "F_DIFFUSE_WARP": 1},
            floats={
                "g_flSpecularScale": 2.0,
                "g_flRimLightScale": 3.0,
                "g_flSpecularExponent": 16.0,
            },
            vectors={
                "g_vRimLightColor": (0.2, 0.4, 0.6, 0.0),
                "g_vSpecularColor": (0.3, 0.5, 0.7, 0.0),
            },
        )
        shader = shader_node(material, "hero.vfx")

        self.assertEqual(source(shader.inputs["Base Color"]), ("g_tColor", "Color"))
        normal_map = linked_node(shader.inputs["Normal"])
        self.assertEqual(normal_map.bl_idname, "ShaderNodeNormalMap")
        self.assertEqual(source(normal_map.inputs["Color"]), ("g_tNormal", "Color"))
        self.assertEqual(source(shader.inputs["Diffuse Roughness"]), ("g_tMasks1", "Green"))
        self.assertEqual(source(shader.inputs["Metallic"]), ("g_tMasks1", "Blue"))

        emission = linked_node(shader.inputs["Emission Color"])
        self.assertEqual(emission.operation, "SCALE")
        self.assertEqual(source(emission.inputs[0]), ("g_tColor", "Color"))
        self.assertEqual(source(emission.inputs["Scale"]), ("g_tMasks1", "Alpha"))

        specular = linked_node(shader.inputs["Specular IOR Level"])
        self.assertEqual(specular.operation, "MULTIPLY")
        self.assertEqual(source(specular.inputs[0]), ("g_tMasks2", "Red"))
        self.assertAlmostEqual(specular.inputs[1].default_value, 2.0, places=6)

        sheen = linked_node(shader.inputs["Sheen Weight"])
        self.assertEqual(sheen.operation, "MULTIPLY")
        self.assertEqual(source(sheen.inputs[0]), ("g_tMasks2", "Green"))
        self.assertAlmostEqual(sheen.inputs[1].default_value, 3.0, places=6)
        np.testing.assert_allclose(shader.inputs["Sheen Tint"].default_value, (0.2, 0.4, 0.6, 1.0))
        self.assertFalse(shader.inputs["Coat Weight"].is_linked)
        np.testing.assert_allclose(shader.inputs["Coat Tint"].default_value, (1.0, 1.0, 1.0, 1.0))

        specular_tint = linked_node(shader.inputs["Specular Tint"])
        self.assertEqual(specular_tint.blend_type, "MIX")
        self.assertEqual(source(specular_tint.inputs[MIX_FACTOR]), ("g_tMasks2", "Blue"))
        np.testing.assert_allclose(specular_tint.inputs[MIX_A].default_value, (0.3, 0.5, 0.7, 1.0))
        tinted_base = linked_node(specular_tint.inputs[MIX_B])
        self.assertEqual(tinted_base.blend_type, "MULTIPLY")
        self.assertEqual(source(tinted_base.inputs[MIX_A]), ("g_tColor", "Color"))

        roughness = linked_node(shader.inputs["Roughness"])
        self.assertEqual(roughness.operation, "POWER")
        self.assertAlmostEqual(roughness.inputs[1].default_value, 0.25, places=6)
        divide = linked_node(roughness.inputs[0])
        exponent_plus_two = linked_node(divide.inputs[1])
        exponent = linked_node(exponent_plus_two.inputs[0])
        self.assertEqual(source(exponent.inputs[0]), ("g_tMasks2", "Alpha"))
        self.assertAlmostEqual(exponent.inputs[1].default_value, 16.0, places=6)

        self.assertTrue(material.node_tree.nodes["g_tNormal"].image.colorspace_settings.is_data)
        self.assertTrue(material.node_tree.nodes["g_tMasks1"].image.colorspace_settings.is_data)
        self.assertTrue(material.node_tree.nodes["g_tMasks2"].image.colorspace_settings.is_data)
        self.assertEqual(
            texture_nodes(material),
            {"g_tColor", "g_tNormal", "g_tMasks1", "g_tMasks2"},
        )

    def test_hero_color_tint_multiplies_base_color(self):
        material = build("hero.vfx", ("g_tColor",), vectors={"g_vColorTint": (0.5, 0.25, 1.0, 0.0)})
        shader = shader_node(material, "hero.vfx")

        tinted = linked_node(shader.inputs["Base Color"])
        self.assertEqual(tinted.blend_type, "MULTIPLY")
        self.assertEqual(source(tinted.inputs[MIX_A]), ("g_tColor", "Color"))
        np.testing.assert_allclose(tinted.inputs[MIX_B].default_value, (0.5, 0.25, 1.0, 1.0))

    def test_hero_detail_modes_use_red_mask_and_detail_uv(self):
        for mode, blend_type in ((1, "ADD"), (2, "ADD"), (3, "MULTIPLY")):
            with self.subTest(mode=mode):
                material = build(
                    "hero.vfx",
                    ("g_tColor", "g_tDetail", "g_tMasks1"),
                    ints={"F_DETAIL": mode, "F_MASKS_1": 1},
                    floats={"g_flDetailBlendFactor": 0.4},
                    vectors={
                        "g_vDetailTexCoordScale": (3.0, 2.0, 0.0, 0.0),
                        "g_vDetailTexCoordOffset": (0.25, 0.5, 0.0, 0.0),
                    },
                )
                shader = shader_node(material, "hero.vfx")
                detail_blend = linked_node(shader.inputs["Base Color"])
                self.assertEqual(detail_blend.blend_type, blend_type)
                self.assertEqual(source(detail_blend.inputs[MIX_A]), ("g_tColor", "Color"))
                factor = linked_node(detail_blend.inputs[MIX_FACTOR])
                self.assertEqual(factor.operation, "MULTIPLY")
                self.assertEqual(source(factor.inputs[0]), ("g_tMasks1", "Red"))
                self.assertAlmostEqual(factor.inputs[1].default_value, 0.4, places=6)

                transform = linked_node(material.node_tree.nodes["g_tDetail"].inputs["Vector"])
                self.assertEqual(tuple(transform.inputs["g_vTexCoordScale"].default_value), (3.0, 2.0, 0.0))
                self.assertEqual(tuple(transform.inputs["g_vTexCoordOffset"].default_value), (0.25, 0.5, 0.0))

                if mode == 3:
                    doubled = linked_node(detail_blend.inputs[MIX_B])
                    self.assertEqual(doubled.blend_type, "MULTIPLY")
                    self.assertEqual(source(doubled.inputs[MIX_A]), ("g_tDetail", "Color"))
                    self.assertEqual(tuple(doubled.inputs[MIX_B].default_value), (2.0, 2.0, 2.0, 1.0))
                elif mode == 2:
                    emission = linked_node(shader.inputs["Emission Color"])
                    self.assertEqual(emission.operation, "ADD")
                    detail_emission = linked_node(emission.inputs[0])
                    self.assertEqual(detail_emission.operation, "SCALE")
                    self.assertEqual(source(detail_emission.inputs[0]), ("g_tDetail", "Color"))

        unsupported = build(
            "hero.vfx",
            ("g_tColor", "g_tDetail"),
            ints={"F_DETAIL": 4},
        )
        self.assertEqual(source(shader_node(unsupported, "hero.vfx").inputs["Base Color"]),
                         ("g_tColor", "Color"))
        self.assertNotIn("g_tDetail", texture_nodes(unsupported))

    def test_hero_detail2_add_self_illum_uses_its_own_transform(self):
        material = build(
            "hero.vfx",
            ("g_tColor", "g_tDetail2"),
            ints={"F_DETAIL_2": 2},
            floats={"g_flDetail2BlendFactor": 0.25},
            vectors={
                "g_vDetail2TexCoordScale": (4.0, 5.0, 0.0, 0.0),
                "g_vDetail2TexCoordOffset": (0.1, 0.2, 0.0, 0.0),
            },
        )
        shader = shader_node(material, "hero.vfx")
        blend = linked_node(shader.inputs["Base Color"])
        self.assertEqual(blend.blend_type, "ADD")
        self.assertAlmostEqual(blend.inputs[MIX_FACTOR].default_value, 0.25, places=6)
        self.assertEqual(source(blend.inputs[MIX_B]), ("g_tDetail2", "Color"))

        emission = linked_node(shader.inputs["Emission Color"])
        self.assertEqual(emission.operation, "SCALE")
        self.assertEqual(source(emission.inputs[0]), ("g_tDetail2", "Color"))
        self.assertAlmostEqual(emission.inputs["Scale"].default_value, 0.25, places=6)

        transform = linked_node(material.node_tree.nodes["g_tDetail2"].inputs["Vector"])
        self.assertEqual(tuple(transform.inputs["g_vTexCoordScale"].default_value), (4.0, 5.0, 0.0))
        np.testing.assert_allclose(transform.inputs["g_vTexCoordOffset"].default_value, (0.1, 0.2, 0.0))

    def test_hero_separate_uvs_and_render_modes(self):
        alpha_test = build(
            "hero.vfx",
            ("g_tColor", "g_tNormal"),
            ints={
                "F_ALPHA_TEST": 1,
                "F_SEPARATE_ALPHA_TRANSFORM": 1,
                "F_SEPARATE_NORMAL_TRANSFORM": 1,
            },
            floats={
                "g_flAlphaTestReference": 0.35,
                "g_flAlphaTexCoordRotation": 90.0,
                "g_flNormalTexCoordRotation": 180.0,
            },
            vectors={
                "g_vAlphaTexCoordScale": (2.0, 3.0, 0.0, 0.0),
                "g_vNormalTexCoordScale": (4.0, 5.0, 0.0, 0.0),
            },
        )
        nodes = alpha_test.node_tree.nodes
        alpha_transform = linked_node(nodes["g_tColor Alpha"].inputs["Vector"])
        normal_transform = linked_node(nodes["g_tNormal"].inputs["Vector"])
        self.assertEqual(tuple(alpha_transform.inputs["g_vTexCoordScale"].default_value), (2.0, 3.0, 0.0))
        self.assertAlmostEqual(alpha_transform.inputs["g_flTexCoordRotation"].default_value, 90.0)
        self.assertEqual(tuple(normal_transform.inputs["g_vTexCoordScale"].default_value), (4.0, 5.0, 0.0))
        self.assertAlmostEqual(normal_transform.inputs["g_flTexCoordRotation"].default_value, 180.0)
        clip = next(node for node in nodes if node.label == ALPHA_CLIP_LABEL)
        self.assertAlmostEqual(clip.inputs[1].default_value, 0.35, places=6)
        self.assertEqual(alpha_test.surface_render_method, "DITHERED")
        self.assertTrue(alpha_test.use_backface_culling)

        translucent = build("hero.vfx", ("g_tColor",), ints={"F_TRANSLUCENT": 1, "F_RENDER_BACKFACES": 1})
        self.assertEqual(linked_node(output_node(translucent).inputs["Surface"]).bl_idname, "ShaderNodeMixShader")
        self.assertEqual(translucent.surface_render_method, "BLENDED")
        self.assertFalse(translucent.use_backface_culling)

        additive = build("hero.vfx", ("g_tColor",), ints={"F_ADDITIVE_BLEND": 1})
        self.assertEqual(linked_node(output_node(additive).inputs["Surface"]).bl_idname, "ShaderNodeAddShader")
        self.assertEqual(additive.surface_render_method, "BLENDED")

    def test_global_lit_simple_packed_specular_tints_and_uvs(self):
        material = build(
            "global_lit_simple.vfx",
            ("g_tColor", "g_tNormal", "g_tSpecular", "g_tTintMask"),
            ints={
                "F_NORMAL_MAP": 1,
                "F_SPECULAR": 1,
                "F_TINT_MASK": 1,
                "F_TINT_MASK_2": 1,
            },
            floats={"g_flBumpStrength": 0.4, "g_flSpecularIntensity": 2.0},
            vectors={
                "g_vColorTint": (0.1, 0.2, 0.3, 0.0),
                "g_vColorTint2": (0.4, 0.5, 0.6, 0.0),
                "g_vColorTint3": (0.7, 0.8, 0.9, 0.0),
                "g_vColorTint4": (1.0, 0.9, 0.8, 0.0),
                "g_vTexCoordScale": (3.0, 2.0, 0.0, 0.0),
                "g_vTexCoordOffset": (0.25, 0.5, 0.0, 0.0),
            },
        )
        shader = shader_node(material, "global_lit_simple.vfx")

        tinted = linked_node(shader.inputs["Base Color"])
        self.assertEqual(tinted.blend_type, "MULTIPLY")
        self.assertEqual(source(tinted.inputs[MIX_A]), ("g_tColor", "Color"))
        mask2 = linked_node(tinted.inputs[MIX_B])
        self.assertEqual(mask2.name, "Tint Mask 2")
        self.assertEqual(source(mask2.inputs[MIX_FACTOR]), ("g_tTintMask", "Blue"))
        lower = linked_node(mask2.inputs[MIX_A])
        upper = linked_node(mask2.inputs[MIX_B])
        self.assertEqual(source(lower.inputs[MIX_FACTOR]), ("g_tTintMask", "Green"))
        self.assertEqual(source(upper.inputs[MIX_FACTOR]), ("g_tTintMask", "Green"))
        np.testing.assert_allclose(lower.inputs[MIX_A].default_value, (0.1, 0.2, 0.3, 1.0))
        np.testing.assert_allclose(lower.inputs[MIX_B].default_value, (0.4, 0.5, 0.6, 1.0))
        np.testing.assert_allclose(upper.inputs[MIX_A].default_value, (0.7, 0.8, 0.9, 1.0))
        np.testing.assert_allclose(upper.inputs[MIX_B].default_value, (1.0, 0.9, 0.8, 1.0))

        transform = linked_node(material.node_tree.nodes["g_tColor"].inputs["Vector"])
        self.assertEqual(transform, linked_node(material.node_tree.nodes["g_tNormal"].inputs["Vector"]))
        self.assertEqual(transform, linked_node(material.node_tree.nodes["g_tSpecular"].inputs["Vector"]))
        self.assertEqual(transform, linked_node(material.node_tree.nodes["g_tTintMask"].inputs["Vector"]))
        self.assertEqual(tuple(transform.inputs["g_vTexCoordScale"].default_value), (3.0, 2.0, 0.0))
        self.assertEqual(tuple(transform.inputs["g_vTexCoordOffset"].default_value), (0.25, 0.5, 0.0))

        normal_map = linked_node(shader.inputs["Normal"])
        self.assertAlmostEqual(normal_map.inputs["Strength"].default_value, 0.4, places=6)
        self.assertEqual(source(normal_map.inputs["Color"]), ("g_tNormal", "Color"))

        reflectance = linked_node(shader.inputs["Specular IOR Level"])
        self.assertEqual(reflectance.operation, "MULTIPLY")
        self.assertEqual(source(reflectance.inputs[0]), ("g_tSpecular", "Red"))
        self.assertAlmostEqual(reflectance.inputs[1].default_value, 25.0, places=6)
        self_illum = linked_node(shader.inputs["Emission Color"])
        self.assertEqual(self_illum.operation, "SCALE")
        self.assertEqual(source(self_illum.inputs["Scale"]), ("g_tSpecular", "Green"))

        self.assertTrue(material.node_tree.nodes["g_tNormal"].image.colorspace_settings.is_data)
        self.assertTrue(material.node_tree.nodes["g_tSpecular"].image.colorspace_settings.is_data)
        self.assertTrue(material.node_tree.nodes["g_tTintMask"].image.colorspace_settings.is_data)

    def test_global_lit_simple_gates_unsupported_and_optional_textures(self):
        material = build(
            "global_lit_simple.vfx",
            (
                "g_tColor",
                "g_tNormal",
                "g_tSpecular",
                "g_tTintMask",
                "g_tAOMap",
                "g_tParallaxMask",
                "g_tScrollSpeed",
                "g_tWindMask",
                "g_tWindNoise",
            ),
        )
        shader = shader_node(material, "global_lit_simple.vfx")
        self.assertEqual(texture_nodes(material), {"g_tColor"})
        self.assertFalse(shader.inputs["Normal"].is_linked)
        self.assertFalse(shader.inputs["Emission Color"].is_linked)
        self.assertAlmostEqual(shader.inputs["Specular IOR Level"].default_value, 0.0, places=6)

        constant_specular = build(
            "global_lit_simple.vfx",
            ("g_tColor",),
            ints={"F_SPECULAR": 1},
            floats={"g_flSpecularIntensity": 0.8},
        )
        shader = shader_node(constant_specular, "global_lit_simple.vfx")
        self.assertAlmostEqual(shader.inputs["Specular IOR Level"].default_value, 1.0, places=6)

    def test_global_lit_simple_render_modes(self):
        opaque = build("global_lit_simple.vfx", ("g_tColor",))
        self.assertEqual(linked_node(output_node(opaque).inputs["Surface"]),
                         shader_node(opaque, "global_lit_simple.vfx"))
        self.assertTrue(opaque.use_backface_culling)

        alpha_test = build(
            "global_lit_simple.vfx",
            ("g_tColor",),
            ints={"F_ALPHA_TEST": 1},
            floats={"g_flAlphaTestReference": 0.33},
        )
        clip = next(node for node in alpha_test.node_tree.nodes if node.label == ALPHA_CLIP_LABEL)
        self.assertAlmostEqual(clip.inputs[1].default_value, 0.33, places=6)
        self.assertEqual(alpha_test.surface_render_method, "DITHERED")

        for flag in ("F_TRANSLUCENT", "F_OVERLAY"):
            with self.subTest(flag=flag):
                material = build(
                    "global_lit_simple.vfx",
                    ("g_tColor",),
                    ints={flag: 1, "F_RENDER_BACKFACES": 1},
                    floats={"g_flOpacityScale": 0.4},
                )
                surface = linked_node(output_node(material).inputs["Surface"])
                self.assertEqual(surface.bl_idname, "ShaderNodeMixShader")
                opacity = linked_node(surface.inputs[0])
                self.assertEqual(opacity.operation, "MULTIPLY")
                self.assertEqual(source(opacity.inputs[0]), ("g_tColor", "Alpha"))
                self.assertAlmostEqual(opacity.inputs[1].default_value, 0.4, places=6)
                self.assertEqual(material.surface_render_method, "BLENDED")
                self.assertFalse(material.use_backface_culling)

        additive = build(
            "global_lit_simple.vfx",
            ("g_tColor",),
            ints={"F_ADDITIVE_BLEND": 1},
            floats={"g_flOpacityScale": 0.5},
        )
        self.assertEqual(linked_node(output_node(additive).inputs["Surface"]).bl_idname, "ShaderNodeAddShader")
        self.assertEqual(additive.surface_render_method, "BLENDED")

        mod2x = build(
            "global_lit_simple.vfx",
            ("g_tColor",),
            ints={"F_MOD_2X": 1},
        )
        self.assertEqual(linked_node(output_node(mod2x).inputs["Surface"]).bl_idname,
                         "ShaderNodeBsdfTransparent")
        self.assertEqual(mod2x.surface_render_method, "BLENDED")

    def test_source2_shader_metadata_distinguishes_supported_and_dummy_handlers(self):
        for shader in ("hero.vfx", "global_lit_simple.vfx"):
            with self.subTest(shader=shader):
                material = build(shader, ("g_tColor",))
                metadata = {
                    "source2_shader": material["source2_shader"],
                    "source2_shader_supported": material["source2_shader_supported"],
                }
                self.assertEqual(
                    json.loads(json.dumps(metadata)),
                    {"source2_shader": shader, "source2_shader_supported": True},
                )

        unsupported_shader = "dota_unhandled_test.vfx"
        material = build(unsupported_shader, ("g_tColor",))
        metadata = {
            "source2_shader": material["source2_shader"],
            "source2_shader_supported": material["source2_shader_supported"],
        }
        self.assertEqual(
            json.loads(json.dumps(metadata)),
            {"source2_shader": unsupported_shader, "source2_shader_supported": False},
        )


if __name__ == "__main__":
    unittest.main()
