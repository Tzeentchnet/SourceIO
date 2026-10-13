from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from ..workshop import ContentRelativePath, WorkshopTargetProfile
from ..export import LossReport


class AuthoringScope(str, Enum):
    SELECTED = "selected"
    COLLECTION = "collection"
    SCENE = "scene"


class AuthoringGrouping(str, Enum):
    COMBINED = "combined"
    BATCH = "batch"


class AuthoringArtifactKind(str, Enum):
    MODEL = "model"
    MESH = "mesh"
    MATERIAL = "material"
    TEXTURE_DESCRIPTOR = "texture_descriptor"
    TEXTURE_SOURCE = "texture_source"
    MANIFEST = "manifest"
    REPORT = "report"
    LOG = "log"


_ASSET_NAME = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_NON_ASSET_NAME = re.compile(r"[^a-z0-9]+")


def suggest_asset_name(value: str) -> str:
    suggestion = _NON_ASSET_NAME.sub("_", str(value).casefold()).strip("_")
    if not suggestion:
        return "sourceio_asset"
    if not suggestion[0].isalnum():
        suggestion = f"sourceio_{suggestion}"
    return suggestion


def is_valid_asset_name(value: str) -> bool:
    return isinstance(value, str) and _ASSET_NAME.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class Source2AuthoringRequest:
    target: WorkshopTargetProfile
    asset_name: str
    model_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("models/sourceio")
    )
    material_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("materials/sourceio")
    )
    texture_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("materials/sourceio/textures")
    )
    scope: AuthoringScope = AuthoringScope.SELECTED
    grouping: AuthoringGrouping = AuthoringGrouping.COMBINED
    overwrite: bool = False
    compile_after_export: bool = False

    def __post_init__(self):
        if not isinstance(self.target, WorkshopTargetProfile):
            raise TypeError("target must be a WorkshopTargetProfile")
        if not isinstance(self.asset_name, str):
            raise TypeError("asset_name must be a string")
        object.__setattr__(self, "model_directory", ContentRelativePath(self.model_directory))
        object.__setattr__(self, "material_directory", ContentRelativePath(self.material_directory))
        object.__setattr__(self, "texture_directory", ContentRelativePath(self.texture_directory))
        if isinstance(self.scope, str) and not isinstance(self.scope, AuthoringScope):
            object.__setattr__(self, "scope", AuthoringScope(self.scope))
        if isinstance(self.grouping, str) and not isinstance(self.grouping, AuthoringGrouping):
            object.__setattr__(self, "grouping", AuthoringGrouping(self.grouping))


@dataclass(frozen=True, slots=True)
class AuthoringArtifact:
    path: ContentRelativePath
    kind: AuthoringArtifactKind
    payload: bytes | str

    def __post_init__(self):
        object.__setattr__(self, "path", ContentRelativePath(self.path))
        if isinstance(self.kind, str) and not isinstance(self.kind, AuthoringArtifactKind):
            object.__setattr__(self, "kind", AuthoringArtifactKind(self.kind))
        if not isinstance(self.payload, (bytes, str)):
            raise TypeError("Authoring artifact payloads must be bytes or text")
        if self.path.name.endswith("_c"):
            raise ValueError(f"Authoring artifacts cannot be compiled resources: {self.path}")

    def encoded(self) -> bytes:
        return self.payload.encode("utf-8") if isinstance(self.payload, str) else self.payload

    @property
    def size(self) -> int:
        return len(self.encoded())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.encoded()).hexdigest()


@dataclass(frozen=True, slots=True)
class CompileStep:
    source: ContentRelativePath
    expected_outputs: tuple[ContentRelativePath, ...]
    arguments: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "source", ContentRelativePath(self.source))
        object.__setattr__(
            self,
            "expected_outputs",
            tuple(ContentRelativePath(path) for path in self.expected_outputs),
        )
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.source.name.endswith("_c"):
            raise ValueError(f"Compiler source cannot be compiled: {self.source}")
        if not self.expected_outputs:
            raise ValueError("Compile steps require at least one expected output")
        if any(not path.name.endswith("_c") for path in self.expected_outputs):
            raise ValueError("Compile step outputs must end with '_c'")
        if len(set(self.expected_outputs)) != len(self.expected_outputs):
            raise ValueError("Compile step outputs must be unique")
        if any(not isinstance(argument, str) for argument in self.arguments):
            raise TypeError("Compile step arguments must be strings")


@dataclass(slots=True)
class AuthoringBackendOutput:
    artifacts: tuple[AuthoringArtifact, ...]
    compile_steps: tuple[CompileStep, ...]
    model_paths: tuple[ContentRelativePath, ...]
    report: LossReport = field(default_factory=LossReport)

    def __post_init__(self):
        self.artifacts = tuple(self.artifacts)
        self.compile_steps = tuple(self.compile_steps)
        self.model_paths = tuple(ContentRelativePath(path) for path in self.model_paths)

    @classmethod
    def create(
            cls,
            artifacts: Iterable[AuthoringArtifact],
            compile_steps: Iterable[CompileStep],
            model_paths: Iterable[ContentRelativePath],
            *,
            report: LossReport | None = None,
    ) -> AuthoringBackendOutput:
        return cls(
            tuple(artifacts),
            tuple(compile_steps),
            tuple(model_paths),
            report if report is not None else LossReport(),
        )
