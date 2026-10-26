# Development and testing

## Development checkout

The repository can be installed directly as a legacy add-on while developing.
Release packages should be built as Blender extensions:

```text
python tools/build_extension.py --blender path/to/blender [--platform windows-x64]
```

The builder writes one zip per platform to `dist/`, with only that platform's
native library. Supported package targets are Windows x64, Linux x64, macOS
x64, and macOS arm64.

## Linux native library

The Linux native library is built by the **Build Linux pylib** workflow from
the source revisions pinned in `tools/pylib_sources.json`.

- Build target: `manylinux_2_28`.
- libstdc++ and libgcc are linked statically.
- CI imports the result with Python 3.11 and Python 3.13.
- `tools/build_extension.py` refuses Linux binaries whose GLIBC, GLIBCXX, or
  CXXABI requirements exceed the compatibility policy.

## Test layers

Current expected counts and installed-game evidence are maintained in
[`plan.md`](../plan.md). Run the examples below from the repository root.

### Unit and Blender suites

Run the non-Blender tests with the Python bundled by Blender or another
compatible Python environment:

```text
python -m pytest tests -q -p no:cacheprovider --ignore=tests/blender_tests
```

Run Blender tests inside Blender:

```text
blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, '..'); import SourceIO; SourceIO.register(); sys.exit(pytest.main(['tests/blender_tests', '-q', '--ignore=tests/blender_tests/test_skins.py']))"
blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, '..'); sys.exit(pytest.main(['tests/blender_tests/test_skins.py', '-q']))"
```

The renamed-module smoke test imports every shipped Python module through an
alternate package name, then checks register/unregister/register:

```text
blender -b --factory-startup --python tests/e2e/run_renamed_smoke.py
```

### Public sample corpus

`tests/fetch_samples.py` downloads hash-verified public samples into the
git-ignored `samples/` directory. The end-to-end runner imports them through the
real operators and compares decoded texture pixels:

```text
python tests/fetch_samples.py
blender -b --factory-startup --python tests/e2e/run_sample_imports.py -- [--filter TEXT] [--json report.json]
```

### Installed-game gates

The installed-game runner imports assets through each game's real search paths:

```text
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/Team Fortress 2/tf"
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/Source SDK Base 2013 Singleplayer/sourcetest" --expect-profile sdk2013_sp
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/Source SDK Base 2013 Multiplayer/sourcetest" --expect-profile sdk2013_mp
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/Counter-Strike Global Offensive/game/csgo" [--load-placeholders] [--clips PATTERNS]
blender -b --factory-startup --python tests/e2e/run_game_imports.py -- --game "<steam>/common/dota 2 beta/game/dota" --expect-profile dota
```

Useful options include:

- `--load-placeholders` to expand every map placeholder;
- `--clips "*"` to load every reachable Source 2 graph clip;
- `--json report.json` for a machine-readable report;
- `--save` or `--save-dir` for manual review;
- `SOURCEIO_PERF_TRACE=1` for bounded aggregate import timings.

Proprietary game files and generated outputs remain local and must not be
committed.

## Official-tool integration gates

These tests create uniquely named disposable content and remove it afterward.
Missing proprietary tools skip opt-in integration tests; explicitly requesting
compilation in the product remains an error when tools are missing.

### Dota 2 Workshop Tools

```powershell
$env:SOURCEIO_DOTA2_WORKSHOP_ROOT = "<steam>\common\dota 2 beta"
python -m pytest tests/source2_workshop/test_dota_workshop_roundtrip.py -q
```

This is a compiler compatibility gate, not a public Dota authoring workflow.

### CS2 Workshop Tools

```powershell
$env:SOURCEIO_CS2_WORKSHOP_ROOT = "<steam>\common\Counter-Strike Global Offensive"
python -m pytest tests/source2_workshop/test_cs2_workshop_contract.py -q
```

The Blender-authored round trip runs inside Blender:

```text
blender -b --factory-startup --python-expr "import sys, pytest; sys.path.insert(0, '..'); import SourceIO; SourceIO.register(); sys.exit(pytest.main(['tests/blender_tests/test_cs2_authoring_roundtrip.py', '-q']))"
```

### TF2 Source 1 toolchain

```powershell
$env:SOURCEIO_SOURCE1_TOOLCHAIN_ROOT = "<steam>\common\Team Fortress 2"
python -m pytest tests/source1_toolchain/test_source1_toolchain_contract.py -q
```

The corresponding Blender-authored test verifies geometry, materials, textures,
official compilation, re-import, and manifest-driven recompile.

### Source SDK Base 2013

Install both Source SDK Base 2013 profiles from Steam. The consolidated gate
validates their manifests and tools, creates disposable mod/content roots,
compiles and recompiles the same generated SMD/QC/VMT/TGA fixture with both
toolchains, re-imports it in Blender, and imports the bundled `sourcetest` HL2
corpus:

```powershell
$env:SOURCEIO_SOURCE_SDK_2013_SP_ROOT = "<steam>\common\Source SDK Base 2013 Singleplayer"
$env:SOURCEIO_SOURCE_SDK_2013_MP_ROOT = "<steam>\common\Source SDK Base 2013 Multiplayer"
$env:SOURCEIO_BLENDER = "<blender>\blender.exe"
python tests/source1_toolchain/run_source_sdk_2013_gate.py --json source-sdk-2013-gate.json
```

The JSON uses the `sourceio.source-sdk-2013-gate` schema and includes Steam
build IDs, SHA-256 tool/content/output fingerprints, bounded compiler outcomes,
Blender re-import assertions, installed-game results, and cleanup status. The
runner returns nonzero for a missing profile, unexpected diagnostic, failed
compiler or re-import, incomplete report, or cleanup failure.

The two shipped `sourcetest` maps have explicit `KNOWN` results for their
missing Lost Coast HDR skybox and unsupported image conversion. Only those
exact diagnostics are accepted; new warnings remain `WARN` and fail the
consolidated gate. Generated sources, compiled outputs, and the disposable mod
remain in a temporary directory and are removed after the report is assembled.

The compiler-only pytest integration can also be run separately:

```powershell
python -m pytest tests/source1_toolchain/test_source_sdk_2013_roundtrip.py -q
```

## Source 2 conformance tools

Generated, redistributable fixtures and normalized snapshots live under
`tests/fixtures` and `tests/conformance`.

The optional ValveResourceFormat differential oracle never searches `PATH` or
downloads an executable. Configure it explicitly:

- `SOURCEIO_VRF_CLI`: path to VRF's `Decompiler`;
- `SOURCEIO_VRF_CLI_SHA256`: optional executable hash pin;
- `SOURCEIO_VRF_TIMEOUT`: positive timeout in seconds.

Without `SOURCEIO_VRF_CLI`, differential tests skip. Invalid configured paths or
hashes are errors.

```text
python tools/source2_conformance.py snapshot
python tools/source2_conformance.py differential FILE.vtex_c
```

VRF's excluded test corpus is not copied or redistributed.

## Experimental boundaries

Some library-level capabilities are intentionally not presented as general
Blender features:

- Particle KV3 remains immutable beside a separately upgraded copy. Only the
  verified `vpcf2` upgrade path runs, with per-component diagnostics; there is
  no particle renderer.
- Cloth reconstruction is disabled by default and requires an explicit backend
  capability report. SourceIO does not create fallback fake cloth.
- Compiled-resource writing is refusal-first. Only whitelisted resource/block
  combinations with valid provenance pass serialization preflight, and writes
  are atomic.

## Project documents

- [`TODO.md`](../TODO.md) is the canonical improvement backlog.
- [`plan.md`](../plan.md) contains validation baselines, installed-game
  evidence, and performance measurements.
- [`AUDIT.md`](../AUDIT.md) records the completed import-speed and
  material-correctness audit plus remaining proposals.
