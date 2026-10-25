# SourceIO user guide

## Importing assets

Use *File > Import > Source Engine Assets* or drag a supported file into
Blender. SourceIO chooses the importer from the extension and, for `.bsp`, from
the file header.

Common drag-and-drop formats include:

- GoldSrc and Source: `.mdl`, `.bsp`, `.vtf`, and `.vmt`;
- Source 2: `.vmdl_c`, `.vmat_c`, `.vtex_c`, `.vphys_c`, `.vmap_c`, map
  `.vpk`, and `.vsnd_c`;
- camera `.dmx` files.

Source 2 animation resources (`.vnmclip_c`, `.vanim_c`, and `.vagrp_c`) can be
dropped onto a compatible selected armature. A standalone `.vanim_c` still
needs its owning animation group's decode key.

### Models and animations

Model import options control materials, physics, attachments, animations, world
scale, and game-specific behavior.

Source 1 include-model animations load by default when animations are enabled.
They can be kept as individual actions or compacted by source model. Source 1
models with flexes expose their controllers under *SourceIO utils > Flex
controllers* when the face mesh is active.

Source 2 models can import embedded animations, external animation groups,
included models, and matching animation-graph clips. Clip matching accepts
comma-separated wildcard patterns such as `idle*, run_n_*`, or `*` for every
reachable clip. SourceIO imports clip data but does not execute an AnimGraph.

### Maps and placeholders

Map import creates the world and entity collections first. Referenced props and
compiler-generated merged geometry may remain as placeholders:

1. Select the placeholders you need.
2. Run *Load Entity*.
3. Keep *Use instances* enabled when repeated models do not need independent
   editing.

Loading only selected placeholders is the most effective way to control import
time and scene size. Disabling *Import materials* is useful for geometry-only
work.

### External texture cache

Without a texture cache, decoded PNG and EXR images are packed into the
`.blend`. A complete CS2 map can therefore produce a file much larger than its
map VPK because most textures come from shared game archives.

Set *Scene Properties > SourceIO configuration > TextureCachePath* before
importing to keep decoded textures external. Later imports can reuse the same
cache. The cache must remain available beside the `.blend`; *File > External
Data > Pack Resources* embeds those images again.

For scale, measured fully loaded CS2 maps produced approximately:

| Map | Map VPK | Packed `.blend` | Without embedded images |
|---|---:|---:|---:|
| `de_dust2` | 250 MiB | 1.6 GiB | 167 MiB |
| `de_inferno` | 432 MiB | 3.0 GiB | 340 MiB |

Actual import time and file size depend on enabled options, game updates,
hardware, and the selected placeholders. Current reproducible performance
baselines are kept in [`plan.md`](../plan.md), not in this user guide.

### Source 1 backface culling

Principled Source 1 materials are two-sided by default. To apply Source-style
backface culling to newly imported Principled VertexLitGeneric materials, open
*Edit > Preferences > Add-ons*, expand SourceIO, and enable **Cull Principled
VertexLitGeneric backfaces**.

Materials with `$nocull 1` remain two-sided. BlenderVertexLitGeneric materials
retain their existing culling behavior regardless of this preference.

## Command line

Enable the extension, then use Blender's `sourceio` command:

```text
blender -c sourceio import [--scale S] [--no-materials] [--animations] [--clips PATTERNS] [--mip-level N] [--no-packed-channels] [--output out.blend] FILE [FILE ...]
blender -c sourceio extract-sound [--output DIR] [--overwrite] FILE.vsnd_c [FILE.vsnd_c ...]
blender -c sourceio extract-vtf [--output DIR] [--format tga|png] [--overwrite] FILE.vtf [FILE.vtf ...]
blender -c sourceio export-modeldoc [--overwrite] OUTPUT.vmdl
blender -c sourceio export-hammer [--overwrite] OUTPUT.vmap
blender -c sourceio export-cs2 --install-root ROOT --addon ADDON --asset NAME [--scope selected|collection|scene] [--batch] [--overwrite] [--compile]
blender -c sourceio recompile-cs2 --install-root ROOT --addon ADDON MANIFEST
blender -c sourceio export-source1 --game-root GAME --content-root CONTENT --asset NAME [--scope selected|collection|scene] [--batch] [--overwrite] [--compile]
blender -c sourceio recompile-source1 --game-root GAME --content-root CONTENT MANIFEST
```

Important options:

- `--clips` imports Source 2 animation-graph clips whose path or name matches the
  supplied patterns.
- `--mip-level` and `--no-packed-channels` apply the same Source 2 texture
  decoding choices used by the UI.
- `--output` saves the final Blender scene after import.
- extraction and authoring commands protect existing files unless
  `--overwrite` is explicit.

`export-modeldoc` and `export-hammer` are lossy static reconstruction commands.
`export-cs2` and `export-source1` are editable authoring workflows documented in
[Static asset authoring](authoring.md).

## Editing VTF textures and sprite sheets

Source VTF particle textures can contain multiple frames plus a sprite-sheet
resource describing animation sequences, frame timing, and atlas rectangles.
SourceIO preserves this metadata and can export everything needed to edit and
rebuild the texture with Valve's `mksheet` and `vtex`.

### Preserved metadata

Imported images can contain:

- `sourceio_vtf_version`, `sourceio_vtf_flags`, and
  `sourceio_vtf_frames`;
- `sourceio_sprite_sheet_sequences`;
- `sourceio_vtf_metadata`, a JSON copy of the VTF header and resources,
  including sheet data, LOD clamps, CRC, VTFLib keyvalues, and Strata Source
  hotspot rectangles.

Blender does not expose image custom properties in the Image Editor. Read them
from Blender's Python console or export the image and inspect `name.vtf.json`.
Sprite sheets are retained as data but are not animated in Blender.

### Exporting for editing

In the Image Editor, choose *Image > Export for External Editing...*, or run:

```text
blender -c sourceio extract-vtf --output DIR [--format tga|png] [--overwrite] FILE.vtf [FILE.vtf ...]
```

For a texture called `name`, SourceIO can write:

| File | Contents |
|---|---|
| `name.tga` | Complete texture; animated VTFs use one `name_frameNNN.tga` per frame. HDR data uses EXR. |
| `name.sht` | Original sprite-sheet bytes. |
| `name_NNN.tga` | One file per unique sheet frame. |
| `name.mks` | Sequences, looping, frame timing, and source-frame references for `mksheet`. |
| `name.vtf.json` | Header, resources, rectangles, and exported file mapping. |

A VTF without a sheet produces only image data and JSON. Existing files are
protected unless overwrite is enabled.

### Rebuilding

Choose one workflow:

1. **Keep the atlas layout.** Edit the complete image without moving its frame
   cells, then run `vtex` with the `.sht` beside the image. This preserves the
   sheet bytes.
2. **Repack the frames.** Edit `name_NNN.tga` or `name.mks`, run
   `mksheet name.mks`, then compile the generated image with `vtex`.

The second workflow replaces the atlas and sheet, so edit either the complete
image or the individual frame files, not both. `vtex` normally expects input
below the game's `materialsrc`; use `-outdir` and, when needed, `-game` for
other locations.

Sheets using `packmode rgb+a` overlap independently packed color and alpha
frames without recording which sequence owns which channel. SourceIO warns for
these sheets; preserving the original atlas layout is the safe workflow.

The export and rebuild paths were validated against all 273 sprite-sheet VTFs
shipped with Team Fortress 2.
