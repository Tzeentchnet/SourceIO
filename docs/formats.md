# Supported formats and game coverage

Support is organized by file format and by game-specific material/entity
handlers. Sharing an engine does not guarantee that every shader, entity, or
resource variation is understood.

## GoldSrc, Source 1, and related formats

| Extension | Contents | SourceIO operation | Notes |
|---|---|---|---|
| `.mdl` | GoldSrc or Source model | Import | Version is detected from the file. |
| `.md3` | Quake III model | Import | Uses the model importer. |
| `.bsp` | Compiled GoldSrc or Source map | Import | GoldSrc and Source maps use separate importers. |
| `.vtf` | Source texture | Import and direct export | Import preserves sprite sheets and metadata. External-edit export writes image data, `.sht`, `.mks`, and JSON. |
| `.vmt` | Source material or skybox | Import | Skybox import reads all six faces. |
| `.smd`, `.qc`, `.vmt`, `.tga` | Editable Source 1 static asset | Authoring output | Optional compilation through official VTEX and StudioMDL tools. |

## Source 2 and DMX

| Extension | Contents | SourceIO operation | Notes |
|---|---|---|---|
| `.vmdl_c` | Compiled model | Import | Optional animations and animation-graph clip discovery. |
| `.vmap_c` | Compiled map | Import | Imports world data and entity/model placeholders. |
| `.vpk` | Packed Source 2 map | Import | Map VPKs only; SourceIO is not a general VPK browser. |
| `.vtex_c` | Compiled texture | Import | Supports mip selection, arrays, slices, HDR, and controlled packed-channel decoding. |
| `.vmat_c` | Compiled material | Import | Shader support varies by game. |
| `.vphys_c` | Compiled physics | Import | Can be loaded with models or independently. |
| `.vnmclip_c` | Animation-graph clip | Import | Applied to a compatible selected armature. |
| `.vanim_c`, `.vagrp_c` | Compiled animation or group | Import | Standalone animations may need their owning group's decode key. |
| `.vsnd_c` | Compiled sound | Import or extract | Supports VSND v1-v4; audio becomes MP3 or WAV. |
| camera `.dmx` | Valve camera | Import | Session DMX import is not registered. |
| `.fbx`, `.vmdl`, `.vmat`, `.tga` | Editable CS2 static asset | Authoring output | Optional Resource Compiler integration for the documented material subset. |
| `.vmdl`, static `.dmx` | Editable model reconstruction | Export | Lossy reconstruction from selected Blender meshes. |
| `.vmap` | Editable Hammer reconstruction | Export | Lossy reconstruction from the active imported map collection. |

Source 2 authoring files without the compiled `_c` suffix are not import
targets. The authoring and reconstruction workflows are described in
[Static asset authoring](authoring.md).

## Texture and animation notes

- Source 2 textures support common BC/DXT formats, BC6H HDR, wrapped
  PNG/JPEG/WEBP payloads, ETC2/EAC, integer formats, float formats, cubemaps,
  arrays, volume slices, and explicit mip selection.
- VCS metadata is used for packed-channel interpretation only when its meaning
  is unambiguous.
- Source 1 model animation support covers common MDL versions and include
  models.
- Source 2 animation support includes embedded animations, animation groups,
  older NTRO resources, additive motion, root motion, morph/user/data channels,
  and animation-graph clips.
- SourceIO retains AnimGraph documents for inspection but does not execute the
  graph.

## Automated game coverage

Installed-game end-to-end gates currently cover:

- **Team Fortress 2:** representative models, include-model animations, Source
  1 materials/textures, four maps, and the Source 1 static-authoring toolchain.
- **Counter-Strike 2:** representative characters, weapons, props, materials,
  textures, animations and animation-graph clips, maps, placeholders, and the
  CS2 Workshop static-authoring toolchain.
- **Dota 2:** representative animated models, common shaders, a texture, the
  main world, entity diagnostics, and a disposable Workshop Tools compiler
  compatibility fixture.

These gates demonstrate known working corpora; they are not claims that every
asset in each game is supported.

## Additional handlers

Dedicated Source 1 map/entity handling also exists for Half-Life 2 and its
episodes, Counter-Strike: Source, Garry's Mod, Source Filmmaker, Black Mesa,
Counter-Strike: Global Offensive, Left 4 Dead 2, Portal, Portal 2 and several
Portal 2 mods, Vampire: The Masquerade - Bloodlines, Vindictus, Titanfall, and
supported Quake III-derived formats.

Source 2 includes dedicated handling for Counter-Strike 2, Dota 2, Half-Life:
Alyx, S&box, and Deadlock. Unrecognized games fall back to generic handling and
should be treated as partial support.
