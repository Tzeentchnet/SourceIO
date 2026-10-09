"""Import assets straight from an installed Source 1 or Source 2 game and report what each importer produced.

Usage:
    blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- \
        --game "<steam>/common/Team Fortress 2/tf" [--model models/player/heavy.mdl ...] [--map ctf_2fort ...] \
        [--include-animations] [--load-placeholders] [--json FILE] [--save FILE.blend]
    blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- \
        --game "<steam>/common/Counter-Strike Global Offensive/game/csgo" [--model models/chicken/chicken.vmdl_c ...] \
        [--map de_dust2 ...] [--include-animations] [--clips "idle*,run_n_*"]

The game folder holds gameinfo.txt (Source 1) or gameinfo.gi (Source 2). With no --model/--map, a default
TF2 or CS2 set is used. Models are read through the game's own search paths (VPKs included), copied with
their companion files into a temporary folder, and imported from there with the game still mounted, so
materials and include models resolve the way they do for a user. Maps are imported from the game's maps
folder (.bsp, or a Source 2 map .vpk). --include-animations imports the animations of Source 1 include
models, or turns on *Import animations* for Source 2 models. --load-placeholders then runs *Load Entity* on
every prop placeholder a map import leaves (Source 2 maps place all their geometry that way). --clips also
imports the animation graph clips of Source 2 models that match the patterns (implies --include-animations;
"*" takes every clip, about 2000 for a CS2 character). --save keeps the scene the last import left. Results use
the same PASS/WARN/FAIL rules as run_sample_imports.py; exit code is 1 if anything failed.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

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
COMPANIONS = (".vvd", ".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx", ".phy", ".ani")


def is_source2(game: Path) -> bool:
    return (game / "gameinfo.gi").is_file()


def mount(game: Path):
    from SourceIO.library.shared.content_manager import ContentManager
    from SourceIO.library.utils.tiny_path import TinyPath
    content_manager = ContentManager()
    content_manager.scan_for_content(TinyPath(game / ("gameinfo.gi" if is_source2(game) else "gameinfo.txt")))
    return content_manager


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


def load_placeholders(result: dict):
    """Run *Load Entity* on every prop placeholder the map import left, and add what it made to ``result``."""
    placeholders = [obj for obj in bpy.data.objects
                    if obj.get("entity_data") and obj["entity_data"].get("prop_path")]
    view_layer = bpy.context.view_layer

    def layer_collections(layer_collection):
        yield layer_collection
        for child in layer_collection.children:
            yield from layer_collections(child)

    # Hidden objects can't be selected; show collections hidden by default (light blockers) while loading.
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
    if hidden:
        result["details"]["hidden_collections"] = len(hidden)
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


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, required=True, help="mod folder that holds gameinfo.txt or gameinfo.gi")
    parser.add_argument("--model", action="append", default=[], help="game-relative .mdl or .vmdl_c path")
    parser.add_argument("--map", action="append", default=[], help="map name in <game>/maps")
    parser.add_argument("--include-animations", action="store_true",
                        help="also import the animations of each model's include models (Source 1), "
                             "or the model's own animations (Source 2)")
    parser.add_argument("--clips", default="", metavar="PATTERNS",
                        help="Source 2: also import the animation graph clips matching these patterns")
    parser.add_argument("--load-placeholders", action="store_true",
                        help="after importing a map, load every prop placeholder (the Load Entity button)")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--save", type=Path, help="save the scene the last import left as this .blend")
    args = parser.parse_args(argv)
    source2 = is_source2(args.game)
    if not source2 and not (args.game / "gameinfo.txt").is_file():
        print(f"No gameinfo.txt or gameinfo.gi in {args.game}")
        return 1
    models, maps = args.model, args.map
    if not models and not maps:
        models, maps = (CS2_MODELS, CS2_MAPS) if source2 else (TF2_MODELS, TF2_MAPS)

    samples.load_addon()
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
                    operator, options = "vmdl", {"import_animations": args.include_animations or bool(args.clips),
                                                 "animation_clips": args.clips}
                else:
                    operator, options = "mdl", {"import_include_animations": args.include_animations}
                result = samples.run_one(path, operator, "files", before_import=lambda: mount(args.game),
                                         options=options)
                result["file"] = model
                result["details"].update(summarize_model())
            results.append(result)
            report(result, model)

        for name in maps:
            path = args.game / "maps" / (f"{name}.vpk" if source2 else f"{name}.bsp")
            if not path.is_file():
                result = {"file": str(path), "status": "FAIL", "seconds": 0, "created": {}, "details": {},
                          "errors": [], "error_count": 0, "message": "map not found"}
            else:
                result = samples.run_one(path, "vmap_vpk" if source2 else "bsp", "filepath")
                if args.load_placeholders and result["status"] != "FAIL":
                    load_placeholders(result)
            results.append(result)
            report(result, f"maps/{path.name}")

    summary = {status: sum(1 for r in results if r["status"] == status) for status in ("PASS", "WARN", "FAIL")}
    print(f"GAME SUMMARY {summary}")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save.resolve()))
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    if bpy.app.background:
        sys.exit(code)
