from __future__ import annotations

import json
import math
import posixpath
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from numbers import Integral, Real
from pathlib import Path, PurePath
from typing import Any, Mapping


class DependencyKind(str, Enum):
    EXTERNAL_REFERENCE = "external_reference"
    INPUT = "input"
    ADDITIONAL_INPUT = "additional_input"
    CHILD = "child"
    CUSTOM = "custom"
    RELATED = "related"
    ARGUMENT = "argument"
    SPECIAL = "special"


def normalize_resource_path(value: str | PurePath) -> str:
    path = str(value).replace("\\", "/")
    if not path:
        return ""
    normalized = posixpath.normpath(path)
    return "" if normalized == "." else normalized


def to_json_safe(value: Any, *, _path: str = "$") -> Any:
    if value is None:
        return value
    if isinstance(value, Enum):
        return to_json_safe(value.value, _path=_path)
    if isinstance(value, (str, bool)):
        return value
    if value.__class__.__name__ == "NullObject":
        return None
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{_path} contains a non-finite number")
        return number
    if isinstance(value, (Path, PurePath)):
        return value.as_posix()
    if isinstance(value, bytes):
        return {"encoding": "hex", "data": value.hex()}
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            string_key = str(key)
            if string_key in result:
                raise ValueError(f"{_path} contains duplicate key {string_key!r} after string conversion")
            result[string_key] = to_json_safe(item, _path=f"{_path}.{string_key}")
        return result
    if isinstance(value, (list, tuple)):
        return [to_json_safe(item, _path=f"{_path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        items = [to_json_safe(item, _path=f"{_path}[]") for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if is_dataclass(value):
        return {
            item.name: to_json_safe(getattr(value, item.name), _path=f"{_path}.{item.name}")
            for item in fields(value)
        }
    value_module = value.__class__.__module__
    if value_module == "numpy" or value_module.startswith("numpy."):
        if hasattr(value, "tolist"):
            return to_json_safe(value.tolist(), _path=_path)
        if hasattr(value, "item"):
            return to_json_safe(value.item(), _path=_path)
    if hasattr(value, "as_posix") and callable(value.as_posix):
        return normalize_resource_path(value.as_posix())
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_json_safe(value.to_dict(), _path=_path)
    raise TypeError(f"{_path} contains unsupported {value.__class__.__name__}; parser objects are not serializable")


@dataclass(slots=True)
class ResourceDependency:
    source: str
    target: str | None
    kind: DependencyKind | str
    origin: str
    search_path: str | None = None
    identifier: int | None = None
    crc: int | None = None
    flags: int | None = None
    available: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.source = normalize_resource_path(self.source)
        if self.target is not None:
            self.target = normalize_resource_path(self.target)
        if self.search_path is not None:
            self.search_path = normalize_resource_path(self.search_path)
        if isinstance(self.kind, str) and not isinstance(self.kind, DependencyKind):
            try:
                self.kind = DependencyKind(self.kind)
            except ValueError:
                pass
        self.metadata = to_json_safe(self.metadata, _path="$.metadata")

    @property
    def kind_name(self) -> str:
        return self.kind.value if isinstance(self.kind, DependencyKind) else str(self.kind)

    def sort_key(self) -> tuple:
        return (
            self.source,
            self.target or "",
            self.kind_name,
            self.origin,
            self.search_path or "",
            -1 if self.identifier is None else self.identifier,
            -1 if self.crc is None else self.crc,
            -1 if self.flags is None else self.flags,
            -1 if self.available is None else int(self.available),
            json.dumps(self.metadata, sort_keys=True, separators=(",", ":")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "source": self.source,
            "target": self.target,
            "kind": self.kind_name,
            "origin": self.origin,
            "search_path": self.search_path,
            "identifier": self.identifier,
            "crc": self.crc,
            "flags": self.flags,
            "available": self.available,
            "metadata": self.metadata,
        }
        return to_json_safe(data)


@dataclass(slots=True)
class ResourceProvenance:
    root_resource: str
    resources: tuple[str, ...] = ()
    dependencies: tuple[ResourceDependency, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()
    unresolved_resources: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self):
        self.root_resource = normalize_resource_path(self.root_resource)
        resource_names = {normalize_resource_path(resource) for resource in self.resources}
        resource_names.add(self.root_resource)
        resource_names.update(
            dependency.target for dependency in self.dependencies if dependency.target is not None
        )
        self.resources = tuple(sorted(resource_names))
        self.dependencies = tuple(sorted(self.dependencies, key=ResourceDependency.sort_key))
        self.cycles = tuple(sorted({tuple(map(normalize_resource_path, cycle)) for cycle in self.cycles}))
        self.unresolved_resources = tuple(
            sorted({normalize_resource_path(resource) for resource in self.unresolved_resources})
        )
        self.metadata = to_json_safe(self.metadata, _path="$.metadata")

    @property
    def is_cyclic(self) -> bool:
        return bool(self.cycles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "sourceio.resource-provenance",
            "schema_version": self.schema_version,
            "root_resource": self.root_resource,
            "resources": list(self.resources),
            "dependencies": [dependency.to_dict() for dependency in self.dependencies],
            "cycles": [list(cycle) for cycle in self.cycles],
            "unresolved_resources": list(self.unresolved_resources),
            "metadata": self.metadata,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            separators=None if indent is not None else (",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_resource(cls, resource, *, resolver=None, recursive: bool = False) -> "ResourceProvenance":
        from .graph import resource_provenance

        return resource_provenance(resource, resolver=resolver, recursive=recursive)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResourceProvenance":
        dependencies = tuple(
            ResourceDependency(
                source=item["source"],
                target=item.get("target"),
                kind=item["kind"],
                origin=item["origin"],
                search_path=item.get("search_path"),
                identifier=item.get("identifier"),
                crc=item.get("crc"),
                flags=item.get("flags"),
                available=item.get("available"),
                metadata=dict(item.get("metadata", {})),
            )
            for item in value.get("dependencies", ())
        )
        return cls(
            root_resource=value["root_resource"],
            resources=tuple(value.get("resources", ())),
            dependencies=dependencies,
            cycles=tuple(tuple(cycle) for cycle in value.get("cycles", ())),
            unresolved_resources=tuple(value.get("unresolved_resources", ())),
            metadata=dict(value.get("metadata", {})),
            schema_version=int(value.get("schema_version", 1)),
        )
