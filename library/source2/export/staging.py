from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping


class AuthoredFileExistsError(FileExistsError):
    pass


class UnsafeExportPathError(ValueError):
    pass


def _relative_output_path(value: str | Path) -> PurePosixPath:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UnsafeExportPathError(f"Export path must be a normalized relative path: {value}")
    if any(":" in part for part in path.parts):
        raise UnsafeExportPathError(f"Export path contains an invalid drive or stream marker: {value}")
    if path.name.endswith("_c"):
        raise UnsafeExportPathError(f"Compiled Source 2 output is not supported: {value}")
    return path


def stage_text_outputs(
        root: str | Path,
        outputs: Mapping[str | Path, str],
        *,
        overwrite: bool = False,
        encoding: str = "utf-8",
) -> tuple[Path, ...]:
    destination_root = Path(root)
    normalized: dict[PurePosixPath, str] = {}
    for relative_path, content in outputs.items():
        output_path = _relative_output_path(relative_path)
        if output_path in normalized:
            raise ValueError(f"Duplicate export output path: {output_path}")
        if not isinstance(content, str):
            raise TypeError("Source 2 export staging only accepts editable text outputs")
        normalized[output_path] = content

    targets = {
        relative_path: destination_root.joinpath(*relative_path.parts)
        for relative_path in normalized
    }
    resolved_root = destination_root.resolve()
    for relative_path, target in targets.items():
        resolved_target = target.resolve(strict=False)
        try:
            resolved_target.relative_to(resolved_root)
        except ValueError as exc:
            raise UnsafeExportPathError(
                f"Export path escapes through a linked parent: {relative_path}"
            ) from exc
    existing = [target for target in targets.values() if os.path.lexists(target)]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in sorted(existing))
        raise AuthoredFileExistsError(f"Refusing to overwrite authored files: {names}")

    staged: dict[PurePosixPath, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for relative_path, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".sourceio-stage",
                dir=target.parent,
                text=True,
            )
            temporary_path = Path(temporary_name)
            staged[relative_path] = temporary_path
            with os.fdopen(descriptor, "w", encoding=encoding, newline="\n") as stream:
                stream.write(normalized[relative_path])
                stream.flush()
                os.fsync(stream.fileno())

        if overwrite:
            for target in existing:
                descriptor, backup_name = tempfile.mkstemp(
                    prefix=f".{target.name}.",
                    suffix=".sourceio-backup",
                    dir=target.parent,
                )
                os.close(descriptor)
                backup = Path(backup_name)
                shutil.copy2(target, backup)
                backups[target] = backup

        for relative_path in sorted(staged, key=lambda item: item.as_posix()):
            target = targets[relative_path]
            os.replace(staged[relative_path], target)
            committed.append(target)
    except Exception:
        for target in reversed(committed):
            backup = backups.get(target)
            if backup is not None and backup.exists():
                os.replace(backup, target)
            elif target.exists():
                target.unlink()
        raise
    finally:
        for temporary_path in staged.values():
            if temporary_path.exists():
                temporary_path.unlink()
        for backup in backups.values():
            if backup.exists():
                backup.unlink()

    return tuple(targets[path] for path in sorted(targets, key=lambda item: item.as_posix()))
