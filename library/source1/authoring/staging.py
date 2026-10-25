from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...shared.authoring import stage_artifacts
from .models import (
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
)
from .target import Source1AuthoringTarget


def _artifact_path(
        target: Source1AuthoringTarget,
        artifact: Source1AuthoringArtifact,
) -> Path:
    if artifact.root == Source1ArtifactRoot.CONTENT:
        return target.content_path(artifact.path)
    if artifact.root == Source1ArtifactRoot.GAME:
        return target.game_path(artifact.path)
    raise ValueError(f"Unsupported Source 1 artifact root: {artifact.root}")


def stage_source1_artifacts(
        target: Source1AuthoringTarget,
        artifacts: Iterable[Source1AuthoringArtifact],
        *,
        overwrite: bool = False,
) -> tuple[Path, ...]:
    return stage_artifacts(
        artifacts,
        lambda artifact: _artifact_path(target, artifact),
        overwrite=overwrite,
    )
