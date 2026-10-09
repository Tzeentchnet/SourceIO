# SourceIO → Blender 5.2 modernization plan

Target: **Blender 5.2.2 LTS only.** Blender 4.x compatibility is dropped.
Branch: `master`. Round 1 was done on `blender-5.2-modernization`, rounds 2–4 on `continue-work` (now parked at the same commit as `master`).

All API claims below were checked against Blender 5.2.2 running headless (`D:/Blender Foundation/Blender 5.2/blender.exe -b`).

## Next

Start here in a new session. Keep this section current: when an item is done, record the result in that round's section below, remove it here, and add anything found along the way.

State (2026-10-08): rounds 3–7 are released as [5.7.0-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.7.0-blender5.2), round 8 as [5.7.1-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.7.1-blender5.2), and round 9 (AnimGraph 2 clips) as [5.7.2-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.7.2-blender5.2); and rounds 10 (relative imports), 11 (clip events, small fixes), 12 (upstream #477 adaptations) and 13 (flex sliders, rim light, custom normals) as [5.8.0-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.8.0-blender5.2) (release notes and README follow the 5.6.0 layout; packages from `tools/build_extension.py`). Rounds 14 (CS2 materials, KNOWN sample status) and 15 (CS2 texture gaps) are on `master`, not released. TF2 (`E:/SteamLibrary/steamapps/common/Team Fortress 2/tf`) and CS2 (`E:/SteamLibrary/steamapps/common/Counter-Strike Global Offensive/game/csgo`; maps ship as `maps/<name>.vpk`) are installed; no Dota 2.

Checks, with the current baseline:

```
cd D:/Github && "D:/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe" -m pytest SourceIO/tests -q -p no:cacheprovider --ignore=SourceIO/tests/blender_tests   # 265 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_armatures', argv=['x'], exit=False)"   # 10 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_flex_controllers', argv=['x'], exit=False)"   # 10 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_skins', argv=['x'], exit=False)"   # 7 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_material_paths', argv=['x'], exit=False)"   # 7 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_custom_normals', argv=['x'], exit=False)"   # 3 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_materials', argv=['x'], exit=False)"   # 15 pass
blender -b --factory-startup --python tests/blender_tests/run_sample_imports.py                                      # 87 PASS / 6 KNOWN / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/blender_tests/run_renamed_smoke.py                                       # 442/442 modules, register/unregister/register OK
blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- --game "<TF2>/tf"                   # 13 PASS / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- --game "<CS2>/game/csgo"            # 12 PASS / 0 WARN / 0 FAIL
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --map de_dust2 --load-placeholders                     # PASS, 570/570 placeholders (135 s, 26 "Unused texture")
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --model <each default model> --clips "*"               # 9 PASS, agents 2089 clips each (~55 s each)
```

### Actionable

1. **Source 2 texture gaps** (rounds 14 and 15). `tools/shader_params.py` prints a CS2 shader's parameter defaults and texture packing; start there. Left:
   - `csgo_character` SSS mask, diffuse falloff, eye albedo/mask, iridescence. Anisotropic gloss is approximated by the average of its two roughnesses (Blender's anisotropy needs a tangent).
   - `generic.vfx` `g_tRoughness`/`g_tMetalnessReflectanceFresnel` with their ranges (6 materials).
   - `csgo_lightmappedgeneric` ignores the per-layer UV transforms (`g_vLayer1TexCoordScale`/`Center`, `g_flLayer1TexCoordRotation`, and the `Normal` variants; 12–21 materials each, mostly identity).
   - `csgo_vertexlitgeneric` detail picks `TEXCOORD_1` by `F_SECONDARY_UV`, a combo that shader doesn't have; its `g_bUseSecondaryUvForDetailTexture` defaults to 1. Unchecked against a model.
   - Unverified: with `F_USE_ALBEDO_FOR_TRANSMISSIVE` the transmission color is taken as the albedo alone (the material's texture is then the default grey, which might multiply it); decals are blended before the tint mask applies.
2. **Smaller CS2 follow-ups** (round 8): `skybox_reference` entities don't import the 3D skybox; unhandled CS2 entities include `env_particle_glow`, `hostage_entity`, `point_perfcapture`, team intro points.
3. **Clip follow-ups** (rounds 9 and 11):
   - Event markers sit at the start frame only; durations (ID events, material attribute and float curve events) and the other fields (attachments, sound positions, curves) aren't kept.
   - Weapon viewmodel clips can't be found from any model: the viewmodel graph pulls them in at runtime through `m_externalGraphSlots` (per-weapon graphs such as `viewmodel_inspects.vnmgraph+ak47.vnmgraph`). The clip importer handles them once extracted; finding them automatically needs whatever ties a weapon to its graphs (item schema or weapon vdata, unverified).
   - The clip importer (`sourceio.vnmclip`) treats the armature's rest pose as Source bone orientations. That holds for SourceIO's Source 2 armatures; other rigs would need their bone orientation corrected.
4. **Flex panel with the armature active** (round 13): the panel and its operators act on `context.object`, so it only shows with the face mesh active. Following an active armature to its flexed child mesh means giving the operators a target object.
5. **Upstream #477 leftovers** (round 12, deferred on purpose): moving the scene settings into one `Scene.sourceio_props` group needs a versioned migration (old .blend values are otherwise lost: tested in the review) and compatibility for mounted-resource collections. Upstream's automatically embedded flex UI script (`Text.use_module`, runs only with auto-execution on) could come back only as an explicit *Embed standalone flex UI* action.

### Blocked on assets

- **Bone collections for the other builders** (optional, no samples): `mdl36` and `mdl2531` read the same `Bone` struct, so `assign_bone_collections` would work there too, but their flags are unverified. GoldSrc and Source 2 have no USED_BY flags; they could get side colours only.
- **GoldSrc v4/v6 animations** (suspected, no samples): `load_animations` keys each frame's parent-relative position and rotation straight onto the pose bones, whose rest pose already holds the bind transform, so the two would add up. v10 was made rest-relative in round 2; v4/v6 may need the same.
- **External mesh morph atlas** (unverified, round 11): `load_external_mesh` resolves `m_pTextureAtlas` against the model resource, as before; it may belong to the mesh or morph set resource. No CS2 or sample model uses this path.
- **Source 2 flex/morph animation channels**: the legacy ANIM path still skips `MorphChannel` segments; CS2 clips carry no float channels, so a model with legacy morph animation is needed to test it.
- **Clip float curves** (`m_floatCurveIDs`, VRF's newer format) aren't decoded; no CS2 clip has any.
- **v49 FRAMEANIM against a real model** (round 6): needs a v49 game (CS:GO, L4D2, Portal 2, SFM; CS2 ships only Source 2 content).

Not needed to import clips: bone masks (`m_maskDefinitions` in a `.vnmskel`) and pose-parameter blending are graph-evaluation features.

### Future work

The Source 1 / Source 2 export plan in [TODO.md](TODO.md); not started.

## Verification

Every change must pass the unit tests and `run_renamed_smoke.py` from the checks under Next. The smoke test loads the shipped files under another package name, imports every module, then runs register → unregister → register; it exits with 1 on any failure. Run the sample, game and Blender test suites when the change touches what they cover.

Real-asset samples (hash-verified downloads into the git-ignored `samples/`):

```
"<blender>/5.2/python/bin/python.exe" -I tests/fetch_samples.py
blender -b --factory-startup --python tests/blender_tests/run_sample_imports.py -- [--filter TEXT] [--json report.json]
```

## Round 1 (branch `blender-5.2-modernization`)

All five phases are done. Phase 1 also fixed the GoldSrc `ActionCurveFactory` callers in mdl4/6/10 (broken by the same overhaul) and the old Principled socket names (`Specular`, `Transmission`, `Emission`) that fail on 5.2. Before round 1 the smoke test failed on the missing `import bpy` in `csgo_weapon.py` (1.9) and the duplicate `vtf_export` Image menu entry (1.5).

### Phase 1 — Bugs users hit

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

### Phase 2 — Remove Blender 4 code paths

No `is_blender_*`, `shadow_method`, `use_auto_smooth`, `ShaderNodeMixRGB` or deprecated material properties remain; the helpers are deleted.

- Deleted the `is_blender_4*`/`is_blender_5` helpers and every branch (~60 call sites): `use_auto_smooth`, `shadow_method`, `material.use_nodes`, the legacy `node_group.inputs.new`, and the non-channelbag path in `ActionCurveFactory`.
- Replaced deprecated material properties: `use_screen_refraction` → `use_raytrace_refraction`, `show_transparent_back` → `use_transparency_overlap`, `blend_method` → `surface_render_method` (via `set_blend_mode`).
- Replaced `ShaderNodeMixRGB` (legacy) with `ShaderNodeMix` (`data_type='RGBA'`) through one helper on `ShaderBase`.
- Removed the 4.x-only `get_directory()` fallback and the conditional `directory` property in `operator_helper.py`; the file handlers register unconditionally.

### Phase 3 — Performance (benchmarked on 5.2.2; old vs new verified equal)

| Change | Before | After |
|--------|--------|-------|
| Custom normals: `custom_normal` FLOAT_VECTOR attribute on POINT domain instead of `normals_split_custom_set_from_vertices` (**reverted in round 13**: free normals don't follow deformation) | 0.396 s | 0.001 s (717k tris) |
| Vertex weights: batch `VertexGroup.add` by (bone, weight) instead of one call per vertex | 0.25 s | 0.05 s (360k verts) |
| Keyframe interpolation: `foreach_set("interpolation")` instead of a per-key `setattr` | per-key Python | single call |
| `FastMesh.from_pydata(shade_flat=False)` where the faces are set smooth right after | double write | single write |

### Phase 4 — Packaging as a 5.2 extension

The manifest validates, all 4 platform packages build, and the add-on installs and enables as `bl_ext.user_default.sourceio`.

- `blender_manifest.toml`: `blender_version_min = "5.2.0"`; platforms windows-x64, linux-x64, macos-x64, macos-arm64; `files` permission.
- Version floor checks and `bl_info["blender"]` bumped to 5.2.0.
- The `sys.modules` alias fix from this phase was superseded in round 10: the add-on now uses relative imports and has no alias.

### Phase 5 — Features (verified in the 5.2.2 UI)

- Delta animations go on NLA strips with `blend_type='COMBINE'` (option).
- File handlers for `.vmdl_c`, `.vphys_c`, `.dmx` (camera); the copy-pasted handler labels are fixed.
- Collapsible `layout.panel()` sections in the MDL import dialog.

The deferred items are all done: `bpy.ops` mode switching (round 7), bone collections and colours (round 5), the CLI command (round 2), the `walk1`-only mdl10 import and per-platform zips (round 2). `Object.visible_shadow` was dropped because material-level shadow disabling already covers sky materials.

### Sample fixes

Sample suite: 59 PASS / 4 WARN / 8 FAIL before, **66 PASS / 5 WARN / 0 FAIL** after (the other samples unchanged). The WARN are missing game content: `dm_lockdown.bsp` (HL2 materials), `rot_main.bsp` (HL2 skybox), GoldSrc `test1-3.bsp` (external WAD textures).

| Sample | Was | Fix |
|--------|-----|-----|
| `source1/maps/rot_main.bsp` | `'NoneType' object has no attribute 'surf_edges'` | Brush entities in maps without face/edge lumps get an empty mesh instead of crashing (`abstract_entity_handlers.py`) |
| `goldsrc/models/cube-tex.mdl` | `struct.error` reading past EOF | Animation RLE header read as two unsigned bytes; runs of ≥128 frames used to go negative. Also guards `total == 0` (infinite loop) and empty first runs (`library/models/mdl/v10/mdl_file.py`) |
| `source2/models/empty_vertex_buffer.vmdl_c` | `no field of name POSITION` | Draw calls whose vertex buffer has no POSITION are skipped with a warning (`vmdl_loader.py`) |
| `source2/textures/R32F`, `RG1616` | reshape errors | Table-driven decode for R8/R16/RG1616/RGBA16161616/R16F/RG1616F/R32F/RG3232F/RGB323232F/RGBA32323232F, plus BGRA8888, IA88, A8, R32_UINT (VRF channel mapping) |
| `source2/textures/PNG_DXT5_*`, `WEBP_RGBA8888` | `KeyError` / `29 is not a valid VTexFormat` | Added enum values 29–33 with a default block size; the embedded PNG/JPEG/WEBP file is packed into Blender directly |
| `source2/maps/small_map_with_material.vpk` | assertion, map not found | Falls back to any `*.vmap_c` in the VPK; physics paths follow the found map |

Found while verifying the textures (also pre-existing): every HDR texture (BC6H, RGBA16161616F) imported with reversed channels, because the native EXR writer stores values in EXR's alphabetical channel order (A, B, G, R). In-memory HDR images were also created as 8-bit sRGB placeholders. Both fixed in `texture_utils.py`, and float formats (R16F…RGBA32323232F) now use the HDR path. Verified pixel-exact for BC6H and RGBA16161616F (memory and disk cache), and within half-float precision for R32F.

## Round 2 (2026-10-07, branch `continue-work`)

| Item | Result |
|------|--------|
| Source 2 animations (TODO) | Ported from VRF (MIT) into `library/source2/animation/` and `blender_bindings/source2/animation_loader.py`. All 12 segment decoders; embedded, `.vagrp`/`.vanim`, include-model and NTRO sources; delta, looping and root motion. Compared with the VRF CLI 20.0 glTF export: 192/192 animations across 9 models, max error 9.7e-5 units / 4.2e-5°. Opt-in through *Import animations* on the VMDL importer. |
| KV3 v2 multi-frame zstd | `binary_keyvalues.py` decodes every zstd frame. The axolotl sample's ANIM block now loads (11/11 animations match VRF). |
| NTRO external references | `_read_ex_ref` falls back to the 32-bit key the RERL table uses. The hand_l_v3 references resolve: 8/8, previously all null. |
| GoldSrc animations | Every embedded sequence imports as a rest-relative action and respects *Load animations*. Pose matches direct FK from the MDL data to 5e-7. |
| ETC2 / ETC2_EAC / R11_EAC / RG11_EAC | NumPy EAC decoder, native ETC2 colour decoding, block-compressed mip sizes for EAC. All 34 VRF texture fixtures import. |
| Test runner pixel checks | Every VTF/VTEX import is compared against SourceIO's decode (bottom-up rows, 8-bit or half-float tolerance). Reintroducing the old HDR channel bug makes BC6H and RGBA16161616F FAIL. |
| Per-platform packages | `tools/build_extension.py`; each zip carries one native lib (Windows 3.4 MB, previously 7.1 MB). Installs and imports. |
| CLI | `blender -c sourceio import ...` (`blender_bindings/cli.py`). Tested through the installed extension: imports, `--output` .blend, exit codes. |
| Samples | 99 files (20 more VRF textures, 3 animation samples). Suite: **88 PASS / 5 WARN / 0 FAIL**; the 5 WARN are missing game content. |

Remaining: Source 2 flex/morph animation channels, AnimGraph 2 (`.vnmclip_c`), bone masks and pose-parameter blending; ~~decals and overlays~~ (Source 1 overlays: round 4); ~~bone collections and colours~~ (round 5); ~~`bpy.ops` mode switching in the armature builders~~ (round 7); ~~relative imports for the extension~~ (round 10); testing against CS2/Dota 2 installs.

## Round 3 (2026-10-08, TF2 install)

```
blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- --game "<steam>/common/Team Fortress 2/tf" [--include-animations] [--model PATH] [--map NAME]
```

Models are read through the game's search paths (VPKs included), with the game mounted during import. Default set: heavy, HWM heavy, scout, spy, minigun, sentry3, bot_heavy, resupply locker, HL2 dog; maps ctf_2fort, cp_badlands, koth_harvest_final, pl_upward. Result: **13 PASS / 0 WARN / 0 FAIL** (was 11 PASS / 2 WARN, with wrong materials on spy and sentry). With `--include-animations`: dog 134 animations (1 + 116 + 9 + 8, matching the MDL headers), heavy 902. HWM heavy: 647 shape keys, 653 flex drivers.

| Bug | Fix |
|-----|-----|
| Sectioned animations left `[0,0,0,0]` rotations wherever a section didn't list a bone (dog 122 tracks, heavy 178, scout 94, sentry3 17) | Unlisted frames hold the rest pose, or identity in delta animations; an empty section still advances the frame offset (`local_animation.py`). No non-unit quaternions remain in any TF2 class animation file |
| `$bottommaterial "water/x.vmt"` is copied into the BSP verbatim, so SourceIO looked up `x.vmt.vmt`; the underwater faces weren't skipped and got an empty material | `strip_vmt_extension()` for BSP texdata, brush models, rope and infodecal materials |
| `ContentManager.check()` was case-sensitive for VPKs while `find_file()` wasn't (`sentry3/Sentry3` missed) | `VPKContentProvider.check` retries lowercase |
| MDL material names like `/../../effects/invulnfx_red` made an absolute path | Join cdmaterials and name, then normalize `..`, as the engine does (`models/materials.py`) |

`tests/animation_tests` used made-up counts and the old array `frames` API; it now checks the counts against the MDL headers (HL2 `dog_*` from TF2's `hl2_misc_dir.vpk`), the per-bone dict, and degenerate rotations: 11 pass. Unit tests: 179 pass. Sample suite unchanged: 88 PASS / 5 WARN / 0 FAIL.

Not done: ~~`tf/custom/*` wildcard search paths are still skipped with a warning~~ (round 4); `_read_frame_animations` (v49+ FRAMEANIM) asserts constant and per-frame data never mix.

## Round 4 (2026-10-08)

| Item | Result |
|------|--------|
| `custom/*` wildcard search paths | Every subfolder and VPK is mounted ahead of the game, in alphabetical order, as the engine does. Split archives mount through `_dir.vpk`; the `_NNN.vpk` chunks are skipped. `tests/content_manager` (3 tests, using a generated VPK v1) fails on the old code. TF2: `tf/custom/workshop` now mounts; still 13 PASS / 0 WARN / 0 FAIL. Unit tests: 182 pass. |
| Overlays (`info_overlay`) | Previously not imported at all: VBSP compiles them into `LUMP_OVERLAYS` and drops the entities. `library/source1/bsp/geometry.py` clips each overlay quad (as two triangles, so texture coordinates are exact) against the brush faces and displacement triangles it lists, then lifts it 0.1 unit off the surface for each render order. Imported into an `overlays` collection with *Load overlays* (default on): 2fort 236/237, Badlands 124/124, Harvest 286/286, Upward 156/156 (the missing 2fort one is on a face perpendicular to it). Conventions checked on the maps: UV point x is U and y is V (the old unused `Overlay.plane` swapped them); V = normal x U, negated by the flip flag; Hammer's U can lean out of the plane and is projected onto it; a face's plane already faces front (`side` is relative to its node). Rendered signs read correctly, flipped ones included. |
| Displacements built from the wrong corner | `import_disp` matched the start position with `np.isclose(..., 0.5e-2)`, which is a *relative* 0.5%, so far from the origin it took a neighbouring corner and rotated the grid. Wrong on Badlands 54/1191, 2fort 2/232, Harvest 2/533. Now the nearest corner, through the shared `displacement_mesh()` (also vectorized). |

`tests/bsp_tests` (12 tests on a stub BSP): basis and flip, clipping, wrapping onto a second face, draping over a displacement, the start-corner case. Unit tests: 194 pass. Samples: 88 PASS / 5 WARN / 0 FAIL (`dm_lockdown` imports its 3 overlays). TF2: 13 PASS / 0 WARN / 0 FAIL.

## Round 5 (2026-10-08)

| Item | Result |
|------|--------|
| Bone collections and colours (Source 1 v44–v52) | `create_armature` in `models/mdl44/import_mdl.py` sorts every bone into a collection by what the engine uses it for: *Deform* (any USED_BY_VERTEX flag), *Procedural* (a procedural rule or ALWAYS_PROCEDURAL; checked first, since helpers skin vertices too), *Bone merge* (weapon/prop/cosmetic attach points), *Attachments* (only carries attachment points), *Other*. Deform bones are coloured by side (left THEME04 blue, right THEME01 red, centre THEME09 yellow); the others by role (procedural purple, bone merge green, attachments teal). The side comes from a whole L/R/Left/Right name token or a CamelCase Left/Right word, so `S2midHousingL` stays centre. Classification is `Bone.role` / `bone_side()` in `library/models/mdl/structs/bone.py`. TF2 heavy: Deform 60, Procedural 2 (`hlp_forearm_*`), Bone merge 15, Attachments 2 (`effect_hand_*`). |
| `Bone.procedural_rule_type` | Held the rule object instead of the type number (wrong argument in `from_buffer`). |

`tests/mdl_tests` (18 tests): side detection, role priority, and the roles and counts of the sample `dog.mdl`. `run_game_imports.py` now reports bone collection counts per model. Unit tests: 212 pass. Samples: 88 PASS / 5 WARN / 0 FAIL. TF2: 13 PASS / 0 WARN / 0 FAIL; scout 59/2/15/2, spy 68 deform / 7 bone merge / 9 attachments, sentry3 26/9, bot_heavy 54 deform / 3 bone merge / 12 other (hitbox-only bones), dog 49/7/2 (procedural tricep/elbow helpers).

## Round 6 (2026-10-08)

| Item | Result |
|------|--------|
| v49+ FRAMEANIM, mixed constant and per-frame data | `_read_frame_animations` asserted that a section has either constants or per-frame data. studiomdl writes constants for every bone that holds still over a section and per-frame data for the rest, so nearly every real frame animation mixes them. Rewritten to read both: each bone stores rotation then position (ROT2 before ROT, full-float before half-float position), constants once per section, frames at `frame_offset + i * frame_length` (the extra overlap frame of non-last sections is ignored). A channel with no flag holds the rest pose (identity in delta animations); previously a rotation-only bone got position (0, 0, 0). The per-frame block is decoded with NumPy (`decode_quat48`, `decode_quat48s`); the Quat48/Quat48S `sqrt` is clamped against rounding. A short `frame_length` raises `ValueError`. |

Layout references: Crowbar's v49 reader and PulseModel's studiomdl-style writer and decoder (both open source). TF2 has no frame animations: its VPKs hold only v44–v48 models (13493 v48), and no loose MDLs exist, so `tests/mdl_tests/test_frame_anim.py` builds the blocks the way studiomdl does: mixed per-bone flags, legacy Quaternion48 and Quaternion48S, half- and full-float positions, delta, and 10 frames in 4 sections with different flags per section. 4 of its 9 tests fail on the old reader. Unit tests: 221 pass. Samples: 88 PASS / 5 WARN / 0 FAIL. TF2: 13 PASS / 0 WARN / 0 FAIL. Not checked against a real v49 model (CS:GO, L4D2, Portal 2 or SFM content would do).

## Round 7 (2026-10-08)

| Item | Result |
|------|--------|
| `bpy.ops` mode switching in the armature builders | Bones can only be created in edit mode, and only `bpy.ops.object.mode_set` enters it (checked on 5.2.2: a `temp_override` is ignored, since edit mode follows the view layer's active object and also takes in every other selected armature). All edit-mode use now goes through one context manager, `edit_armature()` in `utils/bpy_utils.py`: it makes the armature the only selected, active object, links it to the scene for the duration if needed, and restores object mode, the selection and the active object afterwards, also on error. Previously an import with another armature selected put that one in edit mode too, and a failure inside a builder left Blender in edit mode. Every other mode switch is gone: mdl4/6/9/36/2531 no longer go through pose mode and `pose.armature_apply()` but set each edit bone's matrix to its accumulated MDL transform (as mdl10 and mdl44 already did), and keyframing (mdl4/6) and the GoldSrc/Source 2/GLM builders no longer switch modes at all. The only `mode_set` calls left are in `edit_armature`. |
| *Load Ref pose* (v44–v52) | Always failed on a model with local animations (TF2 heavy, sentry3: `'NoneType' object has no attribute 'parent'`) and left Blender in edit mode: it read `pose.bones` while still in edit mode, and indexed the per-bone animation dict from round 3 as a frame list. Now applied after edit mode by `set_pose()` (`models/import_animations.py`) in the bone spaces the animation importer keys in, root correction included, and skipped for a delta animation. TF2 heavy (`@ref`), scout and sentry3 (`@idle_off`): equal to frame 0 of the same animation imported as an action, to 1.7e-5. |
| `import_static_animations` (mdl44) | Removed: it had no callers, used pose mode, and indexed animations the same broken way. |

Checked by importing every sample and TF2 model before and after and comparing each armature: parents, rest matrices, collections, colours, rotation modes, pose, mesh vertices and actions are identical (bone lengths differ by float rounding). The importers now leave the user's active object as it was instead of making the armature active. mdl4/6/9/36/2531 have no samples, so the old (from git) and new builders were run side by side on random 14-bone skeletons at four scales: equal to float32 precision, and equal to the transforms the vertices are built with. `tests/blender_tests/test_armatures.py` (10 tests, run inside Blender): the helper keeps another selected armature out of edit mode and restores selection, also when the block raises; each builder's rest pose equals the accumulated MDL transforms (mdl4/6/9/10/36/2531, v6 rotation mode); `set_pose` gives the expected pose-space matrices. Unit tests: 221 pass. Samples: 88 PASS / 5 WARN / 0 FAIL. TF2: 13 PASS / 0 WARN / 0 FAIL. Smoke test (import every module, register → unregister → register): clean.

## Round 8 (2026-10-08, CS2 install)

```
blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- --game "<steam>/common/Counter-Strike Global Offensive/game/csgo" [--include-animations] [--load-placeholders] [--model PATH] [--map NAME]
```

`run_game_imports.py` now also takes a Source 2 game (a folder with `gameinfo.gi`): `.vmdl_c` models go through the VMDL importer (`--include-animations` turns on *Import animations*), maps are the `maps/<name>.vpk` files through the VPK map importer. A Source 2 map import only places empties, world geometry included, so `--load-placeholders` then runs *Load Entity* on every placeholder, as a user would. Default CS2 set: agents `ctm_sas` and `tm_phoenix`, `glove_hardknuckle` arms, AK-47, Glock, Karambit, chicken, hostage, a dust crate; maps de_dust2, de_inferno, cs_office. `characters/models/*` in CS2 are stubs with a placeholder orange material; the real agents are under `agents/models/`.

Result: **12 PASS / 0 WARN / 0 FAIL**. Agents import 9 meshes each; `tm_phoenix` has all 3 body morphs (its only flexes). With `--include-animations`: chicken 21 actions, AK-47 5, agents 2 (the rest live in AnimGraph 2 clips, Next item 1). With `--load-placeholders`: dust2 570/570 placeholders, 3597 meshes, 277 materials (129 s); inferno 1336/1336, 6797 meshes, 354 materials (379 s); office 636/636, 2714 meshes, 259 materials (64 s); no errors. Before the fixes: inferno WARN with 33 failed lights, dust2 WARN with a failed material and an unloadable placeholder.

| Bug | Fix |
|-----|-----|
| Every spot-shaped `light_omni2` failed (inferno: 33) | `parse_float_vector` returned the KV3 array itself, a read-only view of the file, and the handler adjusts the angles in place. It now returns a copy (`abstract_entity_handlers.py`) |
| Self-illuminated `csgo_vertexlitgeneric` and `csgo_static_overlay` materials failed entirely (dust2 hanging lights) | They set `g_vSelfIllumTint`/`g_flSelfIllumScale` on the `csgo_complex.vfx` group, whose inputs are `SelfIllumTint`/`Emission Strength` (as `csgo_complex.py` already used). A script that checked every `shader.inputs[...]` name against the bundled node groups found one more: `csgo_environment_blend` set `g_flDetailBlendFactor`, which `csgo_lightmappedgeneric.vfx` doesn't have, so any material with a shared colour overlay failed |
| Normal maps reconstructed with NaN Z (AK-47 normal map) | `_normalize` took `sqrt` of a negative value where X²+Y² > 1 after compression; clamped to 0 |
| Entities without a model got `prop_path = error.vmdl_c`, which never loads (dust2 `skybox_reference`) | No `prop_path` when there is no model or it is the engine's `error.vmdl` stand-in |
| The Dota 2 detector claimed a CS2 install (and tagged it with the CS:GO app ID) | `backwalk_file_resolver` also accepts a bare `pak01_dir.vpk`, which every Source 2 game has; the detector now requires the `dota` folder |
| External meshes (`m_refMeshes`): a mesh's `m_morphSet` never loaded, a mesh without MRPH would crash, a missing morph texture crashed | `if morph_set_path := ... is not None` assigned the boolean; `morph_block, = get_block(...)` unpacked a single block; no `None` check (`vmdl_loader.load_external_mesh`). CS2 doesn't use this path (agents embed their meshes); the Source 2 samples still pass |

New tests: `tests/content_manager/test_detectors.py` (fails on the old Dota 2 detector), `tests/texture_tests/test_normal_reconstruction.py` (raises on the old `_normalize`). Unit tests: 225 pass. Samples: 88 PASS / 5 WARN / 0 FAIL. TF2: 13 PASS / 0 WARN / 0 FAIL.

Visual check: cs_office imported with every placeholder loaded and saved with all 733 textures packed (`E:/Tests/cs_office.blend`, 786 MiB uncompressed; the importer already packs the images it creates). The user inspected it in Blender and judged the import acceptable.

## Round 9 (2026-10-08, AnimGraph 2 clips)

| Item | Result |
|------|--------|
| Clip decoder | `library/source2/animation/clip.py`, ported from VRF `ModelAnimation2/AnimationClip.cs`, `ClipAnimation.cs` and `SkeletonRetargeter.cs` (MIT, fetched from VRF master on 2026-10-08). Every frame has the same word layout (per track: 3 rotation words, 3 translation, 1 scale, each left out when static), so all frames decode at once with NumPy from `m_compressedPoseOffsets`. Rotations drop their largest component, whose index is in the top bits of the first two words. NM skeletons (`.vnmskel_c`) give the bind pose (`m_parentSpaceReferencePose`, 8 floats: translation, scale, rotation). Additive clips add position and scale and post-multiply rotation over the bind pose. Root motion (`m_rootMotion.m_transforms`, one per frame) becomes position plus unwrapped yaw; unlike legacy movement it keeps vertical travel, so zeroing Z moved from the Blender side into `SequenceAnimation.decode`. All 2089 clips of an agent and 87 of the chicken decode (188k frames in about 26 s, unit quaternions throughout). |
| Retargeting | A clip plays on a model by bone name, matching world poses: mapped bones take the clip bone's world transform, others follow their parent at bind pose (VRF `SkeletonRetargeter`). The result is model-skeleton locals, so the existing action writer (`_create_action`, cloth re-parenting and root motion included) is reused. Of a clip's main and secondary track sets, the one that drives the most bones of the target is used: the AK-47 picks the `ak47.vnmskel` tracks of a menu clip. |
| Finding clips | `graph_clip_paths()` follows `m_animGraph2Refs` and nested `.vnmgraph` resources (VRF `AnimationGraphLoader`). ctm_sas: 4 graphs, 2089 clips (909 world, 628 viewmodel, 552 UI); chicken 87; weapons, arms, hostage and props have no graph. |
| UI | VMDL import: *Graph clips* (fnmatch patterns on the path, the path without extension or the file name, separated by commas or spaces; empty = none), used with *Import animations*. New `sourceio.vnmclip` operator (menu entry, and drag and drop onto a selected armature) imports clip files onto the active armature, reading the scale the armature was imported with (`import_scale`, now stored by `create_armature`), and shares the slot name of the armature's current action. CLI: `--clips PATTERNS`. `run_game_imports.py`: `--clips`, and a `clip_actions` count. Actions are named after the clip file, with parent folders added only where names collide (`world/chicken/chick_idle01` vs `viewmodel/chicken/chick_idle01`), and record `clip` and `clip_skeleton`. |

Checked against VRF CLI 20.0 glTF exports (additive composed): chicken 87/87 clips, max 8.1e-5 units / 0.0011°; ctm_sas, 50 clips picked across additive, root motion, viewmodel and UI, max 5.2e-5 units / 6.3e-5°; an AK-47 menu clip through both operators (`sourceio.vmdl`, then `sourceio.vnmclip`), max 2.9e-7 units / 0°. With `--clips "*"` the default CS2 models are 9 PASS: the agents import 2091 actions in 52–57 s, the chicken 108 in 6 s.

`tests/animation_tests/test_clips.py` (19 tests): a reference encoder for quaternions (every dropped-component slot), mixed static channels with shuffled frame offsets, short data, additive composition, retargeting through an extra bone and a different bind pose, yaw unwrapping, filters and names; plus the 5 VRF fixtures now in `fetch_samples.py` (`source2/clips`, which the sample runner doesn't import). Mutating the decoder (swapped index bits, ignored offsets, reversed additive order, unmapped bones not following the bind pose) fails them. Unit tests: 244 pass. Samples: 88 PASS / 5 WARN / 0 FAIL. Armature tests: 10 pass. TF2: 13 PASS / 0 WARN / 0 FAIL. CS2: 12 PASS / 0 WARN / 0 FAIL. Smoke test: clean.

## Round 10 (2026-10-08, relative imports)

| Item | Result |
|------|--------|
| Relative imports | Every `from SourceIO.x import y` in the shipped code (1607 imports in 384 files; there were no `import SourceIO.x` forms) is now package-relative, so the add-on works under whatever name Blender loads it as. The `sys.modules['SourceIO']` alias in `__init__.py` is gone. Done by a script that computes each import's relative form from the file's own package; every changed line is an import and line endings are unchanged. `tests/` and `tools/` keep absolute `SourceIO` imports: they run with `D:/Github` on `sys.path`, where `SourceIO` is the real package, and aren't shipped. The `__main__` dev blocks in a few `library` modules now need `python -m SourceIO....`. |

Checked by loading a copy of the shipped files as `bl_ext_test_sourceio` with an import hook that rejects any `SourceIO` import: 444/444 modules import, register → unregister → register is clean, and nothing named `SourceIO` ends up in `sys.modules` (`tests/blender_tests/run_renamed_smoke.py`, exit code 1 on any failure; it also replaces the old scratchpad smoke test). Also built the Windows package with `tools/build_extension.py` and installed it into an isolated `BLENDER_USER_RESOURCES`: it loads as `bl_ext.user_default.sourceio` without any `SourceIO` module, and `blender -c sourceio import` of `dog.mdl` and the axolotl `.vmdl_c` saves a .blend (2 imported, 0 failed). `tests/test_relative_imports.py` fails if a shipped file imports `SourceIO` absolutely again (checked by adding one). Unit tests: 245 pass. Armature tests: 10 pass. Samples: 88 PASS / 5 WARN / 0 FAIL. TF2: 13 PASS / 0 WARN / 0 FAIL. CS2: 12 PASS / 0 WARN / 0 FAIL.

## Round 11 (2026-10-08)

| Item | Result |
|------|--------|
| Clip events | `ClipEvent` (`library/source2/animation/clip.py`) reads a clip's `m_events`; each becomes a pose marker on its action, named `<kind>: <label>` at the frame nearest its start. Times are fractions of the clip: the largest start + duration over all 4028 events of an agent and the chicken is exactly 1.0, and the nova shoot clip's events land on whole frames (7/24, 10/24) only that way. Kinds and labels: Sound (`m_name`, 2696), ID (`m_ID`, plus `/m_secondaryID` when set, 1110), Particle (the `.vpcf` file name, 187), OrientationWarp (16), MaterialAttribute (`m_attributeName`, 15), Legacy (`m_animEventClassName`, 3), FloatCurve (`m_ID`, 1). Secondary animations carry no events, so one bound in place of its main clip (weapons) is given the main clip's. Blender truncates marker names to 63 characters. CS2 with `--clips "*"`: chicken 532 markers, ctm_sas 3496, which is every event; import times unchanged. |
| `MdlV44/V49.from_buffer` padding | After an animation failed to decode, the padding count was negative, so `animations` ended up shorter than `anim_descs`; now padded to the same length. `tests/mdl_tests/test_mdl_file.py` fails the third decode of `dog_animations.mdl` (116 descs) through both classes. |
| Circular imports | `v44/mdl_file.py` imported `v49.flex_expressions`, whose package imports `v49/mdl_file.py`, which imports `MdlV44`: importing `v44` first failed. The add-on only worked because something imported `v49` first. `flex_expressions.py` moved up to `library/models/mdl/` (it is shared by v36, v44, v49 and v2531). `bsp/datatypes/displacement.py` had the same problem through `lumps.face_lump`, used only as an annotation; it is now a `TYPE_CHECKING` import. `tests/test_import_order.py` imports each of the 337 library modules first in a fresh module table (7 s) and fails on either cycle. |
| Stray file | `library/models/mdl/v44/mdl_file copy.py` (an older `mdl_file.py`) deleted. |
| Source 2 model logging | `vmdl_loader.py` and `morph_block.py` logged through Python's root logger, which the runners don't count; they now use `SourceLogMan` loggers (`Source2::Model`, `Source2::Morph`). A missing material is a warning (the mesh still imports; every Source 2 sample is missing its materials). A missing morph atlas is an error that says the flexes aren't imported. An empty atlas path is silent: CS2's `ctm_sas` has 3 eye flexes with no morph data and no atlas (it wears a gas mask). The per-draw-call "Mesh attributes" line is now debug. |
| False "Unused texture" warnings | Shaders that read texture properties directly (`generic.vfx` and others) never marked them used, so the material loader warned and loaded them a second time into an extra node. `Source2ShaderBase.load_texture_or_default` now records every path it loads, and only paths nothing loaded are reported. cs_office: 424 → 418 (`generic.vfx` `g_tColor`/`g_tNormal` gone, 2 layer AO); de_dust2: 541 → 478 (59 `csgo_lightmappedgeneric` layer 1 AO). Objects, meshes, materials and images are unchanged, and so is the time (cs_office 51 s). |

Unit tests: 251 pass. Armature tests: 10 pass. Smoke test: 443/443 modules, clean. Samples: 87 PASS / 6 WARN / 0 FAIL; the new WARN is `stone_tranquility_helm`, whose `_vmorf.vtex` isn't in the samples (VRF doesn't ship it), so its 46 flexes were already being dropped without a counted error. TF2: 13 PASS / 0 WARN / 0 FAIL. CS2: 12 PASS / 0 WARN / 0 FAIL; de_dust2 and cs_office with every placeholder: PASS, 570/570 and 636/636.

## Round 12 (2026-10-08, upstream REDxEYE/SourceIO#477)

Upstream (`0a835d96`, hisprofile's PR #477) was reviewed commit by commit against this fork; every commit conflicts, so the useful parts were adapted by hand rather than cherry-picked. Already here: Blender 5.2 channelbags (upstream adds a 4.4 gate), *World scale* in the MDL dialog, and the `mdl_file copy.py` deletion. Rejected: the four-flag `StudioAnimDesc` decode cache (keyed on nothing that identifies the inputs, returns shared mutable arrays, no measurable gain on external ANI), the section decoder replacement (fails `test_sections_fill_unlisted_bones`), `model_path[-63:]` action names, `str.lstrip("models/")` (turns `models/dog.mdl` into `g.mdl`), and turning the flattened loader API into a list-or-dict.

| Item | Result |
|------|--------|
| Flex controllers panel | The old panel read `Mesh.flex_controllers`, which nothing filled. `create_flex_drivers` now fills `Object.flex_controllers`: Flex Scale, one entry per UI controller (stereo controllers hold their L and R properties) and one per n-way controller. The panel (*SourceIO utils > Flex controllers*) shows the property values, or per entry an additive slider that adds to the current value while dragged, splits stereo controllers by a left/right balance (`Scene.sourceio_flex_lr_balance`), keys on release with auto keying on and restores the values if the drag is cancelled. Buttons key one controller (insert/delete on the current frame), key all, and reset (Flex Scale 1, the rest 0). Adapted from upstream's `flex_operators.py`, without the 4.4 branch, the shared class-level slider set, the positional `layout.label` call or the scene property group. Registered properties are all removed on unregister (upstream leaked `Object.flex_controllers`). TF2 HWM Heavy: 647 shape keys, 653 drivers, 37 controllers. |
| Combo drivers | The shortcut for a combo flex (product of its component flexes, divided by FS^(n-1)) clamped the product before dividing, so at Flex Scale ≠ 1 it saturated early (the review measured HWM `CloseLidLoL` at FS 2: 0.25 instead of 0.3125; 516 of its 653 drivers use the shortcut). Source multiplies the components without clamping; the outer `clamp()` is gone. The leftover `bpy.types.Scene.t = all_exprs` debug assignment is gone too. |
| Animations per include model | With *Include animations* and *Compact animations*, each source model now gets its own compact action named after its file (`dog.mdl`, `dog_animations.mdl`, `dog_gestures.mdl`, `dog_postures.mdl`), so animations with the same name in two include models (Heavy's `@user_ref`) keep it instead of becoming `.001`. New `load_animations_by_model()` returns `(file name, animations)` groups; `load_all_animations()` and `load_all_animations_with_models()` still return flat lists (prop animations use them). Without compact animations nothing changes: every animation is its own action, all with the main model's slot name. TF2: Heavy 4 actions / 902 slots, Dog 4 / 134, Minigun and the resupply locker (no includes) 1 action each. |

`tests/blender_tests/test_flex_controllers.py` (7 tests, synthetic model): the controller list, the unclamped combo (0.75 where the clamp gave 0.5), the stereo slider with balance 0 and -1, key toggle and key all on slotted actions, reset, save and reload, and unregister removing every property. `test_ani_loading.py`: grouped names and order for Dog, the flat list unchanged, a model without includes, and `_model_name` on relative, absolute Windows and bare paths. `run_game_imports.py` reports `flex_controllers` and `compact_actions`. Unit tests: 258 pass. Armature tests: 10 pass. Flex tests: 7 pass. Smoke test: 443/443 modules, clean. Samples: 87 PASS / 6 WARN / 0 FAIL. TF2 with `--include-animations`: 13 PASS / 0 WARN / 0 FAIL.

## Round 13 (2026-10-08, hand test of the flex panel)

The hand test used `E:/Tests/flex_slider_test.blend` (TF2 HWM Heavy, 37 controllers, 648 shape keys; built by importing the model through the game runner). The extension wasn't installed in the user's Blender at first; it was built with `tools/build_extension.py` and installed with `blender --command extension install-file -r user_default --enable`.

| Item | Result |
|------|--------|
| Flex sliders | The additive sliders didn't work in the UI. The slider's update callback started a modal handler, which ended on the mouse release and consumed it, so Blender's slider never left its drag; each later click started a new handler. Passing the release through then showed that the drag's result was thrown away on release (the face reverted). Replaced, at the user's choice, by absolute sliders: a mono controller's slider is its mesh custom property (Blender keys it natively with auto keying); a stereo controller's slider is a computed property (`value`, or `value_signed` for a range below 0) that reads the side the L/R balance gives in full and writes it, plus the other side at its share (left alone at a share of 0), and keys what it wrote with auto keying on. The eye toggle shows a stereo controller's L and R separately. No modal operator remains. Checked by hand: drag, release, balance and auto keying work. |
| Rim light | `$rimlight` set Sheen Weight to `$rimlightboost` (clamped to 1) over the whole surface. Sheen is a velvet layer, so TF2 characters had a white haze head-on. The weight now carries the SDK's rim Fresnel term `pow(1 - N.V, 4)` (Layer Weight *Facing* on the shading normal, to the 4th); the exponent texture's alpha masks it only with `$rimmask`, as in the SDK. |
| Custom normals | The remaining metallic look (under the Material Preview HDRI) came from the normals. Round 1 wrote them as a free `custom_normal` FLOAT_VECTOR attribute, which stays in mesh space. Model meshes are stored Y-up and turned upright by the armature, so after deformation the normals pointed sideways: corner normals agreed with the evaluated faces at 0.14–0.35 on average (0.85–0.89 before deformation). This made the rim Fresnel term about 0.8 on a vest facing the camera, and the reflections streaky. `set_custom_normals` uses `normals_split_custom_set(_from_vertices)` again (fan-space normals, which follow armatures, poses and shape keys). The HL2 barrel sample's normals match its geometry at 0.999; the heavy's bullets agree at only 0.49 in the file itself (artist normals). de_dust2 with every placeholder: 135 s, was about 129 s. |

Found along the way: the parent *SourceIO utils* panel only shows for an active object with `entity_data` or `skin_groups`, so the flex panel hides when an empty or the armature is active. New Blender 5.2 shape keys start at value 1.0.

`tests/blender_tests/test_custom_normals.py` (3 tests): POINT and CORNER normals follow a shape key that lays a quad flat, and no free normal attribute is left; all 3 fail on the round 1 code. `test_flex_controllers.py`: the additive and handler tests are replaced by the balance split, an absolute value that stays and keys, and signed ranges with clamping (9 tests). Unit tests: 258 pass. Blender tests: armatures 10, flex 9, skins 7, material paths 7, custom normals 3. Smoke test: 443/443 modules, clean. Samples: 87 PASS / 6 WARN / 0 FAIL. TF2: 13 PASS / 0 WARN / 0 FAIL. CS2: 12 PASS / 0 WARN / 0 FAIL; de_dust2 with every placeholder: PASS, 570/570.

## Round 14 (2026-10-08, CS2 materials)

Started from the round 11 "Unused texture" item. A survey of every `.vmat_c` in CS2's `pak01_dir.vpk` and the de_dust2, de_inferno and cs_office map VPKs (texture parameters and flags per shader, and decoded channel statistics for a sample of each) found bigger problems behind it.

| Item | Result |
|------|--------|
| Character and weapon shaders | `csgo_character.vfx` (557 materials: agents, hostage, chicken, gloves, patches) had no handler, and `csgo_weapon.py` was never imported, so both fell to `DummyShader`: a grey Principled BSDF with every texture loaded and left unconnected. Both are now `CSGOComplex` subclasses in `csgo_complex.py` (`csgo_weapon.py` deleted). Weapons read roughness from the red channel of `g_tMetalness` (their `*_rough` texture) instead of the normal alpha. Blood masks, patches and stickers are filled in by the game, so they are skipped. ctm_sas 36 → 26 images, AK-47 24 → 9 (9.7 s, was 11.5 s), hostage 45 → 34. |
| Metalness | In every CS2 shader the metalness texture keeps metalness in green (red is roughness on weapons and 0 elsewhere; blue is the cloth mask on characters). `csgo_complex` took metalness from the color alpha whenever `F_METALNESS_TEXTURE` was set: a premier coin's alpha is a constant 0.5 where its green averages 0.90, and the bus glass's alpha is its opacity. It now uses `g_tMetalness` green when the texture exists, and the color alpha only without one (391 of the 423 flagged materials, where the alpha does look like metalness: gold 0.93, a bench 0.12). `csgo_vertexlitgeneric` ignored `g_tMetalness` entirely (all 1958 of its materials have one; 120 are real textures, the rest generated 4×4 zeros). New `Source2ShaderBase._split_metalness_texture`. |
| Translucency | The CS2 handlers tested `S_TRANSLUCENT`, which no CS2 material sets; they use `F_TRANSLUCENT` (550 lightmappedgeneric, 123 character, 81 complex, 43 vertexlitgeneric). `_is_translucent()` accepts either, in every `csgo_*` handler. |
| Ambient occlusion | Skipped on purpose (`csgo_complex` and its subclasses, vertexlitgeneric, foliage, the lightmappedgeneric layers, static overlay), as `vr_complex` and `vr_simple` already did. Source 2 applies it to indirect light only (CS2 materials set `g_flAmbientOcclusionDirectDiffuse` and `DirectSpecular` to 0), which Blender computes itself; multiplying it into the base color would darken direct light too. Most slots hold the white `default_ao` anyway. Foliage also skips `g_tNoiseMap` (wind animation). |
| Smaller fixes | `csgo_foliage` connected the color (not its alpha) to Alpha, so foliage without alpha testing was transparent by luminance. `csgo_complex` applied `g_vColorTint` only when it was white. The *SourceIO utils* parent panel now also shows for an object with flex controllers, and the entity panels' fallback to the first selected object ran only when nothing was selected (an IndexError). `CompiledTextureResource.get_texture_data` decoded every mip at the full resolution, so mips above 0 failed; it now uses the mip's size and raises for a mip the texture doesn't have (nothing in SourceIO asks for one yet). The smoke test skips tracked files deleted in the working tree. |
| Known sample warnings | `run_sample_imports.py` has a KNOWN status: a WARN whose every logged error matches the patterns listed for that sample in `KNOWN_ERRORS` (missing HL2 materials, skybox and WAD textures, the helm's `_vmorf.vtex`, and `patch` materials whose original is missing). Any other error keeps the sample at WARN and becomes its message. |

de_dust2 with every placeholder: PASS, 570/570, 135.6 s (unchanged); "Unused texture" warnings 478 → 26.

`tests/blender_tests/test_source2_materials.py` (8 tests; a fake material resource, no game files): metalness from the texture's green on complex, vertexlitgeneric and character; the color-alpha fallback; the color tint; `F_TRANSLUCENT` on complex, vertexlitgeneric and lightmappedgeneric; weapon roughness from red; the skipped AO, blood, patch, sticker and noise slots; opaque foliage. 7 of the 8 fail on the round 13 code (the fallback passes on both). `tests/texture_tests/test_vtex_mips.py`: mip 1 of BC7, ETC2, DXT1 and ATI2N samples matches the box-filtered mip 0 (mean difference under 0.02), and a missing mip raises. `test_flex_controllers.py`: the parent panel poll. Unit tests: 263 pass. Blender tests: armatures 10, flex 10, skins 7, material paths 7, custom normals 3, Source 2 materials 8. Smoke test: 442/442 modules, clean. Samples: 87 PASS / 6 KNOWN / 0 WARN / 0 FAIL. CS2: 12 PASS / 0 WARN / 0 FAIL. TF2 not rerun (no Source 1 code changed).

## Round 15 (2026-10-08, CS2 texture gaps)

Started from the round 14 texture gaps. A material stores only the parameters an artist changed; the defaults, and how each compiled texture is packed from its sources, are in the shader. CS2's `shaders_pc_dir.vpk` holds each shader's `<name>_pc_50_features.vcs` as a compiled resource whose DATA block is KV3, so SourceIO already reads it. New `tools/shader_params.py` prints the parameters (default, UI group, colour space) and the texture packing of one shader.

| Item | Result |
|------|--------|
| Decals | `csgo_vertexlitgeneric` `g_tDecal` (85 materials) packs the decal color with its translucency in alpha. `F_DECAL_BLEND_MODE` 0 (72 materials; logos, alpha 0–1) lays it over the albedo, 1 (13; `*_ao_decal` textures, near white, alpha 1) multiplies. `g_bUseSecondaryUvForDecal` defaults to 1, so decals read `TEXCOORD_1` unless it is 0. The CS:GO drop crate's logo and hazard stripe render in place. |
| Transmission | `g_tTransmissiveColor` on `csgo_foliage` and `csgo_complex`: the shader adds back-lit diffuse light tinted by it and has no strength parameter, so a Translucent BSDF is added to the surface, masked by the clipped or translucent alpha. `F_USE_ALBEDO_FOR_TRANSMISSIVE` (123 materials) always comes with the default grey texture, and uses the albedo instead. A back-lit aztec fern shows light through its leaves. |
| Layer detail | `csgo_lightmappedgeneric` `g_tLayer1Detail`/`g_tLayer2Detail` (237 and 38 materials) are linear textures averaging 0.5: Source 1's mod2x. The albedo is multiplied by lerp(1, 2 × detail × tint, blend) ahead of the node group's `TextureColor0/1`, with the detail UV scaled by `g_vLayer<n>DetailScale` (default 4). `F_DETAILTEXTURE` 1 details layer 1, 2 both layers. The group's own detail inputs stay unused (its `Detail` group passes the color through in mode 0), so `source2_materials.blend` is unchanged. |
| Anisotropic gloss | Every ATI2N normal map on `csgo_character` (94) and `csgo_complex` (2) belongs to an `F_ANISOTROPIC_GLOSS` material. Its normal map holds only X and Y; roughness is in `g_tAnisoGloss`, red along the tangent and green along the bitangent. The material read roughness from the normal map's alpha, which held the reconstructed Z (about 0.98). The decoder also ran the normal-map steps on `g_tAnisoGloss`, because every CS2 texture compiled from a normal map lists all of them in its edit info. It now keeps textures with the `Mip AnisoRoughness_RG` step raw, and roughness is the average of red and green. The music-kit record goes from matte to about 0.22. |
| Detail UV name | `csgo_complex` details used the UV map `TEXCOORD1`, which no mesh has (`vmdl_loader` names them `TEXCOORD_1`). They now also honour `g_bUseSecondaryUvForDetailTexture` (default 1) under `F_SECONDARY_UV`. |

de_dust2 with every placeholder: PASS, 570/570, 133.7 s; "Unused texture" warnings 26 → 6 (`generic.vfx` roughness and metalness, one tint mask, one self-illumination mask, one height map).

`tests/blender_tests/test_source2_materials.py` (15 tests, was 8): both decal blend modes and the UV switch, transmission from a texture (and through the alpha clip), from the albedo, and none, layer detail with `F_DETAILTEXTURE` 1 and 2, anisotropic roughness and its absence, and the complex detail UV. The 6 that cover new wiring fail on the round 14 code. `tests/texture_tests/test_normal_reconstruction.py`: a synthetic ATI2N block keeps its raw values with the aniso gloss steps (fails without the fix) and is still decoded as a normal map without them. Unit tests: 265 pass. Smoke test: 442/442 modules, clean. Samples: 87 PASS / 6 KNOWN / 0 WARN / 0 FAIL. CS2: 12 PASS / 0 WARN / 0 FAIL. The other Blender suites and TF2 were not rerun (no Source 1 or shared code changed).
