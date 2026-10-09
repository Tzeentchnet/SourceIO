from typing import Callable, Iterator, Optional

from ...app_id import SteamAppId
from ..provider import ContentProvider
from ..resolver import CollisionDiagnostic, normalize_resource_path, normalize_resource_pattern
from ..vpk_archive import VPKArchive, VPKFormatError
from ....utils import Buffer, MemoryBuffer, TinyPath
from ....utils.pylib import VPKFile
from .....logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('VpkProvider')


class VPKContentProvider(ContentProvider):
    def __init__(
        self,
        filepath: TinyPath,
        override_steamid=SteamAppId.UNKNOWN,
        *,
        source_buffer: Buffer | None = None,
        external_opener: Callable[[TinyPath], Buffer | None] | None = None,
    ):
        super().__init__(filepath)
        self._override_steamid = override_steamid
        self._source_buffer = source_buffer
        self._external_opener = external_opener
        self._initialized = False
        self.vpk_archive: VPKFile | None = None
        self._stream_archive: VPKArchive | None = None

    @classmethod
    def from_buffer(
        cls,
        filepath: TinyPath,
        source_buffer: Buffer,
        override_steamid=SteamAppId.UNKNOWN,
        external_opener: Callable[[TinyPath], Buffer | None] | None = None,
    ) -> "VPKContentProvider":
        return cls(
            filepath,
            override_steamid,
            source_buffer=source_buffer,
            external_opener=external_opener,
        )

    def check(self, filepath: TinyPath) -> bool:
        return self.resolve_path(filepath) is not None

    def get_relative_path(self, filepath: TinyPath) -> TinyPath | None:
        return None

    def get_provider_from_path(self, filepath) -> Optional['ContentProvider']:
        if self.check(filepath):
            return self
        return None

    def get_steamid_from_asset(self, asset_path: TinyPath) -> SteamAppId | None:
        if self.check(asset_path):
            return self.steam_id

    def _init(self):
        if self._initialized:
            return
        logger.info(f"Loading {self.filepath!r}")
        if self._source_buffer is None:
            self.vpk_archive = VPKFile(self.filepath)
            try:
                self._stream_archive = VPKArchive(self.filepath)
            except VPKFormatError as ex:
                logger.warn(f"Streaming VPK index unavailable for {self.filepath}: {ex}")
        else:
            self._stream_archive = VPKArchive(
                self._source_buffer,
                name=self.filepath,
                external_opener=self._external_opener,
            )
        self._initialized = True

    def glob(self, pattern: str) -> Iterator[tuple[TinyPath, Buffer]]:
        for key in self.iter_paths(pattern):
            data = self.open_stream(key)
            if data is not None:
                yield key, data

    def find_file(self, filepath: TinyPath) -> Optional[Buffer]:
        return self.open_stream(filepath)

    def open_stream(self, filepath: TinyPath) -> Optional[Buffer]:
        self._init()
        filepath = normalize_resource_path(filepath)
        if self._stream_archive is not None:
            return self._stream_archive.open(filepath)
        if self.vpk_archive is None:
            return None
        file = self.vpk_archive.find_file(filepath)
        if file is None:
            file = self.vpk_archive.find_file(TinyPath(filepath.as_posix().lower()))
        if file is not None:
            return MemoryBuffer(file)
        return None

    def resolve_path(self, filepath: TinyPath) -> TinyPath | None:
        self._init()
        filepath = normalize_resource_path(filepath)
        if self._stream_archive is not None:
            return self._stream_archive.resolve_path(filepath)
        if self.vpk_archive is None:
            return None
        if self.vpk_archive.check(filepath):
            return filepath
        lowered = TinyPath(filepath.as_posix().lower())
        return lowered if self.vpk_archive.check(lowered) else None

    def iter_paths(self, pattern: str = "*") -> Iterator[TinyPath]:
        self._init()
        pattern = normalize_resource_pattern(pattern)
        if self._stream_archive is not None:
            yield from self._stream_archive.iter_paths(pattern)
            return
        if self.vpk_archive is None:
            return
        for key, _ in self.vpk_archive.glob(pattern):
            yield normalize_resource_path(TinyPath(key))

    @property
    def collision_diagnostics(self) -> tuple[CollisionDiagnostic, ...]:
        self._init()
        return self._stream_archive.collisions if self._stream_archive is not None else ()

    @property
    def logical_size(self) -> int | None:
        self._init()
        return self._stream_archive.logical_size if self._stream_archive is not None else None

    @property
    def root(self) -> TinyPath:
        return self.filepath.parent

    @property
    def name(self) -> str:
        return self.filepath.stem

    @property
    def steam_id(self) -> SteamAppId:
        return self._override_steamid
