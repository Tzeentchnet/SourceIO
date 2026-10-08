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
SourceIO installs as a Blender extension. Build the package for your platform from a checkout of this repository:
```
blender --command extension build --split-platforms --output-dir dist
```
Then use *Edit > Preferences > Get Extensions > Install from Disk...* and pick the zip for your platform from `dist/`.
Installing the repository folder as a legacy add-on also still works.

# Usage
In order to find the import tools you simply need to go to File>Import>Source Engine Assets
![](https://cdn.discordapp.com/attachments/786989240529059900/1143975506589515886/image.png)

Most formats can also be dragged and dropped into Blender: `.mdl`, `.bsp`, `.vtf`, `.vmt`, `.vmdl_c`, `.vmat_c`, `.vtex_c`, `.vphys_c`, `.vmap_c`, map `.vpk` files and `.dmx` cameras.

# Changes in this fork

## Blender 5.2 modernization
* **Blender 4 code removed.** All version checks and legacy branches are gone (`use_auto_smooth`, `shadow_method`, `ShaderNodeMixRGB`, the pre-slotted-actions animation API, deprecated material properties).
* **Transparency works again on 5.x.** Alpha-tested materials get a real alpha-clip node, decals and additive materials use *Blended* rendering, and skyboxes and decals no longer cast shadows. Previously these settings were silently skipped on Blender 4.3+.
* **Faster mesh import.**
  * Custom normals use Blender 5's custom-normal attribute: about 400× faster (0.4 s → 0.001 s on a 717k-triangle mesh) and more accurate.
  * Vertex weights are assigned in batches: about 6× faster.
* **Animation importer.** Curves are computed with NumPy, actions use the slotted-action API, and an optional *Delta animations to NLA* setting puts additive animations on muted NLA tracks set to *Combine*.
* **UI.** The MDL import dialog has collapsible sections and now exposes *World scale* and the BVLG option. Drag-and-drop handlers were added for `.vmdl_c`, `.vphys_c` and `.dmx` cameras.
* **Packaging.** A `blender_manifest.toml` makes SourceIO a Blender 5.2 extension (Windows x64, Linux x64, macOS x64/arm64).

## Bug fixes
* Map props with a multi-frame `defaultanim` failed to pose, and GoldSrc animation import was broken (both regressions from the recent animation overhaul).
* GoldSrc animations of 128 or more frames were decoded incorrectly. This broke models whose textures live in a separate `*T.mdl` file.
* HDR Source 2 textures (BC6H, RGBA16161616F) were imported with reversed colour channels and squeezed into an 8-bit image. They are now pixel-exact.
* New Source 2 texture formats: PNG/JPEG/WEBP-wrapped textures, and the 1-, 2- and 3-channel uint, float16 and float32 formats (R32F, RG1616, R16F, RG3232F and others), plus BGRA8888, IA88, A8 and R32_UINT.
* Source 2 models with an attribute-less vertex buffer no longer fail to import.
* Map VPKs whose map isn't named after the VPK file can now be imported.
* Brush entities in BSPs without face data no longer crash the map import.
* Smaller fixes:
  * duplicate *Export to VTF* menu entries after re-enabling the add-on;
  * a leaked scene property;
  * a leaked VTF file handle;
  * a stuck progress bar after errors;
  * an unformatted report message;
  * a broken DMX session importer, now unregistered;
  * a module that failed to import (`csgo_weapon.py`).

## Testing
`tests/fetch_samples.py` downloads about 16 MB of hash-verified sample assets from public repositories into the git-ignored `samples/` folder.
`tests/blender_tests/run_sample_imports.py` imports each one headlessly through the real operators:
```
python tests/fetch_samples.py
blender -b --factory-startup --python tests/blender_tests/run_sample_imports.py -- [--filter TEXT] [--json report.json]
```
Current result: 66 pass, 5 warn (only because game content isn't included), 0 fail. See [plan.md](plan.md) for details.

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
