import fnmatch
from typing import Iterator, Optional
from zipfile import ZipFile

from ...app_id import SteamAppId
from ..provider import ContentProvider
from ..resolver import CollisionDiagnostic, normalize_resource_path, normalize_resource_pattern
from ....utils import Buffer, MemoryBuffer, TinyPath


class ZIPContentProvider(ContentProvider):
    def __init__(self, filepath: TinyPath, steamapp_id: SteamAppId = SteamAppId.UNKNOWN):
        super().__init__(filepath)
        self._steamapp_id = steamapp_id
        self._zip_file = ZipFile(filepath)
        self._cache: dict[str, str] = {}
        collisions: dict[str, set[str]] = {}
        for name in self._zip_file.namelist():
            if name.endswith("/"):
                continue
            try:
                normalized = normalize_resource_path(name)
            except ValueError:
                continue
            key = normalized.as_posix().casefold()
            previous = self._cache.get(key)
            if previous is None:
                self._cache[key] = name
            elif previous != name:
                collisions.setdefault(key, {previous}).add(name)
        self._collisions = tuple(
            CollisionDiagnostic(TinyPath(key), tuple(sorted(candidates)))
            for key, candidates in sorted(collisions.items())
        )

    def check(self, filepath: TinyPath) -> bool:
        return self.resolve_path(filepath) is not None

    def get_relative_path(self, filepath: TinyPath) -> TinyPath | None:
        return None

    def get_provider_from_path(self, filepath) -> Optional['ContentProvider']:
        if self.check(filepath):
            return self

    def get_steamid_from_asset(self, asset_path: TinyPath) -> SteamAppId | None:
        if self.check(asset_path):
            return self.steam_id

    def find_file(self, filepath: TinyPath) -> Optional[Buffer]:
        key = normalize_resource_path(filepath).as_posix().casefold()
        if key in self._cache:
            return MemoryBuffer(self._zip_file.read(self._cache[key]))
        return None

    def glob(self, pattern: str) -> Iterator[tuple[TinyPath, Buffer]]:
        for match in self.iter_paths(pattern):
            yield match, MemoryBuffer(self._zip_file.read(self._cache[match.as_posix().casefold()]))

    def resolve_path(self, filepath: TinyPath) -> TinyPath | None:
        key = normalize_resource_path(filepath).as_posix().casefold()
        stored = self._cache.get(key)
        return normalize_resource_path(stored) if stored is not None else None

    def iter_paths(self, pattern: str = "*") -> Iterator[TinyPath]:
        pattern = normalize_resource_pattern(pattern).casefold()
        for key, stored in sorted(self._cache.items()):
            if fnmatch.fnmatchcase(key, pattern):
                yield normalize_resource_path(stored)

    @property
    def collision_diagnostics(self) -> tuple[CollisionDiagnostic, ...]:
        return self._collisions

    @property
    def root(self) -> TinyPath:
        return self.filepath.parent

    @property
    def name(self) -> str:
        return self.filepath.stem

    @property
    def steam_id(self) -> SteamAppId:
        return self._steamapp_id
