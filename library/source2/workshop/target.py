from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

from ...shared.app_id import SteamAppId


class WorkshopTargetError(ValueError):
    pass


class UnsafeWorkshopPathError(WorkshopTargetError):
    pass


class WorkshopGame(str, Enum):
    DOTA_2 = "dota2"
    COUNTER_STRIKE_2 = "cs2"


@dataclass(frozen=True, slots=True, order=True)
class ContentRelativePath:
    path: PurePosixPath

    def __init__(self, value: str | os.PathLike[str] | ContentRelativePath):
        if isinstance(value, ContentRelativePath):
            normalized = value.path
        else:
            raw = os.fspath(value)
            if not isinstance(raw, str):
                raise TypeError("Content-relative paths must be text paths")
            raw = raw.replace("\\", "/")
            if not raw or raw.startswith("/"):
                raise UnsafeWorkshopPathError(
                    f"Content path must be non-empty and relative: {value!r}"
                )
            parts = raw.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise UnsafeWorkshopPathError(
                    f"Content path must already be normalized: {value!r}"
                )
            if any(":" in part or "\0" in part for part in parts):
                raise UnsafeWorkshopPathError(
                    f"Content path contains an invalid drive or stream marker: {value!r}"
                )
            if any(any(ord(character) < 32 for character in part) for part in parts):
                raise UnsafeWorkshopPathError(
                    f"Content path contains control characters: {value!r}"
                )
            normalized = PurePosixPath(*parts)
        object.__setattr__(self, "path", normalized)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suffix(self) -> str:
        return self.path.suffix

    @property
    def parent(self) -> PurePosixPath:
        return self.path.parent

    @property
    def parts(self) -> tuple[str, ...]:
        return self.path.parts

    def as_posix(self) -> str:
        return self.path.as_posix()

    def compiled(self) -> ContentRelativePath:
        return ContentRelativePath(f"{self.as_posix()}_c")

    def joinpath(self, *parts: str | os.PathLike[str]) -> ContentRelativePath:
        suffix = "/".join(os.fspath(part).replace("\\", "/") for part in parts)
        return ContentRelativePath(f"{self.as_posix()}/{suffix}")

    def __fspath__(self) -> str:
        return self.as_posix()

    def __str__(self) -> str:
        return self.as_posix()


@dataclass(frozen=True, slots=True)
class Source2StaticAuthoringContract:
    modeldoc_version: int
    model_archetype: str
    render_mesh_class: str
    render_mesh_suffix: str
    render_mesh_uses_import_filter: bool
    compiled_render_mesh_is_embedded: bool
    material_shader: str
    source_texture_suffix: str
    source_texture_is_compiler_input: bool
    compiled_texture_suffix: str
    default_material_group_class: str
    compiler_arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Source2WorkshopLayout:
    game: WorkshopGame
    display_name: str
    app_id: SteamAppId
    content_addons_path: ContentRelativePath
    game_addons_path: ContentRelativePath
    game_directory_path: ContentRelativePath
    resource_compiler_path: ContentRelativePath = field(
        default=ContentRelativePath("game/bin/win64/resourcecompiler.exe")
    )
    static_authoring_contract: Source2StaticAuthoringContract | None = None

    @property
    def gameinfo_path(self) -> ContentRelativePath:
        return self.game_directory_path.joinpath("gameinfo.gi")


DOTA2_WORKSHOP_LAYOUT = Source2WorkshopLayout(
    game=WorkshopGame.DOTA_2,
    display_name="Dota 2",
    app_id=SteamAppId.DOTA_2,
    content_addons_path=ContentRelativePath("content/dota_addons"),
    game_addons_path=ContentRelativePath("game/dota_addons"),
    game_directory_path=ContentRelativePath("game/dota"),
)

CS2_STATIC_AUTHORING_CONTRACT = Source2StaticAuthoringContract(
    modeldoc_version=36,
    model_archetype="static_prop_model",
    render_mesh_class="RenderMeshFile",
    render_mesh_suffix=".fbx",
    render_mesh_uses_import_filter=True,
    compiled_render_mesh_is_embedded=True,
    material_shader="csgo_complex.vfx",
    source_texture_suffix=".tga",
    source_texture_is_compiler_input=False,
    compiled_texture_suffix=".vtex_c",
    default_material_group_class="DefaultMaterialGroup",
    compiler_arguments=("-fshallow2", "-nop4"),
)

CS2_WORKSHOP_LAYOUT = Source2WorkshopLayout(
    game=WorkshopGame.COUNTER_STRIKE_2,
    display_name="Counter-Strike 2",
    app_id=SteamAppId.COUNTER_STRIKE_GO,
    content_addons_path=ContentRelativePath("content/csgo_addons"),
    game_addons_path=ContentRelativePath("game/csgo_addons"),
    game_directory_path=ContentRelativePath("game/csgo"),
    static_authoring_contract=CS2_STATIC_AUTHORING_CONTRACT,
)

_ADDON_NAME = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _resolved_beneath(root: Path, relative_path: ContentRelativePath, label: str) -> Path:
    resolved_root = root.resolve(strict=False)
    candidate = root.joinpath(*relative_path.parts)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafeWorkshopPathError(
            f"{label} escapes its configured root: {relative_path}"
        ) from exc
    return resolved_candidate


@dataclass(frozen=True, slots=True)
class WorkshopTargetProfile:
    layout: Source2WorkshopLayout
    install_root: Path
    addon_name: str
    _content_addons_root: Path = field(init=False, repr=False)
    _game_addons_root: Path = field(init=False, repr=False)
    _content_addon_root: Path = field(init=False, repr=False)
    _game_addon_root: Path = field(init=False, repr=False)
    _game_directory: Path = field(init=False, repr=False)
    _gameinfo_path: Path = field(init=False, repr=False)
    _resource_compiler: Path = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.layout, Source2WorkshopLayout):
            raise TypeError("layout must be a Source2WorkshopLayout")
        if not isinstance(self.addon_name, str) or not _ADDON_NAME.fullmatch(self.addon_name):
            raise WorkshopTargetError(
                "Addon name must contain only lowercase ASCII letters, digits, and underscores"
            )

        try:
            install_root = Path(self.install_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise WorkshopTargetError(
                f"{self.layout.display_name} install root is unavailable: {self.install_root}"
            ) from exc
        if not install_root.is_dir():
            raise WorkshopTargetError(
                f"{self.layout.display_name} install root is not a directory: {install_root}"
            )
        object.__setattr__(self, "install_root", install_root)

        content_addons_root = _resolved_beneath(
            install_root, self.layout.content_addons_path, "Content addons directory"
        )
        game_addons_root = _resolved_beneath(
            install_root, self.layout.game_addons_path, "Game addons directory"
        )
        game_directory = _resolved_beneath(
            install_root, self.layout.game_directory_path, "Game directory"
        )
        gameinfo_path = _resolved_beneath(
            install_root, self.layout.gameinfo_path, "GameInfo path"
        )
        resource_compiler = _resolved_beneath(
            install_root, self.layout.resource_compiler_path, "Resource Compiler path"
        )

        required_directories = (
            ("content addons directory", content_addons_root),
            ("game addons directory", game_addons_root),
            ("game directory", game_directory),
        )
        for label, path in required_directories:
            if not path.is_dir():
                raise WorkshopTargetError(
                    f"{self.layout.display_name} {label} is unavailable: {path}"
                )
        required_files = (
            ("gameinfo.gi", gameinfo_path),
            ("resourcecompiler.exe", resource_compiler),
        )
        for label, path in required_files:
            if not path.is_file():
                raise WorkshopTargetError(
                    f"{self.layout.display_name} {label} is unavailable: {path}"
                )

        addon_relative = ContentRelativePath(self.addon_name)
        content_addon_root = _resolved_beneath(
            content_addons_root, addon_relative, "Content addon root"
        )
        game_addon_root = _resolved_beneath(
            game_addons_root, addon_relative, "Game addon root"
        )

        object.__setattr__(self, "_content_addons_root", content_addons_root)
        object.__setattr__(self, "_game_addons_root", game_addons_root)
        object.__setattr__(self, "_content_addon_root", content_addon_root)
        object.__setattr__(self, "_game_addon_root", game_addon_root)
        object.__setattr__(self, "_game_directory", game_directory)
        object.__setattr__(self, "_gameinfo_path", gameinfo_path)
        object.__setattr__(self, "_resource_compiler", resource_compiler)

    @classmethod
    def dota2(
            cls,
            install_root: str | os.PathLike[str],
            addon_name: str,
    ) -> WorkshopTargetProfile:
        return cls(DOTA2_WORKSHOP_LAYOUT, Path(install_root), addon_name)

    @classmethod
    def cs2(
            cls,
            install_root: str | os.PathLike[str],
            addon_name: str,
    ) -> WorkshopTargetProfile:
        return cls(CS2_WORKSHOP_LAYOUT, Path(install_root), addon_name)

    @property
    def game(self) -> WorkshopGame:
        return self.layout.game

    @property
    def app_id(self) -> SteamAppId:
        return self.layout.app_id

    @property
    def static_authoring_contract(self) -> Source2StaticAuthoringContract | None:
        return self.layout.static_authoring_contract

    @property
    def content_addons_root(self) -> Path:
        return self._content_addons_root

    @property
    def game_addons_root(self) -> Path:
        return self._game_addons_root

    @property
    def content_addon_root(self) -> Path:
        return self._content_addon_root

    @property
    def game_addon_root(self) -> Path:
        return self._game_addon_root

    @property
    def game_directory(self) -> Path:
        return self._game_directory

    @property
    def gameinfo_path(self) -> Path:
        return self._gameinfo_path

    @property
    def resource_compiler(self) -> Path:
        return self._resource_compiler

    def content_path(
            self,
            relative_path: str | os.PathLike[str] | ContentRelativePath,
    ) -> Path:
        normalized = ContentRelativePath(relative_path)
        return _resolved_beneath(
            self.content_addon_root, normalized, "Content asset path"
        )

    def game_path(
            self,
            relative_path: str | os.PathLike[str] | ContentRelativePath,
    ) -> Path:
        normalized = ContentRelativePath(relative_path)
        return _resolved_beneath(
            self.game_addon_root, normalized, "Compiled asset path"
        )

    def content_relative(
            self,
            path: str | os.PathLike[str] | ContentRelativePath,
    ) -> ContentRelativePath:
        if isinstance(path, ContentRelativePath):
            return path
        candidate = Path(path)
        if not candidate.is_absolute():
            return ContentRelativePath(path)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_root = self.content_addon_root.resolve(strict=False)
        try:
            relative = resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise UnsafeWorkshopPathError(
                f"Path is outside the content addon root: {path}"
            ) from exc
        return ContentRelativePath(PurePosixPath(*relative.parts))

    def expected_output(
            self,
            source: str | os.PathLike[str] | ContentRelativePath,
    ) -> Path:
        return self.game_path(self.content_relative(source).compiled())
