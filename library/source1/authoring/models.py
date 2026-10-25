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


class Source1CompilerKind(str, Enum):
    VTEX = "vtex"
    STUDIOMDL = "studiomdl"


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
    compile_after_export: bool = False

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


@dataclass(frozen=True, slots=True)
class Source1CompileOutput:
    root: Source1ArtifactRoot
    path: ContentRelativePath

    def __post_init__(self):
        if isinstance(self.root, str) and not isinstance(self.root, Source1ArtifactRoot):
            object.__setattr__(self, "root", Source1ArtifactRoot(self.root))
        object.__setattr__(self, "path", ContentRelativePath(self.path))


@dataclass(frozen=True, slots=True)
class Source1CompileStep:
    compiler: Source1CompilerKind
    source_root: Source1ArtifactRoot
    source: ContentRelativePath
    expected_outputs: tuple[Source1CompileOutput, ...]
    arguments: tuple[str, ...] = ()

    def __post_init__(self):
        if isinstance(self.compiler, str) and not isinstance(
                self.compiler, Source1CompilerKind
        ):
            object.__setattr__(self, "compiler", Source1CompilerKind(self.compiler))
        if isinstance(self.source_root, str) and not isinstance(
                self.source_root, Source1ArtifactRoot
        ):
            object.__setattr__(
                self,
                "source_root",
                Source1ArtifactRoot(self.source_root),
            )
        object.__setattr__(self, "source", ContentRelativePath(self.source))
        object.__setattr__(
            self,
            "expected_outputs",
            tuple(
                output
                if isinstance(output, Source1CompileOutput)
                else Source1CompileOutput(*output)
                for output in self.expected_outputs
            ),
        )
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if not self.expected_outputs:
            raise ValueError("Source 1 compile steps require expected outputs")
        keys = {
            (output.root, output.path)
            for output in self.expected_outputs
        }
        if len(keys) != len(self.expected_outputs):
            raise ValueError("Source 1 compile outputs must be unique")
        if any(not isinstance(argument, str) for argument in self.arguments):
            raise TypeError("Source 1 compiler arguments must be strings")


@dataclass(slots=True)
class Source1BackendOutput:
    artifacts: tuple[Source1AuthoringArtifact, ...]
    compile_steps: tuple[Source1CompileStep, ...]
    model_paths: tuple[ContentRelativePath, ...]
    report: LossReport = field(default_factory=LossReport)

    def __post_init__(self):
        self.artifacts = tuple(self.artifacts)
        self.compile_steps = tuple(self.compile_steps)
        self.model_paths = tuple(ContentRelativePath(path) for path in self.model_paths)

    @classmethod
    def create(
            cls,
            artifacts: Iterable[Source1AuthoringArtifact],
            compile_steps: Iterable[Source1CompileStep],
            model_paths: Iterable[ContentRelativePath],
            *,
            report: LossReport | None = None,
    ) -> Source1BackendOutput:
        return cls(
            tuple(artifacts),
            tuple(compile_steps),
            tuple(model_paths),
            report if report is not None else LossReport(),
        )
