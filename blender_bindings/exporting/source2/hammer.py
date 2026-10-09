from __future__ import annotations

from collections import defaultdict

from ....library.source2.export import (
    HammerMapDocument,
    HammerOverlay,
    HammerProp,
    LossReport,
    Transform,
    WorldLayer,
    hammer_entity_from_mapping,
)
from ....library.source2.export.map_reconstruction import _matrix_transform
from ....library.source2.provenance import ResourceProvenance
from .provenance import read_import_provenance


def _object_payload(obj):
    value = obj.get("entity_data") if hasattr(obj, "get") else None
    return value if hasattr(value, "get") else None


def _scaled_transform(matrix, unit_scale: float, report: LossReport, path):
    transform = _matrix_transform(matrix, report, path)
    if unit_scale == 0:
        report.irrecoverable(
            "map.blender.scale.zero",
            "Imported object has a zero unit scale and cannot be converted back to Hammer units.",
            path=path,
        )
        return transform
    return Transform(
        origin=tuple(value / unit_scale for value in transform.origin),
        angles=transform.angles,
        scale=transform.scale,
        matrix=transform.matrix,
    )


def _collection_objects(collection, report: LossReport):
    seen = set()

    def walk(current, inferred_layer=None):
        layer = inferred_layer
        explicit_layer = current.get("sourceio_world_layer") if hasattr(current, "get") else None
        if explicit_layer:
            layer = str(explicit_layer)
        elif current.name.startswith("static_props_"):
            layer = current.name[len("static_props_"):]
            report.inferred(
                "map.blender.world_layer.collection",
                "A world layer was reconstructed from an imported static-prop collection name.",
                path=("collections", current.name),
            )
        for obj in current.objects:
            if id(obj) not in seen:
                seen.add(id(obj))
                yield obj, layer
        for child in current.children:
            yield from walk(child, layer)

    yield from walk(collection)


def hammer_document_from_blender(collection, *, name: str | None = None,
                                 report: LossReport | None = None) -> HammerMapDocument:
    loss_report = report if report is not None else LossReport()
    entities = []
    props = []
    overlays = []
    layer_names = set()
    source_objects = []

    for object_index, (obj, layer) in enumerate(_collection_objects(collection, loss_report)):
        if layer:
            layer_names.add(layer)
        payload = _object_payload(obj)
        if not payload:
            continue
        source_payload = payload.to_dict() if hasattr(payload, "to_dict") else dict(payload)
        source_objects.append({
            "object": obj.name,
            "layer": layer,
            "payload": source_payload,
        })
        kind = str(payload.get("type", ""))
        model = payload.get("prop_path")
        unit_scale = float(payload.get("scale", 1.0))
        if kind == "aggregate_static_prop":
            if not model:
                continue
            fragments = payload.get("fragments", ())
            for fragment_index, fragment in enumerate(fragments):
                transform = _scaled_transform(
                    fragment["matrix"],
                    unit_scale,
                    loss_report,
                    ("objects", obj.name, "fragments", fragment_index),
                )
                props.append(HammerProp(
                    model=str(model),
                    transform=transform,
                    skin=str(payload.get("skin", "default")),
                    tint=tuple(map(float, fragment["tint_color"]))
                    if fragment.get("tint_color") is not None else None,
                    layer=layer,
                    source_id=f"{obj.name}:{fragment_index}",
                    properties={"sourceio_draw_call": fragment.get("draw_call", fragment_index)},
                ))
            loss_report.deferred(
                "map.aggregate.expanded",
                "An imported aggregate was expanded into individual Hammer props.",
                path=("objects", obj.name),
            )
            continue

        if kind != "static_prop" and "entity" in payload and hasattr(payload["entity"], "get"):
            entities.append(hammer_entity_from_mapping(
                payload["entity"],
                report=loss_report,
                path=("objects", obj.name, "entity"),
                layer=layer,
            ))
            continue

        if not model:
            continue
        transform = _scaled_transform(
            obj.matrix_world,
            unit_scale,
            loss_report,
            ("objects", obj.name, "transform"),
        )
        if "overlay" in obj.name.lower() or bool(payload.get("overlay", False)):
            if not payload.get("overlay", False):
                loss_report.inferred(
                    "map.overlay.object_name",
                    "An imported object was classified as an overlay from its compiler-generated name.",
                    path=("objects", obj.name),
                )
            overlays.append(HammerOverlay(
                name=obj.name,
                transform=transform,
                layer=layer,
                source_model=str(model),
                properties=dict(payload),
            ))
        else:
            tint = payload.get("tint_color")
            props.append(HammerProp(
                model=str(model),
                transform=transform,
                skin=str(payload.get("skin", "default")),
                tint=tuple(map(float, tint)) if tint is not None else None,
                layer=layer,
                source_id=obj.name,
                properties={"shadow_only": bool(payload.get("shadow_only", False))},
            ))

    provenance = None
    payload = read_import_provenance(collection)
    if payload and isinstance(payload.get("provenance"), dict):
        provenance = ResourceProvenance.from_dict(payload["provenance"])
    return HammerMapDocument(
        name=name or collection.name,
        entities=tuple(entities),
        props=tuple(props),
        overlays=tuple(overlays),
        world_layers=tuple(WorldLayer(layer) for layer in sorted(layer_names)),
        provenance=provenance,
        metadata={"source": "blender", "source_objects": source_objects},
    )
