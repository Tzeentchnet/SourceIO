from __future__ import annotations

import csv
import io
import json
import uuid
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from ...utils import datamodel
from .bundle import ExportBundle
from .diagnostics import DiagnosticSeverity, LossReport
from .domain import (
    EntityConnection,
    HammerEntity,
    HammerMapDocument,
    HammerOverlay,
    HammerProp,
    Transform,
    WorldLayer,
)


def _uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"sourceio:vmap:{name}")


def _parse_vector(value: Any, *, default: tuple[float, float, float], field: str,
                  report: LossReport, path: tuple[str | int, ...]) -> tuple[float, float, float]:
    if value is None:
        report.missing(
            f"map.transform.{field}.missing",
            f"Entity {field} is missing; Hammer's default {default} is used.",
            path=path + (field,),
        )
        return default
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        parts = value
    elif hasattr(value, "tolist"):
        parts = value.tolist()
    else:
        report.missing(
            f"map.transform.{field}.invalid",
            f"Entity {field} cannot be represented; Hammer's default {default} is used.",
            path=path + (field,),
            details={"value_type": value.__class__.__name__},
        )
        return default
    if len(parts) != 3:
        report.missing(
            f"map.transform.{field}.invalid",
            f"Entity {field} must have three components; Hammer's default {default} is used.",
            path=path + (field,),
            details={"value": str(value)},
        )
        return default
    try:
        return tuple(float(component) for component in parts)
    except (TypeError, ValueError):
        report.missing(
            f"map.transform.{field}.invalid",
            f"Entity {field} is not numeric; Hammer's default {default} is used.",
            path=path + (field,),
            details={"value": str(value)},
        )
        return default


def _connection_from_mapping(value: Mapping[str, Any]) -> EntityConnection | None:
    output_name = value.get("output_name", value.get("outputName", value.get("output")))
    target_name = value.get("target_name", value.get("targetName", value.get("target")))
    input_name = value.get("input_name", value.get("inputName", value.get("input")))
    if not all(isinstance(item, str) and item for item in (output_name, target_name, input_name)):
        return None
    return EntityConnection(
        output_name=output_name,
        target_name=target_name,
        input_name=input_name,
        parameter=str(value.get("parameter", value.get("overrideParam", ""))),
        delay=float(value.get("delay", 0.0)),
        times_to_fire=int(value.get("times_to_fire", value.get("timesToFire", -1))),
        target_type=int(value.get("target_type", value.get("targetType", 0))),
    )


def _parse_output_string(output_name: str, value: str) -> EntityConnection | None:
    rows = list(csv.reader(io.StringIO(value)))
    if len(rows) != 1 or len(rows[0]) not in (3, 4, 5):
        return None
    row = rows[0]
    try:
        delay = float(row[3]) if len(row) >= 4 and row[3] else 0.0
        times = int(row[4]) if len(row) >= 5 and row[4] else -1
    except ValueError:
        return None
    return EntityConnection(output_name, row[0], row[1], row[2], delay, times)


def hammer_entity_from_mapping(
        entity: Mapping[str, Any],
        *,
        report: LossReport | None = None,
        path: tuple[str | int, ...] = ("entities",),
        layer: str | None = None,
) -> HammerEntity:
    loss_report = report if report is not None else LossReport()
    values = entity.get("values", entity)
    if not isinstance(values, Mapping):
        raise TypeError("Entity values must be a mapping")
    properties = dict(values)
    classname = str(properties.get("classname", ""))
    if not classname:
        loss_report.irrecoverable(
            "map.entity.classname.missing",
            "An entity without classname cannot be emitted to Hammer.",
            path=path,
        )

    connections: list[EntityConnection] = []
    for key in ("connections", "connectionsData", "outputs"):
        connection_values = properties.get(key, ())
        if isinstance(connection_values, Mapping):
            connection_values = connection_values.values()
        if isinstance(connection_values, Iterable) and not isinstance(connection_values, (str, bytes)):
            for connection_index, connection_value in enumerate(connection_values):
                if not isinstance(connection_value, Mapping):
                    loss_report.missing(
                        "map.entity.io.invalid",
                        "Unstructured entity I/O remains in the sidecar.",
                        path=path + (key, connection_index),
                    )
                    continue
                connection = _connection_from_mapping(connection_value)
                if connection is None:
                    loss_report.missing(
                        "map.entity.io.invalid",
                        "Entity I/O is missing output, target, or input names and remains in the sidecar.",
                        path=path + (key, connection_index),
                    )
                else:
                    connections.append(connection)

    for key, value in properties.items():
        if not key.startswith("On") or not isinstance(value, str):
            continue
        connection = _parse_output_string(key, value)
        if connection is not None:
            connections.append(connection)
            loss_report.inferred(
                "map.entity.io.parsed_legacy",
                "A legacy comma-separated output was reconstructed as Hammer connection data.",
                path=path + (key,),
            )

    transform = Transform(
        origin=_parse_vector(
            properties.get("origin"),
            default=(0.0, 0.0, 0.0),
            field="origin",
            report=loss_report,
            path=path,
        ),
        angles=_parse_vector(
            properties.get("angles"),
            default=(0.0, 0.0, 0.0),
            field="angles",
            report=loss_report,
            path=path,
        ),
        scale=_parse_vector(
            properties.get("scales", properties.get("scale")),
            default=(1.0, 1.0, 1.0),
            field="scale",
            report=loss_report,
            path=path,
        ),
    )
    return HammerEntity(
        classname=classname,
        properties=properties,
        transform=transform,
        connections=tuple(connections),
        layer=layer,
        source_id=str(properties.get("hammeruniqueid", properties.get("hammerUniqueId", ""))) or None,
    )


def _entity_property_value(value: Any, *, report: LossReport, path: tuple[str | int, ...]) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        report.inferred(
            "map.entity.property.none",
            "A null entity property is represented as an empty Hammer string.",
            path=path,
        )
        return ""
    if isinstance(value, bool):
        report.inferred(
            "map.entity.property.typed",
            "A typed entity property is represented using Hammer's string KeyValue form.",
            path=path,
            details={"source_type": "bool"},
        )
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        report.inferred(
            "map.entity.property.typed",
            "A typed entity property is represented using Hammer's string KeyValue form.",
            path=path,
            details={"source_type": value.__class__.__name__},
        )
        return str(value)
    if isinstance(value, (list, tuple)) and all(isinstance(item, (int, float, str)) for item in value):
        report.inferred(
            "map.entity.property.vector",
            "An array entity property is represented as a space-separated Hammer string.",
            path=path,
        )
        return " ".join(map(str, value))
    report.deferred(
        "map.entity.property.sidecar",
        "A structured entity property remains in the sidecar and is JSON-encoded in Hammer.",
        path=path,
        details={"source_type": value.__class__.__name__},
    )
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class _HammerDmxBuilder:
    def __init__(self, document: HammerMapDocument, report: LossReport):
        self.document = document
        self.report = report
        self.model = datamodel.DataModel("vmap", 29)
        self.model.allow_random_ids = False
        self.node_id = 1
        self.root = self.model.add_element(
            "CMapRootElement",
            id=_uuid(f"{document.name}:root"),
        )
        self.layer_nodes: dict[str, datamodel.Element] = {}

    def _element(self, name: str, element_type: str, identity: str):
        return self.model.add_element(name, element_type, id=_uuid(f"{self.document.name}:{identity}"))

    def _map_node(self, element, transform: Transform):
        element["origin"] = datamodel.Vector3(transform.origin)
        element["angles"] = datamodel.Angle(transform.angles)
        element["scales"] = datamodel.Vector3(transform.scale)
        element["nodeID"] = self.node_id
        self.node_id += 1
        element["children"] = datamodel.make_array([], datamodel.Element)
        element["editorOnly"] = False
        element["force_hidden"] = False
        element["transformLocked"] = False
        element["variableTargetKeys"] = datamodel.make_array([], str)
        element["variableNames"] = datamodel.make_array([], str)
        return element

    def _base_entity(self, element, properties: Mapping[str, Any],
                     connections: Iterable[EntityConnection], identity: str):
        plugs = self._element("relayPlugData", "DmePlugList", f"{identity}:plugs")
        plugs["names"] = datamodel.make_array([], str)
        plugs["dataTypes"] = datamodel.make_array([], int)
        plugs["plugTypes"] = datamodel.make_array([], int)
        plugs["descriptions"] = datamodel.make_array([], str)
        element["relayPlugData"] = plugs

        connection_elements = []
        for index, connection in enumerate(connections):
            item = self._element(
                connection.output_name,
                "DmeConnectionData",
                f"{identity}:connection:{index}",
            )
            item["outputName"] = connection.output_name
            item["targetType"] = connection.target_type
            item["targetName"] = connection.target_name
            item["inputName"] = connection.input_name
            item["overrideParam"] = connection.parameter
            item["delay"] = float(connection.delay)
            item["timesToFire"] = int(connection.times_to_fire)
            connection_elements.append(item)
        element["connectionsData"] = datamodel.make_array(connection_elements, datamodel.Element)

        entity_properties = self._element(
            "entity_properties",
            "EditGameClassProps",
            f"{identity}:properties",
        )
        for key in sorted(properties):
            entity_properties[str(key)] = _entity_property_value(
                properties[key],
                report=self.report,
                path=("entity_properties", identity, str(key)),
            )
        element["entity_properties"] = entity_properties

    def _parent(self, layer: str | None):
        if layer and layer in self.layer_nodes:
            return self.layer_nodes[layer]
        if layer:
            self.report.missing(
                "map.world_layer.missing",
                "An object references an unavailable world layer and was placed in the world root.",
                details={"layer": layer},
            )
        return self.world

    def _add_entity(self, entity: HammerEntity, index: int):
        if not entity.classname:
            return
        identity = f"{entity.source_id or entity.classname}:{index}"
        element = self._element(entity.classname, "CMapEntity", f"entity:{identity}")
        self._map_node(element, entity.transform)
        properties = dict(entity.properties)
        properties["classname"] = entity.classname
        self._base_entity(element, properties, entity.connections, f"entity:{identity}")
        element["hitNormal"] = datamodel.Vector3((0.0, 0.0, 1.0))
        element["isProceduralEntity"] = False
        self._parent(entity.layer)["children"].append(element)

    def _authored_model_path(self, model: str, path: tuple[str | int, ...]) -> str:
        if model.endswith("_c"):
            authored = model[:-2]
            self.report.inferred(
                "map.prop.authored_model_path",
                "A compiled model suffix was removed to form an authored Hammer reference.",
                path=path,
                details={"compiled": model, "authored": authored},
            )
            return authored
        return model

    def _add_prop(self, prop: HammerProp, index: int):
        identity = f"{prop.source_id or prop.model}:{index}"
        element = self._element(PurePosixPath(prop.model).stem, "CMapEntity", f"prop:{identity}")
        self._map_node(element, prop.transform)
        properties = dict(prop.properties)
        properties.update({
            "classname": "prop_static",
            "model": self._authored_model_path(prop.model, ("props", index, "model")),
            "skin": prop.skin,
        })
        if prop.tint is not None:
            properties["rendercolor"] = " ".join(str(component) for component in prop.tint[:3])
        self._base_entity(element, properties, (), f"prop:{identity}")
        element["hitNormal"] = datamodel.Vector3((0.0, 0.0, 1.0))
        element["isProceduralEntity"] = False
        self._parent(prop.layer)["children"].append(element)

    def _add_overlay(self, overlay: HammerOverlay, index: int):
        if overlay.source_model:
            self.report.deferred(
                "map.overlay.model_proxy",
                "Compiled overlay geometry is represented by a prop_static model reference; projection semantics remain in the sidecar.",
                path=("overlays", index),
            )
            self._add_prop(HammerProp(
                model=overlay.source_model,
                transform=overlay.transform,
                layer=overlay.layer,
                source_id=overlay.name,
                properties={"sourceio_overlay_material": overlay.material or ""},
            ), index)
            return
        self.report.deferred(
            "map.overlay.sidecar",
            "Overlay data remains in the sidecar because no safe editable mesh or source model is available.",
            path=("overlays", index),
        )

    def build(self):
        self.root["isprefab"] = False
        self.root["editorbuild"] = 0
        self.root["editorversion"] = 400
        self.root["showgrid"] = True
        self.root["snaprotationangle"] = 15
        self.root["gridspacing"] = 64.0
        self.root["show3dgrid"] = True
        self.root["itemFile"] = ""

        camera = self._element("defaultcamera", "CStoredCamera", "default-camera")
        camera["position"] = datamodel.Vector3((0.0, -1000.0, 1000.0))
        camera["lookat"] = datamodel.Vector3((0.0, 0.0, 0.0))
        self.root["defaultcamera"] = camera
        cameras = self._element("3dcameras", "CStoredCameras", "cameras")
        cameras["activecamera"] = -1
        cameras["cameras"] = datamodel.make_array([], datamodel.Element)
        self.root["3dcameras"] = cameras

        self.world = self._element("world", "CMapWorld", "world")
        self._map_node(self.world, Transform())
        world_properties = dict(self.document.world_properties)
        world_properties["classname"] = "worldspawn"
        self._base_entity(self.world, world_properties, (), "world")
        self.world["nextDecalID"] = 0
        self.world["fixupEntityNames"] = True
        self.world["mapUsageType"] = "standard"
        self.root["world"] = self.world

        for index, layer in enumerate(sorted(self.document.world_layers, key=lambda item: item.name)):
            if layer.name in self.layer_nodes:
                self.report.irrecoverable(
                    "map.world_layer.name.duplicate",
                    "Duplicate world-layer names cannot be represented safely; the later layer remains only in the sidecar.",
                    path=("world_layers", index),
                    details={"name": layer.name},
                )
                continue
            layer_node = self._element(layer.name, "CMapWorldLayer", f"layer:{layer.name}:{index}")
            self._map_node(layer_node, Transform())
            layer_node["worldLayerName"] = layer.name
            layer_node["force_hidden"] = not layer.visible
            self.world["children"].append(layer_node)
            self.layer_nodes[layer.name] = layer_node

        for index, entity in enumerate(self.document.entities):
            self._add_entity(entity, index)
        for index, prop in enumerate(self.document.props):
            self._add_prop(prop, index)
        for index, overlay in enumerate(self.document.overlays):
            self._add_overlay(overlay, index)

        visibility = self._element("visbility", "CVisibilityMgr", "visibility")
        self._map_node(visibility, Transform())
        visibility["nodes"] = datamodel.make_array([], datamodel.Element)
        visibility["hiddenFlags"] = datamodel.make_array([], int)
        self.root["visbility"] = visibility

        variables = self._element("mapVariables", "CMapVariableSet", "variables")
        variables["variableNames"] = datamodel.make_array([], str)
        variables["variableValues"] = datamodel.make_array([], str)
        variables["variableTypeNames"] = datamodel.make_array([], str)
        variables["variableTypeParameters"] = datamodel.make_array([], str)
        variables["m_ChoiceGroups"] = datamodel.make_array([], datamodel.Element)
        self.root["mapVariables"] = variables

        selection = self._element("rootSelectionSet", "CMapSelectionSet", "selection-root")
        selection["children"] = datamodel.make_array([], datamodel.Element)
        selection["selectionSetName"] = ""
        selection["selectionSetData"] = None
        for index, layer_name in enumerate(sorted(self.layer_nodes)):
            layer_selection = self._element(
                layer_name,
                "CMapSelectionSet",
                f"selection-layer:{layer_name}:{index}",
            )
            layer_selection["children"] = datamodel.make_array([], datamodel.Element)
            layer_selection["selectionSetName"] = layer_name
            layer_selection["selectionSetData"] = self.layer_nodes[layer_name]
            selection["children"].append(layer_selection)
        self.root["rootSelectionSet"] = selection
        self.root["m_ReferencedMeshSnapshots"] = datamodel.make_array([], datamodel.Element)
        self.root["m_bIsCordoning"] = False
        self.root["m_bCordonsVisible"] = False
        self.root["nodeInstanceData"] = datamodel.make_array([], datamodel.Element)
        return self.model


class HammerMapExporter:
    def build(
            self,
            document: HammerMapDocument,
            *,
            stem: str | None = None,
            report: LossReport | None = None,
    ) -> ExportBundle:
        loss_report = report if report is not None else LossReport()
        output_stem = stem or document.name
        if PurePosixPath(output_stem).name != output_stem or output_stem in ("", ".", ".."):
            raise ValueError("Hammer output stem must be a single non-empty file name")
        if any(character in output_stem for character in ('"', "\r", "\n", "\0")):
            raise ValueError("Hammer output stem contains characters that cannot be represented safely")
        if not document.entities and not document.props and not document.overlays:
            loss_report.missing(
                "map.contents.empty",
                "No recoverable entities, props, or overlays were supplied.",
                severity=DiagnosticSeverity.WARNING,
            )

        model = _HammerDmxBuilder(document, loss_report).build()
        sidecar = {
            "schema": "sourceio.hammer-map-export",
            "schema_version": 1,
            "document": document.to_dict(),
        }
        files = {
            f"{output_stem}.vmap": model.echo("keyvalues2", 4),
            f"{output_stem}.sourceio.json": json.dumps(sidecar, indent=2, sort_keys=True, allow_nan=False),
            f"{output_stem}.loss.json": loss_report.to_json(),
        }
        return ExportBundle(files, loss_report)

    def export(
            self,
            document: HammerMapDocument,
            output_directory,
            *,
            stem: str | None = None,
            overwrite: bool = False,
            report: LossReport | None = None,
    ):
        bundle = self.build(document, stem=stem, report=report)
        return bundle.write(output_directory, overwrite=overwrite)


def export_hammer_map(document: HammerMapDocument, output_directory, **kwargs):
    return HammerMapExporter().export(document, output_directory, **kwargs)
