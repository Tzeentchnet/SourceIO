from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from ..workshop import WorkshopTargetProfile
from .models import AuthoringArtifact


def stage_authoring_artifacts(
        target: WorkshopTargetProfile,
        artifacts: Iterable[AuthoringArtifact],
        *,
        overwrite: bool = False,
) -> tuple[Path, ...]:
    values = tuple(artifacts)
    by_path = {artifact.path: artifact for artifact in values}
    if len(by_path) != len(values):
        raise ValueError("Authoring artifact paths must be unique")
    targets = {
        path: target.content_path(path)
        for path in by_path
    }
    existing = [path for path in targets.values() if os.path.lexists(path)]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in sorted(existing))
        raise FileExistsError(f"Refusing to overwrite authored files: {names}")

    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for relative_path in sorted(by_path, key=lambda path: path.as_posix()):
            destination = targets[relative_path]
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".sourceio-stage",
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            staged[destination] = temporary
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(by_path[relative_path].encoded())
                stream.flush()
                os.fsync(stream.fileno())

        if overwrite:
            for destination in existing:
                descriptor, backup_name = tempfile.mkstemp(
                    prefix=f".{destination.name}.",
                    suffix=".sourceio-backup",
                    dir=destination.parent,
                )
                os.close(descriptor)
                backup = Path(backup_name)
                shutil.copy2(destination, backup)
                backups[destination] = backup

        for destination in sorted(staged, key=lambda path: str(path).casefold()):
            os.replace(staged[destination], destination)
            committed.append(destination)
    except Exception:
        for destination in reversed(committed):
            backup = backups.get(destination)
            if backup is not None and backup.exists():
                os.replace(backup, destination)
            elif destination.exists():
                destination.unlink()
        raise
    finally:
        for temporary in staged.values():
            if temporary.exists():
                temporary.unlink()
        for backup in backups.values():
            if backup.exists():
                backup.unlink()

    return tuple(
        targets[path]
        for path in sorted(targets, key=lambda item: item.as_posix())
    )
