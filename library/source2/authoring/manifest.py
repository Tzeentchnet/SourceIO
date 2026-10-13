from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..workshop import ContentRelativePath, WorkshopTargetProfile
from .models import AuthoringArtifact, CompileStep, Source2AuthoringRequest


class AuthoringManifestError(ValueError):
    pass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ManifestArtifact:
    path: ContentRelativePath
    kind: str
    size: int
    sha256: str

    def __post_init__(self):
        object.__setattr__(self, "path", ContentRelativePath(self.path))
        if self.size < 0:
            raise ValueError("Manifest artifact size cannot be negative")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("Manifest artifact SHA-256 must be lowercase hexadecimal")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path.as_posix(),
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class Source2AuthoringManifest:
    target_game: str
    target_app_id: int
    addon_name: str
    asset_name: str
    model_paths: tuple[ContentRelativePath, ...]
    artifacts: tuple[ManifestArtifact, ...]
    compile_steps: tuple[CompileStep, ...]
    schema_version: int = 1

    @classmethod
    def build(
            cls,
            request: Source2AuthoringRequest,
            artifacts: tuple[AuthoringArtifact, ...],
            compile_steps: tuple[CompileStep, ...],
            model_paths: tuple[ContentRelativePath, ...],
    ) -> Source2AuthoringManifest:
        records = tuple(
            ManifestArtifact(
                artifact.path,
                artifact.kind.value,
                artifact.size,
                artifact.sha256,
            )
            for artifact in sorted(artifacts, key=lambda item: item.path.as_posix())
        )
        return cls(
            target_game=request.target.layout.game.value,
            target_app_id=int(request.target.layout.app_id),
            addon_name=request.target.addon_name,
            asset_name=request.asset_name,
            model_paths=tuple(sorted(model_paths, key=ContentRelativePath.as_posix)),
            artifacts=records,
            compile_steps=tuple(compile_steps),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "sourceio.source2-authoring-manifest",
            "schema_version": self.schema_version,
            "target": {
                "game": self.target_game,
                "app_id": self.target_app_id,
                "addon": self.addon_name,
            },
            "asset": {
                "name": self.asset_name,
                "models": [path.as_posix() for path in self.model_paths],
            },
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "compile": [
                {
                    "source": step.source.as_posix(),
                    "expected_outputs": [
                        output.as_posix() for output in step.expected_outputs
                    ],
                    "arguments": list(step.arguments),
                }
                for step in self.compile_steps
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"

    @classmethod
    def from_json(cls, value: str) -> Source2AuthoringManifest:
        try:
            data = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AuthoringManifestError(f"Invalid authoring manifest JSON: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema") != "sourceio.source2-authoring-manifest":
            raise AuthoringManifestError("Not a SourceIO Source 2 authoring manifest")
        if data.get("schema_version") != 1:
            raise AuthoringManifestError(
                f"Unsupported authoring manifest version: {data.get('schema_version')!r}"
            )
        try:
            target = data["target"]
            asset = data["asset"]
            artifacts = tuple(
                ManifestArtifact(
                    ContentRelativePath(item["path"]),
                    str(item["kind"]),
                    int(item["size"]),
                    str(item["sha256"]),
                )
                for item in data["artifacts"]
            )
            compile_steps = tuple(
                CompileStep(
                    ContentRelativePath(item["source"]),
                    tuple(ContentRelativePath(path) for path in item["expected_outputs"]),
                    tuple(str(argument) for argument in item.get("arguments", ())),
                )
                for item in data["compile"]
            )
            manifest = cls(
                target_game=str(target["game"]),
                target_app_id=int(target["app_id"]),
                addon_name=str(target["addon"]),
                asset_name=str(asset["name"]),
                model_paths=tuple(ContentRelativePath(path) for path in asset["models"]),
                artifacts=artifacts,
                compile_steps=compile_steps,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthoringManifestError(f"Malformed authoring manifest: {exc}") from exc
        if len({artifact.path for artifact in manifest.artifacts}) != len(manifest.artifacts):
            raise AuthoringManifestError("Manifest artifact paths must be unique")
        return manifest

    def validate_target(self, target: WorkshopTargetProfile):
        expected = (
            target.layout.game.value,
            int(target.layout.app_id),
            target.addon_name,
        )
        actual = (self.target_game, self.target_app_id, self.addon_name)
        if actual != expected:
            raise AuthoringManifestError(
                "Manifest target does not match the configured Workshop target"
            )
