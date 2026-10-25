from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ...shared.authoring import stage_artifacts
from ..workshop import WorkshopTargetProfile
from .models import AuthoringArtifact


def stage_authoring_artifacts(
        target: WorkshopTargetProfile,
        artifacts: Iterable[AuthoringArtifact],
        *,
        overwrite: bool = False,
) -> tuple[Path, ...]:
    return stage_artifacts(
        artifacts,
        lambda artifact: target.content_path(artifact.path),
        overwrite=overwrite,
        sort_key=lambda artifact: artifact.path.as_posix(),
    )
