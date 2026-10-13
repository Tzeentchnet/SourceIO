import json
import math
import unittest
from unittest import mock

import bpy
import numpy as np

from SourceIO.blender_bindings.material_loader.shader_base import (
    ALPHA_CLIP_LABEL,
    MIX_A,
    MIX_B,
    MIX_FACTOR,
)
from SourceIO.blender_bindings.material_loader.shaders.source2_shader_base import Source2ShaderBase
from SourceIO.library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS
from SourceIO.tests.blender_tests.test_source2_materials import (
    FakeMaterial,
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
        self.assertTrue(specular.use_clamp)  # a Blinn-Phong scale must not push reflectance past Blender's range

        sheen = linked_node(shader.inputs["Sheen Weight"])
        self.assertEqual(sheen.operation, "MULTIPLY")
        self.assertEqual(source(sheen.inputs[0]), ("g_tMasks2", "Green"))
        self.assertAlmostEqual(sheen.inputs[1].default_value, 3.0, places=6)
        self.assertTrue(sheen.use_clamp)
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

    @staticmethod
    def bake_base_color(material, uv_layers: dict[str, tuple[float, float]], input_name: str = "Base Color"):
        """Bake what feeds the material's Base Color (or another shader input) on a quad whose UV maps are
        constant (vertex paint streams), returning the RGB."""
        mesh = bpy.data.meshes.new("paint_quad")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        mesh.uv_layers.new(name="TEXCOORD").data.foreach_set("uv", (0, 0, 1, 0, 1, 1, 0, 1))
        for name, value in uv_layers.items():
            mesh.uv_layers.new(name=name).data.foreach_set("uv", value * 4)
        mesh.materials.append(material)
        obj = bpy.data.objects.new("paint_quad", mesh)
        bpy.context.scene.collection.objects.link(obj)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        base_color = shader_node(material, material["source2_shader"]).inputs[input_name]
        emission = nodes.new("ShaderNodeEmission")
        links.new(base_color.links[0].from_socket, emission.inputs["Color"])
        links.new(emission.outputs[0], output_node(material).inputs["Surface"])
        image = bpy.data.images.new("bake", 2, 2, float_buffer=True)
        image.colorspace_settings.name = "Non-Color"
        target = nodes.new("ShaderNodeTexImage")
        target.image = image
        nodes.active = target
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
        scene.cycles.device = "CPU"
        for other in bpy.context.scene.objects:  # the bake covers every selected object
            other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.bake(type="EMIT", margin=0)
        pixels = np.array(image.pixels[:], dtype=np.float32).reshape(-1, 4)
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
        return pixels[:, :3].mean(0)

    @staticmethod
    def constant_image(node, rgba):
        image = bpy.data.images.new(f"{node.name} constant", 1, 1, alpha=True, float_buffer=True)
        image.colorspace_settings.name = "Non-Color"
        image.pixels = rgba
        original = node.image
        for texture in node.id_data.nodes:
            if texture.bl_idname == "ShaderNodeTexImage" and texture.image == original:
                texture.image = image

    def test_multiblend_weights_tints_and_vertex_paint_match_vrf(self):
        srgb = lambda c: np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)  # noqa: E731
        colors = np.array(((0.8, 0.2, 0.1), (0.1, 0.7, 0.2), (0.2, 0.3, 0.9), (0.6, 0.6, 0.1)))
        reveals = (1.0, 0.5, 0.4, 0.6)
        amounts, softness = np.array((0.5, 0.38, 0.3)), np.array((0.2, 0.1, 0.4))
        tint_masks = (0.25, 0.5, 1.0, 0.0)
        tints = np.array(((0.9, 0.8, 0.7), (0.5, 1.0, 0.5), (1.0, 1.0, 1.0), (0.4, 0.6, 0.8)))
        tints_b = np.array(((0.2, 0.4, 0.6), (1.0, 0.5, 0.25), (0.3, 0.3, 0.3), (1.0, 1.0, 1.0)))
        paint = np.array((0.9, 0.7, 0.5))
        layers = range(4)
        material = build(
            "multiblend.vfx",
            tuple(f"g_tColor{n}" for n in layers) + ("g_tTintMasks", "g_tParallaxMask"),
            ints={"F_TINT_MASK": 1},
            vectors={**{f"g_vColorTint{n}": (*tints[n], 0.0) for n in layers},
                     **{f"g_vColorTintB{n}": (*tints_b[n], 0.0) for n in layers}},
        )
        for n in layers:
            self.constant_image(material.node_tree.nodes[f"g_tColor{n}"], (*colors[n], reveals[n]))
        self.constant_image(material.node_tree.nodes["g_tTintMasks"], tint_masks)

        baked = self.bake_base_color(material, {
            "TEXCOORD_1": tuple(amounts[:2]), "TEXCOORD_1_2": (amounts[2], 0.0),
            "TEXCOORD_2": tuple(softness[:2]), "TEXCOORD_2_2": (softness[2], 0.0),
            "TEXCOORD_3": tuple(paint[:2]), "TEXCOORD_3_2": (paint[2], 1.0),
        })

        def smoothstep(low, high, x):
            t = np.clip((x - low) / (high - low), 0.0, 1.0)
            return t * t * (3.0 - 2.0 * t)

        weights, remaining = [], 1.0
        for n in (1, 2, 3):
            weight = smoothstep(max(reveals[n] - softness[n - 1], 0.0), min(reveals[n] + softness[n - 1], 1.0),
                                amounts[n - 1])
            weight = min(weight, remaining)
            weights.append(weight)
            remaining -= weight
        weights.insert(0, remaining)
        self.assertTrue(all(weight > 0.01 for weight in weights), weights)
        layer_tints = [srgb(tints_b[n]) + (srgb(tints[n]) - srgb(tints_b[n])) * tint_masks[n] for n in layers]
        expected = sum(weights[n] * colors[n] * layer_tints[n] for n in layers) * srgb(paint)
        np.testing.assert_allclose(baked, expected, atol=2e-3)

    def test_multiblend_gates_optional_inputs(self):
        material = build("multiblend.vfx", tuple(f"g_tColor{n}" for n in range(4)) + ("g_tNormal0", "g_tSpecular0"))
        shader = shader_node(material, "multiblend.vfx")
        self.assertFalse(shader.inputs["Normal"].is_linked)
        self.assertFalse(shader.inputs["Emission Color"].is_linked)
        self.assertEqual(shader.inputs["Specular IOR Level"].default_value, 0.0)
        self.assertFalse({"g_tNormal0", "g_tSpecular0", "g_tTintMasks"} & texture_nodes(material))

        material = build("multiblend.vfx", tuple(f"g_t{kind}{n}" for kind in ("Color", "Normal", "Specular")
                                                 for n in range(4)),
                         ints={"F_NORMAL_MAP": 1, "F_SPECULAR": 1}, floats={"g_flBumpStrength": 0.5})
        shader = shader_node(material, "multiblend.vfx")
        normal_map = linked_node(shader.inputs["Normal"])
        self.assertEqual(normal_map.bl_idname, "ShaderNodeNormalMap")
        self.assertAlmostEqual(normal_map.inputs["Strength"].default_value, 0.5, places=6)
        self.assertTrue(shader.inputs["Specular IOR Level"].is_linked)
        self.assertTrue(shader.inputs["Emission Color"].is_linked)
        self.assertTrue({f"g_tNormal{n}" for n in range(4)} <= texture_nodes(material))

    def test_crystal_albedo_tints_match_bytecode(self):
        srgb = lambda c: np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)  # noqa: E731
        color, mask = np.array((0.8, 0.6, 0.4)), 0.25
        tint, tint2 = np.array((0.9, 0.7, 0.5)), np.array((0.3, 0.6, 0.9))
        vectors = {"g_vColorTint": (*tint, 1.0), "g_vColorTint2": (*tint2, 0.0)}

        material = build("crystal.vfx", ("g_tColor", "g_tTintMask"), ints={"F_TINT_MASK": 1}, vectors=vectors)
        self.constant_image(material.node_tree.nodes["g_tColor"], (*color, 1.0))
        self.constant_image(material.node_tree.nodes["g_tTintMask"], (0.9, mask, 0.9, 1.0))
        expected = color * srgb(tint) * (srgb(tint2) + (srgb(tint) - srgb(tint2)) * mask)  # model tint 1
        np.testing.assert_allclose(self.bake_base_color(material, {}), expected, atol=2e-3)

        # Without the mask the tint applies twice, as the compiled shader does.
        material = build("crystal.vfx", ("g_tColor", "g_tTintMask"), vectors=vectors)
        self.assertNotIn("g_tTintMask", texture_nodes(material))
        self.constant_image(material.node_tree.nodes["g_tColor"], (*color, 1.0))
        np.testing.assert_allclose(self.bake_base_color(material, {}), color * srgb(tint) ** 2, atol=2e-3)

    def test_crystal_draws_opaque_back_faces_and_cube_map_reflections(self):
        cube_images = {}

        def fake_cube(shader, texture_path, width=0):
            return cube_images.setdefault(texture_path, bpy.data.images.new(texture_path, 4, 2))

        textures = ("g_tColor", "g_tNormal", "g_tCubeMapExterior", "g_tCubeMapInterior")
        flags = {"F_NORMAL_MAP": 1, "F_SPECULAR_CUBE_MAP": 1, "F_SEPARATE_INTERIOR_CUBE_MAP": 1}
        floats = {"g_flOpacityScale": 0.66, "g_flCubeMapScalarExterior": 11.875, "g_flCubeMapScalarInterior": 10.0,
                  "g_flFresnelExponent": 2.1, "g_flFresnelReflectance": 0.2, "g_flBumpStrength": 2.0}
        with mock.patch.object(Source2ShaderBase, "load_cubemap_equirect", fake_cube):
            material = build("crystal.vfx", textures, ints=flags, floats=floats)
        shader = shader_node(material, "crystal.vfx")
        self.assertEqual(material.surface_render_method, "BLENDED")
        self.assertFalse(material.use_backface_culling)
        self.assertTrue(material.use_transparency_overlap)

        # Front faces are blended by alpha times the opacity scale, back faces are opaque.
        alpha = linked_node(shader.inputs["Alpha"])
        self.assertEqual(alpha.operation, "MAXIMUM")
        opacity = linked_node(alpha.inputs[0])
        self.assertEqual(source(opacity.inputs[0]), ("g_tColor", "Alpha"))
        self.assertAlmostEqual(opacity.inputs[1].default_value, 0.66, places=6)
        self.assertEqual(alpha.inputs[1].links[0].from_socket.name, "Backfacing")

        normal_map = linked_node(shader.inputs["Normal"])
        self.assertEqual(normal_map.bl_idname, "ShaderNodeNormalMap")
        self.assertAlmostEqual(normal_map.inputs["Strength"].default_value, 2.0, places=6)

        # The emission is the cube maps (interior on back faces) times the Fresnel term.
        reflection = linked_node(shader.inputs["Emission Color"])
        self.assertEqual(reflection.operation, "SCALE")
        fresnel = linked_node(reflection.inputs["Scale"])
        self.assertEqual(fresnel.operation, "ADD")
        self.assertAlmostEqual(fresnel.inputs[0].default_value, 0.2, places=6)
        environments = {node.name: node for node in material.node_tree.nodes
                        if node.bl_idname == "ShaderNodeTexEnvironment"}
        self.assertEqual(set(environments), {"g_tCubeMapExterior", "g_tCubeMapInterior"})
        reflected = linked_node(environments["g_tCubeMapExterior"].inputs["Vector"])
        self.assertEqual(reflected.operation, "REFLECT")
        self.assertEqual(linked_node(environments["g_tCubeMapInterior"].inputs["Vector"]), reflected)
        self.assertEqual(linked_node(reflected.inputs[1]), normal_map)
        scales = {linked_node(node.inputs[0]).name: node.inputs["Scale"].default_value
                  for node in material.node_tree.nodes
                  if node.bl_idname == "ShaderNodeVectorMath" and node.operation == "SCALE"
                  and node.inputs[0].is_linked
                  and linked_node(node.inputs[0]).bl_idname == "ShaderNodeTexEnvironment"}
        self.assertAlmostEqual(scales["g_tCubeMapExterior"], 11.875, places=5)
        self.assertAlmostEqual(scales["g_tCubeMapInterior"], 10.0, places=5)

        # Without a separate interior cube map back faces reflect the exterior one; without cube maps nothing.
        with mock.patch.object(Source2ShaderBase, "load_cubemap_equirect", fake_cube):
            material = build("crystal.vfx", textures, ints={"F_SPECULAR_CUBE_MAP": 1})
        self.assertEqual({node.name for node in material.node_tree.nodes
                          if node.bl_idname == "ShaderNodeTexEnvironment"}, {"g_tCubeMapExterior"})
        material = build("crystal.vfx", textures)
        self.assertFalse(shader_node(material, "crystal.vfx").inputs["Emission Color"].is_linked)
        self.assertFalse({"g_tNormal"} & texture_nodes(material))

    def test_cube_maps_become_equirectangular_images_with_forward_at_the_center(self):
        face_colors = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1))  # F B L R U D

        class FakeCube:
            def is_cubemap(self):
                return True

            def get_resolution(self, mip):
                return 8, 8

            def get_cubemap_face(self, face, mip):
                return np.tile(np.array((*face_colors[face], 1.0), np.float32), 64), None

            def get_texture_format(self):
                return None

        material = FakeMaterial("crystal.vfx")
        material.get_child_resource = lambda *args: FakeCube()
        shader = Source2ShaderBase(None, material)
        image = shader.load_cubemap_equirect("materials/test/cube.vtex")
        self.assertEqual(tuple(image.size), (32, 16))
        pixels = np.array(image.pixels[:]).reshape(16, 32, 4)
        np.testing.assert_allclose(pixels[8, 16, :3], face_colors[0], atol=1e-3)  # +X, Source's forward
        np.testing.assert_allclose(pixels[15, 16, :3], face_colors[4], atol=1e-3)  # up
        np.testing.assert_allclose(pixels[0, 16, :3], face_colors[5], atol=1e-3)  # down
        self.assertIs(shader.load_cubemap_equirect("materials/test/cube.vtex"), image)

    def test_water_dota_surface_and_world_space_normal(self):
        srgb = lambda c: np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)  # noqa: E731
        refraction_tint = np.array((0.533, 0.565, 0.275))
        reflection_tint = np.array((0.75, 0.75, 0.75))
        floats = {"g_flNormalUvScale": 500.0, "g_flWorldPositionScale": 2.0, "g_flBumpStrength": 1.0,
                  "g_flReflectance": 0.01, "g_flReflectionPower": 3.0}
        vectors = {"g_vRefractionTint": (*refraction_tint, 0.0), "g_vReflectionTint": (*reflection_tint, 0.0),
                   "g_vReflectionColor": (0.1, 0.22, 0.25, 0.0), "g_vReflectionDir": (-0.45, -0.5, 0.499, 0.0)}
        material = build("water_dota.vfx", ("g_tNormal", "g_tNoise", "g_tFlow"), ints={"F_FLOW_NORMALS": 1},
                         floats=floats, vectors=vectors)
        shader = shader_node(material, "water_dota.vfx")
        self.assertEqual(material.surface_render_method, "DITHERED")
        self.assertTrue(material.use_raytrace_refraction)
        self.assertEqual(shader.inputs["Transmission Weight"].default_value, 1.0)
        self.assertAlmostEqual(shader.inputs["IOR"].default_value, 1.333, places=5)
        np.testing.assert_allclose(shader.inputs["Base Color"].default_value[:3], srgb(refraction_tint), atol=1e-5)
        np.testing.assert_allclose(shader.inputs["Specular Tint"].default_value[:3], srgb(reflection_tint),
                                   atol=1e-5)
        f0 = ((1.333 - 1) / (1.333 + 1)) ** 2
        self.assertAlmostEqual(shader.inputs["Specular IOR Level"].default_value, 0.5 * 0.01 / f0, places=5)
        self.assertEqual(texture_nodes(material), {"g_tNormal"})

        # The normal map tiles in Source units: (x, y) * g_flWorldPositionScale / g_flNormalUvScale.
        uv = linked_node(material.node_tree.nodes["g_tNormal"].inputs[0])
        self.assertEqual(uv.operation, "SCALE")
        self.assertEqual(linked_node(uv.inputs[0]).bl_idname, "ShaderNodeNewGeometry")
        self.assertAlmostEqual(uv.inputs["Scale"].default_value, 2.0 / 500.0 / SOURCE2_HAMMER_UNIT_TO_METERS,
                               places=4)
        highlight = linked_node(shader.inputs["Emission Color"])
        self.assertEqual(highlight.operation, "SCALE")
        power = linked_node(highlight.inputs["Scale"])
        self.assertEqual(power.operation, "POWER")
        self.assertAlmostEqual(power.inputs[1].default_value, 3.0, places=6)

        # xy * (|flow|^2 + 0.1) * g_flBumpStrength, with the default flow (1, 0); clamped to unit length.
        for bump, expected in ((0.5, (0.55, 0.0, math.sqrt(1 - 0.55 ** 2))), (2.5, (1.0, 0.0, 0.0))):
            with self.subTest(bump=bump):
                material = build("water_dota.vfx", ("g_tNormal",), floats={"g_flBumpStrength": bump})
                self.constant_image(material.node_tree.nodes["g_tNormal"], (1.0, 0.5, 1.0, 1.0))
                np.testing.assert_allclose(self.bake_base_color(material, {}, "Normal"), expected, atol=2e-3)

        material = build("water_dota.vfx", ("g_tNormal",), vectors={"g_vReflectionColor": (0.0, 0.0, 0.0, 0.0)})
        self.assertFalse(shader_node(material, "water_dota.vfx").inputs["Emission Color"].is_linked)

    def test_source2_shader_metadata_distinguishes_supported_and_dummy_handlers(self):
        for shader in ("hero.vfx", "global_lit_simple.vfx", "multiblend.vfx", "crystal.vfx", "water_dota.vfx"):
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
