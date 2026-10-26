# SourceIO improvement backlog

This is the canonical list of open planned or desired work.

- Current validation counts, installed-game evidence, and performance results
  belong in [plan.md](plan.md).
- Supported workflows and limitations belong in [README.md](README.md) and
  [`docs/`](docs/).
- Historical audit findings and completed performance work belong in
  [AUDIT.md](AUDIT.md).

Completed work is removed rather than retained as checked history.

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

## Import improvements

- [ ] Add evidence-backed handlers for the remaining CS2 entities currently
      reported as unsupported: `env_particle_glow`, `hostage_entity`,
      `point_perfcapture`, and the team-introduction entities.
- [ ] Represent Source 2 animation-event durations and useful payload fields in
      Blender instead of exposing only start-frame markers.
- [ ] Discover weapon viewmodel clips reached through AnimGraph
      `m_externalGraphSlots`; first identify the authoritative item-schema or
      weapon-vdata relationship.
- [ ] Make the flex-controller panel and operators resolve the flexed child mesh
      when its armature is active.
- [ ] Preserve VTF metadata on TTH/TTZ texture imports.

## Performance candidates

Measure each candidate on a representative installed asset before changing
behavior. Retain before/after JSON timing reports and verify identical imported
output counts.

- [ ] Split the current Source 2 `import_drawcall` timing into index remapping,
      Blender mesh creation, attribute population, and material attachment, then
      optimize only the measured dominant phase.
- [ ] Evaluate wrapping compatible DXT1/DXT5 VTF payloads as DDS to avoid decode,
      PNG encode, and PNG decode. Verify orientation and BC5/ATI2N normal handling
      before enabling it.
- [ ] Batch Source 1 BSP displacements by material and use bulk color-attribute
      writes.
- [ ] Cache repeated VMT property evaluation per shader-handler instance after
      proxies have run.
- [ ] Benchmark and, if justified, hoist repeated MDL flex-loop vertex slicing;
      separately verify whether `mesh.update()` and unconditional
      `mesh.validate()` are redundant.

Skipping unused Source 2 material textures is not currently planned: the
measured `de_dust2` pass took 0.001 seconds. Reconsider only if another corpus
shows meaningful cost.

## Needs interactive or real-asset validation

- [ ] In a Blender UI session, verify flex-slider release commit, Esc/right-click
      cancellation, sequential scripted value updates, and one-step undo.
- [ ] Validate Half-Life: Alyx `light_spot` and `light_ortho` directions and cone
      semantics against an installed HL:A corpus.
- [ ] Verify MDL36 and MDL2531 bone-role flags before enabling bone collections
      for those builders.
- [ ] Verify whether GoldSrc v4/v6 animation transforms need the same
      rest-relative conversion as v10.
- [ ] Determine whether an external mesh morph atlas belongs to the model, mesh,
      or morph-set resource before changing resource lookup.
- [ ] Validate Source v49 `FRAMEANIM` against a real CS:GO, L4D2, Portal 2, or
      Source Filmmaker model.
- [ ] Validate legacy ANIM `MorphChannel` segments and clip float curves against
      real Source 2 assets; synthetic coverage already exists.

## Authoring workflow decisions

- [ ] Decide whether a unified multi-profile `export`/`recompile` CLI improves on
      the existing engine-specific commands; implement it only if it removes real
      duplication for users or automation.
- [ ] Select the next non-static authoring slice and define its smallest
      official-tool round trip before implementation.

## Parked authoring expansions

These are desired capabilities, not scheduled commitments. Unpark one only with
a specific target profile, representative legal fixtures, and an installed-tool
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
- Do not add completed history, architecture descriptions, support matrices,
  validation command inventories, or third-party tool surveys here.
- New items must state a concrete user outcome and the evidence required to call
  them complete.
