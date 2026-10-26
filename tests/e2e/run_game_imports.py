"""Import assets straight from an installed Source 1 or Source 2 game and report what each importer produced.

Usage:
    blender -b --factory-startup --python tests/e2e/run_game_imports.py -- \
        --game "<steam>/common/Team Fortress 2/tf" [--model models/player/heavy.mdl ...] [--map ctf_2fort ...] \
        [--include-animations] [--load-placeholders] [--json FILE] [--save FILE.blend]
    blender -b --factory-startup --python tests/e2e/run_game_imports.py -- \
        --game "<steam>/common/Counter-Strike Global Offensive/game/csgo" [--model models/chicken/chicken.vmdl_c ...] \
        [--map de_dust2 ...] [--include-animations] [--clips "idle*,run_n_*"]
    blender -b --factory-startup --python tests/e2e/run_game_imports.py -- \
        --game "<steam>/common/dota 2 beta/game/dota" [--model models/heroes/abaddon/abaddon.vmdl_c ...] \
        [--material materials/models/heroes/abaddon/abaddon_body.vmat_c ...] \
        [--texture panorama/images/heroes/npc_dota_hero_abaddon_png.vtex_c ...] [--map dota]

The game folder holds gameinfo.txt (Source 1) or gameinfo.gi (Source 2). With no explicit assets, a
profile-specific TF2, Source SDK Base 2013, CS2 or Dota 2 corpus is used. Assets are read through the
game's own search paths (VPKs included), copied into a temporary folder, and imported with the game still
mounted so dependencies resolve the way they do for a user. Source 1 maps can be loose or VPK-backed;
Source 2 map VPKs are imported in place. --include-animations imports Source 1 include-model animations
or Source 2 model animations.
The default Dota profile enables model animations because they are part of its acceptance gate: a hero or
creep model that has or references animations must import at least one action.
--load-placeholders runs *Load Entity* on every prop placeholder left by a Source 2 map. --clips imports
matching Source 2 animation-graph clips and implies --include-animations; "*" can import thousands.
--save keeps the scene the last import left. Results use the same basic PASS/KNOWN/WARN/FAIL rules as
run_sample_imports.py plus profile-specific minimum assertions; KNOWN is limited to explicit
profile/asset diagnostics, and exit code is 1 if anything failed.
Set SOURCEIO_PERF_TRACE=1 before starting Blender to print bounded aggregate import timings and include
them in the JSON report's optional ``performance`` object.
"""
import argparse
import hashlib
import importlib
import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_sample_imports as samples  # noqa: E402

TF2_MODELS = [
    "models/player/heavy.mdl",  # flexes, include models
    "models/player/hwm/heavy.mdl",  # high-quality facial flexes
    "models/player/scout.mdl",
    "models/player/spy.mdl",
    "models/weapons/c_models/c_minigun/c_minigun.mdl",
    "models/buildables/sentry3.mdl",
    "models/bots/heavy/bot_heavy.mdl",
    "models/props_gameplay/resupply_locker.mdl",
    "models/dog.mdl",  # HL2 content, v44 with v48 include models and .ani blocks
]
TF2_MAPS = ["ctf_2fort", "cp_badlands", "koth_harvest_final", "pl_upward"]
SOURCE_SDK_2013_MODELS = [
    "models/dog.mdl",  # include models, external ANI blocks, flexes
    "models/alyx.mdl",  # skinned character and facial flexes
    "models/props_c17/oildrum001.mdl",  # static prop, physics, materials
]
SOURCE_SDK_2013_MAPS = ["background01", "test_hardware"]
SOURCE_SDK_2013_KNOWN_ERRORS = {
    map_name: (
        r"Image format conversion not supported",
        r"Failed to load Skybox",
        r"SkyboxException: Failed to load texture skybox/sky_lostcoast_hdrbk",
    )
    for map_name in SOURCE_SDK_2013_MAPS
}
CS2_MODELS = [
    "agents/models/ctm_sas/ctm_sas.vmdl_c",  # characters/models/ holds placeholder stubs
    "agents/models/tm_phoenix/tm_phoenix.vmdl_c",
    "agents/models/shared/arms/glove_hardknuckle/glove_hardknuckle.vmdl_c",
    "weapons/models/ak47/weapon_rif_ak47.vmdl_c",
    "weapons/models/glock18/weapon_pist_glock18.vmdl_c",
    "weapons/models/knife/knife_karambit/weapon_knife_karambit.vmdl_c",
    "models/chicken/chicken.vmdl_c",
    "models/hostage/hostage.vmdl_c",
    "models/props/de_dust/hr_dust/dust_crates/dust_crate_assembly_100x100_01.vmdl_c",
]
CS2_MAPS = ["de_dust2", "de_inferno", "cs_office"]
DOTA_MODELS = [
    "models/heroes/abaddon/abaddon.vmdl_c",
    "models/props_gameplay/aegis.vmdl_c",
    "models/creeps/lane_creeps/creep_2021_dire/creep_2021_dire_melee.vmdl_c",
]
DOTA_MATERIALS = [
    "materials/models/heroes/abaddon/abaddon_body.vmat_c",
    "materials/models/heroes/bane/grip_color.vmat_c",
]
DOTA_MATERIAL_SHADERS = {
    DOTA_MATERIALS[0]: "hero.vfx",
    DOTA_MATERIALS[1]: "global_lit_simple.vfx",
}
DOTA_TEXTURES = ["panorama/images/heroes/npc_dota_hero_abaddon_png.vtex_c"]
DOTA_MAPS = ["dota"]
COMPANIONS = (".vvd", ".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx", ".phy", ".ani")


@dataclass(frozen=True)
class GameProfile:
    key: str
    name: str
    app_id: int
    source2: bool
    models: tuple[str, ...]
    maps: tuple[str, ...]
    materials: tuple[str, ...] = ()
    textures: tuple[str, ...] = ()
    default_animations: bool = False
    fingerprint_files: tuple[str, ...] = ()
    install_fingerprint_files: tuple[str, ...] = ()


GAME_PROFILES = {
    440: GameProfile(
        "tf2",
        "Team Fortress 2",
        440,
        False,
        tuple(TF2_MODELS),
        tuple(TF2_MAPS),
        fingerprint_files=("tf2_misc_dir.vpk", "tf2_textures_dir.vpk"),
    ),
    243730: GameProfile(
        "sdk2013_sp",
        "Source SDK Base 2013 Singleplayer",
        243730,
        False,
        tuple(SOURCE_SDK_2013_MODELS),
        tuple(SOURCE_SDK_2013_MAPS),
        default_animations=True,
        fingerprint_files=("sourcetest_pak_dir.vpk",),
        install_fingerprint_files=(
            "bin/vtex.exe",
            "bin/studiomdl.exe",
            "hl2/hl2_misc_dir.vpk",
            "hl2/hl2_textures_dir.vpk",
        ),
    ),
    243750: GameProfile(
        "sdk2013_mp",
        "Source SDK Base 2013 Multiplayer",
        243750,
        False,
        tuple(SOURCE_SDK_2013_MODELS),
        tuple(SOURCE_SDK_2013_MAPS),
        default_animations=True,
        fingerprint_files=("sourcetest_pak_dir.vpk",),
        install_fingerprint_files=(
            "bin/vtex.exe",
            "bin/studiomdl.exe",
            "hl2mp/hl2mp_pak_dir.vpk",
            "hl2/hl2_misc_dir.vpk",
            "hl2/hl2_textures_dir.vpk",
        ),
    ),
    730: GameProfile(
        "cs2",
        "Counter-Strike 2",
        730,
        True,
        tuple(CS2_MODELS),
        tuple(CS2_MAPS),
        fingerprint_files=("pak01_dir.vpk", "shaders_pc_dir.vpk"),
    ),
    570: GameProfile(
        "dota",
        "Dota 2",
        570,
        True,
        tuple(DOTA_MODELS),
        tuple(DOTA_MAPS),
        tuple(DOTA_MATERIALS),
        tuple(DOTA_TEXTURES),
        True,
        ("pak01_dir.vpk", "shaders_pc_dir.vpk"),
    ),
}


def is_source2(game: Path) -> bool:
    return (game / "gameinfo.gi").is_file()


def mount(game: Path):
    from SourceIO.library.shared.content_manager import ContentManager
    from SourceIO.library.utils.tiny_path import TinyPath
    content_manager = ContentManager()
    content_manager.clean()
    content_manager.scan_for_content(TinyPath(game / ("gameinfo.gi" if is_source2(game) else "gameinfo.txt")))
    return content_manager


def detect_profile(game: Path) -> tuple[GameProfile | None, int]:
    content_manager = mount(game)
    try:
        app_id = int(content_manager.steam_id)
    finally:
        content_manager.clean()
    if app_id <= 0 and not is_source2(game):
        from SourceIO.library.utils import kv1
        from SourceIO.library.utils.kv1 import KV1Block
        _, game_info = kv1.load(game / "gameinfo.txt").top()
        if isinstance(game_info, KV1Block):
            file_system = game_info.get("filesystem")
            if isinstance(file_system, KV1Block):
                try:
                    app_id = int(file_system.get("steamappid", 0))
                except (TypeError, ValueError):
                    app_id = 0
    return GAME_PROFILES.get(app_id), app_id


def extract_resource(game: Path, resource: str, out_dir: Path) -> Path | None:
    from SourceIO.library.utils.tiny_path import TinyPath
    content_manager = mount(game)
    try:
        resource_path = TinyPath(resource)
        buffer = content_manager.find_file(resource_path)
        if buffer is None:
            return None
        target = out_dir / resource
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(buffer.read())
        return target
    finally:
        content_manager.clean()


def extract_map(game: Path, name: str, out_dir: Path) -> Path | None:
    if is_source2(game):
        path = game / "maps" / f"{name}.vpk"
        return path if path.is_file() else None
    loose_path = game / "maps" / f"{name}.bsp"
    if loose_path.is_file():
        return loose_path
    return extract_resource(game, f"maps/{name}.bsp", out_dir)


def extract_model(game: Path, model: str, out_dir: Path) -> Path | None:
    from SourceIO.library.models.mdl.v49.mdl_file import MdlV49
    from SourceIO.library.utils.tiny_path import TinyPath
    content_manager = mount(game)
    mdl_path = TinyPath(model)
    buffer = content_manager.find_file(mdl_path)
    if buffer is None:
        return None
    data = buffer.read()
    paths = [mdl_path]
    buffer.seek(0)
    # A compiled Source 2 model has no companion files: what it references resolves through the mounted game.
    if mdl_path.suffix != ".vmdl_c":
        try:
            mdl = MdlV49.from_buffer(buffer)
            anim_block = getattr(mdl.header, "anim_block_name", "")
            paths += [TinyPath(include) for include in mdl.include_models]
            if anim_block:
                paths.append(TinyPath(anim_block))
        except Exception:
            pass  # older versions: the importer itself reports what it cannot read
        stem = mdl_path.with_suffix("")
        paths += [TinyPath(str(stem) + suffix) for suffix in COMPANIONS]

    target = out_dir / model
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    for path in paths[1:]:
        if (companion := content_manager.find_file(path)) is not None:
            destination = out_dir / path.as_posix()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(companion.read())
    content_manager.clean()
    return target


def source2_model_has_animations(path: Path) -> bool:
    """Whether a compiled model has animations of its own or references any (groups, include models, graphs).
    Props under heroes/ (the lich's ice spire) can have a single bone and nothing to play."""
    from SourceIO.library.source2 import CompiledModelResource
    from SourceIO.library.source2.animation.loader import DATA_BLOCK, ModelDataBlock
    from SourceIO.library.utils import FileBuffer
    from SourceIO.library.utils.tiny_path import TinyPath
    with FileBuffer(path) as buffer:
        resource = CompiledModelResource.from_buffer(buffer, TinyPath(path.name))
        if resource.has_block("ANIM") or resource.has_block("AGRP"):
            return True
        data = resource.get_block(ModelDataBlock, block_id=DATA_BLOCK) or {}
        return any(data.get(key) for key in ("m_refAnimGroups", "m_refAnimIncludeModels", "m_animGraph2Refs"))


def summarize_model() -> dict:
    shape_keys = sum(len(mesh.shape_keys.key_blocks) - 1 for mesh in bpy.data.meshes if mesh.shape_keys)
    drivers = sum(len(key.animation_data.drivers) for key in bpy.data.shape_keys if key.animation_data)
    slots = sum(len(action.slots) for action in bpy.data.actions)
    flex_controllers = sum(len(obj.flex_controllers) for obj in bpy.data.objects)
    # Source 1 compact animations: one action per source model, named after it, with a slot per animation
    actions = sorted(action.name for action in bpy.data.actions if action.name.endswith(".mdl"))
    clips = sum(1 for action in bpy.data.actions if "clip" in action)
    markers = sum(len(action.pose_markers) for action in bpy.data.actions)
    nla_tracks = sum(len(obj.animation_data.nla_tracks) for obj in bpy.data.objects if obj.animation_data)
    bone_collections = {}
    for armature in bpy.data.armatures:
        for collection in armature.collections_all:
            bone_collections[collection.name] = bone_collections.get(collection.name, 0) + len(collection.bones)
    return {"shape_keys": shape_keys, "flex_drivers": drivers, "flex_controllers": flex_controllers,
            "action_slots": slots, "compact_actions": actions, "clip_actions": clips,
            "pose_markers": markers, "nla_tracks": nla_tracks, "bone_collections": bone_collections}


def summarize_materials() -> dict:
    node_counts = {
        material.name: len(material.node_tree.nodes) if material.node_tree is not None else 0
        for material in bpy.data.materials
    }
    dummy_materials = sorted(
        material.name
        for material in bpy.data.materials
        if material.node_tree is not None and "DUMMY" in material.node_tree.nodes
    )
    source2_shaders = sorted({
        str(material.get("source2_shader"))
        for material in bpy.data.materials
        if material.get("source2_shader")
    })
    unsupported_source2_shaders = sorted({
        str(material.get("source2_shader"))
        for material in bpy.data.materials
        if material.get("source2_shader")
        and not material.get("source2_shader_supported", False)
    })
    return {
        "material_node_counts": node_counts,
        "dummy_materials": dummy_materials,
        "source2_shaders": source2_shaders,
        "unsupported_source2_shaders": unsupported_source2_shaders,
    }


def summarize_entity_reports() -> list[dict]:
    reports = []
    for collection in bpy.data.collections:
        for key in ("sourceio_entity_report", "sourceio_entity_import_report"):
            raw = collection.get(key)
            if raw is None:
                continue
            report_data = json.loads(raw) if isinstance(raw, str) else dict(raw)
            reports.append({"collection": collection.name, **report_data})
            break
    return reports


def load_placeholders(result: dict):
    """Run *Load Entity* on every prop placeholder the map import left, and add what it made to ``result``."""
    placeholders = [obj for obj in bpy.data.objects
                    if obj.get("entity_data") and obj["entity_data"].get("prop_path")]
    view_layer = bpy.context.view_layer

    def layer_collections(layer_collection):
        yield layer_collection
        for child in layer_collection.children:
            yield from layer_collections(child)

    # Hidden objects can't be selected; include and show collections excluded or hidden by default (world
    # layers not visible on spawn, light blockers) while loading.
    excluded = [layer for layer in layer_collections(view_layer.layer_collection) if layer.exclude]
    for layer in excluded:
        layer.exclude = False
    hidden = [layer for layer in layer_collections(view_layer.layer_collection) if layer.hide_viewport]
    for layer in hidden:
        layer.hide_viewport = False
    for obj in view_layer.objects:
        obj.select_set(obj in placeholders)
    before = samples.snapshot()
    samples.captured_errors.clear()
    started = time.perf_counter()
    try:
        bpy.ops.sourceio.load_placeholder('EXEC_DEFAULT')
    except Exception as ex:
        result["status"], result["message"] = "FAIL", f"load_placeholder: {str(ex).strip().splitlines()[-1]}"
    result["seconds"] = round(result["seconds"] + time.perf_counter() - started, 3)
    for layer in hidden:
        layer.hide_viewport = True
    for layer in excluded:
        layer.exclude = True
    if hidden:
        result["details"]["hidden_collections"] = len(hidden)
    if excluded:
        result["details"]["excluded_collections"] = len(excluded)
    for name in samples.COUNTED:
        added = sum(1 for item in getattr(bpy.data, name) if item not in before[name])
        if added:
            result["created"][name] = result["created"].get(name, 0) + added
    # With *Replace entity* on, a loaded placeholder is deleted and its model takes its place.
    remaining = set(bpy.data.objects)
    loaded = sum(1 for obj in placeholders if obj not in remaining or obj["entity_data"].get("imported"))
    result["details"]["placeholders"] = f"{loaded}/{len(placeholders)}"
    errors = list(dict.fromkeys(samples.captured_errors))
    result["errors"] = (result["errors"] + errors)[:20]
    result["error_count"] += len(errors)
    if result["status"] == "PASS" and (errors or loaded < len(placeholders)):
        result["status"] = "WARN"
        result["message"] = errors[0] if errors else f"only {loaded} of {len(placeholders)} placeholders loaded"


def report(result: dict, label: str):
    created = ", ".join(f"{k}={v}" for k, v in result["created"].items()) or "-"
    extra = ", ".join(f"{k}={v}" for k, v in result["details"].items() if v)
    print(f"\nGAME {result['status']:4} {result['seconds']:7.2f}s  {label}  [{created}] {{{extra}}}"
          + (f"  :: {result['message']}" if result["message"] else ""))


def fail_unless(result: dict, requirements: dict[str, int], *, message: str):
    missing = [
        f"{name}>={minimum} (got {result['created'].get(name, 0)})"
        for name, minimum in requirements.items()
        if result["created"].get(name, 0) < minimum
    ]
    if result["status"] != "FAIL" and missing:
        result["status"] = "FAIL"
        result["message"] = f"{message}: " + ", ".join(missing)


def validate_result(profile: GameProfile | None, kind: str, asset: str, result: dict, *,
                    expect_actions: bool = False):
    if result["status"] == "FAIL":
        return
    if kind == "model":
        fail_unless(result, {"objects": 1, "meshes": 1}, message="model acceptance failed")
        if profile and profile.key.startswith("sdk2013_"):
            fail_unless(
                result,
                {"materials": 1, "images": 1},
                message="Source SDK 2013 model dependencies missing",
            )
            if asset == "models/dog.mdl":
                fail_unless(
                    result,
                    {"armatures": 1, "actions": 1},
                    message="Source SDK 2013 animation acceptance failed",
                )
        if profile and profile.key == "dota":
            fail_unless(result, {"materials": 1, "images": 1}, message="Dota model dependencies missing")
            if expect_actions and ("/heroes/" in asset or "/creeps/" in asset):
                fail_unless(result, {"armatures": 1, "actions": 1}, message="Dota animation acceptance failed")
            if asset == DOTA_MODELS[0] and result["details"].get("shape_keys", 0) < 1:
                result["status"] = "FAIL"
                result["message"] = "Dota hero acceptance failed: no shape keys"
    elif kind == "material":
        fail_unless(result, {"materials": 1, "images": 1}, message="material acceptance failed")
        if result["status"] != "FAIL" and result["details"].get("empty_materials", 0):
            result["status"] = "FAIL"
            result["message"] = "material acceptance failed: empty material graph"
        if result["status"] != "FAIL" and result["details"].get("dummy_materials"):
            result["status"] = "FAIL"
            result["message"] = "material acceptance failed: unsupported dummy shader"
        unsupported = result["details"].get("unsupported_source2_shaders", ())
        if result["status"] != "FAIL" and unsupported:
            result["status"] = "FAIL"
            result["message"] = (
                "material acceptance failed: unsupported Source 2 shader(s): "
                + ", ".join(unsupported)
            )
        expected_shader = DOTA_MATERIAL_SHADERS.get(asset) if profile and profile.key == "dota" else None
        if (
                result["status"] != "FAIL"
                and expected_shader
                and expected_shader not in result["details"].get("source2_shaders", ())
        ):
            result["status"] = "FAIL"
            result["message"] = (
                f"Dota material acceptance failed: expected shader {expected_shader}"
            )
    elif kind == "texture":
        fail_unless(result, {"images": 1}, message="texture acceptance failed")
    elif kind == "map":
        fail_unless(result, {"objects": 1, "collections": 1}, message="map acceptance failed")
        if profile and profile.key.startswith("sdk2013_"):
            fail_unless(
                result,
                {"objects": 10, "materials": 1},
                message="Source SDK 2013 map acceptance failed",
            )
        if profile and profile.key == "dota":
            fail_unless(result, {"objects": 1000, "collections": 5}, message="Dota world acceptance failed")
            reports = result["details"].get("entity_reports", ())
            if result["status"] != "FAIL" and not reports:
                result["status"] = "FAIL"
                result["message"] = "Dota world acceptance failed: no entity import report"
            elif result["status"] != "FAIL":
                entity_count = sum(int(report.get("entity_count", 0)) for report in reports)
                represented = sum(
                    int(report.get("totals", {}).get(category, 0))
                    for report in reports
                    for category in ("handled", "represented")
                )
                unsupported = sum(int(report.get("totals", {}).get("unsupported", 0)) for report in reports)
                if entity_count < 4000 or represented < 4000 or unsupported > 100:
                    result["status"] = "FAIL"
                    result["message"] = (
                        "Dota world entity acceptance failed: "
                        f"entities={entity_count}, handled/represented={represented}, unsupported={unsupported}"
                    )


def classify_known_result(
        profile: GameProfile | None,
        kind: str,
        asset: str,
        result: dict,
):
    if (
            result["status"] != "WARN"
            or profile is None
            or not profile.key.startswith("sdk2013_")
            or kind != "map"
    ):
        return
    patterns = SOURCE_SDK_2013_KNOWN_ERRORS.get(asset)
    errors = result.get("errors", ())
    if not patterns or not errors:
        return
    if all(
            any(re.search(pattern, error, re.IGNORECASE | re.DOTALL) for pattern in patterns)
            for error in errors
    ):
        result["status"] = "KNOWN"
        result["message"] = (
            "known Source SDK 2013 sourcetest texture/skybox limitations"
        )


def file_fingerprint(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path.resolve()), "exists": False}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def installation_report(game: Path, profile: GameProfile | None, app_id: int) -> dict:
    gameinfo = game / ("gameinfo.gi" if is_source2(game) else "gameinfo.txt")
    files = {"gameinfo": file_fingerprint(gameinfo)}
    if profile:
        for relative_path in profile.fingerprint_files:
            files[relative_path] = file_fingerprint(game / relative_path)
    if is_source2(game):
        files["resourcecompiler.exe"] = file_fingerprint(game.parent / "bin/win64/resourcecompiler.exe")

    common = next((parent for parent in (game, *game.parents) if parent.name.lower() == "common"), None)
    manifest = common.parent / f"appmanifest_{app_id}.acf" if common is not None else None
    manifest_fingerprint = file_fingerprint(manifest) if manifest is not None else {"exists": False}
    build_id = None
    install_root = None
    if manifest is not None and manifest.is_file():
        manifest_text = manifest.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'"buildid"\s+"(\d+)"', manifest_text)
        build_id = match.group(1) if match else None
        install_match = re.search(r'"installdir"\s+"([^"]+)"', manifest_text)
        if common is not None and install_match:
            install_root = common / install_match.group(1)
    manifest_fingerprint["build_id"] = build_id
    install_files = {}
    if profile and install_root is not None:
        for relative_path in profile.install_fingerprint_files:
            install_files[relative_path] = file_fingerprint(
                install_root.joinpath(*relative_path.split("/"))
            )

    return {
        "game_path": str(game.resolve()),
        "app_id": app_id,
        "profile": profile.key if profile else None,
        "steam_manifest": manifest_fingerprint,
        "install_path": (
            str(install_root.resolve(strict=False))
            if install_root is not None
            else None
        ),
        "files": files,
        "install_files": install_files,
    }


def save_review_copy(save_dir: Path | None, kind: str, asset: str, result: dict):
    """Save the scene an import left as <save_dir>/<kind>__<asset>.blend for manual review (images are packed)."""
    if save_dir is None:
        return
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(asset).with_suffix("").as_posix().replace("/", "__"))
    path = save_dir / f"{kind}__{name}.blend"
    save_dir.mkdir(parents=True, exist_ok=True)
    # Blender doesn't save data without users: a material or texture import alone leaves its material and images
    # unused, so show each unused material on a sphere and keep unused images with a fake user.
    for index, material in enumerate([material for material in bpy.data.materials if material.users == 0]):
        mesh = bpy.data.meshes.new(f"preview_{material.name}")
        sphere = bmesh.new()
        bmesh.ops.create_uvsphere(sphere, u_segments=64, v_segments=32, radius=1.0, calc_uvs=True)
        sphere.to_mesh(mesh)
        sphere.free()
        mesh.shade_smooth()
        mesh.materials.append(material)
        obj = bpy.data.objects.new(mesh.name, mesh)
        obj.location.x = index * 2.5
        bpy.context.scene.collection.objects.link(obj)
    for image in bpy.data.images:
        if image.users == 0:
            image.use_fake_user = True
    started = time.perf_counter()
    bpy.ops.wm.save_as_mainfile(filepath=str(path.resolve()), copy=True, compress=True)
    result.setdefault("details", {})["saved_blend"] = str(path)
    print(f"    saved {path} ({time.perf_counter() - started:.1f} s)")


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, required=True, help="mod folder that holds gameinfo.txt or gameinfo.gi")
    parser.add_argument("--model", action="append", default=[], help="game-relative .mdl or .vmdl_c path")
    parser.add_argument("--material", action="append", default=[], help="game-relative .vmat_c path")
    parser.add_argument("--texture", action="append", default=[], help="game-relative .vtex_c path")
    parser.add_argument("--map", action="append", default=[], help="map name in <game>/maps")
    parser.add_argument("--expect-profile", choices=tuple(
        profile.key for profile in GAME_PROFILES.values()
    ),
                        help="fail if installed-game detection selects a different profile")
    parser.add_argument("--include-animations", action="store_true",
                        help="also import the animations of each model's include models (Source 1), "
                             "or the model's own animations (Source 2)")
    parser.add_argument("--clips", default="", metavar="PATTERNS",
                        help="Source 2: also import the animation graph clips matching these patterns")
    parser.add_argument("--load-placeholders", action="store_true",
                        help="after importing a map, load every prop placeholder (the Load Entity button)")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--save", type=Path, help="save the scene the last import left as this .blend")
    parser.add_argument("--save-dir", type=Path,
                        help="save a .blend after every import into this directory, for manual review")
    args = parser.parse_args(argv)
    source2 = is_source2(args.game)
    if not (args.game / ("gameinfo.gi" if source2 else "gameinfo.txt")).is_file():
        print(f"No gameinfo.txt or gameinfo.gi in {args.game}")
        return 1

    samples.load_addon()
    perf_sampler = importlib.import_module(f"{samples.REPO_ROOT.name}.library.utils.perf_sampler")
    perf_sampler.reset_stats()
    profile, app_id = detect_profile(args.game)
    if args.expect_profile and (profile is None or profile.key != args.expect_profile):
        actual = profile.key if profile else f"unknown app ID {app_id}"
        print(f"Expected {args.expect_profile} profile, detected {actual}")
        return 1

    models, maps = list(args.model), list(args.map)
    materials, textures = list(args.material), list(args.texture)
    using_defaults = not any((models, maps, materials, textures))
    if using_defaults:
        if profile is None:
            print(f"No default installed-game corpus for app ID {app_id}; specify assets explicitly")
            return 1
        models = list(profile.models)
        maps = list(profile.maps)
        materials = list(profile.materials)
        textures = list(profile.textures)
    import_animations = args.include_animations or bool(args.clips) or bool(using_defaults and profile
                                                                            and profile.default_animations)

    results = []
    with tempfile.TemporaryDirectory(prefix="sourceio_game_") as tmp:
        for model in models:
            path = extract_model(args.game, model, Path(tmp))
            if path is None:
                result = {"file": model, "status": "FAIL", "seconds": 0, "created": {}, "details": {},
                          "errors": [], "error_count": 0, "message": "not found in the game's search paths"}
            else:
                # The operator unmounts everything when it finishes, so mount the game for each import.
                if path.suffix == ".vmdl_c":
                    operator, options = "vmdl", {"import_animations": import_animations,
                                                 "animation_clips": args.clips}
                else:
                    operator, options = "mdl", {"import_animations": import_animations,
                                                "import_include_animations": import_animations}
                result = samples.run_one(path, operator, "files", before_import=lambda: mount(args.game),
                                         options=options)
                result["file"] = model
                result["details"].update(summarize_model())
                result["details"].update(summarize_materials())
            expect_actions = (import_animations and path is not None and path.suffix == ".vmdl_c"
                              and source2_model_has_animations(path))
            validate_result(profile, "model", model, result, expect_actions=expect_actions)
            classify_known_result(profile, "model", model, result)
            if path is not None:
                save_review_copy(args.save_dir, "model", model, result)
            results.append(result)
            report(result, model)

        for kind, assets, operator in (
                ("material", materials, "vmat"),
                ("texture", textures, "vtex"),
        ):
            for asset in assets:
                path = extract_resource(args.game, asset, Path(tmp))
                if path is None:
                    result = {"file": asset, "status": "FAIL", "seconds": 0, "created": {}, "details": {},
                              "errors": [], "error_count": 0, "message": "not found in the game's search paths"}
                else:
                    before_import = (lambda: mount(args.game)) if kind == "material" else None
                    result = samples.run_one(path, operator, "files", before_import=before_import)
                    result["file"] = asset
                    if kind == "material":
                        result["details"].update(summarize_materials())
                validate_result(profile, kind, asset, result)
                classify_known_result(profile, kind, asset, result)
                if path is not None:
                    save_review_copy(args.save_dir, kind, asset, result)
                results.append(result)
                report(result, asset)

        for name in maps:
            path = extract_map(args.game, name, Path(tmp))
            expected_path = args.game / "maps" / (
                f"{name}.vpk" if source2 else f"{name}.bsp"
            )
            if path is None:
                result = {"file": str(expected_path), "status": "FAIL", "seconds": 0, "created": {}, "details": {},
                          "errors": [], "error_count": 0, "message": "map not found"}
            else:
                before_import = None if source2 else (lambda: mount(args.game))
                result = samples.run_one(
                    path,
                    "vmap_vpk" if source2 else "bsp",
                    "filepath",
                    before_import=before_import,
                )
                if args.load_placeholders and result["status"] != "FAIL":
                    load_placeholders(result)
                entity_reports = summarize_entity_reports()
                if entity_reports:
                    result["details"]["entity_reports"] = entity_reports
            validate_result(profile, "map", name, result)
            classify_known_result(profile, "map", name, result)
            if path is not None and path.is_file():
                save_review_copy(args.save_dir, "map", name, result)
            results.append(result)
            report(result, f"maps/{name}{'.vpk' if source2 else '.bsp'}")

    summary = {
        status: sum(1 for result in results if result["status"] == status)
        for status in ("PASS", "KNOWN", "WARN", "FAIL")
    }
    print(f"GAME SUMMARY {summary}")
    performance = perf_sampler.snapshot_stats()
    if performance["enabled"]:
        perf_sampler.print_stats()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        report_data = {
            "schema": "sourceio.game-import-report",
            "schema_version": 2,
            "installation": installation_report(args.game, profile, app_id),
            "selection": {
                "models": models,
                "materials": materials,
                "textures": textures,
                "maps": maps,
                "include_animations": import_animations,
                "clips": args.clips,
                "load_placeholders": args.load_placeholders,
            },
            "summary": summary,
            "results": results,
        }
        if performance["enabled"]:
            report_data["performance"] = performance
        args.json.write_text(json.dumps(report_data, indent=2))
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save.resolve()))
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    if bpy.app.background:
        sys.exit(code)
