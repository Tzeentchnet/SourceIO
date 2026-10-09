import math
from typing import Any

import bpy

from ...shader_base import ExtraMaterialParameters, Nodes
from ..node_math import NodeMath, Scalar, Vector, group_call, node_group
from .csgo_environment import UP, CSGOEnvironment, Layer, _colorize_tint_group, srgb_to_linear

# vmdl_loader's names for the VertexPaintBlendParams stream (TEXCOORD4): layer 2 and 3 weights in x and y,
# wetness and blend softness in z and w
BLEND_UV = "TEXCOORD_4"
BLEND_UV_ZW = "TEXCOORD_4_2"


def _band_weight(m: NodeMath, difference, factor, height, softness, mask_with_height) -> Scalar:
    """VRF's BlendBandWeight: a crossfade across the seam, forced to 1 where the paint is nearly full and to 0
    where it is nearly empty, scaled by the layer's height by g_flMaskWithHeight."""
    softness = m.max(softness, 1e-4)
    quarter = softness * 0.25
    edge = 0.02 + quarter
    crossfade = m.saturate(0.5 + difference / (2.0 * softness))
    edge_high = m.saturate((m.saturate(factor + difference * 0.1 + 0.2) - (0.98 - quarter)) / edge)
    crossfade = m.lerp(crossfade, 1.0, edge_high)
    edge_low = m.saturate((edge - m.saturate(factor + difference * 0.05 - 0.05)) / edge)
    crossfade = m.lerp(crossfade, 0.0, edge_low)
    return m.saturate(crossfade * m.lerp(0.5, height, mask_with_height) * 2.0)


def _blend_layer_group():
    """VRF's BlendLayer (F_USE_NEW_BLENDING): a layer's weight from its height plus its signed paint factor,
    measured against the height carried up from the layers below. Factor is the layer's paint (after the
    1.1x - 0.05 remap); nothing shows where it is 0."""

    def build(m: NodeMath, i):
        present = m.greater(i['Factor'], 0.0)
        signed = 2.0 * i['Factor'] - 1.0
        height = (i['Height'] - i['Zero Point']) * i['Scale'] + signed + m.max(i['Signed Raw Below'], 0.0)
        difference = height - m.lerp(i['Signed Carry Below'], i['Under Height Below'], i['Underlying Influence'])
        weight = _band_weight(m, difference, i['Factor'], i['Height'], i['Softness'], i['Mask With Height']) * present
        # The carried height and signed factor move up only where the layer is actually present.
        strong = m.greater(weight, 0.05)
        below = i['Under Height Below']
        return {'Weight': weight, 'Difference': difference * present, 'Signed Raw': signed * present,
                'Signed Carry': signed * weight * strong,
                'Under Height': m.lerp(below, m.lerp(below, m.max(below, height), weight), strong)}

    inputs = {name: ('float', 0.0) for name in ('Signed Raw Below', 'Signed Carry Below', 'Under Height Below',
                                                'Factor', 'Height', 'Zero Point', 'Scale', 'Softness',
                                                'Underlying Influence', 'Mask With Height')}
    return node_group('SourceIO Environment Blend Layer', inputs,
                      {name: 'float' for name in ('Weight', 'Difference', 'Signed Raw', 'Signed Carry',
                                                  'Under Height')}, build)


def _blend_weights_group():
    """VRF's GetBlendWeights (the legacy blend): normalized weights of two height-scaled layers, where the paint
    factor slides the upper layer's height from below the lower one to above it."""

    def build(m: NodeMath, i):
        softness, factor = i['Softness'], i['Factor']
        h1 = i['Scale 1'] + softness
        height1 = i['Height 1'] * h1
        h2 = i['Scale 2'] + softness
        h22 = i['Height 2'] * (i['Scale 2'] - softness)
        blend1 = (-i['Zero Point 1'] * h1 - (1.0 - i['Zero Point 2']) * h2) - softness
        blend2 = (1.0 - i['Zero Point 1']) * h1 + i['Zero Point 2'] * h2
        height2 = h22 + m.lerp(blend1, blend2, factor)
        top = m.max(height1, height2) - softness
        weight1 = m.max(height1 - top, 0.0) + 0.001  # bias towards the lower layer
        weight2 = m.max(height2 - top, 0.0)
        total = weight1 + weight2
        return {'Weight 1': weight1 / total, 'Weight 2': weight2 / total}

    inputs = {name: ('float', 0.0) for name in ('Height 1', 'Height 2', 'Scale 1', 'Scale 2', 'Zero Point 1',
                                                'Zero Point 2', 'Factor', 'Softness')}
    return node_group('SourceIO Environment Blend Weights', inputs, {'Weight 1': 'float', 'Weight 2': 'float'},
                      build)


def _border_group():
    """VRF's ApplyBlendBorder (F_BLEND_EFFECTS_<n>): a band of Spread around the seam (difference 0, moved by
    Offset), softened by the blend and border softness, where both layers are recolored by Tint: Mode 0 Multiply,
    1 Replace, 2 Mod2x, 3 Colorize, and their roughness set to Roughness with Use Roughness. Layer Amount weighs
    the band where the lower layer (x) or this one (y) shows. The band needs the paint to cross over, so it is
    only drawn by the height-band blend."""

    def build(m: NodeMath, i):
        factor = i['Factor']
        paint = m.saturate(factor * 5.0) * m.saturate((1.0 - factor) * 5.0)
        spread = i['Spread'] * (0.5 + 0.5 * i['Height Scale']) * paint
        softness = i['Softness'] + i['Border Softness']
        distance = m.abs(i['Difference'] + i['Offset'])
        band = ((1.0 - m.smoothstep(spread - softness, spread + softness, distance))
                * (1.0 - m.saturate(softness / i['Spread'] * 0.01)))
        height = m.lerp(0.5, i['Height'], i['Mask With Height'])
        sides = m.lerp(m.saturate(i['Layer Amount'].x * height * 2.0), m.saturate(i['Layer Amount'].y * height * 2.0),
                       i['Weight'])
        amount = (band * m.saturate(paint * m.lerp(4.0, 1.0, m.saturate(softness * 0.5))) * sides
                  * m.greater(i['Spread'], 0.0))
        mask = m.lerp(1.0, i['Tint Mask'], i['Use Tint Mask'])
        weight = amount * mask
        mode = i['Mode']
        is_replace, is_mod2x, is_colorize = (m.math('COMPARE', mode, value, 0.5) for value in (1.0, 2.0, 3.0))
        factor_color = m.lerp((1.0, 1.0, 1.0), i['Tint'] * (1.0 + is_mod2x), weight)
        outputs = {}
        for side in ('Lower', 'Upper'):
            color = i[side]
            colorized = group_call(m, _colorize_tint_group(), {'Color': color, 'Tint': i['Tint'], 'Amount': mask})
            recolored = m.select(is_replace, m.lerp(color, i['Tint'], weight), color * factor_color)
            outputs[side] = m.select(is_colorize, m.lerp(color, colorized['Color'], amount), recolored)
            outputs[f'{side} Roughness'] = m.lerp(i[f'{side} Roughness'], i['Roughness'], weight * i['Use Roughness'])
        return outputs

    inputs = {'Lower': ('color', (1, 1, 1)), 'Upper': ('color', (1, 1, 1)), 'Lower Roughness': ('float', 0.5),
              'Upper Roughness': ('float', 0.5), 'Difference': ('float', 0.0), 'Factor': ('float', 0.0),
              'Height Scale': ('float', 1.0), 'Softness': ('float', 0.0), 'Weight': ('float', 0.0),
              'Height': ('float', 0.5), 'Mask With Height': ('float', 0.0), 'Tint Mask': ('float', 1.0),
              'Spread': ('float', 0.1), 'Border Softness': ('float', 0.1), 'Offset': ('float', 0.0),
              'Tint': ('color', (1, 1, 1)), 'Layer Amount': ('vector', (1.0, 0.0, 0.0)),
              'Use Tint Mask': ('float', 0.0), 'Mode': ('float', 0.0), 'Use Roughness': ('float', 0.0),
              'Roughness': ('float', 0.5)}
    return node_group('SourceIO Environment Blend Border', inputs,
                      {'Lower': 'color', 'Upper': 'color', 'Lower Roughness': 'float', 'Upper Roughness': 'float'},
                      build)


class CSGOEnvironmentBlend(CSGOEnvironment):
    """csgo_environment_blend: two or (F_ENABLE_LAYER_3) three csgo_environment layers blended by the painted
    weights in TEXCOORD_4 (VRF's csgo_environment.frag). Not handled besides what CSGOEnvironment lists: the bevel
    (a screen-space slope), wetness, and the blend softness's growth with distance (it depends on the mip level)."""
    SHADER: str = 'csgo_environment_blend.vfx'

    def _blend_params(self, m: NodeMath):
        xy = self.create_node(Nodes.ShaderNodeUVMap)
        xy.uv_map = BLEND_UV
        zw = self.create_node(Nodes.ShaderNodeUVMap)
        zw.uv_map = BLEND_UV_ZW
        return m.vector(xy.outputs[0]), m.vector(zw.outputs[0])

    def _facing(self, m: NodeMath, n: int) -> Scalar:
        """F_BLEND_BY_FACING_DIRECTION_<n>: how much the vertex normal faces g_vFacingDirection<n>, through a
        smoothstep set by g_flFacingDirectionMaskSpread<n> and g_vFacingDirectionMaskSoftness<n>."""
        x, y, z = self._vector(f"g_vFacingDirection{n}", (0.0, 0.0, 1.0, 0.0))[:3]
        length = math.sqrt(x * x + y * y + (z or 0.0001) ** 2)
        direction = (x / length, y / length, (z or 0.0001) / length)
        spread = self._float(f"g_flFacingDirectionMaskSpread{n}", 0.5)
        softness = self._float(f"g_vFacingDirectionMaskSoftness{n}", 0.1)
        geometry = self.create_node(Nodes.ShaderNodeNewGeometry)
        facing = m.dot(direction, geometry.outputs['Normal']) * 0.5 + 0.5
        return m.smoothstep(max(0.0, 1.0 - spread - softness), min(1.0, 1.0 - spread + 0.001 + softness), facing)

    @staticmethod
    def _combine_normal(m: NodeMath, lower, upper, weight, combine: float, replace: float) -> Vector:
        if combine:
            lower = m.lerp(lower, m.normalize(lower + upper - UP), weight * combine)
        return m.lerp(lower, upper, weight * replace)

    def _combine_color(self, m: NodeMath, lower, upper, weight, overlay: float, replace: float) -> Vector:
        """Mod2x-overlay the gamma-encoded upper color onto the lower one, then replace toward it."""
        if overlay > 0.0:
            gamma = m.vector(self._linear_to_srgb(m.vsaturate(upper).socket))
            lower = lower * m.lerp((1.0, 1.0, 1.0), gamma * 2.0, weight * overlay)
        return m.lerp(lower, upper, weight * replace)

    @staticmethod
    def _combine_roughness(m: NodeMath, lower, upper, weight, combine: float, replace: float) -> Scalar:
        if combine:
            lower = lower * (1.0 + (upper - lower) * 2.0 * weight * combine)
        return m.lerp(lower, upper, weight * replace)

    def _border(self, m: NodeMath, n: int, lower: Layer, upper: Layer, lower_roughness, carry: dict, factor,
                height_scale, softness, weight, tint_mask):
        """Recolor both sides of layer n's seam (see _border_group); returns the new lower and upper colors and
        roughness."""
        tint = srgb_to_linear(self._vector(f"g_vBorderTint{n}", (1.0, 1.0, 1.0, 0.0))[:3])
        result = group_call(m, _border_group(), {
            'Lower': lower.color, 'Upper': upper.color, 'Lower Roughness': lower_roughness,
            'Upper Roughness': upper.roughness, 'Difference': carry['Difference'], 'Factor': factor,
            'Height Scale': height_scale, 'Softness': softness, 'Weight': weight, 'Height': upper.height,
            'Mask With Height': self._float(f"g_flMaskWithHeight{n}", 0.0), 'Tint Mask': tint_mask,
            'Spread': self._float(f"g_flBorderSpread{n}", 0.1),
            'Border Softness': self._float(f"g_flBorderSoftness{n}", 0.1),
            'Offset': self._float(f"g_flBorderOffset{n}", 0.0), 'Tint': tint,
            'Layer Amount': (*self._vector(f"g_vBorderLayerAmount{n}", (1.0, 0.0, 0.0, 0.0))[:2], 0.0),
            'Use Tint Mask': float(self._int(f"g_bBorderTintMask{n}", 0)),
            'Mode': float(self._int(f"F_BORDER_BLEND_MODE_{n}", 0)),
            'Use Roughness': float(self._int(f"F_BORDER_ROUGHNESS_{n}", 0)),
            'Roughness': self._float(f"g_fBorderRoughness{n}", 0.5)})
        return result['Lower'], result['Upper'], result['Lower Roughness'], result['Upper Roughness']

    def _shared_color_overlay(self, m: NodeMath, color, tint_masks, shares) -> Vector:
        """F_SHARED_COLOR_OVERLAY: g_tSharedColorOverlay (linear), unpacked to [-1, 1], brightens or darkens the color
        through g_flOverlayBrightnessContrast and g_flOverlayDarknessContrast, by each layer's share times
        g_vColorOverlayLayerStrengths, masked by the layer's tint mask (or its inverse, for a negative strength)
        by |g_vColorOverlayTintMaskStrengths|."""
        if not self._int("F_SHARED_COLOR_OVERLAY", 0) or not self._have_texture("g_tSharedColorOverlay"):
            self._skip_texture("g_tSharedColorOverlay")
            return color
        layer_strengths = self._vector("g_vColorOverlayLayerStrengths", (1.0, 1.0, 1.0, 0.0))
        mask_strengths = self._vector("g_vColorOverlayTintMaskStrengths", (0.0, 0.0, 0.0, 0.0))
        amount: Scalar | float = 0.0
        for tint_mask, share, layer_strength, mask_strength in zip(tint_masks, shares, layer_strengths,
                                                                   mask_strengths):
            mask = tint_mask if mask_strength > 0.0 else 1.0 - tint_mask
            amount = amount + m.lerp(1.0, mask, abs(mask_strength)) * (layer_strength * share)
        uv_set = self._int("g_nColorOverlayUVSet", 2)
        # sRGB: the textures store a neutral linear 0.5 (median about 0.75, reflectivity about 0.5).
        overlay_texture = self._get_texture("g_tSharedColorOverlay", (0.5, 0.5, 0.5, 1.0))
        self.connect_nodes(self._transformed_uv("Overlay", "", uv_set), overlay_texture.inputs[0])
        overlay = m.vector(overlay_texture.outputs[0]) * 2.0 - 1.0
        bright = self._float("g_flOverlayBrightnessContrast", 1.0)
        dark = self._float("g_flOverlayDarknessContrast", 1.0)
        channels = []
        for channel in (overlay.x, overlay.y, overlay.z):
            brighten = (1.0 - m.pow(1.0 - m.max(channel, 0.0), bright)) * bright
            darken = (m.pow(1.0 + m.min(channel, 0.0), dark) - 1.0) * dark
            channels.append(m.max(1.0 + brighten + darken, 0.0))
        return color * m.lerp((1.0, 1.0, 1.0), m.combine(*channels), amount)

    def create_nodes(self, material: bpy.types.Material, extra_parameters: dict[ExtraMaterialParameters, Any]):
        m = NodeMath(material.node_tree, self.create_node)
        three = bool(self._int("F_ENABLE_LAYER_3", 0))
        layers = [self._layer(m, n) for n in ((1, 2, 3) if three else (1, 2))]
        if not three:
            self._skip_textures_with_prefix("g_tColor3", "g_tHeight3", "g_tNormal3", "g_tNormalDetail3")
        first, second = layers[0], layers[1]
        new_blending = bool(self._int("F_USE_NEW_BLENDING", 0))

        blend_xy, blend_zw = self._blend_params(m)
        paint2, paint3 = blend_xy.x, blend_xy.y
        if self._int("F_BLEND_BY_FACING_DIRECTION_2", 0):
            paint2 = paint2 * self._facing(m, 2)
        softness: Scalar | float = self._float("g_flBlendSoftness2", 0.01)
        if three:
            softness3 = self._float("g_flBlendSoftness3", 0.01)
            softness = m.lerp(m.select(m.less(paint2, 0.001), softness3, softness), softness3, paint3)
            if self._int("F_BLEND_BY_FACING_DIRECTION_3", 0) == 1:
                paint3 = paint3 * self._facing(m, 3)
        softness = m.clamp(blend_zw.y + softness, 0.001, 1.0)
        factor2 = m.saturate(paint2 * 1.1 - 0.05)
        factor3 = m.saturate(paint3 * 1.1 - 0.05)

        scale1, scale2 = self._float("g_flHeightMapScale1", 1.0), self._float("g_flHeightMapScale2", 1.0)
        zero1, zero2 = self._float("g_flHeightMapZeroPoint1", 0.5), self._float("g_flHeightMapZeroPoint2", 0.5)
        base1, base2 = first.height - zero1, second.height - zero2
        carry2 = {'Difference': 0.0}
        if new_blending:
            carry2 = group_call(m, _blend_layer_group(), {
                'Signed Raw Below': 0.0, 'Signed Carry Below': 0.0, 'Under Height Below': base1 * scale1,
                'Factor': factor2, 'Height': second.height, 'Zero Point': zero2, 'Scale': scale2,
                'Softness': softness, 'Underlying Influence': self._float("g_flUnderlyingHeightMapInfluence2", 1.0),
                'Mask With Height': self._float("g_flMaskWithHeight2", 0.0)})
            weight2 = carry2['Weight']
            base_weight2 = 1.0 - weight2
        else:
            weights = group_call(m, _blend_weights_group(), {
                'Height 1': base1, 'Height 2': base2, 'Scale 1': scale1, 'Scale 2': scale2, 'Zero Point 1': zero1,
                'Zero Point 2': zero2, 'Factor': paint2, 'Softness': softness})
            weight2, base_weight2 = weights['Weight 2'], weights['Weight 1']
        tint_mask_below = m.lerp(first.tint_mask, second.tint_mask, weight2)

        roughness = first.roughness
        if new_blending and self._int("F_BLEND_EFFECTS_2", 0):
            first.color, second.color, roughness, second.roughness = self._border(
                m, 2, first, second, roughness, carry2, factor2, max(abs(scale1), abs(scale2)), softness, weight2,
                tint_mask_below)

        normal = self._combine_normal(m, first.normal, second.normal, weight2,
                                      self._float("g_flNormalCombine2", 0.0), self._float("g_flNormalReplace2", 1.0))
        color = self._combine_color(m, first.color, second.color, weight2, self._float("g_flColorOverlay2", 0.0),
                                    self._float("g_flColorReplace2", 1.0))
        roughness = self._combine_roughness(m, roughness, second.roughness, weight2,
                                            self._float("g_flRoughnessCombine2", 0.0),
                                            self._float("g_flRoughnessReplace2", 1.0))
        metalness = first.metalness * base_weight2 + second.metalness * weight2
        base_height = base1 * base_weight2 + base2 * weight2

        weight3: Scalar | float = 0.0
        if three:
            third = layers[2]
            scale3, zero3 = self._float("g_flHeightMapScale3", 1.0), self._float("g_flHeightMapZeroPoint3", 0.5)
            if new_blending:
                carry3 = group_call(m, _blend_layer_group(), {
                    'Signed Raw Below': carry2['Signed Raw'], 'Signed Carry Below': carry2['Signed Carry'],
                    'Under Height Below': carry2['Under Height'], 'Factor': factor3, 'Height': third.height,
                    'Zero Point': zero3, 'Scale': scale3, 'Softness': softness,
                    'Underlying Influence': self._float("g_flUnderlyingHeightMapInfluence3", 1.0),
                    'Mask With Height': self._float("g_flMaskWithHeight3", 0.0)})
                weight3 = carry3['Weight']
            else:
                weight3 = group_call(m, _blend_weights_group(), {
                    'Height 1': m.max(base_height, base2), 'Height 2': third.height - zero3,
                    'Scale 1': max(scale1, scale2), 'Scale 2': scale3, 'Zero Point 1': max(zero1, zero2),
                    'Zero Point 2': zero3, 'Factor': factor3, 'Softness': softness})['Weight 2']
            if new_blending and self._int("F_BLEND_EFFECTS_3", 0):
                below = Layer(color, None, first.height, first.tint_mask_raw, tint_mask_below, metalness,
                              roughness, normal)
                color, third.color, roughness, third.roughness = self._border(
                    m, 3, below, third, roughness, carry3, factor3,
                    m.max(m.abs(m.lerp(scale1, scale2, weight2)), abs(scale3)), softness, weight3,
                    m.lerp(tint_mask_below, third.tint_mask, weight3))
            normal = self._combine_normal(m, normal, third.normal, weight3, self._float("g_flNormalCombine3", 0.0),
                                          self._float("g_flNormalReplace3", 1.0))
            color = self._combine_color(m, color, third.color, weight3, self._float("g_flColorOverlay3", 0.0),
                                        self._float("g_flColorReplace3", 1.0))
            roughness = self._combine_roughness(m, roughness, third.roughness, weight3,
                                                self._float("g_flRoughnessCombine3", 0.0),
                                                self._float("g_flRoughnessReplace3", 1.0))
            metalness = metalness * (1.0 - weight3) + third.metalness * weight3

        share2 = weight2 * (1.0 - weight3)
        share1 = m.saturate(1.0 - share2 - weight3)
        shares = [share1, share2] + ([weight3] if three else [])
        color = self._shared_color_overlay(m, color, [layer.tint_mask for layer in layers], shares)

        # The painted vertex color, by g_nVertexColorMode<n>: 0 masked by the tint mask, 1 unmasked, 2 disabled
        # (layer 3's disabled mode adds its weight back, as VRF has it).
        modes = [self._int(f"g_nVertexColorMode{n}", 0) for n in range(1, len(layers) + 1)]
        masked = first.tint_mask * share1 + second.tint_mask * share2
        unmasked = float(modes[0] != 0) * share1 + float(modes[1] != 0) * share2
        enabled = float(modes[0] != 2) * share1 + float(modes[1] != 2) * share2
        if three:
            masked = masked + layers[2].tint_mask * weight3
            unmasked = unmasked + float(modes[2] != 0) * weight3
        vertex_mask = m.saturate(unmasked + masked) * enabled
        if three:
            vertex_mask = vertex_mask + float(modes[2] != 2) * weight3
        color = color * m.lerp((1.0, 1.0, 1.0), self._vertex_paint(m), m.saturate(vertex_mask))

        self._surface(m, color, metalness, roughness, normal, first.alpha)
