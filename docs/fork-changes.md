# Changes in this Blender 5.2 fork

This document summarizes the major differences from upstream
[REDxEYE/SourceIO](https://github.com/REDxEYE/SourceIO). It is background, not a
release changelog; Git history and GitHub releases remain authoritative for
individual changes.

## Blender 5.2 modernization

- Removed Blender 4 compatibility branches and deprecated APIs.
- Adopted Blender 5.2 material transparency, slotted actions, custom-normal,
  node, and extension APIs.
- Packaged SourceIO as split-platform Blender extensions.
- Added a manylinux-compatible Linux native library build with ABI policy
  checks.
- Batched vertex-weight assignment and modernized mesh creation.
- Added context-safe register/unregister behavior and extension-safe imports.

## Import performance

- Material and image lookup use indexed identities instead of repeated linear
  scans.
- 8-bit texture data remains byte-based through decode and PNG staging.
- Normal-map and SSBump conversion happens before image creation.
- Temporary import PNGs use Blender's bundled Pillow at a measured fast
  compression level.
- Single-frame 2D DXT1/DXT5 VTFs keep their compressed mip payloads through
  default-enabled direct DDS staging; the add-on preference can force decoded
  PNG, and transformed normals and unsupported layouts always retain that path.
- Source 2 aggregate placeholders batch selected draw calls and reuse decoded
  vertex/index buffers.
- Source 2 model draw calls reuse resolved, loaded materials with matching
  texture settings and import scale.
- Optional bounded timings are available through
  `SOURCEIO_PERF_TRACE=1`.

Current reproducible benchmark evidence is maintained in
[`plan.md`](../plan.md) rather than copied here.

## Source 2 animations

- Imports embedded animations, external groups, included models, and older
  NTRO-format animations.
- Supports position, rotation, scale, additive motion, looping, root motion,
  morph/user/data channels, NM float curves, and rich event payloads.
- Imports `.vanim_c` and `.vagrp_c` directly onto compatible selected
  armatures.
- Discovers and imports AnimationGraph 2 `.vnmclip_c` clips by wildcard.
- Matches clips to model skeletons by bone name.
- Preserves AnimGraph documents for inspection without executing them.
- Honors compiled default mesh-group masks for character models.

## Source 2 textures, sounds, and resources

- Added ETC2/EAC, BC6H HDR, wrapped PNG/JPEG/WEBP, integer, float16, and
  float32 texture formats.
- Added explicit mip selection, arrays, volume slices, cube arrays, embedded
  images, sprite sheets, and cubemap-radiance metadata.
- Shared mip and packed-channel settings across direct texture, material,
  model, map, and placeholder imports.
- Added deterministic layered content resolution for loose files, mounted
  VPKs, and nested archives.
- Added VSND v1-v4 import and extraction, including compiler companion data
  when present.
- Added provenance-aware static ModelDoc/DMX and Hammer reconstruction exports.

## Source 2 maps and materials

- Added CS2 3D skyboxes, world environment setup, sunlight, lamp shapes, light
  blockers, readable compiler-generated object names, and aggregate fragment
  placement.
- Added game-specific handling for common CS2 character, weapon, unlit,
  overlay, generic, environment, and blended-environment shaders.
- Added color correction, tint masks, roughness, metalness, normal/detail
  layers, per-layer transforms, vertex paint, height blending, alpha test,
  additive/modulate modes, and light transmission where verified.
- Added Dota 2 world-layer collections and material handling for terrain,
  crystals, water, hero, and global-lit assets.
- Added full-resolution alpha-test sampling to prevent distant fences,
  foliage, and grates from disappearing in Blender's mipmaps.
- Added Source-style sampler interpolation and independent S/T clamping.

## Source 1 materials and models

- Added a Blender 5.2 Principled VertexLitGeneric path alongside the existing
  BlenderVertexLitGeneric option.
- Corrected phong masks, exponent mapping, Fresnel ranges, envmaps, tinting,
  additive materials, `$alpha`, `$selfillumtint`, detail colorspaces, and
  `$basealphaenvmapmask`.
- Added opt-in Principled backface culling while keeping two-sided behavior as
  the default.
- Added Source 1 overlays clipped to their target faces and displacements.
- Added bone collections and role/side colors for common Source MDL versions.
- Added a flex-controller panel with stereo controls, keying, reset, and access
  through the active armature when it has one flex-enabled child mesh.
- Imports Source 1 include-model animations by default when animation import is
  enabled.
- Supports one action per animation or compact actions grouped by include
  model.
- Corrected GoldSrc animation import and Source v49+ frame-animation decoding.

## VTF editing

- Preserves VTF headers, flags, frames, sprite-sheet sequences, and resource
  metadata on Blender images, including VTFs reconstructed from TTH/TTZ pairs.
- Exports complete images, individual frames, `.sht`, `mksheet` `.mks`, and
  JSON metadata for external editing.
- Handles animated and HDR VTF data.
- Validated both supported rebuild workflows against every sprite-sheet VTF
  shipped with Team Fortress 2.

See [VTF editing](user-guide.md#editing-vtf-textures-and-sprite-sheets).

## Static authoring

- Added non-destructive CS2 Workshop static-asset authoring with FBX, ModelDoc
  VMDL, VMAT, TGA, Resource Compiler integration, manifests, and recompile.
- Added Source 1 static-asset authoring with SMD, QC, VMT, TGA, VTEX settings,
  VTEX/StudioMDL integration, manifests, and recompile.
- Added verified CS2 base color, normal, roughness, metalness, and alpha-test
  mappings.
- Added Source 1 imported-VMT reuse, base color/tint, alpha, normal, two-sided,
  and explicit phong authoring.
- Deprecated the incomplete export-node prototype while preserving `.blend`
  load compatibility.

See [Static asset authoring](authoring.md).

## Notable correctness fixes

- Corrected CS2 sky assignment and Source 2 sun direction.
- Converted CS2 light brightness from stops and imported rectangular, disc,
  barn, and spot-shaped lights correctly.
- Restored CS2 unlit, translucent, tinted, anisotropic, metalness, and
  environment material behavior.
- Fixed repeated aggregate world fragments that previously disappeared.
- Corrected Source 2 texture transform direction, rotation, and pivot handling.
- Restored deforming custom normals for armatures and shape keys.
- Fixed Source 1 rim lighting, flex scaling, default prop poses, and animation
  section rotations.
- Fixed HDR Source 2 channel order and precision.
- Fixed Source 2 external mesh morphs and malformed compressed normal pixels.
- Fixed map VPKs whose internal map name differs from the archive name.
- Fixed brush entities without face data, multi-frame zstd KV3, older NTRO
  external references, case/path material resolution, and water bottom
  materials.
- Fixed edit-mode leaks, progress-bar leaks, file-handle leaks, duplicate VTF
  menu entries, and registration leaks.

## Deliberate boundaries

- SourceIO does not execute AnimGraphs or reproduce runtime graph evaluation.
- Blood, patch, sticker, and similar game-populated layers are not fabricated.
- Dota water flow driven at runtime is not simulated.
- Static reconstruction does not invent rigs, animation sources, brush
  topology, or other data absent from compiled resources.
- Experimental particle, cloth, and compiled-writing APIs remain
  capability-gated and are not advertised as general Blender workflows.
