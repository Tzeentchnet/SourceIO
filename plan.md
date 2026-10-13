# SourceIO work plan

Target: **Blender 5.2.2 LTS only.** Blender 4.x compatibility is dropped.
Branch: `master` (`dotafeatures` and `feature/flex-controller-modal` are merged).

All API claims below were checked against Blender 5.2.2 running headless (`D:/Blender Foundation/Blender 5.2/blender.exe -b`).

## Current work

Start here in a new session. Keep this section current: remove completed items, update the validation baseline, and add concrete follow-ups found along the way. Completed implementation history belongs in the local Git-ignored planning archive (`docs/modernization-history.md`, rounds 1–25) rather than this file; "round N" below refers to its sections.

State (2026-10-12): [5.12.0-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.12.0-blender5.2) is the current release (Dota 2 world/material support, CS2 Workshop static authoring, SFM-style flex controllers, Source 1 prop placeholder fix). `origin/master` and the release tag point to the validated 5.12.0 release commit. TF2 (`E:/SteamLibrary/steamapps/common/Team Fortress 2/tf`), CS2 (`E:/SteamLibrary/steamapps/common/Counter-Strike Global Offensive/game/csgo`; maps ship as `maps/<name>.vpk`), Dota 2 (`E:/SteamLibrary/steamapps/common/dota 2 beta/game/dota`), Dota 2 Workshop Tools, and CS2 Workshop Tools are installed. The Dota compatibility milestone and the first CS2 editable static-authoring/compiler vertical slice have passed their installed-tools exit gates on `dotafeatures`.

Checks, with the current baseline. End-to-end runners (`tests/e2e/`) first; the unit and Blender tests cover what an E2E run can't see (exact values, node wiring, formats without samples):

```
blender -b --factory-startup --python tests/e2e/run_renamed_smoke.py                                       # 612/612 modules, register/unregister/register OK
blender -b --factory-startup --python tests/e2e/run_sample_imports.py                                      # 87 PASS / 6 KNOWN / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<TF2>/tf"                   # 13 PASS / 0 WARN / 0 FAIL
blender -b ... run_game_imports.py -- --game "<TF2>/tf" --load-placeholders                                  # 13 PASS; 2fort 2320/2320, badlands 1308/1308, harvest 673/673, upward 1332/1332 placeholders
blender -b ... run_game_imports.py -- --game "<Dota2>/game/dota" --expect-profile dota --load-placeholders    # 7 PASS; dota 5311/5311 placeholders (~336 s)
# Add --save-dir DIR to any run_game_imports.py run to keep one packed .blend per asset for visual review.
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<CS2>/game/csgo"            # 12 PASS / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<Dota2>/game/dota" --expect-profile dota  # 7 PASS / 0 WARN / 0 FAIL; app ID 570
$env:SOURCEIO_DOTA2_WORKSHOP_ROOT="<Dota2>"; python -m pytest tests/source2_workshop/test_dota_workshop_roundtrip.py -q  # 1 pass; generated model/material/texture compile and re-import
$env:SOURCEIO_CS2_WORKSHOP_ROOT="<CS2>"; python -m pytest tests/source2_workshop/test_cs2_workshop_contract.py -q         # 3 pass; official ModelDoc/FBX/VMAT/TGA contract and re-import
$env:SOURCEIO_CS2_WORKSHOP_ROOT="<CS2>"; blender -b --factory-startup --python-expr "..."                                # 10 pass; Blender authoring plus compile/re-import/recompile/cleanup
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --map de_dust2 --load-placeholders                     # PASS, 698/698 placeholders, 3D skybox, 2 hidden collections, 295 materials (~183 s)
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --map de_nuke --load-placeholders                      # PASS, 1108/1108 placeholders, 286 materials, 175 lights
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --model <each default model> --clips "*"               # 9 PASS, agents 2089 clips each (~55 s each)
cd D:/Github && "D:/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe" -m pytest SourceIO/tests -q -p no:cacheprovider --ignore=SourceIO/tests/blender_tests   # 505 pass, 4 skip (tests/archive left out; includes tests/conformance, tests/source2_core, tests/source2_export, tests/experimental)
cd D:/Github && blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, 'D:/Github'); import SourceIO; SourceIO.register(); sys.exit(pytest.main(['SourceIO/tests/blender_tests', '-q', '--ignore=SourceIO/tests/blender_tests/test_skins.py']))"  # 203 pass, 1 skip, 23 subtests
cd D:/Github && blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, 'D:/Github'); sys.exit(pytest.main(['SourceIO/tests/blender_tests/test_skins.py', '-q']))"  # 8 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_armatures', argv=['x'], exit=False)"   # 10 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_flex_controllers', argv=['x'], exit=False)"   # 19 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_skins', argv=['x'], exit=False)"   # 8 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_material_paths', argv=['x'], exit=False)"   # 7 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_custom_normals', argv=['x'], exit=False)"   # 3 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_materials', argv=['x'], exit=False)"   # 61 pass; 42 shader follow-up tests in six adjacent modules, 103 total
cd D:/Github && blender -b --factory-startup --python-expr "import sys, pytest; sys.exit(pytest.main(['SourceIO/tests/blender_tests/test_source2_dota_materials.py', 'SourceIO/tests/blender_tests/test_source2_materials.py', '-q']))"  # 70 pass, 14 subtests
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_maps', argv=['x'], exit=False)"   # 11 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_lights', argv=['x'], exit=False)"   # 9 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_animations', argv=['x'], exit=False)"   # 8 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_sounds', argv=['x'], exit=False)"   # 2 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import unittest; unittest.main(module='SourceIO.tests.blender_tests.test_source2_exports', argv=['x'], exit=False)"   # 3 pass
python tools/build_extension.py --blender "D:/Blender Foundation/Blender 5.2/blender.exe" --platform windows-x64       # built
```

### Actionable

1. **Remaining Source 2 shader fidelity**:
   - Environment wetness/live-weather state, screen-derivative blend bevels, and mip/distance-dependent softness growth remain unsupported rather than approximated.
   - Character diffuse falloff, animated eyes, and iridescence remain unsupported pending verified shader behavior.
   - Some runtime dynamic VFX expressions still need an explicit supported subset and deterministic Blender equivalents.
2. **Smaller CS2 follow-ups** (rounds 8, 17, 19, 20 and 22):
   - Lights inside their fixtures (round 22): de_dust2's brightest barn light (`light_barn` at 325 2371 31) sits 9 cm inside the opaque, self-illuminated bulb of `dust_hanging_light_02_on` (`csgo_vertexlitgeneric`, `F_SELF_ILLUM`), so in Blender it lights nothing (it didn't as a point light either). The lamp's aggregate has `NoShadows` (0x20) in `m_allFlags`, but so do 216 of de_dust2's 379 aggregates and 79 of its 138 static scene objects (plants, crates), most likely meaning "shadow baked, not drawn into dynamic shadow maps", so the flag alone can't turn shadows off. Candidates: turn shadow rays off for self-illuminated meshes that enclose a light, or for `NoShadows` objects a light sits inside. Flags come as integers or as enum names (`OBJECT_TYPE_RENDER_TO_CUBEMAPS`).
   - Light shapes, what's left (round 22): barn lights lose their rectangular shape (`shape` 0; spots are elliptical), skirts, `range` and cookies (`lightcookie`, 177 of 563 barns, mostly `flashlight.vtex`); `soft_x`/`soft_y` as the spot blend and `luminaire_size / 2` as the radius are unverified. The one orthographic barn (de_nuke) is an area light with irradiance π × brightness near it. `light_omni2` tubes (`shape` 1 and 2, 41 lights) are point lights (`shape` isn't read), and whether `size_params[0]` is a sphere's radius is unverified (`# TODO` in `handle_light_omni2`: the other axes are ignored); outer angles over 90° stay point lights (Blender's spot cone ends at 180°). No light uses `range` (EEVEE's custom distance could).
   - Fog (round 20): `env_cubemap_fog` (fog colored by the sky, on every surveyed map) and `env_gradient_fog` aren't imported; a World volume could approximate the height fog.
   - Unverified (round 20): `g_flBrightnessExposureBias` multiplies the sky by 2^bias (de_dust2 0.765, the other surveyed maps 0); `g_flRenderOnlyExposureBias` (always 0) applies to camera rays only. HL:A's `light_spot` and `light_ortho` still use the entity rotation as is, which points them along −Z where the entity points along +X (as the sun did; `_set_light_rotation` fixes that for the others), and `light_spot` takes `outerconeangle` as the full cone (`spot_size = radians(outerconeangle)`, where CS2's omni2 doubles its half angle); no HL:A install to check.
   - EEVEE lights the dust2 fence's wires much brighter than Cycles (wire color about 0.55 against 0.33 under the same sun and sky, from either side). Not an alpha problem; cause not found.
   - World-node models with no material in their name (`node000_lr0_c0_s_cb_nomerge3`, `mesh_cm00_lp<hash>`; the de_dust2 skybox's 14 cloud cards are `nomerge1` to `nomerge44`) keep the remainder of the compiled name (`world_node_model_name` already strips the compiler prefix and names `nomerge<n>_<material>`, `mesh_mat<n>_<material>`, aggregate, overlay and light-blocker models); they could be renamed after their material once loaded.
   - 3D skyboxes: the skybox's ground can poke through the map's lowest floors (in game it is drawn behind the world). `sky_camera` `use_angles` is ignored (no CS2 skybox sets it), and the skybox's lights other than `light_environment` keep their energy at 16× the distance.
   - Unhandled CS2 entities include `env_particle_glow`, `hostage_entity`, `point_perfcapture`, team intro points.
3. **Clip follow-ups** (rounds 9 and 11):
   - Event markers sit at the start frame only and have no length. The full events, durations included, are kept only as data in the action's `sourceio_events` JSON property (`animation_loader.py`); nothing in the timeline shows a duration or uses the other fields (attachments, sound positions, curves).
   - Weapon viewmodel clips can't be found from any model: the viewmodel graph pulls them in at runtime through `m_externalGraphSlots` (per-weapon graphs such as `viewmodel_inspects.vnmgraph+ak47.vnmgraph`). The clip importer handles them once extracted; finding them automatically needs whatever ties a weapon to its graphs (item schema or weapon vdata, unverified).
   - The clip importer (`sourceio.vnmclip`) treats the armature's rest pose as Source bone orientations. That holds for SourceIO's Source 2 armatures; other rigs would need their bone orientation corrected.
4. **Flex additive slider, check in the UI** (2026-10-12 review fixes): the modal drag path can't run headless. In a UI session, check that dragging a stereo slider commits on release and cancels on Esc/right-click; that a script setting several stereo `value`s in a row applies all of them (each commits the previous session early); and that undo still takes one step per drag (the 5.11.2-flex-controller.1 GUI smoke confirmed this before the review fixes; `reset_flex_deltas` on `undo_post` now also clears any leftover additive value).
5. **Flex panel with the armature active** (round 13): the panel and its operators act on `context.object`, so it only shows with the face mesh active. Following an active armature to its flexed child mesh means giving the operators a target object.
6. **Upstream #477 leftovers** (round 12, deferred on purpose): moving the scene settings into one `Scene.sourceio_props` group needs a versioned migration (old .blend values are otherwise lost: tested in the review) and compatibility for mounted-resource collections. Upstream's automatically embedded flex UI script (`Text.use_module`, runs only with auto-execution on) could come back only as an explicit *Embed standalone flex UI* action.

### Blocked on assets

- **Bone collections for the other builders** (optional, no samples): `mdl36` and `mdl2531` read the same `Bone` struct, so `assign_bone_collections` would work there too, but their flags are unverified. GoldSrc and Source 2 have no USED_BY flags; they could get side colours only.
- **GoldSrc v4/v6 animations** (suspected, no samples): `load_animations` keys each frame's parent-relative position and rotation straight onto the pose bones, whose rest pose already holds the bind transform, so the two would add up. v10 was made rest-relative in round 2; v4/v6 may need the same.
- **External mesh morph atlas** (unverified, round 11): `load_external_mesh` resolves `m_pTextureAtlas` against the model resource, as before; it may belong to the mesh or morph set resource. No CS2 or sample model uses this path.
- **v49 FRAMEANIM against a real model** (round 6): needs a v49 game (CS:GO, L4D2, Portal 2, SFM; CS2 ships only Source 2 content).

Done in round 25, still untested against a real asset: legacy ANIM `MorphChannel` segments become shape-key drivers (`library/source2/animation/animation.py`; `test_source2_animations` covers a synthetic one), and clip float curves (`m_floatCurveIDs`) are decoded as `NmFloatCurve` channels and written as `sourceio:curve:*` properties; no CS2 model or clip has either. `library/source2/utils/decode_animations.py`, the older decoder, is imported nowhere and can be deleted.

Not needed to import clips: bone masks (`m_maskDefinitions` in a `.vnmskel`) and pose-parameter blending are graph-evaluation features.

### Future work

The CS2 static authoring slice in [TODO.md](TODO.md) is implemented: deterministic editable FBX/VMDL/VMAT/TGA staging, preflight and manifests, non-destructive Blender extraction, File > Export and CLI entry points, optional official compilation, manifest recompile, and compiled re-import are validated. The current authored material contract is intentionally limited to opaque base color or reuse of an existing VMAT. Expanding that contract, implementing the Source 1 backend/compiler slice, and supporting skeletal/animated/physics/map authoring remain future work. Static ModelDoc/Hammer reconstruction stays a separate explicitly lossy workflow.

## Verification

The end-to-end runners in `tests/e2e/` are the main gate: `run_renamed_smoke.py` (every shipped Python file imported under another package name, then register → unregister → register), `run_sample_imports.py` (every fetched sample through the real operators) and `run_game_imports.py` (models and maps from an installed game). They fail when an import raises, logs an unexpected error, creates nothing or decodes a texture's top mip wrongly; they don't compare counts or values. Every change must pass the smoke test and the unit tests; run the sample, game and Blender suites when the change touches what they cover.

Unit tests that an E2E run already covers move to `tests/archive/` (its README says why each one moved). A plain `pytest SourceIO/tests` leaves the archive out (`tests/conftest.py`); `pytest SourceIO/tests/archive` runs it (2 pass).

Real-asset samples (hash-verified downloads into the git-ignored `samples/`):

```
"<blender>/5.2/python/bin/python.exe" -I tests/fetch_samples.py
blender -b --factory-startup --python tests/e2e/run_sample_imports.py -- [--filter TEXT] [--json report.json]
```

Source 2 resource conformance (round 25; `tests/conformance` runs the same checks under pytest). The fixtures in `tests/fixtures/source2_generated/` are generated locally, not taken from games or VRF's corpus. Rebuild them with `tools/build_source2_fixtures.py` only when the format coverage changes, and update the snapshot with them. The VRF oracle is optional and runs only with an explicit CLI path (`SOURCEIO_VRF_CLI`, optionally pinned by `SOURCEIO_VRF_CLI_SHA256`):

```
"<blender>/5.2/python/bin/python.exe" tools/source2_conformance.py snapshot    # strict snapshot matched 7 fixtures
"<blender>/5.2/python/bin/python.exe" tools/source2_conformance.py self-test   # passed: 7 valid, 23 malformed fixtures
"<blender>/5.2/python/bin/python.exe" tools/vrf_oracle.py state                # unconfigured unless SOURCEIO_VRF_CLI is set
```

Command line (`blender -c sourceio`, `blender_bindings/cli.py`): `import`, `extract-sound`, and the static reconstructions `export-modeldoc` and `export-hammer`.
