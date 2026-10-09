from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ...utils import TinyPath
from .resolver import ResolvedResource, ResourceResolverProtocol


@dataclass(frozen=True, slots=True)
class AssetIndexEntry:
    path: TinyPath
    resource: ResolvedResource

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return self.path.suffix.casefold()


class AssetIndex(Sequence[AssetIndexEntry]):
    """Lazy, deterministic, UI-independent view of resolver assets."""

    def __init__(self, resolver: ResourceResolverProtocol, patterns: tuple[str, ...] = ("*",)):
        self._resolver = resolver
        self._patterns = patterns
        self._entries: tuple[AssetIndexEntry, ...] | None = None

    @property
    def built(self) -> bool:
        return self._entries is not None

    @property
    def patterns(self) -> tuple[str, ...]:
        return self._patterns

    def invalidate(self) -> None:
        self._entries = None

    def _build(self) -> tuple[AssetIndexEntry, ...]:
        if self._entries is not None:
            return self._entries
        resources: dict[str, ResolvedResource] = {}
        for pattern in self._patterns:
            for resource in self._resolver.iter(pattern):
                resources.setdefault(resource.path.as_posix().casefold(), resource)
        ordered = sorted(resources.values(), key=lambda item: (item.path.as_posix().casefold(), item.path.as_posix()))
        self._entries = tuple(AssetIndexEntry(resource.path, resource) for resource in ordered)
        return self._entries

    def __len__(self) -> int:
        return len(self._build())

    def __getitem__(self, index: int | slice) -> AssetIndexEntry | tuple[AssetIndexEntry, ...]:
        return self._build()[index]

    def __iter__(self) -> Iterator[AssetIndexEntry]:
        return iter(self._build())

    def find(self, path: str | TinyPath) -> AssetIndexEntry | None:
        key = TinyPath(path).as_posix().casefold()
        return next((entry for entry in self._build() if entry.path.as_posix().casefold() == key), None)

    def search(self, query: str) -> tuple[AssetIndexEntry, ...]:
        needle = query.casefold()
        return tuple(entry for entry in self._build() if needle in entry.path.as_posix().casefold())

    def snapshot(self) -> tuple[AssetIndexEntry, ...]:
        return self._build()
