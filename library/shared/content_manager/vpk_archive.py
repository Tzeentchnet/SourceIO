from __future__ import annotations

import fnmatch
import io
import os
import struct
from dataclasses import dataclass
from typing import Callable, Iterator

from ...utils import Buffer, MemoryBuffer, TinyPath
from .resolver import CollisionDiagnostic, normalize_resource_path, normalize_resource_pattern

VPK_SIGNATURE = 0x55AA1234
VPK_EMBEDDED_ARCHIVE = 0x7FFF
_ENTRY = struct.Struct("<IHHIIH")


class VPKFormatError(ValueError):
    pass


class FileSliceBuffer(Buffer):
    """A seekable view over a range of a file without copying the whole entry."""

    def __init__(self, path: TinyPath, offset: int, size: int):
        super().__init__()
        file_size = os.path.getsize(os.fspath(path))
        if offset < 0 or size < 0 or offset + size > file_size:
            raise VPKFormatError(
                f"VPK entry range {offset}:{offset + size} exceeds {path} ({file_size} bytes)"
            )
        self._file = open(os.fspath(path), "rb")
        self._start = offset
        self._size = size
        self._offset = 0

    @property
    def data(self) -> bytes:
        with self.save_current_offset():
            self.seek(0)
            return self.read()

    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._offset

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.remaining()
        size = min(size, self.remaining())
        if size <= 0:
            return b""
        self._file.seek(self._start + self._offset)
        data = self._file.read(size)
        self._offset += len(data)
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._offset + offset
        elif whence == io.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError("Invalid whence argument")
        if target < 0 or target > self._size:
            raise BufferError("Offset is out of bounds")
        self._offset = target
        return target

    def ro_view(self, offset: int = -1, size: int = -1) -> memoryview:
        if offset == -1:
            offset = self._offset
        if size == -1:
            size = self._size - offset
        if offset < 0 or size < 0 or offset + size > self._size:
            raise ValueError("Offset and size must be within the bounds of the buffer")
        current = self._offset
        try:
            self.seek(offset)
            return memoryview(self.read(size))
        finally:
            self._offset = current

    def read_array(self, fmt: str, count: int) -> list:
        size = struct.calcsize(fmt) * count
        return MemoryBuffer(self.read(size)).read_array(fmt, count)

    def slice(self, offset: int | None = None, size: int = -1) -> MemoryBuffer:
        if offset is None:
            offset = self._offset
        return MemoryBuffer(self.ro_view(offset, size))

    @property
    def closed(self) -> bool:
        return self._file.closed

    def close(self) -> None:
        self._file.close()


class BufferSlice(Buffer):
    """A seekable bounded view that retains its source buffer."""

    def __init__(self, source: Buffer, offset: int, size: int, *, close_source: bool = False):
        super().__init__()
        if offset < 0 or size < 0 or offset + size > source.size():
            raise VPKFormatError(
                f"VPK entry range {offset}:{offset + size} exceeds its {source.size()}-byte source"
            )
        self._source = source
        self._start = offset
        self._size = size
        self._offset = 0
        self._close_source = close_source
        self._closed = False

    @property
    def data(self) -> memoryview:
        return self.ro_view(0, self._size)

    def size(self) -> int:
        return self._size

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._offset

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.remaining()
        size = min(size, self.remaining())
        if size <= 0:
            return b""
        with self._source.read_from_offset(self._start + self._offset):
            data = self._source.read(size)
        self._offset += len(data)
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._offset + offset
        elif whence == io.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError("Invalid whence argument")
        if target < 0 or target > self._size:
            raise BufferError("Offset is out of bounds")
        self._offset = target
        return target

    def ro_view(self, offset: int = -1, size: int = -1) -> memoryview:
        if offset == -1:
            offset = self._offset
        if size == -1:
            size = self._size - offset
        if offset < 0 or size < 0 or offset + size > self._size:
            raise ValueError("Offset and size must be within the bounds of the buffer")
        return self._source.ro_view(self._start + offset, size)

    def read_array(self, fmt: str, count: int) -> list:
        size = struct.calcsize(fmt) * count
        return MemoryBuffer(self.read(size)).read_array(fmt, count)

    def slice(self, offset: int | None = None, size: int = -1) -> MemoryBuffer:
        if offset is None:
            offset = self._offset
        return MemoryBuffer(self.ro_view(offset, size))

    @property
    def closed(self) -> bool:
        return self._closed or self._source.closed

    def close(self) -> None:
        if not self._closed and self._close_source:
            self._source.close()
        self._closed = True


@dataclass(frozen=True, slots=True)
class VPKEntry:
    path: TinyPath
    crc32: int
    preload: bytes
    archive_index: int
    offset: int
    size: int


class VPKArchive:
    """Small VPK directory reader used for safe lookup and zero-copy entry streams."""

    def __init__(
        self,
        source: TinyPath | Buffer,
        *,
        name: TinyPath | None = None,
        external_opener: Callable[[TinyPath], Buffer | None] | None = None,
    ):
        self._path = source if isinstance(source, TinyPath) else None
        self._buffer = source if isinstance(source, Buffer) else None
        self._source_size = (
            os.path.getsize(os.fspath(source))
            if isinstance(source, TinyPath)
            else source.size()
        )
        self.name = name or (source if isinstance(source, TinyPath) else TinyPath("memory_dir.vpk"))
        self._external_opener = external_opener
        self._entries: dict[str, VPKEntry] = {}
        self._collisions: list[CollisionDiagnostic] = []
        self._version = 0
        self._data_offset = 0
        self._parse()

    @property
    def collisions(self) -> tuple[CollisionDiagnostic, ...]:
        return tuple(self._collisions)

    @property
    def paths(self) -> tuple[TinyPath, ...]:
        return tuple(entry.path for _, entry in sorted(self._entries.items()))

    @property
    def logical_size(self) -> int:
        return sum(len(entry.preload) + entry.size for entry in self._entries.values())

    def _read_at(self, offset: int, size: int) -> bytes:
        if self._path is not None:
            with open(os.fspath(self._path), "rb") as stream:
                stream.seek(offset)
                return stream.read(size)
        if self._buffer is None:
            return b""
        with self._buffer.read_from_offset(offset):
            return self._buffer.read(size)

    @staticmethod
    def _cstring(data: bytes, offset: int) -> tuple[str, int]:
        end = data.find(b"\x00", offset)
        if end == -1:
            raise VPKFormatError("Unterminated string in VPK directory tree")
        try:
            value = data[offset:end].decode("utf8")
        except UnicodeDecodeError:
            value = data[offset:end].decode("latin1")
        return value, end + 1

    def _parse(self) -> None:
        header = self._read_at(0, 28)
        if len(header) < 12:
            raise VPKFormatError(f"{self.name} is too small to be a VPK")
        signature, version, tree_size = struct.unpack_from("<III", header)
        if signature != VPK_SIGNATURE:
            raise VPKFormatError(f"{self.name} has an invalid VPK signature")
        if version == 1:
            header_size = 12
        elif version == 2:
            if len(header) < 28:
                raise VPKFormatError(f"{self.name} has a truncated VPK v2 header")
            header_size = 28
        else:
            raise VPKFormatError(f"Unsupported VPK version {version}")
        self._version = version
        self._data_offset = header_size + tree_size
        tree = self._read_at(header_size, tree_size)
        if len(tree) != tree_size:
            raise VPKFormatError(f"{self.name} has a truncated VPK directory tree")

        cursor = 0
        while cursor < len(tree):
            extension, cursor = self._cstring(tree, cursor)
            if not extension:
                break
            while cursor < len(tree):
                directory, cursor = self._cstring(tree, cursor)
                if not directory:
                    break
                while cursor < len(tree):
                    filename, cursor = self._cstring(tree, cursor)
                    if not filename:
                        break
                    if cursor + _ENTRY.size > len(tree):
                        raise VPKFormatError(f"{self.name} has a truncated VPK entry")
                    crc, preload_size, archive_index, offset, size, terminator = _ENTRY.unpack_from(tree, cursor)
                    cursor += _ENTRY.size
                    if terminator != 0xFFFF:
                        raise VPKFormatError(f"{self.name} has an invalid VPK entry terminator")
                    preload = tree[cursor:cursor + preload_size]
                    if len(preload) != preload_size:
                        raise VPKFormatError(f"{self.name} has truncated preload data")
                    cursor += preload_size
                    base = filename if extension == " " else f"{filename}.{extension}"
                    path = TinyPath(base if directory == " " else f"{directory}/{base}")
                    path = normalize_resource_path(path)
                    entry = VPKEntry(path, crc, preload, archive_index, offset, size)
                    if (
                        archive_index == VPK_EMBEDDED_ARCHIVE
                        and self._data_offset + offset + size > self._source_size
                    ):
                        raise VPKFormatError(f"{self.name} has an embedded entry outside the archive")
                    key = path.as_posix().casefold()
                    previous = self._entries.get(key)
                    if previous is not None:
                        candidates = tuple(sorted({previous.path.as_posix(), path.as_posix()}))
                        self._collisions.append(CollisionDiagnostic(TinyPath(key), candidates))
                        continue
                    self._entries[key] = entry

    def resolve_path(self, path: TinyPath) -> TinyPath | None:
        normalized = normalize_resource_path(path)
        entry = self._entries.get(normalized.as_posix().casefold())
        return entry.path if entry is not None else None

    def check(self, path: TinyPath) -> bool:
        return self.resolve_path(path) is not None

    def _chunk_path(self, archive_index: int) -> TinyPath:
        name = self.name.as_posix()
        if name.casefold().endswith("_dir.vpk"):
            return TinyPath(f"{name[:-8]}_{archive_index:03d}.vpk")
        if name.casefold().endswith(".vpk"):
            return TinyPath(f"{name[:-4]}_{archive_index:03d}.vpk")
        return TinyPath(f"{name}_{archive_index:03d}.vpk")

    def _data_stream(self, entry: VPKEntry) -> Buffer:
        if entry.archive_index == VPK_EMBEDDED_ARCHIVE:
            offset = self._data_offset + entry.offset
            if self._path is not None:
                return FileSliceBuffer(self._path, offset, entry.size)
            if self._buffer is None:
                raise VPKFormatError(f"{self.name} has no embedded data source")
            return BufferSlice(self._buffer, offset, entry.size)

        chunk_path = self._chunk_path(entry.archive_index)
        if self._external_opener is not None:
            chunk = self._external_opener(chunk_path)
            if chunk is None:
                raise FileNotFoundError(f"Missing VPK split archive {chunk_path}")
            return BufferSlice(chunk, entry.offset, entry.size, close_source=True)
        if self._path is None:
            raise FileNotFoundError(f"Missing VPK split archive {chunk_path}")
        disk_chunk = self._path.parent / chunk_path.name
        if not disk_chunk.is_file():
            raise FileNotFoundError(f"Missing VPK split archive {disk_chunk}")
        return FileSliceBuffer(disk_chunk, entry.offset, entry.size)

    def open(self, path: TinyPath) -> Buffer | None:
        normalized = normalize_resource_path(path)
        entry = self._entries.get(normalized.as_posix().casefold())
        if entry is None:
            return None
        data = self._data_stream(entry)
        if not entry.preload:
            return data
        try:
            payload = entry.preload + data.read()
        finally:
            data.close()
        return MemoryBuffer(payload)

    def iter_paths(self, pattern: str = "*") -> Iterator[TinyPath]:
        normalized = normalize_resource_pattern(pattern).casefold()
        for key, entry in sorted(self._entries.items()):
            if fnmatch.fnmatchcase(key, normalized):
                yield entry.path
