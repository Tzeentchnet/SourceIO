from collections import Counter, OrderedDict
from dataclasses import dataclass
from hashlib import md5
from os import PathLike
from typing import Any, Optional, TypeVar, Union, Iterator, Hashable

from ..app_id import SteamAppId
from .detectors import detect_game
from .provider import ContentProvider
from .providers import register_provider
from .providers.hfs_provider import HFS1ContentProvider, HFS2ContentProvider
from .providers.loose_files import LooseFilesContentProvider
from .providers.source1_gameinfo_provider import Source1GameInfoProvider
from .providers.source2_gameinfo_provider import Source2GameInfoProvider
from .providers.vpk_provider import VPKContentProvider
from .providers.zip_content_provider import ZIPContentProvider
from .resolver import (
    CollisionDiagnostic,
    MountLayer,
    NestedArchiveLimitError,
    ResolvedResource,
    ResourceRef,
    canonical_resource_path,
    normalize_resource_path,
    normalize_resource_pattern,
    split_resource_locator,
)
from .vpk_archive import VPKFormatError
from ...utils import Buffer, FileBuffer, TinyPath, backwalk_file_resolver, corrected_path
from ...utils.path_utilities import get_mod_path
from ...utils.singleton import SingletonMeta
from ....logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('ContentManager')

AnyContentDetector = TypeVar('AnyContentDetector', bound='ContentDetector')
AnyContentProvider = TypeVar('AnyContentProvider', bound='ContentProvider')

MAX_CACHE_SIZE = 16
META_CACHE_SIZE = 200_000
DEFAULT_MAX_NESTED_ARCHIVE_DEPTH = 4
DEFAULT_MAX_NESTED_ARCHIVE_SIZE = 256 * 1024 * 1024

K = TypeVar('K', bound=Hashable)
T = TypeVar('T')
ResourcePath = str | PathLike[str] | TinyPath | ResourceRef


@dataclass(frozen=True, slots=True)
class _MountRecord:
    layer: MountLayer
    order: int


@dataclass(frozen=True, slots=True)
class _NestedContext:
    provider: VPKContentProvider
    parent: ContentProvider
    archive_path: TinyPath
    depth: int
    chain: tuple[TinyPath, ...]


class _LRU(OrderedDict[K, T]):
    """Minimal LRU with O(1) move-to-end on get/set."""

    def __init__(self, maxsize: int):
        super().__init__()
        self.maxsize = maxsize

    def get(self, key, default=None) -> T | None:
        v = super().get(key, default)
        if v is not default:
            self.move_to_end(key)
        return v

    def set(self, key:K, value:T):
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.maxsize:
            self.popitem(last=False)


def get_loose_file_fs_root(path: TinyPath):
    return get_mod_path(path)


class ContentManager(ContentProvider, metaclass=SingletonMeta):

    def __init__(self):
        super().__init__(TinyPath("."))
        self.children: set[ContentProvider] = set()
        self._mounts: dict[ContentProvider, _MountRecord] = {}
        self._mount_order = 0
        self._steam_id = -1
        self._cache: _LRU[TinyPath, Buffer] = _LRU(MAX_CACHE_SIZE)
        self._exists_cache: _LRU[TinyPath, bool] = _LRU(META_CACHE_SIZE)
        self._owner_cache: _LRU[TinyPath, ContentProvider] = _LRU(META_CACHE_SIZE)
        self._nested_cache: dict[tuple[int, str], _NestedContext] = {}
        self._current_packages: list[_NestedContext] = []
        self._resolution_collisions: dict[tuple[str, tuple[str, ...]], CollisionDiagnostic] = {}
        self.max_nested_archive_depth = DEFAULT_MAX_NESTED_ARCHIVE_DEPTH
        self.max_nested_archive_size = DEFAULT_MAX_NESTED_ARCHIVE_SIZE
        self.first_import: TinyPath = None
        self.priority_list: list[ContentProvider] = None

    @property
    def root(self) -> TinyPath:
        return self.filepath

    @property
    def name(self) -> str:
        return "ContentManager"

    def check(self, filepath: ResourcePath) -> bool:
        if not self._is_resource_reference(filepath):
            raw_path = TinyPath(filepath)
            if raw_path.is_absolute():
                return raw_path.is_file()
        return self.find(filepath) is not None

    def get_provider_from_path(self, filepath):
        if not self._is_resource_reference(filepath):
            raw_path = TinyPath(filepath)
            if raw_path.is_absolute():
                filepath = self.get_relative_path(raw_path)
                if filepath is None:
                    return None
        resource = self.find(filepath)
        return resource.provider if resource is not None else None

    def get_steamid_from_asset(self, asset_path: TinyPath) -> SteamAppId | None:
        raw_path = TinyPath(asset_path)
        if raw_path.is_absolute():
            asset_path = self.get_relative_path(raw_path)
            if asset_path is None:
                return None
        resource = self.find(asset_path)
        return resource.provider.steam_id if resource is not None else None

    def _find_steam_appid(self, path: TinyPath):
        """Populate self._steam_id by scanning for steam_appid.txt."""
        if self._steam_id != -1:
            return
        if path.is_file():
            path = path.parent
        file = backwalk_file_resolver(path, 'steam_appid.txt')
        if file is not None:
            with file.open('r') as f:
                try:
                    value = f.read().strip("\x00\n\t\r")
                    self._steam_id = int(value.strip())
                except Exception as e:
                    logger.exception(f'Failed to parse steam id due to: {e}')
                    self._steam_id = -1

    def get_relative_path(self, filepath: TinyPath):
        """Return a relative path under any child root, if resolvable."""
        if not filepath.is_absolute():
            return normalize_resource_path(filepath)
        for provider in self._ordered_children():
            if (rel_path := provider.get_relative_path(filepath)) is not None:
                return rel_path
        return None

    def scan_for_content(self, scan_path: TinyPath):
        """Discover and mount providers for the given path."""
        providers = detect_game(scan_path)
        if providers:
            for provider in providers:
                logger.info(f"Mounted: {provider}")
                self.add_child(provider)
            return
        self._find_steam_appid(scan_path)
        if scan_path.suffix == '.vpk':
            if scan_path.exists():
                self.add_child(VPKContentProvider(scan_path))
                return

        root_path = get_loose_file_fs_root(scan_path)
        if root_path:
            self.add_child(LooseFilesContentProvider(root_path))
        if root_path:
            gameinfos = list(root_path.glob('*gameinfo.txt'))
            if not gameinfos:
                # for unknown gameinfo like gameinfo_srgb, they are confusing content manager steam id thingie
                gameinfos = root_path.glob('gameinfo_*.txt')
            for gameinfo in gameinfos:
                try:
                    sub_manager = Source1GameInfoProvider(gameinfo)
                except ValueError as ex:
                    logger.exception(f"Failed to parse gameinfo for {gameinfo}", ex)
                    continue
                self.add_child(sub_manager)
                logger.info(f'Registered provider for {root_path.stem}')

            gameinfos = root_path.glob('*gameinfo*.gi')
            for gameinfo in gameinfos:
                sub_manager = Source2GameInfoProvider(gameinfo)
                self.add_child(sub_manager)
        else:
            if root_path.is_dir():
                self.add_child(LooseFilesContentProvider(root_path))
            else:
                root_path = root_path.parent
                self.add_child(LooseFilesContentProvider(root_path))
        pass

    def add_child(self, child: ContentProvider, layer: MountLayer | None = None):
        """Mount a provider at an explicit deterministic precedence layer."""
        if child not in self.children:
            self.children.add(child)
        if child not in self._mounts or layer is not None:
            mount_layer = layer if layer is not None else self._infer_mount_layer(child)
            self._mounts[child] = _MountRecord(mount_layer, self._mount_order)
            self._mount_order += 1
        self._invalidate_resolution()

    mount = add_child

    def glob(self, pattern: str) -> Iterator[tuple[TinyPath, Buffer]]:
        for resource in self.iter(pattern):
            stream = resource.open_stream()
            if stream is not None:
                yield resource.path, stream

    def find_file(self, filepath: ResourcePath, do_not_cache=False) -> Buffer | None:
        """Compatibility API returning a Buffer for the layered resolver result."""
        if not self._is_resource_reference(filepath):
            raw_path = TinyPath(filepath)
            if raw_path.is_absolute():
                raw_path = corrected_path(raw_path)
                return FileBuffer(raw_path) if raw_path.is_file() else None
        if do_not_cache or self._is_resource_reference(filepath):
            return self.open_stream(filepath)

        key = self._key(TinyPath(filepath))
        buffer = self._cache.get(key)
        if buffer is not None and not buffer.closed:
            buffer.seek(0)
            return buffer
        resource = self.find(filepath)
        if resource is None:
            return None
        buffer = resource.open_stream()
        if buffer is None:
            return None
        self._cache.set(key, buffer)
        self._note_hit(key, resource.provider)
        return buffer

    def open(self, path: ResourcePath) -> Buffer | None:
        return self.open_stream(path)

    def open_stream(self, path: ResourcePath) -> Buffer | None:
        resource = self.find(path)
        return resource.open_stream() if resource is not None else None

    def resolve(self, reference: Any) -> Buffer | None:
        """Implement ``library.source2.interfaces.ResourceResolver`` structurally."""
        return self.open_stream(reference)

    def find(self, path: ResourcePath) -> ResolvedResource | None:
        if self._is_resource_reference(path):
            raw_path = getattr(path, "path", None)
            if raw_path is None:
                return None
            source = getattr(path, "source", None)
            source_path = getattr(path, "source_path", None)
            if source is None and source_path:
                source = self._find_single(normalize_resource_path(source_path), None)
        else:
            source = None
            raw_path = path
        locator = split_resource_locator(raw_path)
        if len(locator) > 1:
            return self._find_explicit(locator, source)
        return self._find_single(locator[0], source)

    def iter(self, pattern: str = "*") -> Iterator[ResolvedResource]:
        pattern = normalize_resource_pattern(pattern)
        seen: dict[str, str] = {}
        for provider, layer in self._provider_order(None):
            for actual_path in provider.iter_paths(pattern):
                key = actual_path.as_posix().casefold()
                label = self._provider_label(provider)
                previous = seen.get(key)
                if previous is not None:
                    self._record_collision(
                        CollisionDiagnostic(actual_path, (previous, label), "multiple mounts contain the same resource")
                    )
                    continue
                seen[key] = label
                yield self._make_resolved(actual_path, actual_path, provider, layer)

    @property
    def collision_diagnostics(self) -> tuple[CollisionDiagnostic, ...]:
        diagnostics = dict(self._resolution_collisions)
        providers = [provider for provider, _ in self._provider_order(None)]
        for provider in providers:
            for diagnostic in provider.collision_diagnostics:
                key = (diagnostic.normalized_path.as_posix(), diagnostic.candidates)
                diagnostics[key] = diagnostic
        return tuple(
            sorted(
                diagnostics.values(),
                key=lambda item: (item.normalized_path.as_posix().casefold(), item.candidates),
            )
        )

    def configure_nested_archives(self, *, max_depth: int | None = None, max_size: int | None = None) -> None:
        if max_depth is not None:
            if max_depth < 0:
                raise ValueError("max_depth must be non-negative")
            self.max_nested_archive_depth = max_depth
        if max_size is not None:
            if max_size <= 0:
                raise ValueError("max_size must be positive")
            self.max_nested_archive_size = max_size

    def _find_single(self, path: TinyPath, source: ResolvedResource | None) -> ResolvedResource | None:
        path = normalize_resource_path(path)
        providers = self._provider_order(source)
        hits: list[tuple[ContentProvider, MountLayer, TinyPath]] = []
        for provider, layer in providers:
            actual = provider.resolve_path(path)
            if actual is not None:
                hits.append((provider, layer, actual))
        if hits:
            labels = tuple(self._provider_label(provider) for provider, _, _ in hits)
            if len(labels) > 1:
                self._record_collision(
                    CollisionDiagnostic(path, labels, "multiple mounts contain the same resource")
                )
            provider, layer, actual = hits[0]
            key = self._key(path)
            self._note_hit(key, provider)
            return self._make_resolved(path, actual, provider, layer, labels[1:])

        for provider, _ in providers:
            nested = self._find_automatic_nested(provider, path)
            if nested is not None:
                self._note_hit(self._key(path), nested.provider)
                return nested
        self._note_miss(self._key(path))
        return None

    def _find_explicit(
        self,
        locator: tuple[TinyPath, ...],
        source: ResolvedResource | None,
    ) -> ResolvedResource | None:
        first = locator[0]
        mounted = next(
            (
                provider
                for provider, _ in self._provider_order(source)
                if isinstance(provider, VPKContentProvider)
                and provider.filepath.name.casefold() == first.name.casefold()
            ),
            None,
        )
        if mounted is not None:
            current = mounted
        else:
            archive_resource = self._find_single(first, source)
            if archive_resource is None:
                return None
            current_context = self._get_nested_context(
                archive_resource.provider,
                archive_resource.path,
                explicit=True,
            )
            current = current_context.provider

        for index, segment in enumerate(locator[1:]):
            is_last = index == len(locator) - 2
            if is_last:
                actual = current.resolve_path(segment)
                if actual is None:
                    return None
                return self._make_resolved(segment, actual, current, MountLayer.CURRENT_PACKAGE)
            actual_archive = current.resolve_path(segment)
            if actual_archive is None:
                return None
            current = self._get_nested_context(current, actual_archive, explicit=True).provider
        return None

    def _find_automatic_nested(
        self,
        parent: ContentProvider,
        requested: TinyPath,
    ) -> ResolvedResource | None:
        if requested.suffix.casefold() == ".vpk":
            return None
        archive_path = requested.with_suffix(".vpk")
        actual_archive = parent.resolve_path(archive_path)
        if actual_archive is None:
            return None
        try:
            context = self._get_nested_context(parent, actual_archive, explicit=False)
            candidates = [requested]
            if archive_path.parent != archive_path:
                prefix = archive_path.parent.as_posix().rstrip("/") + "/"
                if requested.as_posix().casefold().startswith(prefix.casefold()):
                    candidates.append(TinyPath(requested.as_posix()[len(prefix):]))
            for candidate in candidates:
                actual = context.provider.resolve_path(candidate)
                if actual is not None:
                    return self._make_resolved(
                        requested,
                        actual,
                        context.provider,
                        MountLayer.CURRENT_PACKAGE,
                    )
        except (NestedArchiveLimitError, VPKFormatError, FileNotFoundError) as ex:
            logger.warn(f"Cannot resolve nested VPK {actual_archive}: {ex}")
        return None

    def _get_nested_context(
        self,
        parent: ContentProvider,
        archive_path: TinyPath,
        *,
        explicit: bool,
    ) -> _NestedContext:
        key = (id(parent), canonical_resource_path(archive_path))
        cached = self._nested_cache.get(key)
        if cached is not None:
            return cached

        parent_context = self._nested_context_for(parent)
        depth = (parent_context.depth if parent_context is not None else 0) + 1
        if depth > self.max_nested_archive_depth:
            error = NestedArchiveLimitError(
                f"Nested VPK depth {depth} exceeds limit {self.max_nested_archive_depth}"
            )
            if explicit:
                raise error
            raise error

        archive_buffer = parent.open_stream(archive_path)
        if archive_buffer is None:
            raise FileNotFoundError(f"Nested VPK {archive_path} disappeared during resolution")
        size = archive_buffer.size()
        if size > self.max_nested_archive_size:
            archive_buffer.close()
            error = NestedArchiveLimitError(
                f"Nested VPK {archive_path} is {size} bytes; limit is {self.max_nested_archive_size}"
            )
            if explicit:
                raise error
            raise error

        chain = self._archive_chain(parent) + (archive_path,)
        provider = VPKContentProvider.from_buffer(
            archive_path,
            archive_buffer,
            parent.steam_id,
            external_opener=parent.open_stream,
        )
        try:
            provider.collision_diagnostics
        except (VPKFormatError, FileNotFoundError):
            archive_buffer.close()
            raise
        logical_size = provider.logical_size
        if logical_size is not None and logical_size > self.max_nested_archive_size:
            archive_buffer.close()
            raise NestedArchiveLimitError(
                f"Nested VPK {archive_path} expands to {logical_size} bytes; "
                f"limit is {self.max_nested_archive_size}"
            )
        context = _NestedContext(provider, parent, archive_path, depth, chain)
        self._nested_cache[key] = context
        self._current_packages.insert(0, context)
        self._cache.clear()
        self._clear_meta_caches()
        return context

    def _make_resolved(
        self,
        requested: TinyPath,
        actual: TinyPath,
        provider: ContentProvider,
        layer: MountLayer,
        collisions: tuple[str, ...] = (),
    ) -> ResolvedResource:
        return ResolvedResource(
            requested,
            actual,
            provider,
            layer,
            self._provider_label(provider),
            self._archive_chain(provider),
            collisions,
            lambda provider=provider, actual=actual: provider.open_stream(actual),
            self._provider_context(provider),
        )

    def _provider_order(
        self,
        source: ResolvedResource | None,
    ) -> list[tuple[ContentProvider, MountLayer]]:
        ordered: list[tuple[ContentProvider, MountLayer]] = []
        seen: set[int] = set()

        if source is not None:
            source_context = source._context
            if not source_context and isinstance(source.provider, (VPKContentProvider, ZIPContentProvider)):
                source_context = (source.provider,)
            for provider in source_context:
                if id(provider) not in seen:
                    ordered.append((provider, MountLayer.CURRENT_PACKAGE))
                    seen.add(id(provider))

        for context in self._current_packages:
            if id(context.provider) not in seen:
                ordered.append((context.provider, MountLayer.CURRENT_PACKAGE))
                seen.add(id(context.provider))

        for provider in self._ordered_children():
            if id(provider) in seen:
                continue
            ordered.append((provider, self._mount_record(provider).layer))
            seen.add(id(provider))
        return ordered

    def _ordered_children(self) -> list[ContentProvider]:
        providers = sorted(
            self.children,
            key=lambda provider: (
                self._mount_record(provider).layer,
                self._mount_record(provider).order,
                provider.filepath.as_posix().casefold(),
                provider.class_name(),
            ),
        )
        if isinstance(self.priority_list, list):
            prioritized = [provider for provider in self.priority_list if provider in self.children]
            return prioritized + [provider for provider in providers if provider not in prioritized]
        if self.first_import is not None and self.priority_list is None:
            import_path = self.first_import.as_posix().casefold().rstrip("/")
            for provider in providers:
                if not isinstance(
                    provider,
                    (Source1GameInfoProvider, Source2GameInfoProvider, LooseFilesContentProvider),
                ):
                    continue
                provider_path = provider.filepath
                if provider_path.is_file():
                    provider_path = provider_path.parent
                prefix = provider_path.as_posix().casefold().rstrip("/") + "/"
                if import_path == prefix[:-1] or import_path.startswith(prefix):
                    self.priority_list = [provider]
                    return [provider] + [item for item in providers if item != provider]
            self.priority_list = False
        return providers

    def _mount_record(self, provider: ContentProvider) -> _MountRecord:
        record = self._mounts.get(provider)
        if record is None:
            record = _MountRecord(self._infer_mount_layer(provider), self._mount_order)
            self._mounts[provider] = record
            self._mount_order += 1
        return record

    @staticmethod
    def _infer_mount_layer(provider: ContentProvider) -> MountLayer:
        parts = {part.casefold() for part in provider.filepath.parts}
        if parts.intersection({"addon", "addons", "custom"}):
            return MountLayer.ADDONS
        if "workshop" in parts:
            return MountLayer.WORKSHOP
        if isinstance(provider, LooseFilesContentProvider):
            return MountLayer.LOOSE_FILES
        if isinstance(provider, (VPKContentProvider, ZIPContentProvider, HFS1ContentProvider, HFS2ContentProvider)):
            return MountLayer.GAME_PACKAGES
        if isinstance(provider, (Source1GameInfoProvider, Source2GameInfoProvider)):
            return MountLayer.SEARCH_PATHS
        return MountLayer.SEARCH_PATHS

    def _nested_context_for(self, provider: ContentProvider) -> _NestedContext | None:
        return next(
            (context for context in self._nested_cache.values() if context.provider is provider),
            None,
        )

    def _provider_context(self, provider: ContentProvider) -> tuple[ContentProvider, ...]:
        context: list[ContentProvider] = []
        current: ContentProvider | None = provider
        while current is not None:
            if isinstance(current, (VPKContentProvider, ZIPContentProvider)):
                context.append(current)
            nested = self._nested_context_for(current)
            current = nested.parent if nested is not None else None
        return tuple(context)

    def _archive_chain(self, provider: ContentProvider) -> tuple[TinyPath, ...]:
        nested = self._nested_context_for(provider)
        if nested is not None:
            return nested.chain
        if isinstance(provider, (VPKContentProvider, ZIPContentProvider)):
            return (provider.filepath,)
        return ()

    @staticmethod
    def _provider_label(provider: ContentProvider) -> str:
        return f"{provider.class_name()}:{provider.filepath.as_posix()}"

    @staticmethod
    def _is_resource_reference(value: Any) -> bool:
        return isinstance(value, ResourceRef) or (
            hasattr(value, "path")
            and hasattr(value, "resource_id")
            and hasattr(value, "source_path")
        )

    def _record_collision(self, diagnostic: CollisionDiagnostic) -> None:
        candidates = tuple(dict.fromkeys(diagnostic.candidates))
        normalized = CollisionDiagnostic(diagnostic.normalized_path, candidates, diagnostic.reason)
        key = (normalized.normalized_path.as_posix().casefold(), normalized.candidates)
        if key not in self._resolution_collisions:
            self._resolution_collisions[key] = normalized
            logger.warn(
                f"Resource collision for {normalized.normalized_path}: "
                + ", ".join(normalized.candidates)
            )

    def _invalidate_resolution(self) -> None:
        self._cache.clear()
        self._clear_meta_caches()
        self._resolution_collisions.clear()

    # TODO: MAYBE DEPRECATED
    def serialize(self):
        """Serialize mounted providers.

        Maps are emitted last. A ``.bsp`` entry is stored by path and, when that
        path is relative, can only be resolved on load through a provider that is
        already mounted -- so it has to come after the game directories and
        archives that might contain it. ``self.children`` is a set, so the ordering
        is imposed here rather than inherited.
        """
        serialized = {}
        providers = sorted(
            self._ordered_children(),
            key=lambda provider: (
                str(provider.filepath).endswith('.bsp'),
                provider.filepath.as_posix().casefold(),
            ),
        )
        for provider in providers:
            name = provider.unique_name.replace('\'', '').replace('\"', '').replace(' ', '_')
            info = {"name": name, "path": str(provider.filepath)}
            serialized[md5(name.encode("utf8")).hexdigest()] = info

        return serialized

    # TODO: MAYBE DEPRECATED
    def deserialize(self, data: dict[str, Union[str, dict]]):
        """Recreate mounts from serialized data.

        Maps are mounted last: a ``.bsp`` recorded with a relative path can only be
        located through an already-mounted provider, and ``data`` is an unordered
        dict, so processing it inline would resolve or fail depending on iteration
        order.
        """
        deferred_maps = []
        for name, item in data.items():
            name = item["name"]
            path = item["path"]
            t_path = TinyPath(path)
            if path.endswith('.vpk'):
                provider = VPKContentProvider(t_path)
                self.add_child(register_provider(provider))
            elif path.endswith('.pk3'):
                provider = ZIPContentProvider(t_path)
                self.add_child(register_provider(provider))
            elif path.endswith('.txt'):
                try:
                    provider = Source1GameInfoProvider(t_path)
                except (ValueError, FileNotFoundError) as ex:
                    logger.exception(f"Failed to parse gameinfo for {t_path}", ex)
                    continue
                self.add_child(register_provider(provider))
            elif path.endswith('.gi'):
                provider = Source2GameInfoProvider(t_path)
                self.add_child(register_provider(provider))
            elif path.endswith('.bsp'):
                deferred_maps.append(t_path)
            elif path.endswith('.hfs'):
                provider = HFS1ContentProvider(t_path)
                self.add_child(register_provider(provider))
            elif name == 'hfs':
                provider = HFS2ContentProvider(t_path)
                self.add_child(register_provider(provider))
            else:
                provider = LooseFilesContentProvider(t_path)
                self.add_child(register_provider(provider))

        for t_path in deferred_maps:
            self._mount_map_pak(t_path)

    def _mount_map_pak(self, map_path: TinyPath):
        """Mount a map's embedded PAK lump, if the map can still be found."""
        from ...source1.bsp.bsp_file import open_bsp
        if map_path.is_absolute():
            full_path = map_path
        else:
            provider = self.get_content_provider_from_asset_path(map_path)
            if provider is None or provider.root is None:
                # Nothing mounted contains this map -- e.g. the game directory it
                # came from is no longer mounted. The embedded PAK only holds
                # map-local overrides, so carry on without it.
                logger.warn(f'Cannot resolve {map_path} to a mounted provider, skipping its embedded PAK')
                return
            full_path = provider.root / map_path
        if not full_path.exists():
            logger.warn(f'Map {full_path} no longer exists, skipping its embedded PAK')
            return
        with FileBuffer(full_path) as f:
            pak_lump = open_bsp(map_path, f, self).get_lump('LUMP_PAK')
        if pak_lump and pak_lump not in self.children:
            self.add_child(register_provider(pak_lump), MountLayer.CURRENT_PACKAGE)

    def clean(self):
        """Reset mounts and caches."""
        for context in self._nested_cache.values():
            source = context.provider._source_buffer
            if source is not None and not source.closed:
                source.close()
        self.children.clear()
        self._mounts.clear()
        self._nested_cache.clear()
        self._current_packages.clear()
        self._resolution_collisions.clear()
        self._mount_order = 0
        self._steam_id = -1
        self.max_nested_archive_depth = DEFAULT_MAX_NESTED_ARCHIVE_DEPTH
        self.max_nested_archive_size = DEFAULT_MAX_NESTED_ARCHIVE_SIZE
        self._cache.clear()
        self._clear_meta_caches()

    @property
    def steam_id(self):
        if self._steam_id != -1:
            return self._steam_id
        used_appids = Counter([child.steam_id for child in self.children if child.steam_id > 0])
        if len(used_appids) == 0:
            return 0
        return used_appids.most_common(1)[0][0]

    def get_content_provider_from_asset_path(self, asset_path: TinyPath) -> Optional[ContentProvider]:
        resource = self.find(asset_path)
        return resource.provider if resource is not None else None

    def _key(self, path: TinyPath) -> TinyPath:
        if path.is_absolute():
            rel = self.get_relative_path(path)
            if rel is None:
                return path
            path = rel
        return TinyPath(normalize_resource_path(path).as_posix().casefold())

    def _note_hit(self, k: TinyPath, owner: ContentProvider) -> None:
        """Mark path as existing and owned by provider."""
        self._exists_cache.set(k, True)
        self._owner_cache.set(k, owner)

    def _note_miss(self, k: TinyPath) -> None:
        """Mark path as non-existent."""
        self._exists_cache.set(k, False)
        self._owner_cache.pop(k, None)

    def _clear_meta_caches(self) -> None:
        """Clear metadata caches."""
        self._exists_cache.clear()
        self._owner_cache.clear()
