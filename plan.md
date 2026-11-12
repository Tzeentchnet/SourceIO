# SourceIO validation baseline

This file records current verification and performance evidence. It is not a
roadmap: all open planned or desired work belongs in [TODO.md](TODO.md).
Commands and developer setup belong in
[Development and testing](docs/development.md).

Targets: **Blender 5.2.2 LTS** as the minimum/baseline and **Blender 5.3.0**
as an additional compatibility gate. Blender 4.x compatibility is
intentionally not maintained.

Last reconciled: **2026-11-11**.

## Automated baseline

| Gate | Current result |
|---|---|
| Renamed-module smoke | 639/639 modules; register/unregister/register passed on Blender 5.2.2 and 5.3.0 |
| Public sample imports | 87 pass, 6 known, 0 warn, 0 fail |
| Non-Blender tests | 607 passed, 7 skipped |
| Blender tests excluding skins | 301 passed, 2 skipped, 17 warnings, 67 subtests on each of Blender 5.2.2 and 5.3.0 |
| Blender skin tests | 8 passed on each of Blender 5.2.2 and 5.3.0 |
| Source 1 material/NPR focus | 44 passed, 30 subtests on each of Blender 5.2.2 and 5.3.0 |
| Source 1 `game_write` focus | 1 passed |
| Windows extension package | Built successfully with both versioned NPR node assets; ID `sourceio`, minimum Blender 5.2.0 |

The end-to-end runners are the primary import gate:

- `run_renamed_smoke.py` imports every shipped module through an alternate
  package name and exercises registration lifecycle.
- `run_sample_imports.py` runs the public hash-verified corpus through the real
  operators and compares decoded texture pixels.
- `run_game_imports.py` resolves and imports assets through an installed game's
  actual search paths.

Unit and Blender suites cover exact values, node wiring, safety boundaries, and
formats without representative public samples.

### Source 1 `game_write` and NPR compatibility

Exact case-insensitive `game_write` search-path keys now mount in declared
order. A synthetic provider fixture verifies mount ownership, precedence, and
lookup of an asset available only through the writable root. An installed
Half-Life 2: Episode Two corpus was unavailable, so that real-game gate remains
open in [TODO.md](TODO.md).

The Principled `VertexLitGeneric` path remains the default. The opt-in
BlenderVertexLitGeneric/NPR path uses the unchanged legacy node asset on
Blender 5.2 and the closure-based asset on Blender 5.3+, including
`$phongwarptexture` and split EEVEE/Cycles material outputs. The persistent
64x64 EXR regression test asserts finite, non-empty output and a greater than
0.01 light-warp delta on both versions plus a greater than 0.01 phong-warp
delta on Blender 5.3.

Controlled 96x96 renders with generated textures produced:

| Comparison | Blender 5.2.2 EEVEE | Blender 5.3.0 EEVEE | Cycles |
|---|---:|---:|---:|
| Dark versus bright light warp | 43/255 maximum delta; 8,191 changed channel values | 46/255 maximum delta; 8,282 changed channel values | No pixel delta on either version |
| Light warp versus light + phong warp | No pixel delta | 105/255 maximum delta; 358 changed channel values | No pixel delta on either version |

All render buffers were finite. The Blender 5.2 asset remains
`e90765d52aef9847d9645366b20a41fce8af77bf5f7f954926d44e7e1a1f30da`;
the Blender 5.3 asset is
`d904803e57c5c5b0f4e1f252704e783cdb6269378664cac3b9eb2e8efc8183f9`.
The Windows package contains both assets while retaining extension ID
`sourceio` and minimum Blender version 5.2.0.

## Installed-game evidence

| Profile | Current gate |
|---|---|
| Team Fortress 2 | 13 representative model/map assets pass; all placeholders load on `ctf_2fort` (2,320), `cp_badlands` (1,308), `koth_harvest_final` (673), and `pl_upward` (1,332) |
| Source SDK Base 2013 | Singleplayer and Multiplayer each import three representative HL2 models; both bundled `sourcetest` maps complete with only their two explicitly classified texture/skybox limitations; combined result is 6 pass, 4 known, 0 warn, 0 fail |
| Counter-Strike 2 | 12 representative model/map assets pass; `de_dust2` loads 698/698 placeholders and `de_nuke` loads 1,108/1,108; nine default models import all requested graph clips |
| Dota 2 | 7 representative assets pass with app ID 570; the main world loads 5,311/5,311 placeholders with expected excluded destruction layers |

These results prove the tested corpora, not complete support for every asset in
each game.

## Official-tool gates

| Toolchain | Current gate |
|---|---|
| Dota 2 Workshop Tools | Generated static model/material/texture fixture compiles and re-imports |
| CS2 Workshop Tools | Three format/contract tests plus one Blender-authored base-color/normal/roughness/metalness/alpha-test round trip pass |
| TF2 VTEX and StudioMDL | Source 1 toolchain contract and Blender-authored compile/recompile round trip pass; MDL/VVD/VTX/VTF outputs re-import |
| Source SDK Base 2013 VTEX and StudioMDL | Singleplayer and Multiplayer compile, re-import, and manifest-recompile the same disposable generated fixture; each produces verified MDL v48, VVD, DX90 VTX, and two VTF outputs |
| TF2 `mksheet` and VTEX | All 273 shipped sprite-sheet VTFs preserve sequences, timing, frame pixels, and original-sheet bytes through the supported rebuild paths |

Generated fixtures and reports may be committed only when redistributable.
Proprietary game files, tool binaries, compiled outputs, and disposable addons
remain local.

### Source SDK Base 2013 reproducibility evidence

| Profile | App ID | Build ID | `vtex.exe` SHA-256 | `studiomdl.exe` SHA-256 |
|---|---:|---:|---|---|
| Singleplayer | 243730 | 267858 | `301930cc821ede0cf0e9fa167a42e877b1f08af509536fcf744acd2c1c662e89` | `cbd54cbea25f122369626dc781461929b20a8a6a63de804716dd6ca2e1fd917b` |
| Multiplayer | 243750 | 17413218 | `b90b97bc105048ee6d8dd3dda08d87e22665589c533d18eb7756dab68c091c0d` | `85463cd4dc40530a8bd49f0a68b82848f0df639137397df2f79ffda2aaed1e5c` |

The consolidated `sourceio.source-sdk-2013-gate` report passed both profiles,
six compiler steps, six recompile steps, two Blender geometry/material
re-imports, and ten installed-game imports, then verified temporary-workspace
removal. The local report was 62,628 bytes with SHA-256
`13e74d5c97bcfaa498a9be0be25ef055b1c3580f4cfde6d78ad97231246c46a3`.
Its four `KNOWN` map results are limited to the bundled `sourcetest` maps'
missing Lost Coast HDR skybox and unsupported image conversion; no unexpected
warnings or failures were accepted.

## Import performance evidence

Enable bounded aggregate timings with `SOURCEIO_PERF_TRACE=1`. The installed-game
runner prints the report and includes it in JSON output. Ordinary imports do not
capture timings.

Warm-cache measurements from 2026-10-24:

| Corpus | Stage | Wall time | Relevant measured work |
|---|---|---:|---|
| TF2 `ctf_2fort` | Initial baseline | 21.72 s | PNG encoding: 13.36 s for 212 images |
| TF2 `ctf_2fort` | Fast PNG staging | 11.02 s | PNG encoding: 3.46 s; 49.3% total improvement |
| CS2 `de_dust2 --load-placeholders` | Initial baseline | 289.57 s | PNG encoding: 97.84 s; 3,803 model loads |
| CS2 `de_dust2 --load-placeholders` | Fast PNG staging | 268.02 s | PNG encoding: 34.08 s |
| CS2 `de_dust2 --load-placeholders` | Aggregate model reuse | 219.68 s | 672 model loads; all output counts unchanged; another 18.0% improvement |

A controlled three-run warm-cache A/B from 2026-10-25 used Blender 5.2.2,
the current `master` as the baseline, and interleaved measurements after one
warm-up per tree:

| Corpus | Stage | Median wall time | Relevant measured work |
|---|---|---:|---|
| TF2 `ctf_2fort` | Decoded VTF to PNG | 14.17 s (13.92–14.21) | 212 PNG encodes; 4.43 s in the representative median report |
| TF2 `ctf_2fort` | Direct DDS staging | 10.04 s (9.88–10.05) | 191 DDS images and 21 PNG fallbacks; PNG encoding 0.70 s; 29.1% total improvement |

All six measured runs produced 3,308 objects, 599 meshes, 193 materials, and
225 images with no errors and identical details and diagnostics. Compatible
single-frame 2D DXT1/DXT5 textures retain their full compressed mip chain;
transformed normals, animation, cubemaps, volumes, ATI2N/BC5, and unsupported
formats keep the decoded path. Direct DDS staging is enabled by default and can
be disabled in the add-on preferences for newly loaded, uncached textures.
Public Blender samples verify orientation and alpha while allowing only the
decoder-defined BC endpoint variance: at most 7/255 in RGB and 1/255 in
interpolated DXT5 alpha. Absolute times are comparable only within this A/B, not
with the separate 2026-10-24 measurements.

A controlled warm-cache A/B from 2026-10-25 used Blender 5.2.2 and the same
instrumented tree, disabling only the new draw-call material cache for the
baseline:

| Corpus | Stage | Wall time | Relevant measured work |
|---|---|---:|---|
| CS2 `de_dust2 --load-placeholders` | Material cache disabled | 228.69 s | 3,768 material-resource lookups and `load_material` calls; material resolution: 185.77 s |
| CS2 `de_dust2 --load-placeholders` | Draw-call material reuse | 144.10 s | 295 material-resource lookups and `load_material` calls; material resolution: 100.59 s; 37.0% total improvement |

Both A/B runs produced 4,679 objects, 746 images, 32 lights, 4,048
collections, 3,768 meshes, 295 materials, and 698/698 loaded placeholders,
with identical details and diagnostics. The retained JSON reports include
separate material resolution, resource lookup, buffer decode, index remapping,
mesh creation, material attachment, attribute population, and mesh validation
timings.

The Source 2 unused-texture pass took 0.001 seconds on this `de_dust2` corpus.
The Source 1 MDL flex loop was not exercised by the map benchmarks. Future
performance work must start with a representative measurement rather than these
unexercised hypotheses.

Timing regions are nested and must not be added together. Benchmark comparisons
must retain the game build, import selection, cache state, output counts, and
JSON report.

## Validation policy

Run the smallest gate that directly exercises a change, then expand only where
the affected surface requires it:

| Change | Minimum evidence |
|---|---|
| Blender-free parser, serializer, or service | Focused tests plus the non-Blender suite |
| Blender importer, material, animation, or UI integration | Focused Blender tests, full Blender suite, and renamed-module smoke |
| Game-specific model, map, entity, or shader behavior | Relevant installed-game gate with machine-readable report |
| Authoring or compiler integration | Focused tests plus the matching official-tool round trip and re-import |
| Packaging or native-library change | Platform package build and applicable ABI/import checks |
| Documentation only | Markdown link validation and `git diff --check` |

An end-to-end runner fails when import raises, emits an unexpected error, creates
nothing, or decodes a texture incorrectly. It is not a substitute for focused
assertions about exact values.

Update this file only after a gate actually passes. When expected counts change,
record why in the implementing commit rather than preserving completed history
here.
