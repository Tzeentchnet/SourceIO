from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from os import PathLike
from typing import Any, Mapping, Protocol, TypeAlias, runtime_checkable

from ..utils import Buffer


class ResourceKind(str, Enum):
    UNKNOWN = "unknown"
    GENERIC = "generic"
    KEYVALUES3 = "keyvalues3"
    MODEL = "model"
    MESH = "mesh"
    MATERIAL = "material"
    TEXTURE = "texture"
    PHYSICS = "physics"
    MORPH = "morph"
    ANIMATION = "animation"
    ANIMATION_GROUP = "animation_group"
    ANIMATION_SEQUENCE = "animation_sequence"
    ANIMATION_GRAPH = "animation_graph"
    ANIMATION_CLIP = "animation_clip"
    WORLD = "world"
    WORLD_NODE = "world_node"
    MAP = "map"
    ENTITY_LUMP = "entity_lump"
    RESOURCE_MANIFEST = "resource_manifest"
    PARTICLE_SYSTEM = "particle_system"
    PARTICLE_SNAPSHOT = "particle_snapshot"
    SOUND = "sound"
    SOUND_EVENT = "sound_event"
    SHADER = "shader"
    PANORAMA = "panorama"
    FONT = "font"
    NAVIGATION = "navigation"
    POST_PROCESS = "post_process"

    MANIFEST = RESOURCE_MANIFEST
    PARTICLE = PARTICLE_SYSTEM


class Maturity(IntEnum):
    UNSUPPORTED = 0
    EXPERIMENTAL = 1
    PARTIAL = 2
    STABLE = 3

    NONE = UNSUPPORTED
    MATURE = STABLE


class ResourceOperation(str, Enum):
    READ = "read"
    EXTRACT = "extract"
    RENDER = "render"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class ResourceCapabilities:
    read: Maturity = Maturity.UNSUPPORTED
    extract: Maturity = Maturity.UNSUPPORTED
    render: Maturity = Maturity.UNSUPPORTED
    write: Maturity = Maturity.UNSUPPORTED

    def maturity_for(self, operation: ResourceOperation | str) -> Maturity:
        try:
            operation = ResourceOperation(operation)
        except ValueError as exc:
            raise ValueError(f"Unknown resource operation: {operation!r}") from exc
        return getattr(self, operation.value)

    def supports(
            self,
            operation: ResourceOperation | str,
            minimum: Maturity = Maturity.EXPERIMENTAL,
    ) -> bool:
        return self.maturity_for(operation) >= minimum


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.WARNING
    path: str | None = None
    offset: int | None = None
    block_name: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self):
        if not self.code:
            raise ValueError("Diagnostic.code must not be empty")
        if not self.message:
            raise ValueError("Diagnostic.message must not be empty")
        if self.offset is not None and self.offset < 0:
            raise ValueError("Diagnostic.offset must not be negative")


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    kind: ResourceKind
    resource_version: int
    header_version: int
    path: str | None = None
    extension: str | None = None
    compiler: str | None = None
    input_path: str | None = None
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self):
        if not 0 <= self.resource_version <= 0xFFFF:
            raise ValueError("ResourceIdentity.resource_version must fit in uint16")
        if not 0 <= self.header_version <= 0xFFFF:
            raise ValueError("ResourceIdentity.header_version must fit in uint16")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("ResourceIdentity.confidence must be between 0 and 1")
        if self.extension is not None and self.extension and not self.extension.startswith("."):
            raise ValueError("ResourceIdentity.extension must start with '.'")

    @property
    def version(self) -> int:
        return self.resource_version

    @property
    def is_known(self) -> bool:
        return self.kind is not ResourceKind.UNKNOWN


@dataclass(frozen=True, slots=True)
class ResourceRef:
    path: str | None = None
    resource_id: int | None = None
    kind: ResourceKind = ResourceKind.UNKNOWN
    source_path: str | None = None
    optional: bool = False

    def __post_init__(self):
        if self.path is None and self.resource_id is None:
            raise ValueError("ResourceRef requires a path or resource_id")
        if self.resource_id is not None and not 0 <= self.resource_id <= 0xFFFF_FFFF_FFFF_FFFF:
            raise ValueError("ResourceRef.resource_id must fit in uint64")


ResourceBuffer: TypeAlias = Buffer | bytes | bytearray | memoryview
ResourcePath: TypeAlias = str | PathLike[str] | None


@runtime_checkable
class ResourceResolver(Protocol):
    def resolve(self, reference: ResourceRef) -> Buffer | bytes | bytearray | memoryview | None:
        """Return resource bytes for ``reference``, or ``None`` when unresolved."""
        ...


__all__ = [
    "Diagnostic",
    "DiagnosticSeverity",
    "Maturity",
    "ResourceBuffer",
    "ResourceCapabilities",
    "ResourceIdentity",
    "ResourceKind",
    "ResourceOperation",
    "ResourcePath",
    "ResourceRef",
    "ResourceResolver",
]
