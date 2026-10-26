[![Blender](https://img.shields.io/badge/Blender->=_5.2-orange?logo=blender&logoColor=white)](https://www.blender.org/download)
[![Upstream Discord](https://img.shields.io/discord/554001378532655104?label=Upstream%20Discord&logo=discord&logoColor=white)](https://discord.gg/SF82W6aZ67)

# SourceIO for Blender 5.2

SourceIO is a Blender extension for importing assets from GoldSrc, Source, and
Source 2 games. It handles models, maps, materials, textures, animations,
physics, and Source 2 sounds, with game-specific support for titles including
Team Fortress 2, Counter-Strike 2, and Dota 2.

This repository is a Blender 5.2-only fork of
[REDxEYE/SourceIO](https://github.com/REDxEYE/SourceIO). For Blender 4.x, use
the upstream add-on. The linked Discord server is also maintained by the
upstream project.

## Highlights

- Import GoldSrc, Source 1, and Source 2 models and maps.
- Build Blender materials from common Source 1 and Source 2 shaders.
- Import Source 1 and Source 2 animations, including Source 2 animation-graph
  clips.
- Preserve VTF metadata and export textures and sprite sheets for external
  editing.
- Extract Source 2 audio.
- Author and optionally compile static assets for CS2 Workshop Tools and Source
  1 toolchains.
- Use the same workflows from Blender's UI, drag and drop, or the command line.

## Improvements over upstream

Compared with the upstream baseline this fork started from, it adds:

- **Faster imports:** indexed material/texture caches, default-enabled direct
  DDS staging for compatible Source 1 VTFs, faster PNG staging, and batched
  Source 2 aggregate models. Measured warm-cache gates improved TF2
  `ctf_2fort` by 49%, with DDS staging cutting a later controlled A/B by another
  29%, and fully loaded CS2 `de_dust2` by 24%.
- **Broader Source 2 coverage:** animation groups and graph clips, more texture
  formats and subresources, VSND audio, CS2 map lighting/skyboxes, and extensive
  CS2 and Dota 2 shader support.
- **More accurate materials:** corrected Source 1 phong, Fresnel, envmap, tint,
  alpha, sampler, and normal-map behavior, plus richer CS2 character, world,
  blend, decal, and unlit materials.
- **Editable authoring workflows:** verified static-asset export, official
  compiler integration, manifests, and recompile for CS2 Workshop Tools and
  Source 1 toolchains, including both Source SDK Base 2013 profiles.
- **Blender 5.2-native packaging and testing:** split-platform extensions,
  manylinux-compatible binaries, public sample tests, installed-game gates, and
  official-tool round trips.

See [Changes in this fork](docs/fork-changes.md) and the
[validation baseline](plan.md) for details and measurements.

## Installation

SourceIO requires **Blender 5.2 or newer**.

1. Download the package for your platform from
   [Releases](https://github.com/Tzeentchnet/SourceIO/releases).
2. In Blender, open *Edit > Preferences > Get Extensions*.
3. Choose *Install from Disk...* and select the downloaded zip.

Packages are available for Windows x64, Linux x64, macOS x64, and macOS arm64.
Installing the repository as a legacy add-on remains supported for development
checkouts. See [Development and testing](docs/development.md) to build packages
from source.

## Quick start

### Import

Open *File > Import > Source Engine Assets* and choose an asset. Most common
formats can also be dragged into Blender, including `.mdl`, `.bsp`, `.vtf`,
`.vmt`, `.vmdl_c`, `.vmat_c`, `.vtex_c`, `.vphys_c`, `.vmap_c`, map `.vpk`
files, `.vsnd_c`, and camera `.dmx` files.

Imported maps initially contain placeholders for referenced models and merged
world geometry. Select the placeholders you need and use *Load Entity* to expand
them.

### Export

Use *File > Export* for the supported CS2 Workshop and Source 1 static-asset
workflows. SourceIO writes editable source files and can optionally invoke the
official compilers; it does not silently replace those tools with custom binary
writers.

See [Static asset authoring](docs/authoring.md) for supported material graphs,
target setup, compilation, and recompile behavior.

### Command line

With the extension enabled, Blender can run SourceIO without opening the UI:

```text
blender -c sourceio import --output scene.blend ASSET
```

Import, extraction, authoring, and recompile commands are documented in the
[user guide](docs/user-guide.md#command-line).

## Documentation

- [User guide](docs/user-guide.md) — importing, drag and drop, map handling,
  texture caching, command-line commands, and VTF editing.
- [Static asset authoring](docs/authoring.md) — CS2 Workshop and Source 1
  authoring, compiler integration, material limits, and reconstruction exports.
- [Supported formats and game coverage](docs/formats.md).
- [Development and testing](docs/development.md) — building packages, test
  suites, installed-game gates, and experimental boundaries.
- [Changes in this fork](docs/fork-changes.md) — modernization background,
  major additions, and notable fixes.
- [Improvement backlog](TODO.md) and [validation baseline](plan.md).

## Scope

SourceIO aims to produce useful, editable Blender scenes rather than emulate an
entire game engine. Shader behavior, map entities, and compiled formats vary
between games, so unrecognized games and uncommon assets may receive generic or
partial handling.

Static reconstruction from compiled Source 2 resources is intentionally lossy,
and current authoring workflows target static assets. SourceIO does not execute
AnimGraphs, simulate a Source 2 particle renderer, or claim one-to-one runtime
renderer parity.

## Credits

- SourceIO and its original authors:
  [REDxEYE/SourceIO](https://github.com/REDxEYE/SourceIO).
- [datamodel.py](https://github.com/Artfunkel/BlenderSourceTools/blob/master/io_scene_valvesource/datamodel.py)
  by [Artfunkel](https://github.com/Artfunkel).
- [ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat)
  for Source 2 format research and attributed MIT-licensed ports.
- [BlenderVertexLitGeneric](https://github.com/syborg64/BlenderVertexLitGeneric)
  by [syborg64](https://github.com/syborg64).
- [equilib](https://github.com/haruishi43/equilib) by
  [haruishi43](https://github.com/haruishi43).
- [HFSExtract](https://github.com/yretenai/HFSExtract) by
  [yretenai](https://github.com/yretenai).
- Better HWM expression handling by
  [hisanimations](https://youtube.com/c/hisanimations).
