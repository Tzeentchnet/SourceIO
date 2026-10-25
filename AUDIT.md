# SourceIO 5.14.0 audit: import speed and material accuracy

Scope: the Blender add-on at `21bf348` (5.14.0), focused on import speed and on how closely materials match the game. Source 1 got the deepest review, because it's the default path for most users. Source 2 materials are built from node groups in bundled `.blend` assets and already have extensive tests, so this review only touches them briefly.

**Patch:** `sourceio-audit.patch` (one commit, `f63bd40`, also pushed to `claude/determined-volta-cfat6r`). It applies cleanly to 5.14.0:

```
git am sourceio-audit.patch        # or: git apply sourceio-audit.patch
```

## How this was checked

This container can't run the full add-on. The bundled Linux `pylib.abi3.so` needs glibc 2.43 and the container has 2.39 (finding 1 below). So the patch was checked against the official `bpy` 5.2.3 wheel (Blender 5.2.3 LTS as a Python module), with the native library replaced by stubs. A small Python PNG encoder and fake VTF decoders stood in for the real ones.

| Check | Original | Patched |
|---|---|---|
| Unit tests (`tests/`, no Blender) | 476 pass / 17 fail* | identical set |
| Blender tests (`tests/blender_tests`, under bpy 5.2.3) | 198 pass / 29 fail or error* | identical set + 15 new pass |
| New `test_source1_material_followups.py` | 7 of the original 11 fail | 15 pass |
| Material graphs: 8 VMTs × (Principled, BVLG) built through `ShaderRegistry` | — | only the intended differences (below); BVLG graphs unchanged except sampler flags |
| Decoded pixels (normal map, ssbump), old in-Blender conversion vs new decode-time conversion | — | bit-identical |

\* The baseline failures are the same on both trees. They come from the stubbed native decoder and compressor, and from suites `plan.md` says to run separately (`test_skins`, flex controllers).

**Not checked here:** real VTF/VPK decoding, the e2e runners (`tests/e2e/*`) and real game imports. Run the `plan.md` baseline commands on Windows before you merge.

### Review follow-ups

The review-fix branch also applies `$alpha` before `$alphatest` while preserving the full-resolution alpha
sampling workaround, restores VTF metadata when an image is loaded from the on-disk texture cache, avoids
double-flipping float normal maps, and recognizes a material whose name and `full_path` were retargeted in
place. Blender 5.2.2 validation passes all 15 focused follow-up tests and the full headless Blender suite:
237 passed, 1 skipped, 11 warnings, and 28 subtests passed.

---

## 1. Resolved blocker: Linux native-library compatibility

`library/utils/pylib/linux/pylib.abi3.so` links `sqrtf@GLIBC_2.43` and `__isoc23_*@GLIBC_2.38`. Ubuntu 24.04 LTS (2.39), Debian 13 (2.41) and RHEL/Rocky 9 (2.34) can't load it, so `import SourceIO` fails with `GLIBC_2.43 not found`. `blender_manifest.toml` still lists `linux-x64` as supported.

**Resolved:** the replacement is built from pinned native and transitive source revisions in a `manylinux_2_28` container. It requires at most `GLIBC_2.27` and links libstdc++ and libgcc statically, so it has no `GLIBCXX` or `CXXABI` runtime requirement. CI imports it with both Python 3.11 and 3.13, and `tools/build_extension.py` rejects Linux binaries above the compatibility policy before packaging.

---

## 2. Import speed

### Fixed in the patch

| # | Problem | Fix | Measured (bpy 5.2.3, 2000 items) |
|---|---|---|---|
| S1 | `get_or_create_material` scanned every material and built two `TinyPath`s for each one, on every call. Map imports call it per face group, displacement, overlay and prop material, so the cost grows quadratically. | Path → material index (`bpy_utils.py`). Hits are checked against the material. The index rebuilds when materials are renamed, deleted or added elsewhere, which it detects with a `session_uid` fingerprint (`utils/id_index.py`, one C-level `foreach_get`). | 3.6 s create + 3.4 s repeat hits → **0.65 s + 0.11 s** |
| S2 | `check_texture_cache` scanned every image on every lookup, and `_add_texture` copied the scene's whole `texture_name_to_texture` group on every texture. | Same index scheme for images (`texture_utils.py`); the scene group is written in place. | 2.4 s + 0.9 s → **0.55 s + 0.09 s** |
| S3 | 8-bit VTFs went uint8 → float32 (`/255`) → float32 (`*255`) → uint8 before PNG encoding. That's two full-size passes and about 4× the memory per texture. | `load_texture(..., normalized=False)` keeps the decoder's uint8; `create_and_cache_texture` accepts uint8. | ~65 ms and 64 MB of temporaries saved per 2048² texture |
| S4 | Every normal map (and ssbump) was decoded, packed, then read back with `image.pixels.foreach_get`, flipped in numpy, written back, and **re-packed**, which Blender re-encodes as PNG. | Green flip and ssbump → normal now happen on the decoded array before the single encode. Converted variants are cached under their own identity (`invert_y`, `ssbump+invert_y`) and keep the texture's plain name. If only the plain image is already loaded, the old in-place conversion still runs. | Removes a full decode + float round trip + PNG re-encode per normal map (typically about a third of a map's textures) |

### Recommended next (not in the patch)

1. **Wrap DXT payloads as DDS instead of decoding them.** Most VTFs are DXT1/DXT5. A 128-byte DDS header in front of the VTF's top mip would let Blender load it directly (`image.pack(data=dds_bytes)`). That skips the native decode, the PNG encode and the PNG decode, and keeps packed `.blend` files 4–8× smaller. Check vertical orientation and BC5/ATI2N normal maps first, which still need the Z reconstruction the decoder does now.
2. **PNG encode level.** `encode_png` runs for every texture when no cache path is set. If the Rust encoder uses default compression, a fast level (or `png::Compression::Fast`) cuts encode time a lot, and Blender's decode time barely changes.
3. **Source 2 unused textures.** `ShaderRegistry.source2_create_nodes` decodes every texture a `.vmat` lists but the shader didn't use (`handler.unused_textures`). That's useful for authoring, but costly on CS2/Dota maps. An import option ("Load unreferenced textures", off by default for maps) would skip that work.
4. **Displacements** (`import_bsp.import_disp`): one object per displacement face, each with up to 6 colour layers written through the legacy `mesh.vertex_colors` API. Merging displacements per material into one mesh, and writing `mesh.color_attributes.new(..., 'FLOAT_COLOR', 'CORNER')` in one `foreach_set` per layer, would cut object count and depsgraph cost on displacement-heavy maps (CS:GO, L4D2).
5. **Repeated property evaluation in shaders.** VMT properties such as `self.bumpmap` and `self.basetexture` reload (now cheaply) on every access, and `create_nodes` reads them several times. Caching per handler instance (`functools.cached_property`, set up after proxies run) would remove the rest of that work.
6. **MDL import loops** (`models/mdl49/import_mdl.py` and siblings): the flex loop recomputes `model_vertices = get_slice(...)[vtx_vertices] * scale` for every flex; it can be hoisted out. `mesh_data.update()` right after `from_pydata` repeats work `from_pydata` already did. `mesh.validate()` runs on every mesh even when the source data is well-formed.
7. **Measure before going further.** `library/utils/perf_sampler.timed` exists. Wrapping `import_materials`, `nodes_iterate` (node arrangement on every material) and per-model mesh building in a map import would show where the remaining time goes.

---

## 3. Material accuracy

`use_bvlg` is **off** by default (`import_settings_base.py`), so most users get the Principled VertexLitGeneric path. That path had the most gaps compared with the SDK shader (`vertexlit_and_unlit_generic_ps2x.fxc`).

### Fixed in the patch

| # | Before | After (SDK behaviour) |
|---|---|---|
| A1 | Phong mask (normal or base alpha) drove masked areas to **roughness 1**, which left a broad, dull highlight where the game has none. | The mask scales **Specular IOR Level**, as the SDK scales specular intensity. Roughness stays at the value from the exponent. |
| A2 | `$color2` multiplied the whole texture even with `$blendtintbybasealpha` (TF2 paintable items, team colours); `$blendtintcoloroverbase` was ignored; `$color` and `$color2` didn't combine. | `tinted = base × color×color2`, then `lerp(tinted, tint, $blendtintcoloroverbase)`, then `lerp(base, tinted, base.a)` when `$blendtintbybasealpha` is set. With no base texture, the tint becomes the constant base colour. |
| A3 | `$additive` faked additive blending through inverted colour into Transmission Weight. | Add Shader (Transparent + Principled): the lit colour is added onto what's behind it (`SRC_ONE, DST_ONE`). |
| A4 | `$alpha` was ignored on models. | Scales alpha; below 1 the material renders blended, as in the SDK. |
| A5 | `$selfillumtint` was ignored; emission used the untinted base texture. | Emission colour = tinted albedo × `$selfillumtint`. |
| A6 | VTF sampler flags were stored on the image but never used. | `POINTSAMPLE` → Closest; `CLAMPS` + `CLAMPT` → Extend (sprites, decals, warp ramps no longer bleed at the edges). Applies to every Source 1 shader, including BVLG; interpolation or extension a shader set on purpose is left alone. |
| A7 | BVLG: `$bumpcompress`/`$bumpstretch` without `$compress`/`$stretch` created an Attribute node with no name, so wrinkle normals never blended. | The attribute is named `tension`. |
| A8 | `vertexlit_generic.py` defined `compress` and `lightwarptexture` twice; the first, shadowed versions would have flipped a lightwarp's green channel. | Duplicates removed. Normal-map properties share one helper. |

### Implemented in the correctness follow-up

- **Deterministic shader mode.** BVLG selection is now an explicit material-build parameter instead of global class state. Principled and BVLG variants use distinct, stable material identities, and BSP, entity, VMT and model paths consistently resolve the selected variant.
- **Opt-in Principled culling.** Principled VertexLitGeneric materials remain two-sided by default. An add-on preference enables Source-style culling for newly imported materials while preserving `$nocull 1`; BVLG behavior is unchanged.
- **`$basealphaenvmapmask`.** The default is now 0, matching the SDK. Explicit 0 and 1 continue to override it.
- **Model envmaps.** Principled VertexLitGeneric now maps `$envmap`, `$envmaptint`, `$envmapcontrast`, `$envmapsaturation` and all three mask sources to the Principled specular lobe. Base alpha is inverted, normal-map alpha overrides the other masks, and unsupported bump-map/mask combinations disable the reflection as the SDK does. `$phong` keeps its separate skin-shader behavior.
- **Exact phong exponent textures.** The red channel now follows `sqrt(2 / (3 + factor·r))` at every value, including custom and zero `$phongexponentfactor` values, instead of linearly interpolating roughness between the two endpoints.
- **Per-pixel phong Fresnel.** `$phongfresnelranges` now follows the SDK's view-dependent piecewise curve from low through mid to high, uses the SDK default `[0 0.5 1]`, and composes with `$phongboost` and the phong alpha mask instead of collapsing to a constant.
- **Detail-texture colorspaces.** VertexLitGeneric-family modes other than mod2x now use sRGB, while LightmappedGeneric uses sRGB only for mode 1, matching each SDK sampler state. Cached sRGB and data variants are separate image datablocks, so a VTF reused by different materials no longer changes colorspace underneath an existing graph.
- **Independent VTF clamps.** `CLAMPS` and `CLAMPT` now clamp only the flagged UV axis to the first/last texel centre while the other axis keeps repeating. The full-resolution alpha-test taps inherit the same sampler behavior; textures clamped on both axes continue to use Blender's native Extend mode.

### Confirmed non-goal

- **`$vertexcolor` / `$vertexalpha`** aren't supported by Valve's VertexLitGeneric path (UnlitGeneric and DecalModulate support them), so they should not be added without branch-specific shader evidence.

---

## 4. Other code-quality findings

- **Resolved:** the process-wide `DeprecationWarning` filter was removed.
- **Resolved:** `pause_view_layer_update` now treats the missing private Blender hook as a no-op and restores the hook after normal and exceptional exits when it exists. Focused tests cover all three cases.
- **Resolved:** the model skin-group fallbacks now catch only `TypeError` and `ValueError`.
- Errors in decoders return sentinels inconsistently. `load_texture` returned `(None, 0, 0)` and `load_texture_tth` returned bare `None`, and callers reshaped without checking. The patch makes TTH return `(None, 0, 0)` and makes the VTF importers return `None`, which callers already handle.
- **Resolved:** Source 2 material authoring checks `material.node_tree is None` without reading deprecated `Material.use_nodes`.
