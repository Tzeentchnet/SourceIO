import fnmatch
from pathlib import Path
from typing import Iterator, Optional

from ..provider import ContentProvider
from ..resolver import CollisionDiagnostic, normalize_resource_path, normalize_resource_pattern
from ....utils import Buffer, FileBuffer, TinyPath, corrected_path
from ...app_id import SteamAppId


class LooseFilesContentProvider(ContentProvider):
    def check(self, filepath: TinyPath) -> bool:
        return self.resolve_path(filepath) is not None

    def get_relative_path(self, filepath: TinyPath):
        try:
            relative = Path(filepath).resolve().relative_to(Path(self.root).resolve())
        except ValueError:
            return None
        return normalize_resource_path(TinyPath(relative.as_posix()))

    def get_provider_from_path(self, filepath: TinyPath) -> ContentProvider | None:
        return self if self.resolve_path(filepath) is not None else None

    def get_steamid_from_asset(self, asset_path: TinyPath) -> SteamAppId | None:
        asset_path = corrected_path(asset_path)
        if self.check(asset_path):
            return self.steam_id
        return None

    @property
    def name(self) -> str:
        return self.root.stem

    @property
    def root(self) -> TinyPath:
        return self.filepath

    def __init__(self, filepath: TinyPath, override_steamid=SteamAppId.UNKNOWN):
        self._override_steamid = override_steamid
        self._case_index: dict[str, TinyPath] | None = None
        self._collisions: list[CollisionDiagnostic] = []
        super().__init__(filepath)

    def find_file(self, filepath: str | TinyPath) -> Optional[Buffer]:
        relative = self.resolve_path(TinyPath(filepath))
        if relative is not None:
            return FileBuffer(corrected_path(self.root / relative))
        return None

    def glob(self, pattern: str) -> Iterator[tuple[TinyPath, Buffer]]:
        for path in self.iter_paths(pattern):
            yield path, FileBuffer(corrected_path(self.root / path))

    def _to_relative(self, filepath: TinyPath) -> TinyPath | None:
        if filepath.is_absolute():
            return self.get_relative_path(filepath)
        return normalize_resource_path(filepath)

    def _is_inside_root(self, path: TinyPath) -> bool:
        try:
            Path(path).resolve().relative_to(Path(self.root).resolve())
        except ValueError:
            return False
        return True

    def _build_case_index(self) -> dict[str, TinyPath]:
        if self._case_index is not None:
            return self._case_index
        index: dict[str, TinyPath] = {}
        collisions: dict[str, set[str]] = {}
        for item in sorted(Path(self.root).rglob("*"), key=lambda path: path.as_posix().casefold()):
            if not item.is_file():
                continue
            resolved = TinyPath(item.resolve().as_posix())
            if not self._is_inside_root(resolved):
                continue
            relative = normalize_resource_path(TinyPath(item.relative_to(Path(self.root)).as_posix()))
            key = relative.as_posix().casefold()
            previous = index.get(key)
            if previous is None:
                index[key] = relative
            elif previous.as_posix() != relative.as_posix():
                collisions.setdefault(key, {previous.as_posix()}).add(relative.as_posix())
        self._case_index = index
        self._collisions = [
            CollisionDiagnostic(TinyPath(key), tuple(sorted(candidates)))
            for key, candidates in sorted(collisions.items())
        ]
        return index

    def resolve_path(self, filepath: TinyPath) -> TinyPath | None:
        relative = self._to_relative(filepath)
        if relative is None:
            return None
        parts = relative.parts
        for start in range(len(parts)):
            candidate = TinyPath("/".join(parts[start:]))
            resolved = self._resolve_case_path(candidate)
            if resolved is not None:
                return resolved
        return None

    def _resolve_case_path(self, relative: TinyPath) -> TinyPath | None:
        current = Path(self.root)
        actual_parts: list[str] = []
        for component in relative.parts:
            try:
                matches = sorted(
                    (entry.name for entry in current.iterdir() if entry.name.casefold() == component.casefold()),
                    key=lambda name: (name.casefold(), name),
                )
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                return None
            if not matches:
                return None
            selected = component if component in matches else matches[0]
            actual_parts.append(selected)
            current /= selected
        resolved = TinyPath(current.resolve().as_posix())
        if not current.is_file() or not self._is_inside_root(resolved):
            return None
        return normalize_resource_path(TinyPath("/".join(actual_parts)))

    def iter_paths(self, pattern: str = "*") -> Iterator[TinyPath]:
        pattern = normalize_resource_pattern(pattern).casefold()
        for key, path in sorted(self._build_case_index().items()):
            if fnmatch.fnmatchcase(key, pattern):
                yield path

    @property
    def collision_diagnostics(self) -> tuple[CollisionDiagnostic, ...]:
        self._build_case_index()
        return tuple(self._collisions)

    @property
    def steam_id(self) -> SteamAppId:
        return self._override_steamid
