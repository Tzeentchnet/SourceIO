from abc import abstractmethod
from typing import Iterator, Optional

from ..app_id import SteamAppId
from ...utils import Buffer, FileBuffer, TinyPath, corrected_path
from ....logger import SourceLogMan
from .resolver import CollisionDiagnostic, normalize_resource_path, normalize_resource_pattern

log_manager = SourceLogMan()
logger = log_manager.get_logger('ContentManager')


def find_file_generic(root: TinyPath, filepath: TinyPath) -> Buffer | None:
    filepath = corrected_path(root / normalize_resource_path(filepath))
    if filepath.exists() and filepath.is_file():
        return FileBuffer(filepath)
    return None


def find_path_generic(root: TinyPath, filepath: TinyPath) -> TinyPath | None:
    filepath = corrected_path(root / normalize_resource_path(filepath))
    if filepath.exists():
        return filepath
    return None


def glob_generic(root: TinyPath, pattern: str) -> Iterator[tuple[TinyPath, Buffer]]:
    pattern = normalize_resource_pattern(pattern)
    for filename in sorted(root.rglob(pattern), key=lambda path: path.as_posix().casefold()):
        yield (filename.relative_to(root)).as_posix(), FileBuffer(filename)


# backport
def is_relative_to(path: TinyPath, *other):
    """Return True if the path is relative to another path or False.
    """
    try:
        path.relative_to(*other)
        return True
    except ValueError:
        return False


class ContentProvider:
    @classmethod
    def class_name(cls):
        return cls.__name__

    def __init__(self, filepath: TinyPath):
        self.filepath = corrected_path(filepath)

    def __hash__(self):
        return hash(self.unique_name)

    @abstractmethod
    def glob(self, pattern: str) -> Iterator[tuple[TinyPath, Buffer]]:
        ...

    @abstractmethod
    def find_file(self, filepath: TinyPath) -> Buffer | None:
        ...

    def open_stream(self, filepath: TinyPath) -> Buffer | None:
        return self.find_file(filepath)

    def resolve_path(self, filepath: TinyPath) -> TinyPath | None:
        filepath = normalize_resource_path(filepath)
        return filepath if self.check(filepath) else None

    def iter_paths(self, pattern: str = "*") -> Iterator[TinyPath]:
        for path, buffer in self.glob(normalize_resource_pattern(pattern)):
            try:
                yield TinyPath(path)
            finally:
                buffer.close()

    @property
    def collision_diagnostics(self) -> tuple[CollisionDiagnostic, ...]:
        return ()

    @abstractmethod
    def check(self, filepath: TinyPath) -> bool:
        ...

    @abstractmethod
    def get_relative_path(self, filepath: TinyPath) -> TinyPath | None:
        ...

    @abstractmethod
    def get_provider_from_path(self, filepath) -> Optional['ContentProvider']:
        ...

    @abstractmethod
    def get_steamid_from_asset(self, asset_path: TinyPath) -> SteamAppId | None:
        ...

    @property
    @abstractmethod
    def root(self) -> TinyPath:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def steam_id(self) -> SteamAppId:
        ...

    @property
    def unique_name(self) -> str:
        root_stem = self.root.stem
        if self.steam_id <= 0:
            return f"{root_stem} {self.name}" if root_stem != self.name else self.name
        else:
            return f"{self.steam_id.name} {root_stem} {self.name}" if root_stem != self.name else f"{self.steam_id.name} {self.name}"

    def __eq__(self, other: 'ContentProvider'):
        return self.filepath == other.filepath and self.name == other.name and self.steam_id == other.steam_id

    def __repr__(self):
        return f"{self.__class__.__name__}(\"{self.filepath!s}\")"
