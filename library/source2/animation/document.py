"""Normalized Source 2 animation resources.

Animation graphs are documents, not executable programs here.  Their references,
variations, states, nodes and external slots are normalized for inspection while
the original values remain available as opaque metadata.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import numpy as np

from ..interfaces import (Diagnostic, DiagnosticSeverity, Maturity, ResourceCapabilities,
                          ResourceKind)

if TYPE_CHECKING:
    from .animation import SequenceAnimation
    from .clip import ClipAnimation


ANIMATION_CAPABILITIES = ResourceCapabilities(
    read=Maturity.STABLE,
    extract=Maturity.STABLE,
    render=Maturity.PARTIAL,
    write=Maturity.UNSUPPORTED,
)

ANIMATION_DOCUMENT_CAPABILITIES = ResourceCapabilities(
    read=Maturity.STABLE,
    extract=Maturity.STABLE,
    render=Maturity.UNSUPPORTED,
    write=Maturity.UNSUPPORTED,
)


class UnsupportedAnimationNodeError(ValueError):
    """A required graph node cannot be represented by the requested consumer."""


class AnimationGraphExecutionError(NotImplementedError):
    """Animation graphs are retained as documents and are never evaluated."""


@dataclass(frozen=True, slots=True)
class AnimationImportOptions:
    """Options shared by library and Blender animation import entry points."""

    scale: float = 1.0
    apply_root_motion: bool = True
    include_hidden: bool = False
    bind_morphs: bool = True
    bind_user_channels: bool = True
    preserve_unknown_channels: bool = True
    clip_patterns: tuple[str, ...] = ()
    strict_required_nodes: bool = True
    supported_node_classes: frozenset[str] | None = None

    def __post_init__(self):
        if not np.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("AnimationImportOptions.scale must be a finite positive value")
        object.__setattr__(self, "clip_patterns", tuple(self.clip_patterns))
        if self.supported_node_classes is not None:
            object.__setattr__(self, "supported_node_classes", frozenset(self.supported_node_classes))


@dataclass(frozen=True, slots=True)
class AnimationReference:
    path: str
    kind: ResourceKind = ResourceKind.UNKNOWN
    source: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AnimationGraphNode:
    identifier: str
    class_name: str
    required: bool
    path: str
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AnimationGraphState:
    identifier: str
    name: str
    path: str
    node_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AnimationGraphVariation:
    identifier: str
    parent_identifier: str = ""
    skeleton: str = ""
    resource: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AnimationExternalSlot:
    identifier: str
    slot_type: str
    node_index: int = -1
    resource: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class AnimationDocument:
    """A normalized animation, group, clip or AnimGraph resource."""

    path: str
    kind: ResourceKind
    graph_version: int | None = None
    references: tuple[AnimationReference, ...] = ()
    variations: tuple[AnimationGraphVariation, ...] = ()
    states: tuple[AnimationGraphState, ...] = ()
    nodes: tuple[AnimationGraphNode, ...] = ()
    external_slots: tuple[AnimationExternalSlot, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False)
    diagnostics: tuple[Diagnostic, ...] = ()
    execution_supported: bool = False
    ik_solving_supported: bool = False

    def require_node_support(self, supported_node_classes: Collection[str]) -> None:
        supported = set(supported_node_classes)
        for node in self.nodes:
            if node.required and node.class_name not in supported:
                raise UnsupportedAnimationNodeError(
                    f"Required animation node {node.class_name or '<untyped>'!r} "
                    f"at {node.path} is unsupported"
                )

    def execute(self, *_args, **_kwargs):
        raise AnimationGraphExecutionError(
            "SourceIO preserves animation graph documents but does not execute AnimGraph nodes or solve IK"
        )


@dataclass(frozen=True, slots=True)
class AnimationArtifact:
    """Decoded animations together with their normalized source document."""

    document: AnimationDocument
    animations: tuple[SequenceAnimation | ClipAnimation, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in self.diagnostics)


_REFERENCE_KINDS = {
    ".vanim": ResourceKind.ANIMATION,
    ".vagrp": ResourceKind.ANIMATION_GROUP,
    ".vseq": ResourceKind.ANIMATION_SEQUENCE,
    ".vanmgrph": ResourceKind.ANIMATION_GRAPH,
    ".vanimgraph": ResourceKind.ANIMATION_GRAPH,
    ".vnmgraph": ResourceKind.ANIMATION_GRAPH,
    ".vnmvar": ResourceKind.ANIMATION_GRAPH,
    ".vnmgraphvariation": ResourceKind.ANIMATION_GRAPH,
    ".vnmclip": ResourceKind.ANIMATION_CLIP,
    ".vnmskel": ResourceKind.ANIMATION,
}


def resource_kind_from_path(path: str) -> ResourceKind:
    normalized = str(path).replace("\\", "/").casefold().removesuffix("_c")
    for extension, kind in _REFERENCE_KINDS.items():
        if normalized.endswith(extension):
            return kind
    return ResourceKind.UNKNOWN


def normalize_animation_document(
        data: Mapping[str, Any],
        path: str = "",
        *,
        kind: ResourceKind | None = None,
        options: AnimationImportOptions | None = None,
) -> AnimationDocument:
    """Normalize a Source 2 animation document without evaluating it."""

    options = options or AnimationImportOptions()
    raw = _plain_value(data)
    if not isinstance(raw, dict):
        raise TypeError("Animation document data must be a mapping")

    if kind is None or kind is ResourceKind.UNKNOWN:
        kind = resource_kind_from_path(path)
    graph_version = _graph_version(raw, path) if kind is ResourceKind.ANIMATION_GRAPH else None
    diagnostics: list[Diagnostic] = []
    references = _collect_references(raw)
    nodes = _collect_nodes(raw, diagnostics)
    states = _collect_states(raw)
    variations = _collect_variations(raw)
    external_slots = _collect_external_slots(raw, references)

    document = AnimationDocument(
        path=str(path),
        kind=kind,
        graph_version=graph_version,
        references=tuple(references),
        variations=tuple(variations),
        states=tuple(states),
        nodes=tuple(nodes),
        external_slots=tuple(external_slots),
        metadata=raw,
        diagnostics=tuple(diagnostics),
    )
    if options.strict_required_nodes and options.supported_node_classes is not None:
        document.require_node_support(options.supported_node_classes)
    return document


def _graph_version(data: Mapping[str, Any], path: str) -> int:
    suffix = str(path).replace("\\", "/").casefold()
    if ".vnmgraph" in suffix or ".vnmvar" in suffix:
        return 2
    if ".vanmgrph" in suffix or ".vanimgraph" in suffix:
        return 1
    graph2_fields = {
        "m_variationID", "m_externalGraphSlots", "m_externalPoseSlots",
        "m_referencedGraphSlots", "m_nodePaths",
    }
    return 2 if graph2_fields.intersection(data) else 1


def _collect_references(data: Mapping[str, Any]) -> list[AnimationReference]:
    references: list[AnimationReference] = []
    seen: set[str] = set()

    def add(value: str, source: str, metadata: Mapping[str, Any] | None = None):
        kind = resource_kind_from_path(value)
        if kind is ResourceKind.UNKNOWN:
            return
        key = value.replace("\\", "/").casefold()
        if key in seen:
            return
        seen.add(key)
        references.append(AnimationReference(value, kind, source, metadata or {}))

    def walk(value: Any, value_path: str):
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{value_path}.{key}" if value_path else str(key)
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{value_path}[{index}]")
        elif isinstance(value, str):
            add(value, value_path)

    for index, resource in enumerate(data.get("m_resources", []) or []):
        if isinstance(resource, str):
            add(resource, f"m_resources[{index}]")
    walk(data, "")
    return references


def _collect_nodes(data: Mapping[str, Any], diagnostics: list[Diagnostic]) -> list[AnimationGraphNode]:
    nodes: list[AnimationGraphNode] = []

    for container_path, values in _named_arrays(data, "m_nodes"):
        for index, value in enumerate(values):
            node_path = f"{container_path}[{index}]"
            if not isinstance(value, Mapping):
                diagnostics.append(Diagnostic(
                    "animation.node.invalid",
                    f"Animation node at {node_path} is not a mapping and was retained as opaque metadata",
                    DiagnosticSeverity.WARNING,
                    details={"node_path": node_path, "value": value},
                ))
                continue
            class_name = str(value.get("_class") or value.get("m_type") or value.get("type") or "")
            identifier = str(value.get("m_ID") or value.get("m_id") or value.get("id") or node_path)
            required = bool(
                value.get("m_bRequired", value.get("m_bIsRequired", value.get("required", False)))
            )
            if required and not class_name:
                raise UnsupportedAnimationNodeError(
                    f"Required animation node at {node_path} has no supported class identifier"
                )
            nodes.append(AnimationGraphNode(identifier, class_name, required, node_path, value))
    return nodes


def _collect_states(data: Mapping[str, Any]) -> list[AnimationGraphState]:
    states: list[AnimationGraphState] = []
    for container_path, values in _named_arrays(data, "m_states"):
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                continue
            state_path = f"{container_path}[{index}]"
            identifier = str(value.get("m_ID") or value.get("m_id") or value.get("id") or state_path)
            name = str(value.get("m_name") or value.get("m_sName") or value.get("name") or identifier)
            node_ids = tuple(_state_node_ids(value))
            states.append(AnimationGraphState(identifier, name, state_path, node_ids, value))
    return states


def _state_node_ids(state: Mapping[str, Any]) -> list[str]:
    node_ids = []
    for key in ("m_nodeIDs", "m_nodes", "m_entryNode", "m_rootNode"):
        value = state.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, Mapping):
                item = item.get("m_ID") or item.get("m_id") or item.get("id")
            if item is not None:
                node_ids.append(str(item))
    return node_ids


def _collect_variations(data: Mapping[str, Any]) -> list[AnimationGraphVariation]:
    variations: list[AnimationGraphVariation] = []
    seen: set[str] = set()

    def add(value: Mapping[str, Any], fallback: str = ""):
        identifier = str(
            value.get("m_variationID") or value.get("m_ID") or value.get("id") or fallback
        )
        if not identifier or identifier.casefold() in seen:
            return
        seen.add(identifier.casefold())
        parent = str(value.get("m_parentID") or value.get("m_parentVariationID") or "")
        skeleton = str(value.get("m_skeleton") or "")
        resource = str(value.get("m_variation") or value.get("m_resource") or "")
        variations.append(AnimationGraphVariation(identifier, parent, skeleton, resource, value))

    variation_id = data.get("m_variationID")
    if variation_id:
        add(data, str(variation_id))
    for container_path, values in _named_arrays(data, "m_variations"):
        for index, value in enumerate(values):
            if isinstance(value, Mapping):
                add(value, f"{container_path}[{index}]")
    for container_path, values in _named_arrays(data, "m_variationOverrides"):
        for index, value in enumerate(values):
            if isinstance(value, Mapping):
                add(value, f"{container_path}[{index}]")
    return variations


def _collect_external_slots(
        data: Mapping[str, Any],
        references: Sequence[AnimationReference],
) -> list[AnimationExternalSlot]:
    slots: list[AnimationExternalSlot] = []
    resources = [reference.path for reference in references if reference.source.startswith("m_resources[")]
    slot_fields = {
        "m_externalGraphSlots": "graph",
        "m_externalPoseSlots": "pose",
        "m_referencedGraphSlots": "referenced_graph",
    }
    for field_name, slot_type in slot_fields.items():
        for container_path, values in _named_arrays(data, field_name):
            for index, value in enumerate(values):
                if not isinstance(value, Mapping):
                    continue
                node_index = int(value.get("m_nNodeIdx", value.get("m_nodeIndex", -1)) or -1)
                identifier = str(value.get("m_slotID") or value.get("m_ID") or f"{slot_type}:{index}")
                resource = str(value.get("m_resource") or value.get("m_graph") or "")
                data_slot = int(value.get("m_dataSlotIdx", -1) or -1)
                if not resource and 0 <= data_slot < len(resources):
                    resource = resources[data_slot]
                metadata = dict(value)
                metadata["source_path"] = f"{container_path}[{index}]"
                slots.append(AnimationExternalSlot(identifier, slot_type, node_index, resource, metadata))
    return slots


def _named_arrays(data: Mapping[str, Any], field_name: str):
    def walk(value: Any, value_path: str):
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{value_path}.{key}" if value_path else str(key)
                if key == field_name and isinstance(child, list):
                    yield child_path, child
                yield from walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from walk(child, f"{value_path}[{index}]")

    yield from walk(data, "")


def _plain_value(value: Any) -> Any:
    """Copy KV/NumPy values into inspectable Python containers."""

    if isinstance(value, Mapping) or hasattr(value, "items"):
        return {str(key): _plain_value(child) for key, child in value.items()}
    if isinstance(value, np.ndarray):
        if value.dtype == np.uint8:
            return bytes(value)
        return [_plain_value(child) for child in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return [_plain_value(child) for child in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return value
