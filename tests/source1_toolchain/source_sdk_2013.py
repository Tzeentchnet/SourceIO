from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from SourceIO.library.models.mdl.v49.mdl_file import MdlV49
from SourceIO.library.source1.authoring import (
    Source1ArtifactRoot,
    Source1AuthoringBackend,
    Source1AuthoringPlan,
    Source1AuthoringRequest,
    Source1AuthoringService,
    Source1AuthoringTarget,
)
from SourceIO.library.source1.vtf import load_texture
from SourceIO.library.source2.authoring import (
    AuthoringMaterial,
    AuthoringTexture,
    ExtractedAuthoringModel,
    MaterialAlphaMode,
    TextureRole,
    solid_color_tga,
)
from SourceIO.library.source2.export import (
    CornerData,
    MeshFace,
    MeshVertex,
    ModelDocument,
    StaticMesh,
)
from SourceIO.library.utils import FileBuffer
from SourceIO.library.utils import kv1
from SourceIO.library.utils.kv1 import KV1Block


class SourceSdk2013DiscoveryError(ValueError):
    pass


class SourceSdk2013GateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceSdk2013Profile:
    key: str
    name: str
    install_app_id: int
    content_app_id: int
    game_directory: str
    environment_variable: str
    expected_mdl_versions: tuple[int, ...]
    fingerprint_files: tuple[str, ...]


SINGLEPLAYER = SourceSdk2013Profile(
    "singleplayer",
    "Source SDK Base 2013 Singleplayer",
    243730,
    220,
    "hl2",
    "SOURCEIO_SOURCE_SDK_2013_SP_ROOT",
    (48,),
    (
        "hl2/hl2_misc_dir.vpk",
        "hl2/hl2_textures_dir.vpk",
    ),
)
MULTIPLAYER = SourceSdk2013Profile(
    "multiplayer",
    "Source SDK Base 2013 Multiplayer",
    243750,
    320,
    "hl2mp",
    "SOURCEIO_SOURCE_SDK_2013_MP_ROOT",
    (48,),
    (
        "hl2mp/hl2mp_pak_dir.vpk",
        "hl2/hl2_misc_dir.vpk",
        "hl2/hl2_textures_dir.vpk",
    ),
)
SOURCE_SDK_2013_PROFILES = (SINGLEPLAYER, MULTIPLAYER)
SOURCE_SDK_2013_PROFILE_BY_KEY = {
    profile.key: profile
    for profile in SOURCE_SDK_2013_PROFILES
}


def _require_block(value, label: str) -> KV1Block:
    if not isinstance(value, KV1Block):
        raise SourceSdk2013DiscoveryError(f"{label} is not a KeyValues block")
    return value


def _require_integer(value, label: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise SourceSdk2013DiscoveryError(
            f"{label} must be an integer, got {value!r}"
        ) from exc
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path: Path, *, relative_to: Path | None = None) -> dict:
    resolved = path.resolve(strict=False)
    display_path = resolved
    if relative_to is not None:
        try:
            display_path = resolved.relative_to(relative_to.resolve(strict=False))
        except ValueError:
            pass
    if not resolved.is_file():
        return {
            "path": display_path.as_posix(),
            "exists": False,
        }
    stat = resolved.stat()
    return {
        "path": display_path.as_posix(),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(resolved),
    }


@dataclass(frozen=True, slots=True)
class SourceSdk2013Installation:
    profile: SourceSdk2013Profile
    root: Path
    steamapps_root: Path
    manifest_path: Path
    game_root: Path
    tool_root: Path
    build_id: str

    @classmethod
    def discover(
            cls,
            profile: SourceSdk2013Profile,
            root: str | os.PathLike[str],
    ) -> SourceSdk2013Installation:
        if not isinstance(profile, SourceSdk2013Profile):
            raise TypeError("profile must be a SourceSdk2013Profile")
        try:
            resolved_root = Path(root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} root is unavailable: {root}"
            ) from exc
        if not resolved_root.is_dir():
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} root is not a directory: {resolved_root}"
            )
        common_root = resolved_root.parent
        if common_root.name.casefold() != "common":
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} root must be below a Steam steamapps/common "
                f"directory: {resolved_root}"
            )
        steamapps_root = common_root.parent
        manifest_path = steamapps_root / f"appmanifest_{profile.install_app_id}.acf"
        if not manifest_path.is_file():
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} Steam manifest is unavailable: {manifest_path}"
            )
        try:
            root_key, app_state_value = kv1.load(
                manifest_path,
                escapes=True,
            ).top()
        except Exception as exc:
            raise SourceSdk2013DiscoveryError(
                f"Failed to parse {profile.name} Steam manifest: {manifest_path}"
            ) from exc
        if root_key.casefold() != "appstate":
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} Steam manifest has root {root_key!r}, "
                "expected 'AppState'"
            )
        app_state = _require_block(
            app_state_value,
            f"{profile.name} Steam manifest AppState",
        )
        manifest_app_id = _require_integer(
            app_state.get("appid"),
            f"{profile.name} manifest app ID",
        )
        if manifest_app_id != profile.install_app_id:
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} manifest app ID is {manifest_app_id}, "
                f"expected {profile.install_app_id}"
            )
        install_directory = str(app_state.get("installdir", "")).strip()
        if not install_directory:
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} manifest has no installdir"
            )
        manifest_root = (common_root / install_directory).resolve(strict=False)
        if manifest_root != resolved_root:
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} manifest resolves to {manifest_root}, "
                f"not the configured root {resolved_root}"
            )
        build_id = str(app_state.get("buildid", "")).strip()
        if not build_id.isdigit():
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} manifest has invalid build ID {build_id!r}"
            )

        game_root = resolved_root / profile.game_directory
        gameinfo_path = game_root / "gameinfo.txt"
        if not gameinfo_path.is_file():
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} gameinfo.txt is unavailable: {gameinfo_path}"
            )
        try:
            game_key, game_value = kv1.load(gameinfo_path).top()
        except Exception as exc:
            raise SourceSdk2013DiscoveryError(
                f"Failed to parse {profile.name} gameinfo.txt: {gameinfo_path}"
            ) from exc
        if game_key.casefold() != "gameinfo":
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} gameinfo has root {game_key!r}, "
                "expected 'GameInfo'"
            )
        game_info = _require_block(game_value, f"{profile.name} GameInfo")
        file_system = _require_block(
            game_info.get("filesystem"),
            f"{profile.name} GameInfo/FileSystem",
        )
        content_app_id = _require_integer(
            file_system.get("steamappid"),
            f"{profile.name} content SteamAppId",
        )
        if content_app_id != profile.content_app_id:
            raise SourceSdk2013DiscoveryError(
                f"{profile.name} content SteamAppId is {content_app_id}, "
                f"expected {profile.content_app_id}"
            )

        tool_root = resolved_root / "bin"
        for tool_name in ("vtex.exe", "studiomdl.exe"):
            tool_path = tool_root / tool_name
            if not tool_path.is_file():
                raise SourceSdk2013DiscoveryError(
                    f"{profile.name} compiler is unavailable: {tool_path}"
                )
        for relative_path in profile.fingerprint_files:
            fingerprint_path = resolved_root.joinpath(*relative_path.split("/"))
            if not fingerprint_path.is_file():
                raise SourceSdk2013DiscoveryError(
                    f"{profile.name} content index is unavailable: "
                    f"{fingerprint_path}"
                )

        return cls(
            profile,
            resolved_root,
            steamapps_root,
            manifest_path,
            game_root,
            tool_root,
            build_id,
        )

    def installation_report(self) -> dict:
        files = {
            "steam_manifest": file_fingerprint(
                self.manifest_path,
                relative_to=self.steamapps_root,
            ),
            "gameinfo": file_fingerprint(
                self.game_root / "gameinfo.txt",
                relative_to=self.root,
            ),
            "vtex.exe": file_fingerprint(
                self.tool_root / "vtex.exe",
                relative_to=self.root,
            ),
            "studiomdl.exe": file_fingerprint(
                self.tool_root / "studiomdl.exe",
                relative_to=self.root,
            ),
        }
        for relative_path in self.profile.fingerprint_files:
            files[relative_path] = file_fingerprint(
                self.root.joinpath(*relative_path.split("/")),
                relative_to=self.root,
            )
        return {
            "profile": self.profile.key,
            "name": self.profile.name,
            "install_app_id": self.profile.install_app_id,
            "content_app_id": self.profile.content_app_id,
            "build_id": self.build_id,
            "install_root": str(self.root),
            "game_root": str(self.game_root),
            "tool_root": str(self.tool_root),
            "files": files,
        }


def configured_installation(
        profile: SourceSdk2013Profile,
) -> SourceSdk2013Installation | None:
    configured_root = os.environ.get(profile.environment_variable)
    if not configured_root:
        return None
    return SourceSdk2013Installation.discover(profile, configured_root)


def _fixture_model(asset_name: str) -> ExtractedAuthoringModel:
    vertices = (
        MeshVertex(
            (0.0, 0.0, 0.0),
            CornerData(normal=(0.0, 0.0, 1.0), texcoords=((0.0, 1.0),)),
        ),
        MeshVertex(
            (16.0, 0.0, 0.0),
            CornerData(normal=(0.0, 0.0, 1.0), texcoords=((1.0, 1.0),)),
        ),
        MeshVertex(
            (0.0, 8.0, 0.0),
            CornerData(normal=(0.0, 0.0, 1.0), texcoords=((0.0, 0.0),)),
        ),
    )
    document = ModelDocument(
        asset_name,
        (
            StaticMesh(
                f"{asset_name}_mesh",
                vertices,
                (MeshFace((0, 1, 2), "probe"),),
            ),
        ),
    )
    material = AuthoringMaterial(
        "probe",
        alpha_mode=MaterialAlphaMode.MASK,
        alpha_cutoff=0.4,
        textures=(
            AuthoringTexture(
                "probe_base_color",
                TextureRole.BASE_COLOR,
                solid_color_tga((0.8, 0.2, 0.1, 0.5)),
                extension=".tga",
                srgb=True,
            ),
            AuthoringTexture(
                "probe_alpha",
                TextureRole.ALPHA,
                solid_color_tga((0.5, 0.5, 0.5, 1.0)),
                extension=".tga",
            ),
            AuthoringTexture(
                "probe_normal",
                TextureRole.NORMAL,
                solid_color_tga((0.5, 0.5, 1.0, 1.0)),
                extension=".tga",
            ),
        ),
    )
    return ExtractedAuthoringModel(
        asset_name,
        document,
        b"",
        (material,),
    )


def _quote_gameinfo_path(path: Path) -> str:
    value = str(path.resolve(strict=False))
    if '"' in value or "\n" in value or "\r" in value:
        raise SourceSdk2013GateError(
            f"SDK fixture path cannot be represented in gameinfo.txt: {path}"
        )
    return value


def _fixture_gameinfo(installation: SourceSdk2013Installation) -> str:
    profile = installation.profile
    search_paths = [
        ("game+mod+mod_write+default_write_path", "|gameinfo_path|."),
        ("gamebin", installation.tool_root),
        ("game", installation.game_root),
    ]
    if profile.key == MULTIPLAYER.key:
        for shared_directory in ("hl2_complete", "hl2"):
            shared_root = installation.root / shared_directory
            if shared_root.is_dir():
                search_paths.append(("game", shared_root))
    search_paths.append(("platform", installation.root / "platform"))
    lines = [
        '"GameInfo"',
        "{",
        f'    "game" "SourceIO {profile.name} Gate"',
        (
            '    "type" "singleplayer_only"'
            if profile is SINGLEPLAYER
            else '    "type" "multiplayer_only"'
        ),
        '    "FileSystem"',
        "    {",
        f'        "SteamAppId" "{profile.install_app_id}"',
        '        "SearchPaths"',
        "        {",
    ]
    for kind, path in search_paths:
        path_value = path if isinstance(path, str) else _quote_gameinfo_path(path)
        lines.append(f'            "{kind}" "{path_value}"')
    lines.extend((
        "        }",
        "    }",
        "}",
        "",
    ))
    return "\n".join(lines)


@dataclass(slots=True)
class SourceSdk2013Fixture:
    installation: SourceSdk2013Installation
    workspace: Path
    game_root: Path
    content_root: Path
    target: Source1AuthoringTarget
    asset_name: str
    service: Source1AuthoringService
    plan: Source1AuthoringPlan

    @property
    def model_relative_path(self) -> str:
        return f"models/sourceio/{self.asset_name}/{self.asset_name}.mdl"

    @property
    def model_path(self) -> Path:
        return self.target.game_path(self.model_relative_path)


def create_disposable_fixture(
        installation: SourceSdk2013Installation,
        workspace: str | os.PathLike[str],
        *,
        asset_name: str | None = None,
) -> SourceSdk2013Fixture:
    if not isinstance(installation, SourceSdk2013Installation):
        raise TypeError("installation must be a SourceSdk2013Installation")
    resolved_workspace = Path(workspace).expanduser().resolve(strict=False)
    resolved_workspace.mkdir(parents=True, exist_ok=False)
    game_root = resolved_workspace / "game"
    content_root = resolved_workspace / "content"
    game_root.mkdir()
    content_root.mkdir()
    (game_root / "gameinfo.txt").write_text(
        _fixture_gameinfo(installation),
        encoding="utf-8",
    )
    target = Source1AuthoringTarget(game_root, content_root)
    default_asset_names = {
        SINGLEPLAYER.key: "s1sdk_sp",
        MULTIPLAYER.key: "s1sdk_mp",
    }
    try:
        default_asset_name = default_asset_names[installation.profile.key]
    except KeyError as exc:
        raise SourceSdk2013GateError(
            f"Unsupported Source SDK 2013 profile: "
            f"{installation.profile.key}"
        ) from exc
    resolved_asset_name = asset_name or default_asset_name
    request = Source1AuthoringRequest(
        target,
        resolved_asset_name,
        compile_after_export=True,
    )
    service = Source1AuthoringService()
    backend = Source1AuthoringBackend().build(
        request,
        (_fixture_model(resolved_asset_name),),
    )
    plan = service.plan(request, backend)
    plan.raise_for_errors()
    return SourceSdk2013Fixture(
        installation,
        resolved_workspace,
        game_root,
        content_root,
        target,
        resolved_asset_name,
        service,
        plan,
    )


def _fixture_path(fixture: SourceSdk2013Fixture, path: Path) -> str:
    resolved = path.resolve(strict=False)
    for label, root in (
        ("game", fixture.game_root),
        ("content", fixture.content_root),
    ):
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        return f"{label}/{relative.as_posix()}"
    return str(resolved)


def _compiler_report(fixture: SourceSdk2013Fixture, result) -> dict:
    return {
        "command": list(result.command),
        "source": _fixture_path(fixture, result.source),
        "expected_outputs": [
            _fixture_path(fixture, path)
            for path in result.expected_outputs
        ],
        "returncode": result.returncode,
        "stdout_bytes": result.stdout_bytes,
        "stderr_bytes": result.stderr_bytes,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "log_truncated": result.log_truncated,
        "error_markers": list(result.error_markers),
        "duration_seconds": result.duration_seconds,
    }


def execute_disposable_fixture(fixture: SourceSdk2013Fixture) -> dict:
    result = fixture.service.execute(
        fixture.plan,
        tool_root=fixture.installation.tool_root,
    )
    if len(result.compiler_results) != 3:
        raise SourceSdk2013GateError(
            f"{fixture.installation.profile.name} ran "
            f"{len(result.compiler_results)} compiler steps, expected 3"
        )
    model_path = fixture.model_path
    with FileBuffer(model_path) as buffer:
        mdl = MdlV49.from_buffer(buffer)
    expected_model_name = (
        f"sourceio/{fixture.asset_name}/{fixture.asset_name}.mdl"
    )
    actual_model_name = mdl.header.name.replace("\\", "/")
    if actual_model_name != expected_model_name:
        raise SourceSdk2013GateError(
            f"Compiled model name is {actual_model_name!r}, "
            f"expected {expected_model_name!r}"
        )
    if mdl.header.version not in fixture.installation.profile.expected_mdl_versions:
        raise SourceSdk2013GateError(
            f"Compiled model version is {mdl.header.version}, expected one of "
            f"{fixture.installation.profile.expected_mdl_versions}"
        )

    texture_report = {}
    material_directory = fixture.target.game_path(
        f"materials/models/sourceio/{fixture.asset_name}"
    )
    expected_texture_values = {
        "probe_base_color": (204, 51, 26, 128),
        "probe_normal": (128, 128, 255, 255),
    }
    for texture_name, expected_values in expected_texture_values.items():
        texture_path = material_directory / f"{texture_name}.vtf"
        with texture_path.open("rb") as stream:
            pixels, height, width = load_texture(stream, normalized=False)
        if (width, height) != (4, 4) or pixels.shape != (4, 4, 4):
            raise SourceSdk2013GateError(
                f"Compiled texture {texture_name} decoded as "
                f"{width}x{height} {pixels.shape}, expected 4x4 RGBA"
            )
        channel_min = [
            int(pixels[..., index].min())
            for index in range(pixels.shape[2])
        ]
        channel_max = [
            int(pixels[..., index].max())
            for index in range(pixels.shape[2])
        ]
        if channel_min != channel_max:
            raise SourceSdk2013GateError(
                f"Compiled texture {texture_name} is not uniformly colored: "
                f"{channel_min}..{channel_max}"
            )
        maximum_channel_error = max(
            abs(actual - expected)
            for actual, expected in zip(channel_min, expected_values)
        )
        if maximum_channel_error > 8:
            raise SourceSdk2013GateError(
                f"Compiled texture {texture_name} differs from the generated "
                f"source by {maximum_channel_error} channel values"
            )
        texture_report[texture_name] = {
            "width": width,
            "height": height,
            "channels": pixels.shape[2],
            "expected_rgba": list(expected_values),
            "decoded_rgba": channel_min,
            "maximum_channel_error": maximum_channel_error,
        }

    recompiled = fixture.service.recompile(
        fixture.target,
        result.manifest_path,
        tool_root=fixture.installation.tool_root,
    )
    if len(recompiled) != 3:
        raise SourceSdk2013GateError(
            f"{fixture.installation.profile.name} recompiled "
            f"{len(recompiled)} steps, expected 3"
        )

    source_files = {}
    for artifact in fixture.plan.artifacts:
        if artifact.root != Source1ArtifactRoot.CONTENT:
            continue
        path = fixture.target.content_path(artifact.path)
        source_files[artifact.path.as_posix()] = file_fingerprint(
            path,
            relative_to=fixture.content_root,
        )
    output_files = {}
    for step in fixture.plan.compile_steps:
        for output in step.expected_outputs:
            path = fixture.target.game_path(output.path)
            output_files[output.path.as_posix()] = file_fingerprint(
                path,
                relative_to=fixture.game_root,
            )
    if not all(entry["exists"] for entry in output_files.values()):
        raise SourceSdk2013GateError(
            f"{fixture.installation.profile.name} fixture outputs are incomplete"
        )

    return {
        "status": "PASS",
        "asset_name": fixture.asset_name,
        "workspace": str(fixture.workspace),
        "game_root": str(fixture.game_root),
        "content_root": str(fixture.content_root),
        "model": fixture.model_relative_path,
        "sources": source_files,
        "outputs": output_files,
        "compile": [
            _compiler_report(fixture, compiler_result)
            for compiler_result in result.compiler_results
        ],
        "recompile": [
            _compiler_report(fixture, compiler_result)
            for compiler_result in recompiled
        ],
        "reimport": {
            "mdl_name": actual_model_name,
            "mdl_version": mdl.header.version,
            "textures": texture_report,
        },
    }
