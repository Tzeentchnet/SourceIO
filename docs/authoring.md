# Static asset authoring

SourceIO provides two supported static-authoring workflows:

- **CS2 Workshop static asset** writes editable CS2 content and can run
  Resource Compiler.
- **Source 1 static asset** writes editable SMD/QC/VMT/texture sources and can
  run VTEX and StudioMDL.

Both workflows use the same non-destructive Blender extraction, preflight,
atomic staging, manifest, diagnostics, and recompile model. Unsupported inputs
fail before success is reported.

These workflows author new static assets. The separate `export-modeldoc` and
`export-hammer` commands reconstruct limited editable Source 2 files from
Blender data and cannot recover information absent from compiled resources.

## Shared behavior

- Selected meshes are the default scope; active collection and scene scopes are
  also available.
- Objects can be combined into one asset or exported independently in batch
  mode.
- Evaluated modifiers are exported without modifying source datablocks,
  selection, mode, transforms, or render settings.
- Names and paths are normalized and checked for traversal, reserved names,
  collisions, and target-root containment.
- Existing editable files are protected unless **Overwrite** or `--overwrite`
  is explicit.
- Compilation is opt-in. SourceIO invokes official tools and verifies fresh
  expected outputs instead of writing custom compiled binaries.
- Every successful staging operation writes a deterministic manifest and
  preflight report.
- Recompile validates the target and every authored artifact's size and SHA-256
  before launching a compiler.
- Compiler failures preserve editable files and a bounded diagnostic log.

## CS2 Workshop static assets

Use *File > Export > CS2 Workshop static asset* or:

```text
blender -c sourceio export-cs2 --install-root ROOT --addon ADDON --asset NAME [--scope selected|collection|scene] [--batch] [--overwrite] [--compile]
blender -c sourceio recompile-cs2 --install-root ROOT --addon ADDON MANIFEST
```

### Target

- **CS2 installation** is the root containing both `content` and `game`, such
  as `<steam>/common/Counter-Strike Global Offensive`.
- **Workshop addon** is a lowercase addon name mirrored below
  `content/csgo_addons` and `game/csgo_addons`.
- **Asset name** is a lowercase path-safe model name.

Default authored paths are below `models/sourceio`, `materials/sourceio`, and
`materials/sourceio/textures` inside the content addon.

`SOURCEIO_CS2_WORKSHOP_ROOT` can replace `--install-root`:

```powershell
$env:SOURCEIO_CS2_WORKSHOP_ROOT = "<steam>\common\Counter-Strike Global Offensive"
blender -c sourceio export-cs2 --addon my_addon --asset my_prop --compile
blender -c sourceio recompile-cs2 --addon my_addon sourceio/manifests/my_prop.sourceio.json
```

### Outputs

The workflow emits:

- FBX geometry;
- ModelDoc 36 VMDL;
- `csgo_complex.vfx` VMAT files;
- 32-bit TGA source textures;
- `sourceio/manifests/<asset>.sourceio.json`;
- `sourceio/reports/<asset>.preflight.json`;
- a bounded compile log when compilation fails.

### Supported material graph

An imported or explicitly assigned existing `.vmat` is reused first. Otherwise
the material must have exactly one active Material Output connected directly to
one Principled BSDF.

Supported inputs are:

- **Base Color:** constant color or one directly connected Image Texture.
- **Normal:** unlinked, or an Image Texture Color output through a
  default-strength tangent-space Normal Map node. SourceIO writes
  `TextureNormal` and converts Blender's green-channel handedness.
- **Roughness:** constant or directly connected grayscale Image Texture.
  SourceIO writes linear `TextureRoughness`; Resource Compiler packs it into the
  normal texture's alpha.
- **Metallic:** constant or directly connected grayscale Image Texture.
  SourceIO writes `TextureMetalness` with `F_METALNESS_TEXTURE`. Resource
  Compiler packs opaque metalness into color alpha, or emits `g_tMetalness` when
  alpha testing occupies that channel.
- **Alpha test:** Blender's **Dithered** surface method, using a constant alpha
  or the Alpha output of the Base Color image. SourceIO writes `F_ALPHA_TEST`,
  the Blender threshold, and linear `TextureTranslucency`.

True blended transparency, emission, other texture roles, and procedural graphs
remain blocked until their mappings have official-tool contracts.

### Compilation

Compilation requires CS2 Workshop Tools and
`game/bin/win64/resourcecompiler.exe`. SourceIO verifies mirrored compiled
model, material, and texture outputs. A failed compile leaves the editable
content and writes `sourceio/logs/<asset>.compile.log`.

This is a CS2 static-asset workflow, not a Dota exporter, general Source 2
binary exporter, skeletal exporter, or map-authoring tool.

## Source 1 static assets

Use *File > Export > Source 1 static asset* or:

```text
blender -c sourceio export-source1 --game-root GAME --content-root CONTENT --asset NAME [--scope selected|collection|scene] [--batch] [--overwrite] [--compile]
blender -c sourceio recompile-source1 --game-root GAME --content-root CONTENT MANIFEST
```

### Target

- **Game root:** mod or game directory containing `gameinfo.txt`, such as
  `<TF2>/tf`.
- **SDK content root:** directory below which SourceIO may create `modelsrc`,
  `materialsrc`, and `sourceio` metadata folders.
- **Compiler root:** optional directory containing `vtex.exe` and
  `studiomdl.exe`. When omitted, SourceIO checks the normal adjacent `bin`
  directory and then `SOURCEIO_SOURCE1_TOOL_ROOT`.
- **Asset name:** path-safe model name. Default compiled paths are below
  `models/sourceio/<asset>` and `materials/models/sourceio/<asset>`.

The roots can be supplied through environment variables:

```powershell
$env:SOURCEIO_SOURCE1_GAME_ROOT = "<steam>\common\Team Fortress 2\tf"
$env:SOURCEIO_SOURCE1_CONTENT_ROOT = "<steam>\common\Team Fortress 2"
$env:SOURCEIO_SOURCE1_TOOL_ROOT = "<steam>\common\Team Fortress 2\bin"
blender -c sourceio export-source1 --asset my_prop --compile
blender -c sourceio recompile-source1 sourceio/manifests/my_prop.sourceio.json
```

### Outputs

The workflow emits:

- static reference SMD;
- deterministic QC;
- `VertexLitGeneric` VMT files;
- 32-bit TGA source textures and VTEX settings;
- deterministic manifest and preflight report;
- bounded compile log when compilation fails.

### Supported materials

- An imported Source 1 VMT or
  `sourceio_export_source1_material_path` is reused through a local Patch VMT.
- A direct Principled graph can author base color/tint, alpha test or blending,
  a default-strength tangent-space normal map, and two-sided state.
- Roughness and metalness are not guessed into Source 1 phong controls.
- Explicit phong uses `sourceio_source1_phong`; optional custom properties are
  `sourceio_source1_phong_exponent`, `sourceio_source1_phong_boost`,
  `sourceio_source1_phong_tint`, and
  `sourceio_source1_phong_fresnel_ranges`.
- Unsupported procedural graphs and unrelated PBR slots block export.

### Compilation

SourceIO runs `vtex.exe` and `studiomdl.exe`, sets the `VPROJECT` environment
required by Valve's VTEX, and verifies VTF, MDL, VVD, and DX90 VTX outputs.
Compiler failures leave editable files and write
`sourceio/logs/<asset>.compile.log` below the SDK content root.

The installed TF2 gate and both Source SDK Base 2013 profiles verify positions,
UVs, winding, normals, material identity, and texture data after official
compilation and SourceIO re-import. The SDK 2013 gate compiles into disposable
mod/content roots; it never writes generated files into Valve's shipped game
directories.

This is a static-prop workflow. Skeletal models, animations, physics, LODs,
bodygroups, skins, flexes, and map authoring are outside the current slice.

## Static Source 2 reconstruction

`export-modeldoc` writes ModelDoc plus static DMX from selected Blender meshes.
`export-hammer` writes a static Hammer VMAP from the active imported map
collection. Both include provenance and loss reports and stage outputs
atomically.

These commands do not promise a lossless compiled-to-authored round trip.
Dynamic rigs, complete animation authoring, original brush topology, and data
not present in compiled resources are not fabricated.

## Legacy export nodes

The old **SourceIO model definition** node tree is deprecated. Its node and
socket types remain registered so existing `.blend` files load, but it no longer
appears in the Add menu and its partial QC evaluator is disabled. Use *File >
Export* or the matching command-line workflow.
