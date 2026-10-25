from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Protocol, TypeVar


class EncodedArtifact(Protocol):
    def encoded(self) -> bytes:
        ...


ArtifactT = TypeVar("ArtifactT", bound=EncodedArtifact)


def stage_artifacts(
        artifacts: Iterable[ArtifactT],
        destination_for: Callable[[ArtifactT], Path],
        *,
        overwrite: bool = False,
        sort_key: Callable[[ArtifactT], object] | None = None,
) -> tuple[Path, ...]:
    values = tuple(artifacts)
    by_path: dict[Path, ArtifactT] = {}
    for artifact in values:
        destination = destination_for(artifact)
        if destination in by_path:
            raise ValueError("Authoring artifact paths must be unique")
        by_path[destination] = artifact

    existing = [path for path in by_path if os.path.lexists(path)]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in sorted(existing))
        raise FileExistsError(f"Refusing to overwrite authored files: {names}")

    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    ordered_paths = tuple(sorted(
        by_path,
        key=(
            (lambda path: sort_key(by_path[path]))
            if sort_key is not None
            else (lambda path: str(path).casefold())
        ),
    ))
    try:
        for destination in ordered_paths:
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".sourceio-stage",
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            staged[destination] = temporary
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(by_path[destination].encoded())
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
                backups[destination] = backup
                shutil.copy2(destination, backup)

        for destination in ordered_paths:
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

    return ordered_paths
