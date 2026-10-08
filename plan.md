# SourceIO → Blender 5.2 modernization plan

Target: **Blender 5.2.2 LTS only.** Blender 4.x compatibility is dropped.
Branch: `master`. Round 1 was done on `blender-5.2-modernization`, rounds 2–4 on `continue-work` (now parked at the same commit as `master`).

All API claims below were checked against Blender 5.2.2 running headless (`D:/Blender Foundation/Blender 5.2/blender.exe -b`).

## Next

Start here in a new session. Keep this section current: when an item is done, record the result in that round's section below, remove it here, and add anything found along the way.

State (2026-10-08): round 8 is committed on `master` but not pushed; rounds 3–7 are released as [5.7.0-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.7.0-blender5.2) (release notes and README follow the 5.6.0 layout; packages from `tools/build_extension.py`). TF2 (`E:/SteamLibrary/steamapps/common/Team Fortress 2/tf`) and CS2 (`E:/SteamLibrary/steamapps/common/Counter-Strike Global Offensive/game/csgo`; maps ship as `maps/<name>.vpk`) are installed; no Dota 2.

Checks, with the current baseline:

```
cd D:/Github && "D:/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe" -m pytest SourceIO/tests -q -p no:cacheprovider --ignore=SourceIO/tests/blender_tests   # 225 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_armatures', argv=['x'], exit=False)"   # 10 pass
blender -b --factory-startup --python tests/blender_tests/run_sample_imports.py                                      # 88 PASS / 5 WARN / 0 FAIL
blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- --game "<TF2>/tf"                   # 13 PASS / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- --game "<CS2>/game/csgo"            # 12 PASS / 0 WARN / 0 FAIL
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --map de_dust2 --load-placeholders                     # PASS, 570/570 placeholders (~2 min)
```

1. **AnimGraph 2 clips** (`.vnmclip_c`, 2735 in CS2): CS2 agents and weapons animate through these, not through the model's own ANIM block (`ctm_sas` imports 2 actions). The clip is plain KV3 (`m_trackCompressionSettings` per bone: static flags, constant rotation, translation/scale ranges; `m_compressedPoseData` + `m_compressedPoseOffsets`; `m_rootMotion`; `m_skeleton` → `.vnmskel_c` with bone IDs, parents and parent-space reference pose), all readable through `CompiledResource` + `KVBlock` already. Port the decoder from VRF's `ModelAnimation2/AnimationClip.cs` (MIT; not in the round 2 source cache, fetch it) and compare with the VRF CLI (round 2 left `cli-windows-x64` in an old session scratchpad, `.../1e88980c-.../scratchpad/vrf_cli`; it may be gone). Needs a way to pick clips for a model (its `.vnmgraph_c`, or a file picker matched by skeleton).
2. **Relative imports** for the extension, so the top-level `SourceIO` alias in `sys.modules` can go.
3. **Bone collections for the other builders** (optional): `mdl36` and `mdl2531` read the same `Bone` struct, so `assign_bone_collections` would work there too, but their flags are unverified (no samples). GoldSrc and Source 2 have no USED_BY flags; they could get side colours only.
4. **GoldSrc v4/v6 animations** (suspected, no samples): `load_animations` keys each frame's parent-relative position and rotation straight onto the pose bones, whose rest pose already holds the bind transform, so the two would add up. v10 was made rest-relative in round 2; v4/v6 may need the same.
5. **`MdlV44/V49.from_buffer`** (minor): after an animation fails to decode, `animations.extend([None] * (len(animations) - len(local_animations)))` pads by a negative count, so `animations` ends up shorter than `anim_descs` instead of aligned with it.

6. **Smaller CS2 follow-ups** (round 8): `skybox_reference` entities don't import the 3D skybox; unhandled CS2 entities include `env_particle_glow`, `hostage_entity`, `point_perfcapture`, team intro points; `generic.vfx` logs false "Unused texture" warnings (it reads textures through its own properties); two Source 2 samples log `Failed to find ... morf texture` through the root logger, which the runners don't count.

Now testable with CS2, after item 1: Source 2 flex/morph animation channels, bone masks, pose-parameter blending.
Blocked until a v49 game is installed (CS:GO, L4D2, Portal 2, SFM; CS2 ships only Source 2 content): checking the round 6 FRAMEANIM decoder against a real model.

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

Deferred (larger, needs test assets): ~~replacing `bpy.ops` mode switching in MDL armature builders~~ (round 7), ~~bone collections and colours~~ (round 5). The CLI command was done in round 2; `Object.visible_shadow` was dropped because material-level shadow disabling already covers sky materials.

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

Still needs game installs: real CS2/Dota 2 maps. (Source 1 animated/flexed models: done in round 3 against TF2.)

## Status

- [x] Phase 1 — all items; also fixed the GoldSrc `ActionCurveFactory` callers in mdl4/6/10 (broken by the same overhaul) and the old Principled socket names (`Specular`, `Transmission`, `Emission`) that fail on 5.2
- [x] Phase 2 — no `is_blender_*`, `shadow_method`, `use_auto_smooth`, `ShaderNodeMixRGB` or deprecated material properties remain; helpers deleted
- [x] Phase 3 — free normals, batched weights, `shade_flat=False`, vectorized keyframes (old vs new verified equal)
- [x] Phase 4 — manifest validates; all 4 platform packages build; installs and enables as `bl_ext.user_default.sourceio`
- [x] Phase 5 — delta → NLA COMBINE option, new file handlers, collapsible MDL import dialog (verified in the 5.2.2 UI)

Follow-ups found during execution:
- ~~`models/mdl10/import_mdl.py` only imports the `walk1` sequence~~ — fixed in round 2 (all sequences, rest-relative).
- ~~Every platform zip bundles all three native libs~~ — fixed in round 2 (`tools/build_extension.py`).
- The extension still relies on a top-level `SourceIO` alias in `sys.modules`; a full switch to relative imports would be the extension-guideline-clean fix.

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

Remaining: Source 2 flex/morph animation channels, AnimGraph 2 (`.vnmclip_c`), bone masks and pose-parameter blending; ~~decals and overlays~~ (Source 1 overlays: round 4); ~~bone collections and colours~~ (round 5); ~~`bpy.ops` mode switching in the armature builders~~ (round 7); relative imports for the extension; testing against CS2/Dota 2 installs.

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
