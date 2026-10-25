from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ...source2.workshop import ContentRelativePath
from .models import (
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
    Source1AuthoringRequest,
)
from .target import Source1AuthoringTarget


class Source1AuthoringManifestError(ValueError):
    pass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class Source1ManifestArtifact:
    root: Source1ArtifactRoot
    path: ContentRelativePath
    kind: str
    size: int
    sha256: str

    def __post_init__(self):
        if isinstance(self.root, str) and not isinstance(self.root, Source1ArtifactRoot):
            object.__setattr__(self, "root", Source1ArtifactRoot(self.root))
        object.__setattr__(self, "path", ContentRelativePath(self.path))
        if self.size < 0:
            raise ValueError("Manifest artifact size cannot be negative")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("Manifest artifact SHA-256 must be lowercase hexadecimal")

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root.value,
            "path": self.path.as_posix(),
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class Source1AuthoringManifest:
    game_directory: str
    content_directory: str
    asset_name: str
    model_paths: tuple[ContentRelativePath, ...]
    artifacts: tuple[Source1ManifestArtifact, ...]
    schema_version: int = 1

    @classmethod
    def build(
            cls,
            request: Source1AuthoringRequest,
            artifacts: tuple[Source1AuthoringArtifact, ...],
            model_paths: tuple[ContentRelativePath, ...],
    ) -> Source1AuthoringManifest:
        records = tuple(
            Source1ManifestArtifact(
                artifact.root,
                artifact.path,
                artifact.kind.value,
                artifact.size,
                artifact.sha256,
            )
            for artifact in sorted(
                artifacts,
                key=lambda item: (item.root.value, item.path.as_posix()),
            )
        )
        return cls(
            game_directory=request.target.game_root.name,
            content_directory=request.target.content_root.name,
            asset_name=request.asset_name,
            model_paths=tuple(sorted(model_paths, key=ContentRelativePath.as_posix)),
            artifacts=records,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "sourceio.source1-authoring-manifest",
            "schema_version": self.schema_version,
            "target": {
                "game_directory": self.game_directory,
                "content_directory": self.content_directory,
            },
            "asset": {
                "name": self.asset_name,
                "models": [path.as_posix() for path in self.model_paths],
            },
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"

    @classmethod
    def from_json(cls, value: str) -> Source1AuthoringManifest:
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise Source1AuthoringManifestError(
                f"Invalid Source 1 authoring manifest JSON: {exc}"
            ) from exc
        if (
                not isinstance(data, dict)
                or data.get("schema") != "sourceio.source1-authoring-manifest"
        ):
            raise Source1AuthoringManifestError(
                "Not a SourceIO Source 1 authoring manifest"
            )
        if data.get("schema_version") != 1:
            raise Source1AuthoringManifestError(
                f"Unsupported Source 1 authoring manifest version: {data.get('schema_version')!r}"
            )
        try:
            target = data["target"]
            asset = data["asset"]
            manifest = cls(
                game_directory=str(target["game_directory"]),
                content_directory=str(target["content_directory"]),
                asset_name=str(asset["name"]),
                model_paths=tuple(
                    ContentRelativePath(path) for path in asset["models"]
                ),
                artifacts=tuple(
                    Source1ManifestArtifact(
                        item["root"],
                        item["path"],
                        str(item["kind"]),
                        int(item["size"]),
                        str(item["sha256"]),
                    )
                    for item in data["artifacts"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise Source1AuthoringManifestError(
                f"Malformed Source 1 authoring manifest: {exc}"
            ) from exc
        keys = {(artifact.root, artifact.path) for artifact in manifest.artifacts}
        if len(keys) != len(manifest.artifacts):
            raise Source1AuthoringManifestError(
                "Manifest artifact paths must be unique within each root"
            )
        return manifest

    def validate_target(self, target: Source1AuthoringTarget):
        expected = (target.game_root.name, target.content_root.name)
        actual = (self.game_directory, self.content_directory)
        if actual != expected:
            raise Source1AuthoringManifestError(
                "Manifest target does not match the configured Source 1 target"
            )
