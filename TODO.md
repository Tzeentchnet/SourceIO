# SourceIO Source Export Capability Plan

**Status: the first CS2 static-authoring vertical slice is implemented and has
passed the installed Workshop Tools exit gate.** The broader material matrix,
Source 1 backend, skeletal/animated assets, and map authoring remain future work.
Current priorities are tracked in [plan.md](plan.md).

## Problem and proposed approach

SourceIO is primarily an importer. It has a working single-image Source 1 VTF
exporter, an experimental Source 1 export-node tree, deliberately limited static
Source 2 ModelDoc/DMX and Hammer reconstruction, and now a profile-validated CS2
static-authoring workflow. It does not yet provide the equivalent Source 1
workflow or a general authoring pipeline for every Source/Source 2 asset class.

The proposed solution is a shared, engine-neutral export pipeline with:

- deterministic asset collection, naming, path validation, diagnostics, manifests,
  and compiler execution;
- a non-destructive Blender adapter used by both UI and headless entry points;
- separate Source 2/CS2 and Source 1 format backends;
- editable authoring files as the canonical output;
- optional official compiler integration, never an implicit or silent fallback;
- end-to-end round-trip validation through SourceIO's existing importers.

The first complete authoring vertical slice targets CS2 Workshop Tools and is now
validated against the installed official compiler. Source SDK 2013 and common
Source 1 conventions will follow on the same foundation. Existing static
reconstruction remains a separate input/provenance-preserving workflow.

## Implemented CS2 vertical slice

- File > Export and `export-cs2` use one non-destructive service for selected,
  collection, or scene scope and combined or per-object grouping.
- The backend emits deterministic FBX, ModelDoc 36 VMDL, `csgo_complex.vfx`
  VMAT, 32-bit TGA, preflight report, and hash manifest artifacts.
- Existing SourceIO material provenance or
  `sourceio_export_material_path` reuses a `.vmat`. New material authoring is
  currently limited to an opaque Principled base color (constant or directly
  connected image); unsupported PBR inputs block export.
- Name/path collisions, unsafe paths, unsupported graphs, existing outputs, and
  missing tools fail explicitly before success is reported.
- `resourcecompiler.exe` runs without a shell and must create fresh expected
  outputs without compiler error markers. Failure preserves editable files and
  a bounded local log.
- `recompile-cs2` verifies target identity and every manifest artifact before
  compiling.
- The installed-tools gate compiles and re-imports Blender-authored geometry,
  normals, UVs, material remaps, and the compiler-named texture, then performs a
  manifest-driven recompile and exact addon cleanup.

## Agreed scope and product decisions

### Initial supported assets

- Static models, materials, and textures.
- Source 2 / CS2 first, then Source 1.
- Source 2 reference target: CS2 Workshop Tools.
- Source 1 reference target: Source SDK 2013 and the common Source 1 static-prop
  pipeline.
- Editable source files plus optional invocation of official tools:
  - CS2: FBX, VMDL, VMAT, and source images; optional `resourcecompiler.exe`.
  - Source 1: SMD, QC, VMT, source images and VTEX sidecars; optional `vtex.exe`
    and `studiomdl.exe`.
- Existing direct VTF export remains supported. It is not presented as a silent
  substitute for a requested official-tool compile.

### Blender workflow

- File > Export entry points and a shared headless CLI service.
- Selected objects are the default scope.
- Active collection and whole scene are additional scope choices.
- Multiple objects combine into one model by default.
- An explicit batch-per-object option exports independent model assets.
- Material mapping uses, in precedence order:
  1. explicit SourceIO export overrides;
  2. retained SourceIO import provenance where it is still applicable;
  3. a documented, supported subset of the active Principled BSDF graph.
- Filename-based texture guessing is not used as an authoritative mapping.
- Unsupported procedural or ambiguous graphs produce actionable diagnostics rather
  than plausible-looking but incorrect output.

### Safety and compiler behavior

- Sanitized name/path collisions fail preflight with rename suggestions.
- No files are written until blocking preflight diagnostics are resolved.
- Existing authored files are protected by default; overwriting requires an explicit
  UI option or `--overwrite`.
- Export stages editable files first.
- `Compile after export` is explicit, and a separate recompile action can consume
  the last manifest.
- Compiler paths come from validated target profiles, not arbitrary shell strings.
- External commands use argument arrays without a shell, capture stdout/stderr and
  exit status, and report missing tools or nonzero exits as failures.
- A failed compile leaves the editable staged files and diagnostic log available,
  but is never reported as a successful compiled export.
- Staging works on every platform SourceIO supports. Initial official compiler
  execution is supported where the relevant tools can actually run; Wine support is
  a later profile capability rather than an assumed fallback.

### Explicitly deferred from the first milestones

- Skeletal models, animation, flex/morph data, physics/collision, LODs, bodygroups,
  skins, and attachments.
- General authored maps/worlds (VMF/VMAP), prefabs, entities, and lighting. The
  existing static Hammer reconstruction remains a separate, explicitly lossy path.
- Automatic baking of arbitrary Blender shader graphs.
- Direct writing of compiled MDL/VMDL/VTF/VTEX binaries as the primary workflow.
- GoldSrc export.
- A claim of universal support for every Source or Source 2 game branch.

## Current SourceIO building blocks

- `blender_bindings/source1/vtf/export_vtf.py` and the bundled VTF library already
  provide a mature direct VTF path.
- `library/utils/kv1.py` has an ordered, duplicate-preserving KV1 writer suitable
  for generated VMT data and related text formats.
- `library/source2/export` now provides Source 2 model/map domain objects, geometry
  reconstruction, loss diagnostics, ModelDoc/DMX and Hammer serializers, export
  bundles, and atomic text staging. It is a static reconstruction slice, not the
  engine-neutral authoring request/profile/compiler service described below.
- `library/source2/provenance` and the Blender import adapters retain JSON-safe,
  cycle-safe Source 2 resource provenance. Imported materials retain canonical
  `full_path` identities, and static exports include provenance and loss sidecars.
- `library/utils/s2_keyvalues.py` and
  `library/source2/utils/kv3_generator.py` support text KV3 and the current static
  ModelDoc reconstruction. Official CS2 authoring fixtures must still define the
  broader serializer contract.
- `library/shared/content_manager` already discovers Source 1 and Source 2 game
  layouts and can be extended with export-target profiles.
- `blender_bindings/exporting/source2`, the registered export operators, and
  `blender_bindings/cli.py` expose static `export-modeldoc` and `export-hammer`
  paths. The future authoring UI and CLI should reuse their tested transform,
  provenance, diagnostic, and staging primitives without presenting them as a
  complete compiler workflow.
- Source 1 imported materials retain parsed VMT parameters in addition to their
  canonical resource paths.
- `blender_bindings/ui/export_nodes` is an experimental QC/VTF prototype, not a
  complete or safe export pipeline. The new service should not depend on it.
  After the service is stable, the node tree can either emit the shared export
  model or be clearly deprecated.

## Game and SDK access priorities

This is an acquisition and validation roadmap for overall SourceIO compatibility,
not a claim that the listed products are already supported and not a replacement
for the implementation order below. Steam availability was checked on 2026-10-11.
Only products with a direct public Steam install or purchase path, or tools granted
through such a product, are ranked.

Rank primarily by the breadth and uniqueness of engine/resource coverage unlocked,
official editor/compiler/SDK value, and ability to validate existing partial code.
Use acquisition cost only as a tiebreaker, followed by community reach. A priority
is complete only when its exit gate is reproducible; merely owning or downloading
the product does not close the gap.

Current local access includes Dota 2, Dota 2 Workshop Tools, and CS2 Workshop
Tools. The rows below state validation separately because installation alone does
not complete an exit gate.

| Priority | Game or tool access | Steam access | Major gap addressed and exit gate |
|---:|---|---|---|
| 1 | [Dota 2](https://store.steampowered.com/app/570/Dota_2/) plus [Dota 2 Workshop Tools](https://developer.valvesoftware.com/wiki/Dota_2_Workshop_Tools) | Installed; free game and optional tools | **Exit gate passed 2026-10-11.** Dota mounts as app ID 570; representative heroes, props, animations, `hero.vfx` and `global_lit_simple.vfx` materials, a texture, and the main world pass the installed-game profile. The bounded world report diagnoses all 5,127 entities, and a disposable Workshop addon compiles and re-imports a generated static model/material/texture fixture. Rarer shaders and deliberately deferred world semantics remain explicit boundaries rather than guessed support. |
| 2 | [Counter-Strike 2 Workshop Tools](https://developer.valvesoftware.com/wiki/Counter-Strike_2_Workshop_Tools) | Installed; free optional tools for the free CS2 installation | **Tools installed; exit gate open.** CS2 already supplies the strongest Source 2 E2E corpus, but the export plan still lacks the complete current official VMDL/VMAT contract and authoring workflow. **Exit:** a disposable addon authors, compiles, verifies, and re-imports the supported static model/material/texture vertical slice. |
| 3 | [Source SDK Base 2013](https://developer.valvesoftware.com/wiki/Source_SDK_Base_2013) Singleplayer and Multiplayer, with [Half-Life 2](https://store.steampowered.com/app/220/HalfLife_2/) as the canonical game corpus | Free Steam tools; Half-Life 2 is paid | Supplies the official Source 1 reference, compilers, and a core branch not covered by the TF2 E2E run. **Exit:** discover both SDK profiles, compile SMD/QC/VMT/texture inputs in a disposable mod, verify outputs, re-import them, and run representative Half-Life 2 models and maps without proprietary fixtures in git. |
| 4 | [Half-Life: Alyx](https://store.steampowered.com/app/546560/HalfLife_Alyx/) plus [Workshop Tools](https://developer.valvesoftware.com/wiki/Half-Life:_Alyx_Workshop_Tools) | Paid; tools included; VR is needed to play, not to inspect files | Adds Valve's richest non-CS2 Source 2 corpus. HLA detection, entities, VR shaders, and sample assets exist without full installed-game or tool E2E coverage. **Exit:** representative characters, props, VR materials, animations, physics, sounds, world entities, and one tool-compiled fixture pass reproducibly. |
| 5 | [s&box](https://store.steampowered.com/app/590830/sbox/) | Paid; editor included | Adds a public, current, fast-moving Source 2-derived pipeline. SourceIO has a detector and entity handler but no installed-product conformance or E2E matrix. **Exit:** editor-generated fixtures establish which shared Source 2 resources work, which require a profile, and which are explicitly out of scope. |
| 6 | [Source Filmmaker](https://store.steampowered.com/app/1840/Source_Filmmaker/) | Free | Directly serves SourceIO's Blender audience and adds the SFM DMX/session workflow. SFM is detected, but full session import is disabled as WIP, DMX UI support is camera-focused, and SFM maps reuse the TF2 entity handler. **Exit:** publish a supported/unsupported matrix covering sessions and cameras, models, animation, flexes, materials, particle metadata, Workshop mounting, and maps. |
| 7 | [Half-Life](https://store.steampowered.com/app/70/HalfLife/) as the GoldSrc baseline, with [Sven Co-op](https://store.steampowered.com/app/225840/Sven_Coop/) as a secondary corpus | Half-Life is paid; Sven Co-op is free | GoldSrc is a headline engine family but lacks installed-game E2E coverage comparable to TF2 and CS2. **Exit:** representative maps, skeletal and animated models, WAD textures, external texture resolution, and variant detection pass against both the canonical and expanded corpora. |
| 8 | [Alien Swarm: Reactive Drop](https://store.steampowered.com/app/563560/Alien_Swarm_Reactive_Drop/) plus its [SDK](https://store.steampowered.com/app/593000/Alien_Swarm_Reactive_Drop__SDK/) | Free game; paired Steam SDK tool | Adds a distinct Source branch and a controllable authoring corpus. A Swarm entity handler exists but has no active app-ID, detector/dispatch path, or E2E coverage for this product. **Exit:** SDK-authored map/model/material fixtures and shipped assets define and test the branch-specific parser and entity behavior. |
| 9 | [Left 4 Dead 2](https://store.steampowered.com/app/550/Left_4_Dead_2/) plus [Authoring Tools](https://developer.valvesoftware.com/wiki/Left_4_Dead_2_Authoring_Tools) | Paid; tools available to owners | Exercises a late Source 1 branch, infected shaders, model/animation variants, compressed maps, and game-specific entities. Existing shaders and handlers lack installed-game E2E coverage, and the specialized detector searches for `left4dead.exe` rather than the L4D2 executable. **Exit:** survivors, infected, props, animations, materials, campaign maps, and one tool-built fixture pass. |
| 10 | [Portal 2](https://store.steampowered.com/app/620/Portal_2/) plus [Authoring Tools](https://developer.valvesoftware.com/wiki/Portal_2_Authoring_Tools) | Paid; tools available to owners | Validates an existing detector/entity path and provides a base for public Portal 2 mod regressions. **Exit:** models, animations, portal/gel materials, maps, entities, and one authoring-tools fixture pass before promoting individual mods into the matrix. |
| 11 | [Aperture Desk Job](https://store.steampowered.com/app/1902490/Aperture_Desk_Job/) | Free; controller is required to play | Unlocks an unusually direct latent implementation: `SteamPalEntityHandler` and SteamPal shader support exist, but dispatch is commented out behind an obsolete app-ID assumption. **Exit:** identify the product correctly and verify representative models, materials, animations, maps, lights, and SteamPal-specific entities. |
| 12 | [The Lab](https://store.steampowered.com/app/450390/The_Lab/) and [SteamVR](https://store.steampowered.com/app/250820/SteamVR/) / SteamVR Home | Free; VR is required to run the experiences | Adds early Source 2/VR resource versions and Workshop content. The Robot Repair detector uses a pseudo app ID and its worlds fall back to generic entity handling. **Exit:** produce repeatable compatibility reports for older NTRO resources, Robot Repair, SteamVR Home assets, and world entities. |
| 13 | [Garry's Mod](https://store.steampowered.com/app/4000/Garrys_Mod/) | Paid | Adds GMA archives, mounted cross-game content, Workshop addons, and broad real-world model/material variation. Detection and a GMA provider exist without an installed-game/workshop E2E corpus. **Exit:** base content plus a small local or redistributable addon matrix validates archive handling, mount precedence, models, maps, materials, and HL2-derived entity fallback. |
| 14 | [Black Mesa](https://store.steampowered.com/app/362890/Black_Mesa/) | Paid | Adds a heavily customized modern Source branch. Dedicated detection and entity handling exist without installed-game E2E. **Exit:** Earth and Xen models, custom/PBR materials, maps, static props, and branch-specific lumps import with explicit limitations. |
| 15 | [Dota Underlords](https://store.steampowered.com/app/1046930/Dota_Underlords/) and [Artifact](https://store.steampowered.com/app/583950/Artifact/) | Free | Provides a low-friction secondary Source 2 regression corpus after Dota 2, HLA, and CS2 profiles stabilize. **Exit:** conformance reports identify real resource-version and shader drift; do not add game-specific behavior without a demonstrated difference. |
| 16 | [Titanfall 2](https://store.steampowered.com/app/1237970/Titanfall_2/) and [Apex Legends](https://store.steampowered.com/app/1172470/Apex_Legends/) | Titanfall 2 is paid; Apex is free; EA account integration applies | Represents a deliberate expansion into divergent Respawn BSP/RPak and live-service formats without an official SDK. Existing Titanfall support targets an earlier branch and its detector is disabled. **Exit:** first complete a format and integration feasibility report; claim support only after representative archives, maps, models, materials, and animations run end to end. |
| 17 | [INFRA](https://store.steampowered.com/app/251110/INFRA/) and [Vampire: The Masquerade - Bloodlines](https://store.steampowered.com/app/2600/Vampire_The_Masquerade__Bloodlines/) | Paid | Lower-yield checks for large-map modern Source behavior and early BSP v17 behavior. Both already have detector or entity specialization. **Exit:** add focused installed-game reports only after the main engine/tool baselines above are stable. |

Deadlock is excluded while its Steam page remains invite-only. Titanfall 1,
Portal 2: Community Edition, region-restricted Vindictus access, and delisted or
unreleased products are also excluded until they gain a direct public Steam
acquisition path. Recheck this snapshot before spending money or starting a
priority item.

Proprietary game and tool files remain local and must never be committed. Installed
game checks are opt-in and should emit machine-readable reports plus installation
and manifest hashes. Commit only generated fixtures, fixtures whose redistribution
is explicitly permitted, or download recipes pinned to an open-licensed source.
Prefer generic format improvements; add a game-specific profile, shader, detector,
or entity handler only when a controlled corpus proves the branch difference.

## External tools reviewed

| Tool/reference | Useful precedent | License / constraint | Plan |
|---|---|---|---|
| [Blender Source Tools](https://github.com/Artfunkel/BlenderSourceTools) | SMD/DMX export, export sets, QC compilation, scene-state handling | GPL v2-or-later headers | Use behavior and test cases as inspiration only; do not copy code into SourceIO's MIT codebase. |
| [SourceOps](https://github.com/bonjorno7/SourceOps) | Source 1 model configuration, SMD/FBX and QC stages, compile/view workflow, game validation | GPL-3.0 | Use its separation of export/generate/compile stages as UX inspiration only. |
| [TheJoshCode/Blender-To-And-From-Source](https://github.com/TheJoshCode/Blender-To-And-From-Source) | One-click CS2 FBX/TGA/VMAT staging concept | MIT | Retain the product idea, but rewrite because the reviewed implementation mutates Blender data, exports globally, collides paths, and does not compile or validate. |
| [Blender-Source2-Tool](https://github.com/TiO2EvoLve/Blender-Source2-Tool) | Simple FBX + VMDL + VMAT authoring flow and ModelDoc template | MIT | Use as a format/workflow reference; validate every schema field against CS2 Workshop Tools and rewrite non-destructively. |
| [Crowbar](https://github.com/ZeqMacaw/Crowbar) | Familiar Source compiler configuration and log UX | Custom / no asserted SPDX license | UX inspiration only; no code adaptation. |
| [Source 2 Resource Compiler documentation](https://www.source2.wiki/EngineTools/ResourceCompiler) | `content/` to `game/` boundary, `-i`, `-game`, force modes, child resources, result interpretation | Documentation reference | Treat Source 2 source assets as authoritative and validate the mirrored compiled outputs. |
| [Source 2 content/game documentation](https://www.source2.wiki/Basics/working-on-content/content-and-game) | Mirrored paths, naming constraints, stale compiled assets, source/compiled ownership | Documentation reference | Encode path rules and source-to-game mapping in preflight and target profiles. |

Any code adaptation from an MIT project must still receive a provenance review and
appropriate attribution. GPL and custom-license projects are behavior references
only. The preferred implementation is original code built on SourceIO's existing
MIT primitives and validated against files emitted by official tools.

## Architecture

### 1. Engine-neutral export domain

Generalize the stable diagnostics, geometry, provenance, bundle, and staging
primitives in `library/source2/export` into a Blender-free authoring service under
`library/`. Keep the static reconstruction API intact while adding typed models
such as:

- `ExportRequest`: engine, profile, scope/grouping intent, output root, overwrite
  policy, and compile policy.
- `TargetProfile`: game/content roots, compiler capabilities, path rules, unit and
  axis conventions, and supported material preset.
- `AssetGraph`: model assets and their mesh, material, texture, and dependency
  records.
- `Diagnostic`: stable code, severity, owning asset, message, and suggested fix.
- `ArtifactPlan`: every source file and expected compiled file before anything is
  written.
- `ExportManifest`: profile, Blender source, normalized asset names, file hashes,
  dependencies, generated files, compiler commands, and results.
- `CompileResult`: exact command, working directory, output streams, exit status,
  expected outputs, and verified outputs.

The shared authoring core owns:

- lower-case engine-safe path normalization;
- root containment and traversal rejection;
- reserved names, path length, and extension rules;
- collision detection across all generated and compiler-derived paths;
- deterministic ordering and manifests;
- changed/unchanged/existing-file classification;
- temporary staging followed by per-file atomic replacement, reusing the existing
  tested Source 2 staging behavior;
- structured diagnostics and explicit failure states.

### 2. Blender extraction adapter

Extend the existing non-destructive Source 2 reconstruction adapters under
`blender_bindings/` with a general adapter that converts the chosen scope into the
engine-neutral `AssetGraph`.

- Resolve selected, active-collection, and scene scopes deterministically.
- Support combined and batch-per-object grouping.
- Read evaluated meshes so optional modifiers can be applied without changing the
  original datablocks.
- Triangulate only temporary/evaluated data.
- Preserve object mode, active object, selection, hidden state, material names,
  image paths/formats/sizes, and scene settings on success and failure.
- Carry positions, normals, UV0, face material assignments, and object transforms.
- Apply target-profile unit/axis conversion in one tested location.
- Detect negative scale, missing UVs, empty meshes, invalid material slots, and
  unsupported first-phase data before writing.

Material extraction will have explicit adapters for:

- SourceIO-imported Source 1 materials;
- SourceIO-imported Source 2 materials using their retained provenance and
  canonical resource identities;
- simple active Principled BSDF graphs;
- per-material export overrides for shader preset, texture slots, alpha mode,
  physics surface, and normal-map orientation.

Texture staging will:

- copy compatible file-backed images when no conversion is needed;
- write from temporary buffers/datablocks for packed, generated, or converted
  images;
- preserve source datablocks;
- distinguish sRGB color/emission from linear normal/roughness/metal/AO data;
- deduplicate by semantic use plus content hash;
- make normal-Y conversion an explicit profile/slot operation;
- reject unsupported UDIM, sequence, or ambiguous channel-packed inputs until a
  defined adapter exists.

### 3. Target profiles and compiler runner

Implement profile discovery on top of the existing game detectors.

The CS2 profile validates:

- a `content/csgo_addons/<addon>` authoring root;
- the matching `game/csgo_addons/<addon>` output root;
- `game/bin/win64/resourcecompiler.exe`;
- the relevant `gameinfo.gi`;
- CS2 naming and path-length constraints.

The Source SDK 2013 profile validates:

- the mod/game directory and `gameinfo.txt`;
- model source and materials source roots;
- `studiomdl.exe` and `vtex.exe`;
- the compiled `models/` and `materials/` destinations.

The shared runner:

- never invokes a shell;
- streams/captures output for UI and CLI consumers;
- supports cancellation without name-based process killing;
- treats nonzero status, missing expected output, or stale output as failure;
- writes a bounded log referenced by the manifest;
- allows `Recompile Last Export` only after manifest paths are revalidated.

### 4. Source 2 / CS2 backend

The implemented first authoring backend emits:

- one FBX per combined model asset, or one per object in batch mode;
- a current ModelDoc VMDL for a `static_prop_model`;
- VMAT files using the validated CS2 `csgo_complex.vfx` subset;
- source TGA/PNG textures under the addon's content tree;
- a SourceIO export manifest and preflight report.

Implemented contract details:

- Capture a minimal VMDL and VMAT saved by the current CS2 Workshop Tools as golden
  fixtures before locking the serializer contract.
- Reuse and extend the existing typed ModelDoc generator rather than maintaining
  a raw string template; validate every authoring-only field against official
  Workshop Tools output.
- Wrap Blender's FBX exporter with a context snapshot/restore guard and a temporary
  export selection. Check that the FBX operator exists and report its absence.
- Support the verified opaque base-color/tint subset. Normal, non-default
  roughness/metalness, AO, non-base texture roles, alpha blend/test, emission,
  and procedural graphs remain blocked until each mapping is contract-backed.
- Omit or explicitly configure physics surface data; never guess it from material
  names.
- Generate deterministic material remaps from FBX material names to VMAT paths.
- Compile VMAT dependencies and the VMDL with `resourcecompiler.exe` when requested.
- Verify the mirrored `_c` outputs and then re-import the compiled VMDL, VMAT, and
  VTEX resources with SourceIO for the integration test.

### 5. Source 1 backend

The second backend will emit:

- a static reference SMD with a single root bone, evaluated triangles, normals,
  UVs, and face material names;
- a deterministic QC containing the model path, static-prop setup, body/reference
  mesh, material search path, surface property, and required idle/reference
  sequence;
- `VertexLitGeneric` VMT files for the supported static-model material subset;
- source textures and VTEX sidecar settings;
- a SourceIO export manifest and preflight report.

The initial Source 1 material subset is base color/tint, alpha mode, normal map,
and explicit phong parameters where representable. Roughness, metalness, AO, and
other PBR concepts are not silently converted to unrelated Source 1 parameters;
future opt-in conversion/baking presets can be added with visual regression tests.

When compilation is requested:

- invoke `vtex.exe` for staged texture sources;
- invoke `studiomdl.exe -nop4 -game <mod> <qc>`;
- capture all compiler diagnostics;
- verify MDL, VVD, the expected VTX variant, and VTF outputs;
- re-import the compiled outputs with SourceIO for integration validation.

### 6. UI and CLI

Extend the existing static reconstruction entries with an authoring menu:

- `Source 2 / CS2 Static Assets`;
- `Source 1 Static Assets`;
- `Recompile Last SourceIO Export`.

The export dialog exposes target profile, asset name/path, scope, grouping,
modifier/transform policy, material preset/overrides, overwrite policy, and
`Compile after export`. It shows a preflight summary before execution and directs
the user to full diagnostics when blocked.

`blender -c sourceio export-cs2` calls the same service while retaining the
existing `export-modeldoc` and `export-hammer` static reconstruction commands.
`recompile-cs2` consumes a validated content-relative manifest. UI and CLI runs
over the same fixture produce equivalent manifests and authored files. A general
multi-profile `export` command remains future work for when a second backend
exists.

## Implementation todos

1. [x] **Defining the export contract and fixtures**
   - Record the supported format/material matrix and diagnostic policy.
   - Capture minimal official CS2 VMDL/VMAT and Source SDK 2013 QC/SMD/VMT fixtures.
   - Add golden parse/serialize/compile expectations without checking proprietary
     tool binaries or game content into the repository.

2. [x] **Building the shared export core**
   - Generalize the existing Source 2 domain, diagnostics, safe staging, and export
     bundles with typed requests, profiles, asset graphs, artifact planning,
     manifests, collision detection, and compiler result types.
   - Add deterministic unit tests for every safety boundary.

3. [x] **Building the non-destructive Blender adapter**
   - Implement scope/grouping, evaluated mesh extraction, transform conversion,
     material translation, texture staging, retained-provenance consumption, and
     complete context restoration.
   - Add headless Blender fixtures for success and injected-failure paths.

4. [x] **Adding shared UI and CLI entry points**
   - Register authoring menus/operators and properties alongside the existing
     static reconstruction entries using extension-safe relative imports.
   - Add the general CLI export parser and machine-readable reports without
     duplicating the existing static commands.
   - Keep operators thin; all behavior belongs to the shared service.

5. [x] **Implementing the CS2 authoring backend**
   - Add FBX, current VMDL, CS2 VMAT, texture, material-remap, and content-layout
     generation.
   - Consume the retained Source 2 import provenance and existing typed ModelDoc
     primitives for faithful round trips.

6. [x] **Integrating and validating Resource Compiler**
   - Add CS2 target discovery, compiler invocation, output verification, logs,
     recompile support, and an opt-in Workshop Tools integration test.

7. [x] **Completing the CS2 vertical slice**
   - Validate UI/CLI parity, state preservation, collision blocking, compiled
     re-import, geometry/material/texture tolerances, and extension packaging.

8. [ ] **Implementing the Source 1 authoring backend**
   - Add static SMD, QC, VMT, source-texture, and VTEX-sidecar generation.
   - Refactor only the reusable portions of the existing VTF path and preserve its
     current image-editor behavior.

9. [ ] **Integrating Source 1 compilers**
   - Add Source SDK 2013 profile discovery, VTEX and StudioMDL invocation, output
     verification, logs, recompile support, and opt-in compiler integration tests.

10. [ ] **Completing the Source 1 vertical slice**
    - Validate UI/CLI parity, state preservation, compiled re-import, geometry and
      material tolerances, and regression coverage for direct VTF export.

11. [x] **Documenting and hardening the release**
    - Update README usage, CLI help, supported/unsupported matrices, target setup,
      troubleshooting, and the Blender manifest's file-write permission wording.
    - Run focused unit/headless/compiler tests, the import regression suite, clean
      register/unregister smoke tests, and extension builds.

## Validation and acceptance gates

### Pure tests

- Name normalization, reserved names, path containment, path length, duplicate
  compiler-derived names, and collision suggestions.
- Stable artifact order and byte-for-byte stable manifests.
- VMAT, VMDL, SMD, QC, VMT, and sidecar golden serialization.
- Unit and axis conversion, winding, normals, UVs, and material-slot mapping.
- Material adapter precedence and explicit diagnostics for unsupported graphs.
- Compiler command construction, missing executable, nonzero exit, stale/missing
  outputs, cancellation, and manifest revalidation using mocked processes.

### Headless Blender tests

- Generated scene with multiple meshes, shared and distinct materials, packed and
  file-backed images, modifiers, custom normals, UVs, and deliberate name
  collisions.
- Selected, collection, scene, combined, and batch scopes.
- No writes on blocking preflight errors.
- No changes to object mode, active object, selection, transforms, mesh/material
  datablocks, image dimensions, file paths, formats, or colorspaces after success
  or injected failure.
- UI service and CLI service produce the same authored file hashes and manifest.
- Register/unregister/register succeeds under the renamed Blender extension package.

### Opt-in official-tool integration tests

- A dedicated disposable CS2 addon fixture is staged, compiled, checked for expected
  mirrored outputs, and re-imported.
- A dedicated disposable Source SDK 2013 mod fixture is staged, compiled, checked
  for MDL/VVD/VTX/VTF outputs, and re-imported.
- Geometry bounds, triangle/material assignments, and decoded texture pixels stay
  within documented tolerances.
- Cleanup targets only the test fixture directories resolved during setup.
- Missing proprietary tools skip opt-in integration tests; requesting compilation
  in the product still reports their absence as an error.

### Release gate

- Existing import and VTF-export behavior remains intact.
- No unsupported feature is represented as exported successfully.
- Source files and a usable diagnostic report remain after compiler failure.
- The README and UI accurately describe this as authoring/staging plus optional
  compilation, not direct universal binary export.

## Later roadmap

After both static vertical slices are stable:

1. Add skeletal reference meshes and skin weights.
2. Add animation clips, flex/morph targets, and engine-specific action selection.
3. Add physics/collision, LODs, bodygroups, skins, attachments, and richer material
   presets.
4. Add Source 1 VMF and full Source 2 VMAP/world authoring as separate projects
   with their own intermediate scene representation and validation; keep the
   existing static Hammer reconstruction explicitly separate.
5. Migrate the experimental export node tree onto the shared `AssetGraph`, or
   deprecate it if the property-driven workflow proves clearer.
6. Add additional game profiles and tested Wine-based compiler execution without
   weakening the profile or error model.

## Notes and considerations

- Source formats differ by game branch. Profiles isolate those differences and
  prevent “works for CS2” from being advertised as universal Source 2 support.
- Official-tool output is the acceptance oracle; external community exporters are
  inspiration and test-case sources, not correctness authorities.
- Imported compiled assets cannot always reconstruct their original authoring
  graph. Provenance improves round trips, but lossy cases must remain visible.
