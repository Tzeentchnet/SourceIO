from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields, is_dataclass, replace
from typing import Any, Protocol

from .model import (
    DependencyKind,
    ResourceDependency,
    ResourceProvenance,
    normalize_resource_path,
    to_json_safe,
)


class ResourceResolver(Protocol):
    def resolve(self, resource_path: str) -> Any | None:
        ...


DependencyProvider = Callable[[str], Iterable[ResourceDependency] | None]


def _resource_path(resource: Any) -> str:
    filepath = getattr(resource, "_filepath", None)
    if filepath is not None:
        if hasattr(filepath, "as_posix"):
            return normalize_resource_path(filepath.as_posix())
        return normalize_resource_path(str(filepath))
    name = getattr(resource, "name", None)
    if name is not None:
        return normalize_resource_path(str(name))
    raise TypeError("Resource provenance requires a resource path or name")


def _record_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return to_json_safe(record)
    if is_dataclass(record):
        return {
            item.name: to_json_safe(getattr(record, item.name), _path=f"$.{item.name}")
            for item in fields(record)
        }
    values = {}
    for name in (
        "relative_name",
        "relative_filename",
        "search_path",
        "file_crc",
        "flags",
        "id",
        "name",
        "unk",
        "string",
        "compiler_id",
        "fingerprint",
        "user_data",
        "type",
        "fingerprint_default",
        "data",
    ):
        if hasattr(record, name):
            values[name] = to_json_safe(getattr(record, name), _path=f"$.{name}")
    return values


def _custom_target(data: Mapping[str, Any]) -> str | None:
    for key in (
        "m_RelativeFilename",
        "m_Filename",
        "m_ResourceName",
        "relative_filename",
        "filename",
        "resource_name",
        "name",
    ):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def dependencies_from_rerl(rerl: Iterable[Any], source: str) -> tuple[ResourceDependency, ...]:
    dependencies = []
    for reference in rerl:
        dependencies.append(
            ResourceDependency(
                source=source,
                target=str(reference.name),
                kind=DependencyKind.EXTERNAL_REFERENCE,
                origin="RERL",
                identifier=int(reference.hash),
                metadata={
                    "resource_id": int(reference.r_id),
                    "unknown": int(reference.unk),
                },
            )
        )
    return tuple(sorted(dependencies, key=ResourceDependency.sort_key))


def dependencies_from_edit_info(edit_info: Any, source: str, *, origin: str) -> tuple[ResourceDependency, ...]:
    dependencies: list[ResourceDependency] = []

    def add_inputs(records: Iterable[Any], kind: DependencyKind):
        for record in records:
            dependencies.append(
                ResourceDependency(
                    source=source,
                    target=str(record.relative_name),
                    kind=kind,
                    origin=origin,
                    search_path=str(record.search_path),
                    crc=int(record.file_crc),
                    flags=int(record.flags),
                )
            )

    add_inputs(getattr(edit_info, "inputs", ()), DependencyKind.INPUT)
    add_inputs(getattr(edit_info, "additional_inputs", ()), DependencyKind.ADDITIONAL_INPUT)

    for record in getattr(edit_info, "child_resources", ()):
        dependencies.append(
            ResourceDependency(
                source=source,
                target=str(record.name),
                kind=DependencyKind.CHILD,
                origin=origin,
                identifier=int(record.id),
                metadata={"unknown": int(record.unk)},
            )
        )

    for record in getattr(edit_info, "additional_files", ()):
        dependencies.append(
            ResourceDependency(
                source=source,
                target=str(record.relative_filename),
                kind=DependencyKind.RELATED,
                origin=origin,
                search_path=str(record.search_path),
            )
        )

    for record in getattr(edit_info, "custom_deps", ()):
        raw = _record_dict(record)
        nested = raw.get("data")
        target_data = nested if isinstance(nested, Mapping) else raw
        dependencies.append(
            ResourceDependency(
                source=source,
                target=_custom_target(target_data),
                kind=DependencyKind.CUSTOM,
                origin=origin,
                metadata={"raw": raw},
            )
        )

    for record in getattr(edit_info, "arguments", ()):
        raw = _record_dict(record)
        dependencies.append(
            ResourceDependency(
                source=source,
                target=None,
                kind=DependencyKind.ARGUMENT,
                origin=origin,
                metadata=raw,
            )
        )

    for record in getattr(edit_info, "special_deps", ()):
        raw = _record_dict(record)
        dependencies.append(
            ResourceDependency(
                source=source,
                target=None,
                kind=DependencyKind.SPECIAL,
                origin=origin,
                metadata=raw,
            )
        )

    return tuple(sorted(dependencies, key=ResourceDependency.sort_key))


def _named_block(resource: Any, block_class: type, name: str):
    return resource.get_block(block_class, block_name=name)


def collect_resource_dependencies(resource: Any, *, source: str | None = None) -> tuple[ResourceDependency, ...]:
    from ..blocks.resource_edit_info import ResourceEditInfo, ResourceEditInfo2
    from ..blocks.resource_external_reference_list import ResourceExternalReferenceList

    source_path = normalize_resource_path(source or _resource_path(resource))
    dependencies: list[ResourceDependency] = []

    rerl = _named_block(resource, ResourceExternalReferenceList, "RERL")
    if rerl:
        dependencies.extend(dependencies_from_rerl(rerl, source_path))

    redi = _named_block(resource, ResourceEditInfo, "REDI")
    if redi:
        dependencies.extend(dependencies_from_edit_info(redi, source_path, origin="REDI"))

    red2 = _named_block(resource, ResourceEditInfo2, "RED2")
    if red2:
        dependencies.extend(dependencies_from_edit_info(red2, source_path, origin="RED2"))

    unique: dict[tuple, ResourceDependency] = {}
    for dependency in dependencies:
        unique[dependency.sort_key()] = dependency
    return tuple(unique[key] for key in sorted(unique))


def _canonical_cycle(cycle: tuple[str, ...]) -> tuple[str, ...]:
    body = cycle[:-1]
    if not body:
        return cycle
    rotations = [body[index:] + body[:index] for index in range(len(body))]
    canonical = min(rotations)
    return canonical + (canonical[0],)


def build_dependency_graph(
        root_resource: str,
        provider: DependencyProvider | Mapping[str, Iterable[ResourceDependency]],
        *,
        expand: bool = True,
        metadata: Mapping[str, Any] | None = None,
) -> ResourceProvenance:
    root = normalize_resource_path(root_resource)
    provider_mapping = provider if isinstance(provider, Mapping) else None
    resources = {root}
    edges: dict[tuple, ResourceDependency] = {}
    cycles: set[tuple[str, ...]] = set()
    unresolved: set[str] = set()
    expanded: set[str] = set()
    active: list[str] = []
    active_index: dict[str, int] = {}

    def get_dependencies(resource_path: str) -> Iterable[ResourceDependency] | None:
        if provider_mapping is not None:
            return provider_mapping.get(resource_path)
        return provider(resource_path)

    def visit(resource_path: str):
        if resource_path in active_index:
            start = active_index[resource_path]
            cycles.add(_canonical_cycle(tuple(active[start:] + [resource_path])))
            return
        if resource_path in expanded:
            return

        dependencies = get_dependencies(resource_path)
        if dependencies is None:
            unresolved.add(resource_path)
            return

        active_index[resource_path] = len(active)
        active.append(resource_path)
        normalized_dependencies = []
        for dependency in dependencies:
            normalized = dependency
            if dependency.source != resource_path:
                normalized = replace(dependency, source=resource_path)
            normalized_dependencies.append(normalized)

        for dependency in sorted(normalized_dependencies, key=ResourceDependency.sort_key):
            edges[dependency.sort_key()] = dependency
            target = dependency.target
            if target is None:
                continue
            resources.add(target)
            if target in active_index:
                start = active_index[target]
                cycles.add(_canonical_cycle(tuple(active[start:] + [target])))
            elif expand:
                visit(target)

        active.pop()
        del active_index[resource_path]
        expanded.add(resource_path)

    visit(root)
    return ResourceProvenance(
        root_resource=root,
        resources=tuple(resources),
        dependencies=tuple(edges.values()),
        cycles=tuple(cycles),
        unresolved_resources=tuple(unresolved),
        metadata=dict(metadata or {}),
    )


def _resolve(resolver: ResourceResolver | Callable[[str], Any | None], resource_path: str):
    if callable(resolver):
        return resolver(resource_path)
    resolve = getattr(resolver, "resolve", None)
    if resolve is None or not callable(resolve):
        raise TypeError("resolver must be callable or implement resolve(resource_path)")
    return resolve(resource_path)


def _resource_metadata(resource: Any, *, asset_kind: str | None = None) -> dict[str, Any]:
    header = getattr(resource, "_header", None)
    metadata: dict[str, Any] = {
        "resource_class": resource.__class__.__name__,
    }
    if asset_kind:
        metadata["asset_kind"] = asset_kind
    if header is not None:
        metadata.update({
            "header_version": int(header.header_version),
            "resource_version": int(header.resource_version),
            "blocks": [
                {
                    "name": block.name,
                    "size": int(block.size),
                    "offset": int(block.absolute_offset),
                }
                for block in header.blocks
            ],
        })
    return metadata


def resource_provenance(
        resource: Any,
        *,
        resolver: ResourceResolver | Callable[[str], Any | None] | None = None,
        recursive: bool = False,
        asset_kind: str | None = None,
        metadata: Mapping[str, Any] | None = None,
) -> ResourceProvenance:
    root = _resource_path(resource)
    cache = {root: resource}

    def provider(resource_path: str):
        current = cache.get(resource_path)
        if current is None and resolver is not None:
            current = _resolve(resolver, resource_path)
            if current is not None:
                cache[resource_path] = current
        if current is None:
            return None
        return collect_resource_dependencies(current, source=resource_path)

    resource_metadata = _resource_metadata(resource, asset_kind=asset_kind)
    if metadata:
        resource_metadata.update(to_json_safe(metadata))
    return build_dependency_graph(
        root,
        provider,
        expand=recursive and resolver is not None,
        metadata=resource_metadata,
    )


def import_provenance(
        resource: Any,
        *,
        asset_kind: str,
        resolver: ResourceResolver | Callable[[str], Any | None] | None = None,
        recursive: bool = False,
        metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provenance = resource_provenance(
        resource,
        resolver=resolver,
        recursive=recursive,
        asset_kind=asset_kind,
        metadata=metadata,
    )
    return {
        "schema": "sourceio.import-provenance",
        "schema_version": 1,
        "asset_kind": asset_kind,
        "provenance": provenance.to_dict(),
    }
