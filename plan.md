# SourceIO validation baseline

This file records current verification and performance evidence. It is not a
roadmap: all open planned or desired work belongs in [TODO.md](TODO.md).
Commands and developer setup belong in
[Development and testing](docs/development.md).

Target: **Blender 5.2.2 LTS only.** Blender 4.x compatibility is intentionally
not maintained.

Last reconciled: **2026-10-25**.

## Automated baseline

| Gate | Current result |
|---|---|
| Renamed-module smoke | 635/635 modules; register/unregister/register passed |
| Public sample imports | 87 pass, 6 known, 0 warn, 0 fail |
| Non-Blender tests | 574 passed, 7 skipped |
| Blender tests excluding skins | 276 passed, 2 skipped, 17 warnings, 51 subtests |
| Blender skin tests | 8 passed |
| Windows extension package | Built successfully |

The end-to-end runners are the primary import gate:

- `run_renamed_smoke.py` imports every shipped module through an alternate
  package name and exercises registration lifecycle.
- `run_sample_imports.py` runs the public hash-verified corpus through the real
  operators and compares decoded texture pixels.
- `run_game_imports.py` resolves and imports assets through an installed game's
  actual search paths.

Unit and Blender suites cover exact values, node wiring, safety boundaries, and
formats without representative public samples.

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
