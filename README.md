[![Blender](https://img.shields.io/badge/Blender->=_5.2-orange?logo=blender&logoColor=white)](https://www.blender.org/download)
[![Discord](https://img.shields.io/discord/554001378532655104?label=Chat&logo=discord&logoColor=white)](https://discord.gg/SF82W6aZ67)

# SourceIO for Blender 5.2

> **This is a fork of [REDxEYE/SourceIO](https://github.com/REDxEYE/SourceIO), updated to target Blender 5.2 only.**
> Blender 4.x support has been removed in exchange for 5.2-native code paths, faster imports and packaging as a Blender extension.
> For Blender 4.x use the [upstream add-on](https://github.com/REDxEYE/SourceIO). Credit for SourceIO itself goes to its upstream authors (see [Credits](#credits)).

SourceIO is a Blender add-on for importing GoldSrc, Source and Source 2 engine textures, models and maps.
Upstream Discord server: https://discord.gg/SF82W6aZ67

Current TODO list -> [TODO.md](TODO.md)

Small WIKI -> [WIKI](https://github.com/REDxEYE/SourceIO/wiki)

## Installation (Blender 5.2+)
SourceIO installs as a Blender extension. Download the zip for your platform from the [releases page](https://github.com/Tzeentchnet/SourceIO/releases), or build it from a checkout of this repository:
```
python tools/build_extension.py --blender path/to/blender [--platform windows-x64]
```
This writes one zip per platform to `dist/`, each containing only that platform's native library.
Then use *Edit > Preferences > Get Extensions > Install from Disk...* and pick the zip for your platform.
Installing the repository folder as a legacy add-on also still works.

### Command line
With the extension enabled, files can be imported without opening the UI. The importer is chosen from the file extension, and for `.bsp` from the file header (GoldSrc or Source):
```
blender -c sourceio import [--scale S] [--no-materials] [--animations] [--clips PATTERNS] [--output out.blend] FILE [FILE ...]
```
`--clips` imports a Source 2 model's animation graph clips that match the patterns (see below).

# Usage
In order to find the import tools you simply need to go to File>Import>Source Engine Assets
![](https://cdn.discordapp.com/attachments/786989240529059900/1143975506589515886/image.png)

Most formats can also be dragged and dropped into Blender: `.mdl`, `.bsp`, `.vtf`, `.vmt`, `.vmdl_c`, `.vmat_c`, `.vtex_c`, `.vphys_c`, `.vmap_c`, map `.vpk` files and `.dmx` cameras. Source 2 animation clips (`.vnmclip_c`) can be dropped onto a selected armature.

# Changes in this fork

## Blender 5.2 modernization
* **Blender 4 code removed.** All version checks and legacy branches are gone (`use_auto_smooth`, `shadow_method`, `ShaderNodeMixRGB`, the pre-slotted-actions animation API, deprecated material properties).
* **Transparency works again on 5.x.** Alpha-tested materials get a real alpha-clip node, decals and additive materials use *Blended* rendering, and skyboxes and decals no longer cast shadows. Previously these settings were silently skipped on Blender 4.3+.
* **Faster mesh import.** Vertex weights are assigned in batches: about 6× faster.
* **Animation importer.** Curves are computed with NumPy, actions use the slotted-action API, and an optional *Delta animations to NLA* setting puts additive animations on muted NLA tracks set to *Combine*.
* **UI.** The MDL import dialog has collapsible sections and now exposes *World scale* and the BVLG option. Drag-and-drop handlers were added for `.vmdl_c`, `.vphys_c` and `.dmx` cameras.
* **Packaging.** A `blender_manifest.toml` makes SourceIO a Blender 5.2 extension (Windows x64, Linux x64, macOS x64/arm64). `tools/build_extension.py` builds a separate package per platform (the Windows package is 3.4 MB instead of 7.1 MB).

## New features
* **Source 2 animations.** Enable *Import animations* in the VMDL import dialog. Supported sources:
  * animations embedded in the model;
  * external animation groups (`.vagrp_c` / `.vanim_c`);
  * included models;
  * older NTRO-format files.

  All of VRF's segment decoders are ported. Position, rotation and scale channels are imported, with delta animations, looping flags and root motion. Results match VRF's own glTF export within float precision on every frame of 192 animations across 9 test models. Not yet supported: flex/morph channels, bone masks and pose-parameter blending.
* **Source 2 animation graph clips** (`.vnmclip_c`, AnimGraph 2). CS2 characters animate through these rather than through the model: a CS2 agent has 2 animations of its own and reaches about 2000 clips through its animation graphs. Each clip is authored on its own skeleton and is matched to the model by bone name, the same way VRF does it. Two ways to import them:
  * *Graph clips* in the VMDL import dialog (with *Import animations*) takes the model's clips whose path or name matches the patterns, for example `idle*, run_n_*`, or `*` for all of them (about a minute for a CS2 agent).
  * *File > Import > Source Engine Assets > Source2 animation clip* puts clip files onto the selected armature. Weapons need this route, because their animation comes from the second track set of viewmodel and menu clips, which the weapon's own model doesn't list.

  Additive clips and root motion are supported, and each action records the clip's path. The clip's events (sounds, particles, gameplay IDs) become pose markers on the action at their start frame. On 138 clips (the chicken, a CT agent and an AK-47), the result matches VRF's glTF export within 1e-4 units and 0.002°.
* **GoldSrc animations.** All sequences embedded in a GoldSrc model are imported as actions when *Load animations* is enabled. Previously only a sequence named `walk1` was imported, and its values were wrong.
* **More Source 2 texture formats.** ETC2, ETC2_EAC, R11_EAC and RG11_EAC.
* **Command-line import.** `blender -c sourceio import ...` (see above).
* **Source 1 overlays.** `info_overlay` decals (signs, posters, road markings) are imported into an `overlays` collection, clipped to the faces and displacements they cover. They were previously not imported at all. Controlled by *Load overlays* (on by default).
* **Bone collections and colours** for Source 1 models (MDL v44–v52). Bones are sorted by what the engine uses them for: *Deform*, *Procedural*, *Bone merge*, *Attachments* and *Other*. Deform bones are coloured by side (left blue, right red, centre yellow), and the others by role.
* **Flex controller panel** for Source 1 models with flexes (*SourceIO utils > Flex controllers* in the 3D view sidebar, with the face mesh active): a slider per face controller, keying and reset buttons. A stereo controller has one slider for both sides, split by a left/right balance (the eye icon shows left and right separately). With auto keying on, moving a slider keys it. Adapted from [REDxEYE/SourceIO#477](https://github.com/REDxEYE/SourceIO/pull/477) by hisprofile.
* **One compact action per include model.** With *Include animations* and *Compact animations*, a character's animations are split into one action per source model (`heavy.mdl`, `heavy_animations.mdl`, `heavy_workshop_animations.mdl`), so animations that share a name across them keep it. Also from #477.
* **CS2 character and weapon materials.** Agents, the hostage, the chicken, gloves and weapons used to import with an untextured grey material, because their shaders (`csgo_character`, `csgo_weapon`) had no handler. They now get color, normal, roughness, metalness and tint. Blood masks, patches and stickers, which the game fills in at runtime, are left out.
* **CS2 decals, light transmission and detail textures.** Props with a decal texture (crate logos, painted markings) get it blended in, over the color or multiplied as the material asks. Foliage, awnings and other thin surfaces let light through from behind, as in the game. Map surfaces get their detail textures. de_dust2 now leaves 4 textures unused, down from 26.
* **CS2 texture scales and generic materials.** Blended map surfaces (plaster, gravel, concrete) use each layer's own texture scale, rotation and offset, and prop normal maps their own. Detail textures follow the second UV set on models that have one. Materials on the older `generic` shader (shell casings, the challenge coin) get their roughness, metalness, reflectance and glow.
* **CS2 3D skyboxes.** Importing a CS2 map also imports its 3D skybox (the town and hills around de_dust2, for example), scaled up and placed around the map as the game shows it, in its own collection under the map's. Its *Load Entity* placeholders load like the map's.
* **Readable CS2 map object names.** The map compiler merges Hammer's meshes and props into generated models named like `n0_lr0_c0_s_cb_nomerge6_steam_001`, and Hammer's own names don't survive compiling. Objects are now named after what the model was made for, usually its material (`steam_001`, `agave_plant_01`, `overlay12`); the full compiled path stays in the object's `entity_data`.
* **Light blockers cast shadows only.** Meshes the game only draws into shadow maps (Hammer's `toolsblocklight` brushes, the grey slabs around de_dust2) go into a `shadow_casters` collection that is hidden in the viewport. Show it with its eye icon in the outliner and use *Load Entity* on its placeholders: the loaded meshes are invisible to the camera and to reflections, and cast shadows in renders, as in the game. Hide the collection again to keep them out of the viewport.
* **CS2 unlit materials.** Materials on `csgo_unlitgeneric` (glowing signs and screens, light panels, skybox clouds and mist, the music-kit screens) are drawn unlit, with the game's opaque, translucent, alpha-tested, additive and multiply modes. Their second texture is multiplied in where the material has one, so de_dust2's skybox clouds are clouds, not grey cards.
* **CS2 sky and sunlight.** Importing a CS2 map sets its sky as the scene's world, turned as in the game, and the sun shines from where the sky's glow is, as bright relative to the sky and the map's lamps as the game draws it. A Cycles render of de_dust2 at the default exposure shows sunlit walls under a blue sky, where the sky used to be black and the sun hundreds of times too strong.
* **CS2 lamp shapes.** Rectangular and disc lamps (`light_rect`: ceiling panels, strip lights) are imported as area lights of their size, and barn lights (`light_barn`) as spots that light the same footprint as the game's light, including stretched and off-center ones. Each lamp is as bright 100 units in front of it as the game's. Previously both were point lights.
* **CS2 decals, detail textures and blended floors and walls.** Decals and overlays (`csgo_static_overlay`) use the game's blend modes: alpha-tested decals have hard edges, darkening and brightening decals (Mod2x, ModThenAdd) modulate the surface under them instead of covering it, and unlit decals are no longer shaded. Their opacity, painted vertex colors and color adjustments apply too. Detail textures follow the game's modes (Mod2x, Overlay, detail normals; Multiply and Replace on characters and weapons). Before, details darkened walls or were missing, e.g. on weapon keychains. Two-layer world surfaces (`csgo_lightmappedgeneric`: de_dust2's plaster, asphalt and ground blends) blend where the mapper painted them; before, the painted blend was ignored.
* **`custom/*` search paths.** Every folder and VPK in a game's `custom` folder is mounted ahead of the game, as the engine does, so community content and workshop items resolve.

## Bug fixes
* CS2 maps had no sky: the sky was built but never assigned to the scene, so it wasn't saved either.
* Source 2 suns pointed the wrong way: away from where the sky puts the sun, and at 90° minus the elevation they should have.
* CS2 lamps (`light_omni2`, `light_rect`, `light_barn`) read their brightness as a plain number, but the game stores it in stops: bright lamps came out too dark, and dim ones with a negative value took negative energy.
* CS2 materials on `csgo_unlitgeneric` (306 in the game) were imported as plain grey materials: the shader's handler was never loaded.
* CS2 metalness: props ignored their metalness texture, and materials with one took metalness from the color alpha instead, so gold looked half-metallic and some glass looked like metal.
* CS2 materials with anisotropic gloss (some agents' clothing, the music-kit record) were almost fully rough: roughness was read from a normal map that has none, and the texture that holds it was decoded as a normal map.
* Alpha-tested materials (chain-link fences, foliage cards, grates) thinned out and vanished with distance in EEVEE and the viewport, while Cycles showed them. Blender's mipmaps average the alpha below the cutoff; the alpha is now read at full resolution, as Cycles does. This applies to Source 1 and Source 2 materials.
* Source 2 maps: *Load Entity* on a merged world mesh whose pieces repeat one mesh placed only the first copy and skipped the rest of the merged mesh (22 pieces of de_dust2 and 19 of cs_office were missing).
* Source 2 texture offsets moved textures vertically the wrong way, rotations turned the wrong way, and the rotation center acted as an extra offset.
* CS2 translucent materials rendered opaque (the flag was read under an older name), and foliage without alpha testing was see-through wherever its color was dark. `csgo_complex` materials ignored their color tint.
* CS2 maps no longer load an unconnected image node for every ambient occlusion texture (de_dust2: 478 "Unused texture" warnings down to 26). Source 2 applies ambient occlusion only to indirect light, which Blender computes itself.
* Custom normals didn't follow deformation, so characters were lit and reflected from the wrong directions and looked metallic. Model meshes are turned upright by their armature, and the normals stayed behind; they also ignored poses and shape keys. This came from an earlier speed-up in this fork, now reverted (imports are about 5% slower).
* Source 1 rim lighting (`$rimlight`) covered the whole surface in a white haze, which washed out TF2 characters. It now shows only at the silhouette, as in Source.
* Combo flexes were clamped before being scaled back by *Flex Scale*, so they were too weak at a Flex Scale other than 1 (from #477).
* Map props with a multi-frame `defaultanim` failed to pose, and GoldSrc animation import was broken (both regressions from the recent animation overhaul).
* GoldSrc animations of 128 or more frames were decoded incorrectly. This broke models whose textures live in a separate `*T.mdl` file.
* HDR Source 2 textures (BC6H, RGBA16161616F) were imported with reversed colour channels and squeezed into an 8-bit image. They are now pixel-exact.
* New Source 2 texture formats: PNG/JPEG/WEBP-wrapped textures, and the 1-, 2- and 3-channel uint, float16 and float32 formats (R32F, RG1616, R16F, RG3232F and others), plus BGRA8888, IA88, A8 and R32_UINT.
* Source 2 models with an attribute-less vertex buffer no longer fail to import.
* Map VPKs whose map isn't named after the VPK file can now be imported.
* Brush entities in BSPs without face data no longer crash the map import.
* KV3 v2 data compressed as several zstd frames failed to load. This affected some newer Source 2 models' animation blocks.
* External resource references in older NTRO-format Source 2 files were read as empty.
* Source 1 animations stored in sections had broken rotations on every bone a section didn't list (for example 178 tracks on the TF2 heavy).
* Source 1 v49+ frame animations that mix constant and per-frame data, which is most of them, failed to decode.
* *Load Ref pose* failed on every model with animations and left Blender in edit mode.
* Building an armature put any other selected armature into edit mode too, and a failed import could leave Blender in edit mode.
* Displacements far from the map origin could be built from the wrong corner, which rotated their grid.
* Water materials set with `$bottommaterial` weren't found, and underwater faces got an empty material.
* Model materials with `..` in their path, and models whose VPK path differs in case, failed to resolve.
* CS2 maps: spot-shaped `light_omni2` lights failed to import (33 on de_inferno), and entities without a model left a placeholder that *Load Entity* couldn't load.
* CS:GO Source 2 materials: self-illuminated `csgo_vertexlitgeneric` and `csgo_static_overlay` materials, and `csgo_environment_blend` materials with a colour overlay, failed to build.
* Source 2 normal maps could get corrupt pixels where compression pushed the stored X/Y past length 1.
* A CS2 install was also detected as Dota 2.
* Morphs of Source 2 models with external meshes (`m_morphSet`) never loaded.
* Smaller fixes:
  * duplicate *Export to VTF* menu entries after re-enabling the add-on;
  * a leaked scene property;
  * a leaked VTF file handle;
  * a stuck progress bar after errors;
  * an unformatted report message;
  * a broken DMX session importer, now unregistered;
  * a module that failed to import (`csgo_weapon.py`).

## Testing
`tests/fetch_samples.py` downloads 104 hash-verified sample assets (about 17 MB) from public repositories into the git-ignored `samples/` folder.
`tests/e2e/run_sample_imports.py` imports each one headlessly through the real operators. For every texture, it also checks that the imported pixels match SourceIO's own decode:
```
python tests/fetch_samples.py
blender -b --factory-startup --python tests/e2e/run_sample_imports.py -- [--filter TEXT] [--json report.json]
```
Current result: 87 pass, 6 warn (only because game content isn't included), 0 fail.

`tests/e2e/run_game_imports.py` imports models and maps straight from an installed game through its own search paths. With Team Fortress 2 (9 models, including HL2's dog, and 4 maps), all 13 pass:
```
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/Team Fortress 2/tf"
```
It also takes a Source 2 game. With Counter-Strike 2 (9 models: agents, arms, weapons, chicken, hostage, a prop; and 3 maps), all 12 pass. `--load-placeholders` also loads every prop and world mesh a map places (de_dust2, de_inferno and cs_office load all of them without errors). `--clips "*"` imports every graph clip as well; all 9 models still pass:
```
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/Counter-Strike Global Offensive/game/csgo" [--load-placeholders] [--clips PATTERNS]
```
See [plan.md](plan.md) for details.

# Credits
* [datamodel.py](https://github.com/Artfunkel/BlenderSourceTools/blob/master/io_scene_valvesource/datamodel.py) by [Artfunkel](https://github.com/Artfunkel)
* [ValveResourceFormat](https://github.com/SteamDatabase/ValveResourceFormat) For initial research on Source2 file formats
* [BlenderVertexLitGeneric](https://github.com/syborg64/BlenderVertexLitGeneric) Shader nodegroup by [syborg64](https://github.com/syborg64)
* [equilib](https://github.com/haruishi43/equilib) Cubemap to equirectangular converter by [haruishi43](https://github.com/haruishi43/equilib)
* [HFSExtract](https://github.com/yretenai/HFSExtract) HFS extractor that was used to write native decryptor by [yretenai](https://github.com/yretenai)
* Idea for better HWM expression handling by [hisanimations](https://youtube.com/c/hisanimations)
# Supported formats:

## Source 1
| File Type | Contents                          | Import             | Export            |
| ------    | ------                            | ------             | ------            |
| .MDL      | Model                             | :heavy_check_mark: | :x:               |
| .BSP      | Map Files (Compiled)              | :heavy_check_mark: | Not Planned       |
| .VMF      | Map Files (Hammer Format)         | Not Planned        | Not Planned       |
| .VTF      | Textures                          | :heavy_check_mark: | :x:|
| .VMT      | Materials                         | :heavy_check_mark: | :x:               |

## Source 2
| File Type | Contents                          | Import              | Export       |
|-----------| ------                            |---------------------|--------------|
| .VMDL     | Model                             | :heavy_check_mark:  | Not Planned  |
| .VMAP     | Map Files (Compiled)              | :heavy_check_mark:  | Not Planned  |
| .VMAP     | Map Files (Hammer Format)         | Not Planned         | Not Planned  |
| .VTEX     | Textures                          | :heavy_check_mark:  | :x:          |
| .VMAT     | Materials                         | :heavy_check_mark:  | :x:          |
| .VNMCLIP  | Animation clips (AnimGraph 2)     | :heavy_check_mark:  | :x:          |

## Supported games
| Game      | Status                                                                                          |
|-----------| ------------------------------------------------------------------------------------------------|
| CSGO              | Partial support: models, maps(not all entities), textures, materials                    | 
| TF2               | Full support                                                                            |
| Source FilmMaker  | Full support                                                                            |
| Garry's Mod       | Full support                                                                            |
| HL2 and episodes  | Full support                                                                            |
| Portal 1/2        | Full support                                                                            |
| L4D2              | Full support(expect infected materials)                                                 | 
| Vindictus         | Partial support: (models, maps(not all entities), textures, materials(not all shaders)  | 
| BlackMesa         | Full support                                                                            |
| Titanfall 1       | Partial support: maps(not all entities), models, textures, materials                    |
| Counter Strike 2  | Partial support: (models, maps(not all entities), textures, materials(not all shaders))  |
| Half-Life: Alyx   | Partial support: (models, maps(not all entities), textures, materials(not all shaders)  |
| S&BOX             | Partial support: Waiting for full release                                               |
| Aperture Desk Job | Partial support: (models, maps(not all entities), textures, materials(not all shaders)  |
