from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Callable, Iterator, Protocol, runtime_checkable

from ...utils import Buffer, TinyPath

if TYPE_CHECKING:
    try:
        from ...source2.interfaces import ResourceResolver as Source2ResourceResolver
    except ImportError:
        Source2ResourceResolver = Any


class UnsafeResourcePath(ValueError):
    """Raised when a resource path could escape its mount or archive boundary."""


class NestedArchiveLimitError(ValueError):
    """Raised when nested archive recursion or size limits are exceeded."""


class MountLayer(IntEnum):
    """Resolver precedence. Lower-valued layers win."""

    CURRENT_PACKAGE = 0
    LOOSE_FILES = 100
    ADDONS = 200
    WORKSHOP = 300
    GAME_PACKAGES = 400
    SEARCH_PATHS = 500

    LOOSE = LOOSE_FILES
    ADDON = ADDONS
    CUSTOM = ADDONS
    GAME_PACKAGE = GAME_PACKAGES
    SEARCH_PATH = SEARCH_PATHS


@dataclass(frozen=True, slots=True)
class CollisionDiagnostic:
    normalized_path: TinyPath
    candidates: tuple[str, ...]
    reason: str = "case-insensitive path collision"


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """A resource lookup with an optional package-local dependency context."""

    path: str | os.PathLike[str] | TinyPath | None = None
    resource_id: int | None = None
    kind: Any = None
    source_path: str | None = None
    optional: bool = False
    source: ResolvedResource | None = None

    def __post_init__(self) -> None:
        if self.path is None and self.resource_id is None:
            raise ValueError("ResourceRef requires a path or resource_id")
        if self.resource_id is not None and not 0 <= self.resource_id <= 0xFFFF_FFFF_FFFF_FFFF:
            raise ValueError("ResourceRef.resource_id must fit in uint64")

    def __fspath__(self) -> str:
        if self.path is None:
            raise TypeError("A resource-id-only reference has no filesystem path")
        return os.fspath(self.path)


@dataclass(frozen=True, slots=True)
class ResolvedResource:
    requested_path: TinyPath
    path: TinyPath
    provider: Any
    layer: MountLayer
    mount_name: str
    archive_chain: tuple[TinyPath, ...] = ()
    collisions: tuple[str, ...] = ()
    _opener: Callable[[], Buffer | None] = field(repr=False, compare=False, default=lambda: None)
    _context: tuple[Any, ...] = field(repr=False, compare=False, default=())

    def open(self) -> Buffer | None:
        return self._opener()

    def open_stream(self) -> Buffer | None:
        return self._opener()

    def dependency(self, path: str | os.PathLike[str] | TinyPath) -> ResourceRef:
        return ResourceRef(path, source=self, source_path=self.path.as_posix())

    ref = dependency


@runtime_checkable
class ResourceResolverProtocol(Protocol):
    def open(self, path: str | os.PathLike[str] | TinyPath | ResourceRef) -> Buffer | None:
        ...

    def open_stream(self, path: str | os.PathLike[str] | TinyPath | ResourceRef) -> Buffer | None:
        ...

    def find(self, path: str | os.PathLike[str] | TinyPath | ResourceRef) -> ResolvedResource | None:
        ...

    def iter(self, pattern: str) -> Iterator[ResolvedResource]:
        ...


_DRIVE_PATH = re.compile(r"^[A-Za-z]:($|/)")


def normalize_resource_path(path: str | os.PathLike[str] | TinyPath) -> TinyPath:
    """Normalize a mount-relative path and reject boundary escapes."""

    value = os.fspath(path).replace("\\", "/")
    if "\x00" in value:
        raise UnsafeResourcePath("Resource paths cannot contain NUL bytes")
    if not value:
        raise UnsafeResourcePath("Resource paths cannot be empty")
    if value.startswith("/") or value.startswith("//") or _DRIVE_PATH.match(value):
        raise UnsafeResourcePath(f"Absolute resource path is not allowed: {value!r}")

    parts: list[str] = []
    for part in value.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise UnsafeResourcePath(f"Resource path escapes its mount: {value!r}")
        if ":" in part:
            raise UnsafeResourcePath(f"Resource path contains an invalid boundary marker: {value!r}")
        parts.append(part)
    if not parts:
        raise UnsafeResourcePath(f"Resource path does not name a file: {value!r}")
    return TinyPath("/".join(parts))


def normalize_resource_pattern(pattern: str) -> str:
    value = pattern.replace("\\", "/")
    if "\x00" in value:
        raise UnsafeResourcePath("Resource patterns cannot contain NUL bytes")
    if not value:
        return "*"
    if value.startswith("/") or value.startswith("//") or _DRIVE_PATH.match(value):
        raise UnsafeResourcePath(f"Absolute resource pattern is not allowed: {value!r}")
    parts = []
    for part in value.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise UnsafeResourcePath(f"Resource pattern escapes its mount: {value!r}")
        if ":" in part:
            raise UnsafeResourcePath(f"Resource pattern contains an invalid boundary marker: {value!r}")
        parts.append(part)
    return "/".join(parts) or "*"


def split_resource_locator(path: str | os.PathLike[str] | TinyPath) -> tuple[TinyPath, ...]:
    """Split explicit ``archive.vpk::path`` or ``archive.vpk!/path`` boundaries."""

    value = os.fspath(path).replace("\\", "/").replace("!/", "::")
    parts = value.split("::")
    return tuple(normalize_resource_path(part) for part in parts)


def canonical_resource_path(path: str | os.PathLike[str] | TinyPath) -> str:
    return normalize_resource_path(path).as_posix().casefold()
