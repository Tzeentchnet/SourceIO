from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from ...source2.authoring.models import (
    AuthoringGrouping,
    AuthoringScope,
)
from ...source2.export import LossReport
from ...source2.workshop import ContentRelativePath
from .target import Source1AuthoringTarget


class Source1ArtifactRoot(str, Enum):
    CONTENT = "content"
    GAME = "game"


class Source1ArtifactKind(str, Enum):
    MODEL_SOURCE = "model_source"
    MODEL_SCRIPT = "model_script"
    MATERIAL = "material"
    TEXTURE_SOURCE = "texture_source"
    TEXTURE_SETTINGS = "texture_settings"
    MANIFEST = "manifest"
    REPORT = "report"
    LOG = "log"


@dataclass(frozen=True, slots=True)
class Source1AuthoringRequest:
    target: Source1AuthoringTarget
    asset_name: str
    model_source_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("modelsrc/sourceio")
    )
    material_source_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("materialsrc/models/sourceio")
    )
    model_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("models/sourceio")
    )
    material_directory: ContentRelativePath = field(
        default_factory=lambda: ContentRelativePath("materials/models/sourceio")
    )
    surface_property: str = "default"
    scope: AuthoringScope = AuthoringScope.SELECTED
    grouping: AuthoringGrouping = AuthoringGrouping.COMBINED
    overwrite: bool = False

    def __post_init__(self):
        if not isinstance(self.target, Source1AuthoringTarget):
            raise TypeError("target must be a Source1AuthoringTarget")
        if not isinstance(self.asset_name, str):
            raise TypeError("asset_name must be a string")
        for field_name in (
            "model_source_directory",
            "material_source_directory",
            "model_directory",
            "material_directory",
        ):
            object.__setattr__(
                self,
                field_name,
                ContentRelativePath(getattr(self, field_name)),
            )
        if isinstance(self.scope, str) and not isinstance(self.scope, AuthoringScope):
            object.__setattr__(self, "scope", AuthoringScope(self.scope))
        if isinstance(self.grouping, str) and not isinstance(
                self.grouping, AuthoringGrouping
        ):
            object.__setattr__(self, "grouping", AuthoringGrouping(self.grouping))
        if not isinstance(self.surface_property, str):
            raise TypeError("surface_property must be a string")


@dataclass(frozen=True, slots=True)
class Source1AuthoringArtifact:
    root: Source1ArtifactRoot
    path: ContentRelativePath
    kind: Source1ArtifactKind
    payload: bytes | str

    def __post_init__(self):
        if isinstance(self.root, str) and not isinstance(self.root, Source1ArtifactRoot):
            object.__setattr__(self, "root", Source1ArtifactRoot(self.root))
        object.__setattr__(self, "path", ContentRelativePath(self.path))
        if isinstance(self.kind, str) and not isinstance(self.kind, Source1ArtifactKind):
            object.__setattr__(self, "kind", Source1ArtifactKind(self.kind))
        if not isinstance(self.payload, (bytes, str)):
            raise TypeError("Source 1 authoring artifact payloads must be bytes or text")

    def encoded(self) -> bytes:
        return self.payload.encode("utf-8") if isinstance(self.payload, str) else self.payload

    @property
    def size(self) -> int:
        return len(self.encoded())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.encoded()).hexdigest()


@dataclass(slots=True)
class Source1BackendOutput:
    artifacts: tuple[Source1AuthoringArtifact, ...]
    model_paths: tuple[ContentRelativePath, ...]
    report: LossReport = field(default_factory=LossReport)

    def __post_init__(self):
        self.artifacts = tuple(self.artifacts)
        self.model_paths = tuple(ContentRelativePath(path) for path in self.model_paths)

    @classmethod
    def create(
            cls,
            artifacts: Iterable[Source1AuthoringArtifact],
            model_paths: Iterable[ContentRelativePath],
            *,
            report: LossReport | None = None,
    ) -> Source1BackendOutput:
        return cls(
            tuple(artifacts),
            tuple(model_paths),
            report if report is not None else LossReport(),
        )
