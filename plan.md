# SourceIO work plan

Target: **Blender 5.2.2 LTS only.** Blender 4.x compatibility is dropped.
Branch: `master` (`dotafeatures` and `feature/flex-controller-modal` are merged).

All API claims below were checked against Blender 5.2.2 running headless (`D:/Blender Foundation/Blender 5.2/blender.exe -b`).

## Current work

Start here in a new session. Keep this section current: remove completed items, update the validation baseline, and add concrete follow-ups found along the way. Completed implementation history belongs in the local Git-ignored planning archive (`docs/modernization-history.md`, rounds 1–28) rather than this file; "round N" below refers to its sections.

**Focus.** The primary goal is reliable imports into Blender: correct, editable geometry, armatures, animation, flexes, materials and scene layout. The secondary goal is a straightforward export path (see [TODO.md](TODO.md)). Matching the game renderer one to one (animated surfaces, runtime shader constants, engine-specific fog, exposure and shadow behavior) is out of scope for now. That work, and anything else parked, lives in `docs/deferred-work.md`; move an item back only when it serves one of the two goals.

State (2026-10-12): [5.12.0-blender5.2](https://github.com/Tzeentchnet/SourceIO/releases/tag/5.12.0-blender5.2) is the current release (Dota 2 world/material support, CS2 Workshop static authoring, SFM-style flex controllers, Source 1 prop placeholder fix). `origin/master` and the release tag point to the validated 5.12.0 release commit. TF2 (`E:/SteamLibrary/steamapps/common/Team Fortress 2/tf`), CS2 (`E:/SteamLibrary/steamapps/common/Counter-Strike Global Offensive/game/csgo`; maps ship as `maps/<name>.vpk`), Dota 2 (`E:/SteamLibrary/steamapps/common/dota 2 beta/game/dota`), Dota 2 Workshop Tools, and CS2 Workshop Tools are installed. The Dota compatibility milestone and the first CS2 editable static-authoring/compiler vertical slice have passed their installed-tools exit gates and shipped in 5.12.0 from `master`.

Checks, with the current baseline. End-to-end runners (`tests/e2e/`) first; the unit and Blender tests cover what an E2E run can't see (exact values, node wiring, formats without samples):

```
blender -b --factory-startup --python tests/e2e/run_renamed_smoke.py                                       # 614/614 modules, register/unregister/register OK
blender -b --factory-startup --python tests/e2e/run_sample_imports.py                                      # 87 PASS / 6 KNOWN / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<TF2>/tf"                   # 13 PASS / 0 WARN / 0 FAIL
blender -b ... run_game_imports.py -- --game "<TF2>/tf" --load-placeholders                                  # 13 PASS; 2fort 2320/2320, badlands 1308/1308, harvest 673/673, upward 1332/1332 placeholders
blender -b ... run_game_imports.py -- --game "<Dota2>/game/dota" --expect-profile dota --load-placeholders    # 7 PASS; dota 5311/5311 placeholders, 6 excluded collections (the two destruction layers), no unsupported shaders (~313 s)
# Add --save-dir DIR to any run_game_imports.py run to keep one packed .blend per asset for visual review.
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<CS2>/game/csgo"            # 12 PASS / 0 WARN / 0 FAIL
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<Dota2>/game/dota" --expect-profile dota  # 7 PASS / 0 WARN / 0 FAIL; app ID 570
$env:SOURCEIO_DOTA2_WORKSHOP_ROOT="<Dota2>"; python -m pytest tests/source2_workshop/test_dota_workshop_roundtrip.py -q  # 1 pass; generated model/material/texture compile and re-import
$env:SOURCEIO_CS2_WORKSHOP_ROOT="<CS2>"; python -m pytest tests/source2_workshop/test_cs2_workshop_contract.py -q         # 3 pass; official ModelDoc/FBX/VMAT/TGA contract and re-import
$env:SOURCEIO_CS2_WORKSHOP_ROOT="<CS2>"; blender -b --factory-startup --python-expr "..."                                # 10 pass; Blender authoring plus compile/re-import/recompile/cleanup
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --map de_dust2 --load-placeholders                     # PASS, 698/698 placeholders, 3D skybox, 2 hidden collections, 295 materials (~183 s alone); nameless world-node models renamed after their material
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --map de_nuke --load-placeholders                      # PASS, 1108/1108 placeholders, 286 materials, 175 lights
blender -b ... run_game_imports.py -- --game "<CS2>/game/csgo" --model <each default model> --clips "*"               # 9 PASS, agents 2089 clips each (~55 s each)
cd D:/Github && "D:/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe" -m pytest SourceIO/tests -q -p no:cacheprovider --ignore=SourceIO/tests/blender_tests   # 508 pass, 4 skip (tests/archive left out; includes tests/conformance, tests/source2_core, tests/source2_export, tests/experimental)
cd D:/Github && blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, 'D:/Github'); import SourceIO; SourceIO.register(); sys.exit(pytest.main(['SourceIO/tests/blender_tests', '-q', '--ignore=SourceIO/tests/blender_tests/test_skins.py']))"  # 219 pass, 1 skip, 28 subtests
cd D:/Github && blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, 'D:/Github'); sys.exit(pytest.main(['SourceIO/tests/blender_tests/test_skins.py', '-q']))"  # 8 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_armatures', argv=['x'], exit=False)"   # 10 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source1_animations', argv=['x'], exit=False)"   # 3 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_flex_controllers', argv=['x'], exit=False)"   # 19 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_skins', argv=['x'], exit=False)"   # 8 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_material_paths', argv=['x'], exit=False)"   # 7 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; unittest.main(module='SourceIO.tests.blender_tests.test_custom_normals', argv=['x'], exit=False)"   # 3 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_materials', argv=['x'], exit=False)"   # 61 pass; 46 shader follow-up tests in six adjacent modules, 107 total
cd D:/Github && blender -b --factory-startup --python-expr "import sys, pytest; sys.exit(pytest.main(['SourceIO/tests/blender_tests/test_source2_dota_materials.py', 'SourceIO/tests/blender_tests/test_source2_materials.py', '-q']))"  # 76 pass, 19 subtests
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_maps', argv=['x'], exit=False)"   # 20 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_lights', argv=['x'], exit=False)"   # 9 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_animations', argv=['x'], exit=False)"   # 8 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import SourceIO, unittest; SourceIO.register(); unittest.main(module='SourceIO.tests.blender_tests.test_source2_sounds', argv=['x'], exit=False)"   # 2 pass
cd D:/Github && blender -b --factory-startup --python-expr "import sys; sys.path.insert(0, 'D:/Github'); import unittest; unittest.main(module='SourceIO.tests.blender_tests.test_source2_exports', argv=['x'], exit=False)"   # 3 pass
python tools/build_extension.py --blender "D:/Blender Foundation/Blender 5.2/blender.exe" --platform windows-x64       # built
```

### Import quality (primary)

1. **Unhandled CS2 entities**: `env_particle_glow`, `hostage_entity`, `point_perfcapture`, team intro points.
2. **Clip follow-ups** (rounds 9 and 11):
   - Event markers sit at the start frame only and have no length. The full events, durations included, are kept only as data in the action's `sourceio_events` JSON property (`animation_loader.py`); nothing in the timeline shows a duration or uses the other fields (attachments, sound positions, curves).
   - Weapon viewmodel clips can't be found from any model: the viewmodel graph pulls them in at runtime through `m_externalGraphSlots` (per-weapon graphs such as `viewmodel_inspects.vnmgraph+ak47.vnmgraph`). The clip importer handles them once extracted; finding them automatically needs whatever ties a weapon to its graphs (item schema or weapon vdata, unverified).
3. **Flex panel with the armature active** (round 13): the panel and its operators act on `context.object`, so it only shows with the face mesh active. Following an active armature to its flexed child mesh means giving the operators a target object.

### Export (secondary)

The CS2 static-authoring slice is done (see [TODO.md](TODO.md)): editable FBX/VMDL/VMAT/TGA staging, preflight, manifests, File > Export and CLI, optional compile, recompile and compiled re-import. Next, in order:

1. **Widen the CS2 material contract** (TODO.md item 9) so ordinary Blender PBR materials export without being blocked: normal map, roughness, metalness, then alpha test. Each mapping needs an official Workshop Tools fixture first. Today only opaque base color, or reuse of an existing VMAT, is accepted.
2. **Source 1 static backend** (TODO.md items 10–12): SMD/QC/VMT/texture staging with optional `vtex`/`studiomdl`, on the same shared service. Needs a Source SDK 2013 install.
3. **One export path** (TODO.md item 13): decide whether the experimental export node tree (`blender_bindings/ui/export_nodes`) moves onto the shared service or is deprecated, so users see one way to export.

Skeletal, animated, physics and map authoring are deferred (`docs/deferred-work.md`). Static ModelDoc/Hammer reconstruction stays a separate, explicitly lossy workflow.

### Needs a UI session

Manual checks that can't run headless; do them in a Blender UI session, not in an agent run.

- **Flex additive slider** (2026-10-12 review fixes): check that dragging a stereo slider commits on release and cancels on Esc/right-click; that a script setting several stereo `value`s in a row applies all of them (each commits the previous session early); and that undo still takes one step per drag (the 5.11.2-flex-controller.1 GUI smoke confirmed this before the review fixes; `reset_flex_deltas` on `undo_post` now also clears any leftover additive value).

### Blocked on assets

- **HL:A light directions** (round 20, no HL:A install): HL:A's `light_spot` and `light_ortho` still use the entity rotation as is, which points them along −Z where the entity points along +X (as the sun did; `_set_light_rotation` fixes that for the others), and `light_spot` takes `outerconeangle` as the full cone (`spot_size = radians(outerconeangle)`, where CS2's omni2 doubles its half angle).
- **Bone collections for the other builders** (optional, no samples): `mdl36` and `mdl2531` read the same `Bone` struct, so `assign_bone_collections` would work there too, but their flags are unverified. GoldSrc and Source 2 have no USED_BY flags; they could get side colours only.
- **GoldSrc v4/v6 animations** (suspected, no samples): `load_animations` keys each frame's parent-relative position and rotation straight onto the pose bones, whose rest pose already holds the bind transform, so the two would add up. v10 was made rest-relative in round 2; v4/v6 may need the same.
- **External mesh morph atlas** (unverified, round 11): `load_external_mesh` resolves `m_pTextureAtlas` against the model resource, as before; it may belong to the mesh or morph set resource. No CS2 or sample model uses this path.
- **v49 FRAMEANIM against a real model** (round 6): needs a v49 game (CS:GO, L4D2, Portal 2, SFM; CS2 ships only Source 2 content).

Done in round 25, still untested against a real asset: legacy ANIM `MorphChannel` segments become shape-key drivers (`library/source2/animation/animation.py`; `test_source2_animations` covers a synthetic one), and clip float curves (`m_floatCurveIDs`) are decoded as `NmFloatCurve` channels and written as `sourceio:curve:*` properties; no CS2 model or clip has either.

Not needed to import clips: bone masks (`m_maskDefinitions` in a `.vnmskel`) and pose-parameter blending are graph-evaluation features.

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

Shaders without a VRF port: `tools/shader_params.py` prints a shader's parameters and defaults; `tools/shader_disasm.py` disassembles its compiled DX11 programs per static/dynamic combo (`--list` first), naming the material constants. It needs the game's `bin/win64` (the zstd dictionary in `materialsystem2.dll`, `d3dcompiler_47.dll`) and runs on Windows only:

```
"<blender>/5.2/python/bin/python.exe" -I tools/shader_disasm.py --game "<Dota2>/game/dota" --shader crystal --list
"<blender>/5.2/python/bin/python.exe" -I tools/shader_disasm.py --game "<Dota2>/game/dota" --shader crystal --static 496 --dynamic 35
```

Command line (`blender -c sourceio`, `blender_bindings/cli.py`): `import`, `extract-sound`, and the static reconstructions `export-modeldoc` and `export-hammer`.
