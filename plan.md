# SourceIO → Blender 5.2 modernization plan

Target: **Blender 5.2.2 LTS only.** Blender 4.x compatibility is dropped.
Branch: `blender-5.2-modernization`.

All API claims below were checked against Blender 5.2.2 running headless (`D:/Blender Foundation/Blender 5.2/blender.exe -b`).

## Verification

Every change must pass the headless smoke test. It imports every hand-written module, then runs register → unregister → register:

```
"D:/Blender Foundation/Blender 5.2/blender.exe" -b --factory-startup --python <scratchpad>/smoke.py
```

The baseline (before any changes) fails with:
- `csgo_weapon.py`: `NameError: name 'bpy' is not defined`
- `IMAGE_MT_image` has 2 `vtf_export` entries after re-register

## Phase 1 — Bugs users hit

| # | Bug | Location | Fix |
|---|-----|----------|-----|
| 1.1 | Posing a map prop with a multi-frame `defaultanim` always fails: stale 3-arg call | `models/prop_animations.py:90` | Call the new signature; return real Actions |
| 1.2 | `import_animations_to_armature` returns `[factory]`; `_create_action` returns `None` | `models/import_animations.py` | Return the created Actions |
| 1.3 | Alpha test, decal, additive and GoldSrc transparency are gated behind `if not is_blender_4_3()`, so nothing is set on 5.2 | ~25 shader files, `goldsrc/bsp/import_bsp.py:388` | Route everything through `ShaderBase.set_blend_mode()`; `CLIP` inserts a Math *Greater Than* node using the threshold |
| 1.4 | Skybox "no shadow" was assumed impossible on 4.3+ | `lightmap_generic.py:210` | Light Path *Is Shadow Ray* → Transparent mix |
| 1.5 | `vtf_export` is never removed from the Image menu (stale draw func, duplicates) | `bindings.py` | Remove it in `unregister()` |
| 1.6 | `Scene.import_physics` is never unregistered | `attributes/__init__.py` | `del` it |
| 1.7 | DMX session importer is registered but cannot run (`load_session` and `get_directory` are missing) | `operators/source1_operators.py:148` | Unregister it until it is implemented |
| 1.8 | VTF import leaks a file handle; unformatted report string; progress bar stuck on error | `source1_operators.py:185,460`, `shared_operators.py:81` | Context manager, f-string, try/finally |
| 1.9 | `csgo_weapon.py` is missing `import bpy` | `material_loader/shaders/source2_shaders/csgo_weapon.py` | Add the import |

## Phase 2 — Remove Blender 4 code paths

- Delete `is_blender_4*`/`is_blender_5` helpers and every branch (~60 call sites): `use_auto_smooth`, `shadow_method`, `material.use_nodes`, the legacy `node_group.inputs.new`, and the non-channelbag path in `ActionCurveFactory`.
- Replace deprecated material properties: `use_screen_refraction` → `use_raytrace_refraction`, `show_transparent_back` → `use_transparency_overlap`, `blend_method` → `surface_render_method` (via `set_blend_mode`).
- Replace `ShaderNodeMixRGB` (legacy) with `ShaderNodeMix` (`data_type='RGBA'`) through one helper on `ShaderBase`.
- Remove the 4.x-only `get_directory()` fallback and the conditional `directory` property in `operator_helper.py`; register the file handlers unconditionally.

## Phase 3 — Performance (benchmarked on 5.2.2)

| Change | Before | After |
|--------|--------|-------|
| Custom normals: `custom_normal` FLOAT_VECTOR attribute on POINT domain instead of `normals_split_custom_set_from_vertices` | 0.396 s | 0.001 s (717k tris) |
| Vertex weights: batch `VertexGroup.add` by (bone, weight) instead of one call per vertex | 0.25 s | 0.05 s (360k verts) |
| Keyframe interpolation: `foreach_set("interpolation")` instead of a per-key `setattr` | per-key Python | single call |
| `FastMesh.from_pydata(shade_flat=False)` where the faces are set smooth right after | double write | single write |

## Phase 4 — Packaging as a 5.2 extension

- Add `blender_manifest.toml`: `blender_version_min = "5.2.0"`; platforms windows-x64, linux-x64, macos-x64, macos-arm64; `files` permission.
- Fix the module alias in `__init__.py` (`sys.modules[__name__]` instead of a lookup by folder name, which breaks under `bl_ext.*`).
- Bump the version floor checks and `bl_info["blender"]` to 5.2.0.

## Phase 5 — Features

- Put delta animations on NLA strips with `blend_type='COMBINE'` automatically.
- File handlers for `.vmdl_c`, `.vphys_c`, `.dmx` (camera); fix the copy-pasted handler labels.
- Collapsible `layout.panel()` sections in the import dialogs.

Deferred (larger, needs test assets): replacing `bpy.ops` mode switching in MDL armature builders, the CLI import command, bone collections and colours, `Object.visible_shadow` for tool and sky geometry.

## Execution / ownership

To avoid edit conflicts, file ownership is split, and everyone works in the same tree:

| Owner | Files |
|-------|-------|
| Agent A (Sonnet 5.5) | `blender_bindings/material_loader/**`, `goldsrc/bsp/import_bsp.py` material block — Phases 1.3, 1.4, 1.9, 2 (materials, MixRGB) |
| Agent B (Sonnet 5.5) | `blender_bindings/models/**` except `import_animations.py` and `prop_animations.py`; `source2/vmdl_loader.py`; `utils/fast_mesh.py` — Phases 2 (meshes) and 3 (normals, weights, `shade_flat`) |
| Lead | `__init__.py`, `bindings.py`, `attributes/`, `operators/`, `utils/bpy_utils.py`, `import_animations.py`, `prop_animations.py`, `source1/bsp/import_bsp.py`, manifest — Phases 1.1, 1.2, 1.5–1.8, 2 (operators/animation), 4, 5 |

The lead deletes the `is_blender_*` helpers last, after a grep shows no remaining users.

## Real-asset tests

```
"<blender>/5.2/python/bin/python.exe" -I tests/fetch_samples.py        # 76 files, 16 MB, hash-verified, into git-ignored samples/
blender -b --factory-startup --python tests/blender_tests/run_sample_imports.py -- [--filter TEXT] [--json report.json]
```

Baseline (`master` and the branch before fixes): 59 PASS / 4 WARN / 8 FAIL.
After the importer fixes: **66 PASS / 5 WARN / 0 FAIL**. All other samples are identical to `master`.

WARN (expected: game content isn't present): `dm_lockdown.bsp` (HL2 materials), `rot_main.bsp` (HL2 skybox), GoldSrc `test1-3.bsp` (external WAD textures).

Fixed (all pre-existing on `master`):
| Sample | Was | Fix |
|--------|-----|-----|
| `source1/maps/rot_main.bsp` | `'NoneType' object has no attribute 'surf_edges'` | Brush entities in maps without face/edge lumps get an empty mesh instead of crashing (`abstract_entity_handlers.py`) |
| `goldsrc/models/cube-tex.mdl` | `struct.error` reading past EOF | Animation RLE header read as two unsigned bytes; runs of ≥128 frames used to go negative. Also guards `total == 0` (infinite loop) and empty first runs (`library/models/mdl/v10/mdl_file.py`) |
| `source2/models/empty_vertex_buffer.vmdl_c` | `no field of name POSITION` | Draw calls whose vertex buffer has no POSITION are skipped with a warning (`vmdl_loader.py`) |
| `source2/textures/R32F`, `RG1616` | reshape errors | Table-driven decode for R8/R16/RG1616/RGBA16161616/R16F/RG1616F/R32F/RG3232F/RGB323232F/RGBA32323232F, plus BGRA8888, IA88, A8, R32_UINT (VRF channel mapping) |
| `source2/textures/PNG_DXT5_*`, `WEBP_RGBA8888` | `KeyError` / `29 is not a valid VTexFormat` | Added enum values 29–33 with a default block size; the embedded PNG/JPEG/WEBP file is packed into Blender directly |
| `source2/maps/small_map_with_material.vpk` | assertion, map not found | Falls back to any `*.vmap_c` in the VPK; physics paths follow the found map |

Found while verifying the textures (also pre-existing): every HDR texture (BC6H, RGBA16161616F) imported with reversed channels, because the native EXR writer stores values in EXR's alphabetical channel order (A, B, G, R). In-memory HDR images were also created as 8-bit sRGB placeholders. Both fixed in `texture_utils.py`, and float formats (R16F…RGBA32323232F) now use the HDR path. Verified pixel-exact for BC6H and RGBA16161616F (memory and disk cache), and within half-float precision for R32F.

Still needs game installs: animated or flexed Source 1 models (TF2 / Source SDK Base 2013, incl. `dog_animations.mdl` for `tests/animation_tests`), real CS2/Dota 2 maps.

## Status

- [x] Phase 1 — all items; also fixed the GoldSrc `ActionCurveFactory` callers in mdl4/6/10 (broken by the same overhaul) and the old Principled socket names (`Specular`, `Transmission`, `Emission`) that fail on 5.2
- [x] Phase 2 — no `is_blender_*`, `shadow_method`, `use_auto_smooth`, `ShaderNodeMixRGB` or deprecated material properties remain; helpers deleted
- [x] Phase 3 — free normals, batched weights, `shade_flat=False`, vectorized keyframes (old vs new verified equal)
- [x] Phase 4 — manifest validates; all 4 platform packages build; installs and enables as `bl_ext.user_default.sourceio`
- [x] Phase 5 — delta → NLA COMBINE option, new file handlers, collapsible MDL import dialog (verified in the 5.2.2 UI)

Follow-ups found during execution:
- `models/mdl10/import_mdl.py` only imports the `walk1` sequence (an upstream WIP filter, left as-is).
- Every platform zip still bundles all three native libs; per-platform `paths_exclude_pattern` would cut ~2/3 of the native payload.
- The extension still relies on a top-level `SourceIO` alias in `sys.modules`; a full switch to relative imports would be the extension-guideline-clean fix.
