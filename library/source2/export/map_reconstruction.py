from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Any, Mapping

import numpy as np

from ..keyvalues3.types import NullObject
from ..provenance import to_json_safe
from .diagnostics import LossReport
from .domain import HammerMapDocument, HammerOverlay, HammerProp, Transform, WorldLayer
from .hammer import hammer_entity_from_mapping


def _matrix_transform(value: Any, report: LossReport, path) -> Transform:
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        report.irrecoverable(
            "map.transform.matrix.invalid",
            "A scene-object transform does not contain numeric matrix data.",
            path=path,
            details={"value_type": value.__class__.__name__},
        )
        return Transform()
    if matrix.ndim == 1 and matrix.size in (12, 16):
        matrix = matrix.reshape((3, 4) if matrix.size == 12 else (4, 4))
    if matrix.shape == (3, 4):
        matrix = np.vstack((matrix, (0.0, 0.0, 0.0, 1.0)))
    if matrix.shape != (4, 4):
        report.irrecoverable(
            "map.transform.matrix.invalid",
            "A scene-object transform is not a 3x4 or 4x4 matrix.",
            path=path,
            details={"shape": list(matrix.shape)},
        )
        return Transform(matrix=tuple(tuple(map(float, row)) for row in np.atleast_2d(matrix)))

    translation = tuple(map(float, matrix[:3, 3]))
    basis = matrix[:3, :3]
    scales = np.linalg.norm(basis, axis=0)
    if np.any(scales == 0):
        report.irrecoverable(
            "map.transform.matrix.singular",
            "A scene-object transform has a zero scale axis; its matrix remains in the sidecar.",
            path=path,
        )
        return Transform(origin=translation, matrix=tuple(tuple(map(float, row)) for row in matrix))

    rotation = basis / scales
    gram = rotation.T @ rotation
    if not np.allclose(gram, np.identity(3), atol=1e-5):
        report.deferred(
            "map.transform.matrix.shear",
            "A sheared scene-object transform is approximated as Hammer TRS and retained exactly in the sidecar.",
            path=path,
        )
        u, _, vh = np.linalg.svd(rotation)
        rotation = u @ vh

    if np.linalg.det(rotation) < 0:
        axis = int(np.argmax(scales))
        scales[axis] *= -1
        rotation[:, axis] *= -1

    sy = math.sqrt(rotation[0, 0] ** 2 + rotation[1, 0] ** 2)
    singular = sy < 1e-8
    if not singular:
        x = math.atan2(rotation[2, 1], rotation[2, 2])
        y = math.atan2(-rotation[2, 0], sy)
        z = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        x = math.atan2(-rotation[1, 2], rotation[1, 1])
        y = math.atan2(-rotation[2, 0], sy)
        z = 0.0
    report.inferred(
        "map.transform.matrix.decomposed",
        "A compiled scene-object matrix was decomposed to Hammer origin, angles, and scale.",
        path=path,
    )
    return Transform(
        origin=translation,
        angles=tuple(math.degrees(angle) for angle in (x, y, z)),
        scale=tuple(map(float, scales)),
        matrix=tuple(tuple(map(float, row)) for row in matrix),
    )


def _resource_path(resource, reference) -> str | None:
    if isinstance(reference, str):
        return reference
    path = resource.get_child_resource_path(reference)
    return path.as_posix() if path is not None else None


def _layer_name(group: Mapping[str, Any], index: int, report: LossReport) -> str:
    for key in ("m_layerName", "m_worldLayerName", "m_name"):
        value = group.get(key)
        if isinstance(value, str) and value:
            return value
    prefix = str(group.get("m_worldNodePrefix", f"world_layer_{index}"))
    name = PurePosixPath(prefix).name or f"world_layer_{index}"
    report.inferred(
        "map.world_layer.name",
        "A world-layer name was reconstructed from its world-node prefix.",
        path=("world_layers", index),
        details={"prefix": prefix, "name": name},
    )
    return name


def _scene_object_model(node_resource, scene_object, report, path):
    reference = scene_object.get("m_renderableModel")
    model = _resource_path(node_resource, reference)
    if model is None:
        report.missing(
            "map.scene_object.model.missing",
            "A compiled scene object has no resolvable model reference and remains only in map metadata.",
            path=path,
            details={"reference": str(reference)},
        )
    return model


def _scene_object_tint(value, report: LossReport, path):
    if value is None:
        return None
    try:
        values = tuple(map(float, value))
    except (TypeError, ValueError):
        report.missing(
            "map.scene_object.tint.invalid",
            "A compiled scene-object tint is invalid and remains only in the sidecar.",
            path=path,
            details={"value_type": value.__class__.__name__},
        )
        return None
    if len(values) == 3:
        report.inferred(
            "map.scene_object.tint.alpha",
            "A compiled RGB tint has no alpha component; opaque alpha was inferred.",
            path=path,
        )
        return values + (1.0,)
    if len(values) == 4:
        return values
    report.missing(
        "map.scene_object.tint.invalid",
        "A compiled scene-object tint does not have three or four components and remains only in the sidecar.",
        path=path,
        details={"component_count": len(values)},
    )
    return None


def _scene_objects(node_resource, layer: str, report: LossReport):
    props = []
    overlays = []
    unresolved = []
    source_scene_objects = []
    all_scene_objects = [
        ("scene", scene_object)
        for scene_object in node_resource.get_scene_objects()
    ] + [
        ("aggregate", scene_object)
        for scene_object in node_resource.get_aggregate_scene_objects()
    ]
    for object_index, (kind, scene_object) in enumerate(all_scene_objects):
        path = ("world_layers", layer, kind, object_index)
        source_scene_objects.append({
            "layer": layer,
            "kind": kind,
            "index": object_index,
            "data": to_json_safe(scene_object),
        })
        model = _scene_object_model(node_resource, scene_object, report, path)
        if model is None:
            unresolved.append(to_json_safe(scene_object))
            continue
        transform_value = scene_object.get("m_vTransform")
        transform = _matrix_transform(transform_value, report, path + ("transform",)) \
            if transform_value is not None else Transform()
        skin = str(scene_object.get("skin", "default") or "default")
        tint = _scene_object_tint(
            scene_object.get("m_vTintColor"),
            report,
            path + ("m_vTintColor",),
        )

        model_stem = PurePosixPath(model).stem.lower()
        is_overlay = bool(scene_object.get("m_bIsOverlay", False))
        if not is_overlay and "overlay" in model_stem:
            is_overlay = True
            report.inferred(
                "map.overlay.compiler_name",
                "A compiled world model was classified as an overlay from its compiler-generated name.",
                path=path,
                details={"model": model},
            )
        if is_overlay:
            overlays.append(HammerOverlay(
                name=PurePosixPath(model).stem,
                material=str(scene_object.get("m_material", "")) or None,
                transform=transform,
                layer=layer,
                source_model=model,
                properties={"compiled_scene_object": to_json_safe(scene_object)},
            ))
            continue

        fragments = scene_object.get("m_fragmentTransforms", ())
        draw_infos = scene_object.get("m_aggregateMeshes", ())
        if kind == "aggregate" and draw_infos:
            for fragment_index, draw_info in enumerate(draw_infos):
                fragment_transform = transform
                if draw_info.get("m_bHasTransform", False):
                    if fragment_index < len(fragments):
                        fragment_transform = _matrix_transform(
                            fragments[fragment_index],
                            report,
                            path + ("fragments", fragment_index),
                        )
                    else:
                        report.missing(
                            "map.aggregate.transform.missing",
                            "An aggregate draw call requires a fragment transform that is unavailable; the aggregate transform is used.",
                            path=path + ("fragments", fragment_index),
                        )
                fragment_tint = _scene_object_tint(
                    draw_info.get("m_vTintColor"),
                    report,
                    path + ("aggregate_meshes", fragment_index, "m_vTintColor"),
                ) or tint
                props.append(HammerProp(
                    model=model,
                    transform=fragment_transform,
                    skin=skin,
                    tint=fragment_tint,
                    layer=layer,
                    source_id=f"{layer}:{kind}:{object_index}:{fragment_index}:{model}",
                    properties={
                        "sourceio_aggregate_draw_call": int(draw_info.get("m_nDrawCallIndex", fragment_index)),
                    },
                ))
            report.deferred(
                "map.aggregate.expanded",
                "An aggregate scene object was expanded into individual props; aggregate batching remains in the sidecar.",
                path=path,
            )
        else:
            if kind == "aggregate" and fragments and not draw_infos:
                report.irrecoverable(
                    "map.aggregate.draw_info.missing",
                    "Aggregate fragment transforms have no matching draw-call metadata; a single model proxy is emitted.",
                    path=path,
                )
            props.append(HammerProp(
                model=model,
                transform=transform,
                skin=skin,
                tint=tint,
                layer=layer,
                source_id=f"{layer}:{kind}:{object_index}:{model}",
                properties={"compiled_scene_object_kind": kind},
            ))
    return props, overlays, unresolved, source_scene_objects


def _iter_entity_lumps(world_resource, content_manager, report: LossReport):
    from ..resource_types.compiled_world_resource import CompiledEntityLumpResource

    seen = set()
    active = set()

    def identity(resource):
        filepath = getattr(resource, "_filepath", None)
        return filepath.as_posix() if hasattr(filepath, "as_posix") else str(filepath or id(resource))

    def walk(resource):
        key = identity(resource)
        if key in active:
            report.deferred(
                "map.entity_lump.cycle",
                "An entity-lump dependency cycle was stopped.",
                details={"resource": key},
            )
            return
        if key in seen:
            return
        active.add(key)
        seen.add(key)
        yield resource
        for child in resource.get_child_lumps(content_manager):
            if child is None:
                report.missing(
                    "map.entity_lump.child.missing",
                    "A referenced child entity lump was not found.",
                    details={"parent": key},
                )
                continue
            yield from walk(child)
        active.remove(key)

    for index, reference in enumerate(world_resource.data_block.get("m_entityLumps", ())):
        if isinstance(reference, NullObject):
            continue
        lump = world_resource.get_child_resource(
            reference,
            content_manager,
            CompiledEntityLumpResource,
        )
        if lump is None:
            report.missing(
                "map.entity_lump.missing",
                "A referenced entity lump was not found.",
                path=("entity_lumps", index),
                details={"reference": str(reference)},
            )
            continue
        yield from walk(lump)


def hammer_document_from_compiled(
        map_resource,
        world_resource,
        content_manager,
        *,
        report: LossReport | None = None,
) -> HammerMapDocument:
    loss_report = report if report is not None else LossReport()
    world_data = world_resource.data_block
    layers = []
    props = []
    overlays = []
    unresolved_scene_objects = []
    source_scene_objects = []

    groups = list(world_data.get("m_worldNodes", ()))
    for group_index, group in enumerate(groups):
        layer_name = _layer_name(group, group_index, loss_report)
        prefix_value = group.get("m_worldNodePrefix")
        if not isinstance(prefix_value, str) or not prefix_value:
            loss_report.irrecoverable(
                "map.world_node.prefix.missing",
                "A compiled world layer has no world-node prefix and cannot be reconstructed.",
                path=("world_layers", layer_name),
            )
            continue
        prefix = prefix_value
        layers.append(WorldLayer(
            name=layer_name,
            visible=not bool(group.get("m_bStartHidden", False)),
            source_reference=prefix,
            metadata=to_json_safe(group),
        ))
        node = map_resource.get_worldnode(prefix, content_manager)
        if node is None:
            loss_report.missing(
                "map.world_node.missing",
                "A referenced world node was not found.",
                path=("world_layers", layer_name),
                details={"prefix": prefix},
            )
            continue
        node_props, node_overlays, unresolved, source_objects = _scene_objects(
            node,
            layer_name,
            loss_report,
        )
        props.extend(node_props)
        overlays.extend(node_overlays)
        unresolved_scene_objects.extend(unresolved)
        source_scene_objects.extend(source_objects)

    entities = []
    world_properties = {"classname": "worldspawn"}
    for lump_index, lump in enumerate(_iter_entity_lumps(world_resource, content_manager, loss_report)):
        for entity_index, entity in enumerate(lump.get_entities()):
            values = entity.get("values", entity)
            if not isinstance(values, Mapping):
                loss_report.irrecoverable(
                    "map.entity.values.invalid",
                    "Compiled entity values are not a mapping and cannot be reconstructed.",
                    path=("entity_lumps", lump_index, "entities", entity_index),
                    details={"value_type": values.__class__.__name__},
                )
                continue
            if values.get("classname") == "worldspawn":
                world_properties.update(to_json_safe(values))
                continue
            layer = values.get("layername", values.get("worldlayer"))
            entities.append(hammer_entity_from_mapping(
                entity,
                report=loss_report,
                path=("entity_lumps", lump_index, "entities", entity_index),
                layer=str(layer) if layer else None,
            ))

    return HammerMapDocument(
        name=map_resource.name,
        entities=tuple(entities),
        props=tuple(props),
        overlays=tuple(overlays),
        world_layers=tuple(layers),
        world_properties=world_properties,
        provenance=map_resource.get_resource_provenance(),
        metadata={
            "world_resource": str(getattr(world_resource, "_filepath", world_resource.name)),
            "unresolved_scene_objects": unresolved_scene_objects,
            "compiled_scene_objects": source_scene_objects,
        },
    )
