# SourceIO authoring backlog

This file tracks only open export and authoring work.

- Supported workflows and user-facing limitations belong in [README.md](README.md).
- Current import priorities and validation commands belong in [plan.md](plan.md).
- Historical audit findings and completed performance work belong in [AUDIT.md](AUDIT.md).

The original CS2 and Source 1 static-authoring milestones are complete and are
no longer tracked here.

## Committed next gate

### Source SDK Base 2013 and Half-Life 2

TF2 validates the shared Source 1 authoring pipeline, but it does not replace a
reference Source SDK 2013 gate.

- [ ] Discover and validate both Source SDK Base 2013 profiles.
- [ ] Create a disposable mod fixture without committing proprietary content.
- [ ] Compile generated SMD, QC, VMT, and texture sources with the SDK tools.
- [ ] Verify the expected MDL, VVD, VTX, and VTF outputs and re-import them.
- [ ] Run representative Half-Life 2 models and maps through the installed-game
      regression runner.
- [ ] Record installation and tool hashes plus a machine-readable gate report.

This gate is complete only when it is reproducible from documented local
prerequisites; installing the SDK alone is not completion.

## Desired material-authoring extensions

Each mapping needs an official-tool fixture, an explicit Blender graph contract,
golden serialization, compile verification, and compiled re-import before it can
be advertised.

### CS2

- [ ] Add emissive material authoring.
- [ ] Add true alpha-blended material authoring.
- [ ] Add ambient-occlusion authoring where the selected shader contract supports
      it.
- [ ] Add additional texture roles only when a real material corpus demonstrates
      the required shader mapping.

### Source 1

- [ ] Design opt-in roughness/metalness/AO conversion or baking presets with
      visual regression tests.

Any conversion must be explicit and reproducible; implicit PBR-to-Source-1
conversion remains unsupported.

## Authoring workflow decisions

- [ ] Decide whether a unified multi-profile `export`/`recompile` CLI improves on
      the existing engine-specific commands; implement it only if it removes real
      duplication for users or automation.
- [ ] Select the next non-static authoring slice and define its smallest
      official-tool round-trip before implementation.

## Parked authoring expansions

These are desired capabilities, not scheduled commitments. Unpark one only with a
specific target profile, representative legal fixtures, and an installed-tool
acceptance gate.

### Models

- [ ] Skeletal model authoring.
- [ ] Animation authoring.
- [ ] Flex and morph authoring.
- [ ] Physics and collision authoring.
- [ ] LOD, bodygroup, skin, and attachment authoring.

### Worlds

- [ ] VMF/VMAP world authoring.
- [ ] Prefab, entity, lighting, and dependency authoring.

### Profiles and platforms

- [ ] GoldSrc authoring.
- [ ] Additional Source and Source 2 game profiles where a controlled corpus
      proves branch-specific behavior.
- [ ] Wine-based compiler execution as an explicit validated profile capability.

### Material automation

- [ ] Baking of supported procedural Blender graphs into explicit texture inputs.
- [ ] UDIM, sequence, and defined channel-packed texture workflows.

## Backlog maintenance

- Remove an item when it is completed, rejected, or moved to an issue with an
  owner and acceptance criteria.
- Do not add completed implementation history, architecture descriptions,
  support matrices, validation command inventories, or third-party tool surveys
  here.
- New items must state a concrete user outcome and the evidence required to call
  them complete.
