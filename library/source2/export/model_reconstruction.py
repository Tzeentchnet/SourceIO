from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from ..blocks.kv3_block import KVBlock, custom_type_kvblock
from ..blocks.vertex_index_buffer import VertexIndexBuffer
from ..blocks.vertex_index_buffer.index_buffer import IndexBuffer
from ..blocks.vertex_index_buffer.vertex_buffer import VertexBuffer
from ..keyvalues3.types import NullObject
from ..provenance import to_json_safe
from ..resource_types.compiled_mesh_resource import CompiledMeshResource
from ..resource_types.compiled_physics_resource import CompiledPhysicsResource
from .diagnostics import LossReport
from .domain import (
    AssetReference,
    Attachment,
    AttachmentInfluence,
    BodyGroup,
    Hitbox,
    LODLevel,
    MaterialRemap,
    ModelDocument,
    Skin,
)
from .physics import physics_shapes_from_block
from .render_mesh import decode_compiled_mesh, decode_render_mesh


def _value(mapping: Mapping[str, Any], *keys: str, default=None):
    for key in keys:
        if key in mapping:
            return mapping[key]
    try:
        return mapping[tuple(keys)]
    except (KeyError, TypeError):
        return default


def _set_bits(value: int, limit: int) -> tuple[int, ...]:
    return tuple(index for index in range(limit) if value & (1 << index))


def _mask_value(
        values: list[Any],
        index: int,
        default: int,
        *,
        report: LossReport,
        code: str,
        label: str,
) -> int:
    if index >= len(values):
        report.inferred(
            code,
            f"{label} is missing; a conservative default was used.",
            path=("mesh_references", index),
            details={"default": default},
        )
        return default
    try:
        return int(values[index])
    except (TypeError, ValueError, OverflowError):
        report.irrecoverable(
            code,
            f"{label} is invalid; a conservative default was used.",
            path=("mesh_references", index),
            details={"value_type": values[index].__class__.__name__, "default": default},
        )
        return default


def _resource_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value:
            yield value
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _resource_strings(item)
        return
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _resource_strings(item)


def _attachment_records(data_block: Mapping[str, Any], report: LossReport):
    attachments = []
    seen = set()
    for entry_index, entry in enumerate(data_block.get("m_attachments", ())):
        name = str(_value(entry, "key", "m_name", default=f"attachment_{entry_index}"))
        value = _value(entry, "value", "m_attachment", default=entry)
        if name in seen:
            continue
        seen.add(name)
        names = list(value.get("m_influenceNames", ()))
        offsets = list(value.get("m_vInfluenceOffsets", ()))
        rotations = list(value.get("m_vInfluenceRotations", ()))
        weights = list(value.get("m_influenceWeights", ()))
        count = max(len(names), len(offsets), len(rotations), len(weights))
        influences = []
        for influence_index in range(count):
            parent = str(names[influence_index]) if influence_index < len(names) else ""
            origin = offsets[influence_index] if influence_index < len(offsets) else (0.0, 0.0, 0.0)
            source_rotation = rotations[influence_index] if influence_index < len(rotations) else (1.0, 0.0, 0.0, 0.0)
            if len(source_rotation) != 4:
                report.missing(
                    "model.attachment.rotation.invalid",
                    "Attachment influence has an invalid quaternion and remains only in the sidecar metadata.",
                    path=("attachments", name, influence_index),
                )
                source_rotation = (1.0, 0.0, 0.0, 0.0)
            w, x, y, z = map(float, source_rotation)
            weight = float(weights[influence_index]) if influence_index < len(weights) else 1.0
            influences.append(AttachmentInfluence(
                parent_bone=parent,
                origin=tuple(map(float, origin)),
                rotation=(x, y, z, w),
                weight=weight,
            ))
        attachments.append(Attachment(
            name=name,
            influences=tuple(influences),
            ignore_rotation=bool(value.get("m_bIgnoreRotation", False)),
        ))
    return attachments


def _material_skins(material_groups: Iterable[Mapping[str, Any]]) -> tuple[Skin, ...]:
    groups = list(material_groups)
    if not groups:
        return ()
    default_materials = list(groups[0].get("m_materials", ()))
    skins = []
    for index, group in enumerate(groups):
        materials = list(group.get("m_materials", ()))
        remaps = tuple(
            MaterialRemap(str(source), str(target))
            for source, target in zip(default_materials, materials)
        )
        skins.append(Skin(
            name=str(group.get("m_name", "default" if index == 0 else f"skin_{index}")),
            remaps=remaps,
            is_default=index == 0,
        ))
    return tuple(skins)


def _hitboxes(data: Mapping[str, Any], report: LossReport) -> tuple[Hitbox, ...]:
    sets = _value(data, "m_hitboxSets", "m_HitboxSets", default=())
    result = []
    for set_index, hitbox_set in enumerate(sets):
        set_name = str(_value(hitbox_set, "m_name", "m_Name", default=f"set_{set_index}"))
        records = _value(hitbox_set, "m_hitboxes", "m_Hitboxes", default=())
        for hitbox_index, record in enumerate(records):
            minimum = _value(record, "m_vMinBounds", "m_vMins", "m_vecMins")
            maximum = _value(record, "m_vMaxBounds", "m_vMaxs", "m_vecMaxs")
            if minimum is None or maximum is None:
                report.missing(
                    "model.hitbox.bounds.missing",
                    "Hitbox bounds are unavailable and the raw hitbox remains only in the sidecar metadata.",
                    path=("hitboxes", set_name, hitbox_index),
                )
                continue
            result.append(Hitbox(
                name=str(_value(record, "m_name", "m_Name", default=f"hitbox_{hitbox_index}")),
                set_name=set_name,
                parent_bone=str(_value(record, "m_sBoneName", "m_boneName", "m_BoneName", default="")),
                minimum=tuple(map(float, minimum)),
                maximum=tuple(map(float, maximum)),
                group_id=int(_value(record, "m_nGroupId", "m_nGroup", default=0)),
                surface_property=str(_value(record, "m_sSurfaceProperty", "m_surfaceProperty", default="")),
            ))
    return tuple(result)


def _references(data: Mapping[str, Any], tokens: tuple[str, ...], kind: str) -> tuple[AssetReference, ...]:
    found = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if "ref" not in lowered or not any(token in lowered for token in tokens):
            continue
        for path in _resource_strings(value):
            found[path] = AssetReference(kind=kind, path=path, metadata={"source_key": str(key)})
    return tuple(found[path] for path in sorted(found))


def _external_physics(resource, data, content_manager, report):
    references = []
    shapes = []
    for index, path in enumerate(data.get("m_refPhysicsData", ())):
        path_string = str(path)
        references.append(path_string)
        if content_manager is None:
            report.missing(
                "model.physics.resource.unresolved",
                "External physics is referenced but no content resolver was supplied.",
                path=("physics", index),
                details={"resource": path_string},
            )
            continue
        physics = resource.get_child_resource(path, content_manager, CompiledPhysicsResource)
        if physics is None:
            report.missing(
                "model.physics.resource.missing",
                "Referenced external physics resource was not found.",
                path=("physics", index),
                details={"resource": path_string},
            )
            continue
        shapes.extend(physics.get_physics_shapes(report=report))
    return tuple(references), shapes


def model_document_from_compiled(
        resource,
        *,
        content_manager=None,
        report: LossReport | None = None,
) -> ModelDocument:
    loss_report = report if report is not None else LossReport()
    data = resource.get_block(custom_type_kvblock("PermModelData_t"), block_name="DATA")
    if data is None:
        loss_report.irrecoverable(
            "model.data.missing",
            "Compiled model has no PermModelData_t DATA block.",
        )
        return ModelDocument(resource.name, (), provenance=resource.get_resource_provenance())
    ctrl = resource.get_block(KVBlock, block_name="CTRL")

    lod_distances = tuple(float(value) for value in data.get("m_lodGroupSwitchDistances", ()))
    group_names = tuple(map(str, data.get("m_meshGroups", ())))
    group_masks = list(data.get("m_refMeshGroupMasks", ()))
    lod_masks = list(data.get("m_refLODGroupMasks", ()))
    mask_lod_count = 0
    for value in lod_masks:
        try:
            mask_lod_count = max(mask_lod_count, max(0, int(value)).bit_length())
        except (TypeError, ValueError, OverflowError):
            continue
    lod_count = max(len(lod_distances), mask_lod_count, 1)
    decoded_meshes = []
    attachments = []
    material_groups = []
    flex_references: dict[str, AssetReference] = {}
    embedded_morph_sources = set()
    mesh_memberships: dict[str, list[str]] = {name: [] for name in group_names}
    lod_memberships: dict[int, list[str]] = {index: [] for index in range(lod_count)}

    references = list(data.get("m_refMeshes", ()))
    embedded = list(ctrl.get("embedded_meshes", ())) if ctrl else []
    entry_count = max(len(references), len(embedded))
    for mesh_index in range(entry_count):
        reference = references[mesh_index] if mesh_index < len(references) else None
        group_mask = (
            _mask_value(
                group_masks,
                mesh_index,
                (1 << len(group_names)) - 1,
                report=loss_report,
                code="model.bodygroup.mask.missing",
                label="Compiled mesh-group mask",
            )
            if group_names else 0
        )
        lod_mask = _mask_value(
            lod_masks,
            mesh_index,
            1,
            report=loss_report,
            code="model.lod.mask.missing",
            label="Compiled LOD mask",
        )
        bodygroups = tuple(
            name for index, name in enumerate(group_names)
            if group_mask & (1 << index)
        )
        lods = _set_bits(lod_mask, lod_count) or (0,)
        mesh_data = None
        meshes = ()

        is_embedded = reference is None or isinstance(reference, NullObject) or not reference
        if is_embedded and mesh_index < len(embedded):
            info = embedded[mesh_index]
            morph_block_id = int(_value(info, "morph_block", "m_nMorphBlock", default=-1))
            if morph_block_id >= 0:
                embedded_morph_sources.add(f"block:{morph_block_id}")
            mesh_data = resource.get_block(
                KVBlock,
                block_id=int(_value(info, "data_block", "m_nDataBlock", default=-1)),
            )
            vbib = resource.get_block(
                VertexIndexBuffer,
                block_id=int(_value(info, "vbib_block", "m_nVBIBBlock", default=-1)),
            )
            tools_vbib = resource.get_block(
                VertexIndexBuffer,
                block_id=int(_value(info, "tools_vb_block", "m_nToolsVBIBBlock", default=-1)),
            )
            mesh_name = str(_value(info, "name", "m_Name", default=f"{resource.name}_{mesh_index}"))
            if mesh_data is not None and vbib is not None:
                meshes = decode_render_mesh(
                    mesh_data,
                    vbib.index_buffers,
                    vbib.vertex_buffers,
                    resource,
                    extra_vertex_buffers=tools_vbib.vertex_buffers if tools_vbib else None,
                    name=mesh_name,
                    bodygroups=bodygroups,
                    lods=lods,
                    report=loss_report,
                )
            elif mesh_data is not None and "m_vertexBuffers" in info and "m_indexBuffers" in info:
                meshes = decode_render_mesh(
                    mesh_data,
                    [IndexBuffer.from_kv(value) for value in info["m_indexBuffers"]],
                    [VertexBuffer.from_kv(value) for value in info["m_vertexBuffers"]],
                    resource,
                    name=mesh_name,
                    bodygroups=bodygroups,
                    lods=lods,
                    report=loss_report,
                )
            else:
                loss_report.missing(
                    "model.mesh.embedded.missing",
                    "Embedded mesh blocks are unavailable.",
                    path=("mesh_references", mesh_index),
                )
        else:
            if content_manager is None:
                loss_report.missing(
                    "model.mesh.external.unresolved",
                    "External mesh is referenced but no content resolver was supplied.",
                    path=("mesh_references", mesh_index),
                    details={"resource": str(reference)},
                )
                continue
            mesh_resource = resource.get_child_resource(reference, content_manager, CompiledMeshResource)
            if mesh_resource is None:
                loss_report.missing(
                    "model.mesh.external.missing",
                    "Referenced external mesh resource was not found.",
                    path=("mesh_references", mesh_index),
                    details={"resource": str(reference)},
                )
                continue
            has_block = getattr(mesh_resource, "has_block", None)
            if callable(has_block) and has_block("MRPH"):
                embedded_morph_sources.add(str(getattr(mesh_resource, "_filepath", reference)))
            mesh_data = mesh_resource.get_block(KVBlock, block_name="DATA")
            meshes = decode_compiled_mesh(mesh_resource, report=loss_report)
            meshes = tuple(replace(mesh, bodygroups=bodygroups, lods=lods) for mesh in meshes)

        for submesh_index, mesh in enumerate(meshes):
            unique_name = f"{mesh.name}_ref{mesh_index}_{submesh_index}"
            mesh = replace(mesh, name=unique_name)
            decoded_meshes.append(mesh)
            for group in bodygroups:
                mesh_memberships[group].append(unique_name)
            for lod in lods:
                lod_memberships.setdefault(lod, []).append(unique_name)
        if mesh_data is not None:
            attachments.extend(_attachment_records(mesh_data, loss_report))
            material_groups.extend(mesh_data.get("m_materialGroups", ()))
            for key in ("m_morphSet", "m_pMorphSet", "m_refMorphs"):
                if key in mesh_data:
                    for path in _resource_strings(mesh_data[key]):
                        flex_references[path] = AssetReference("flex", path, metadata={"source_key": key})

    for key in ("m_morphSet", "m_pMorphSet", "m_refMorphs"):
        if key in data:
            for path in _resource_strings(data[key]):
                flex_references[path] = AssetReference("flex", path, metadata={"source_key": key})
    has_block = getattr(resource, "has_block", None)
    if callable(has_block) and has_block("MRPH"):
        embedded_morph_sources.add("MRPH")
    if embedded_morph_sources:
        loss_report.deferred(
            "model.flex.embedded.deferred",
            "Embedded compiled flex delta data cannot be reconstructed as editable source in this static slice.",
            path=("flex_references",),
            details={"sources": sorted(embedded_morph_sources)},
        )

    bodygroups = []
    for name in group_names:
        members = tuple(mesh_memberships[name])
        if members:
            loss_report.inferred(
                "model.bodygroup.choices.compiled",
                "Compilation preserves bodygroup membership but not original choice boundaries; one choice contains all members.",
                path=("bodygroups", name),
            )
            bodygroups.append(BodyGroup(name=name, choices=(members,)))

    active_lods = tuple(
        index for index in range(lod_count)
        if lod_memberships.get(index)
    )
    for index in active_lods:
        if index >= len(lod_distances):
            loss_report.inferred(
                "model.lod.switch_distance.missing",
                "An active compiled LOD has no recoverable switch distance; zero is used.",
                path=("lods", index),
            )
    lod_levels = tuple(
        LODLevel(
            index=index,
            switch_distance=lod_distances[index] if index < len(lod_distances) else 0.0,
            meshes=tuple(lod_memberships.get(index, ())),
        )
        for index in active_lods
    )

    physics_references, physics_shapes = _external_physics(
        resource, data, content_manager, loss_report
    )
    if ctrl and ctrl.get("embedded_physics"):
        block_id = int(_value(
            ctrl["embedded_physics"],
            "phys_data_block",
            "m_nPhysDataBlock",
            default=-1,
        ))
        physics_block = resource.get_block(
            custom_type_kvblock("VPhysXAggregateData_t"),
            block_id=block_id,
        )
        if physics_block is not None:
            physics_shapes.extend(physics_shapes_from_block(physics_block, report=loss_report))
        else:
            loss_report.missing(
                "model.physics.embedded.missing",
                "Embedded physics metadata points to an unavailable block.",
            )

    animation_references = {
        reference.path: reference
        for reference in _references(data, ("anim", "sequence"), "animation")
    }
    for path in physics_references:
        animation_references.pop(path, None)
    embedded_animation = ctrl.get("embedded_animation") if ctrl else None
    has_embedded_animation_blocks = (
        callable(has_block)
        and has_block("ANIM")
        and has_block("AGRP")
    )
    if embedded_animation or has_embedded_animation_blocks:
        loss_report.deferred(
            "model.animation.embedded.deferred",
            "Embedded compiled animation data requires the animation-document exporter and is not emitted by this static slice.",
            path=("animation_references",),
        )

    provenance = resource.get_resource_provenance()
    return ModelDocument(
        name=resource.name,
        meshes=tuple(decoded_meshes),
        bodygroups=tuple(bodygroups),
        lods=lod_levels,
        skins=_material_skins(material_groups or data.get("m_materialGroups", ())),
        attachments=tuple(attachments),
        physics_shapes=tuple(physics_shapes),
        hitboxes=_hitboxes(data, loss_report),
        flex_references=tuple(flex_references[path] for path in sorted(flex_references)),
        animation_references=tuple(animation_references[path] for path in sorted(animation_references)),
        provenance=provenance,
        metadata={
            "source_name": str(data.get("m_name", resource.name)),
            "physics_references": physics_references,
            "embedded_morph_sources": tuple(sorted(embedded_morph_sources)),
            "embedded_animation": to_json_safe(embedded_animation),
        },
    )
