"""Import assets straight from an installed Source 1 game and report what each importer produced.

Usage:
    blender -b --factory-startup --python tests/blender_tests/run_game_imports.py -- \
        --game "<steam>/common/Team Fortress 2/tf" [--model models/player/heavy.mdl ...] [--map ctf_2fort ...] \
        [--json FILE]

With no --model/--map, a default TF2 set is used. Models are read through the game's own search paths
(VPKs included), copied with their companion files into a temporary folder, and imported from there
with the game still mounted, so materials and include models resolve the way they do for a user.
Maps are imported from the game's maps folder. Results use the same PASS/WARN/FAIL rules as
run_sample_imports.py; exit code is 1 if anything failed.
"""
import argparse
import json
import sys
import tempfile
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
COMPANIONS = (".vvd", ".dx90.vtx", ".dx80.vtx", ".sw.vtx", ".vtx", ".phy", ".ani")


def mount(game: Path):
    from SourceIO.library.shared.content_manager import ContentManager
    from SourceIO.library.utils.tiny_path import TinyPath
    content_manager = ContentManager()
    content_manager.scan_for_content(TinyPath(game / "gameinfo.txt"))
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
    nla_tracks = sum(len(obj.animation_data.nla_tracks) for obj in bpy.data.objects if obj.animation_data)
    return {"shape_keys": shape_keys, "flex_drivers": drivers, "action_slots": slots, "nla_tracks": nla_tracks}


def report(result: dict, label: str):
    created = ", ".join(f"{k}={v}" for k, v in result["created"].items()) or "-"
    extra = ", ".join(f"{k}={v}" for k, v in result["details"].items() if v)
    print(f"\nGAME {result['status']:4} {result['seconds']:7.2f}s  {label}  [{created}] {{{extra}}}"
          + (f"  :: {result['message']}" if result["message"] else ""))


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, required=True, help="mod folder that holds gameinfo.txt")
    parser.add_argument("--model", action="append", default=[], help="game-relative .mdl path")
    parser.add_argument("--map", action="append", default=[], help="map name in <game>/maps")
    parser.add_argument("--include-animations", action="store_true",
                        help="also import the animations of each model's include models")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    if not (args.game / "gameinfo.txt").is_file():
        print(f"No gameinfo.txt in {args.game}")
        return 1
    models, maps = args.model, args.map
    if not models and not maps:
        models, maps = TF2_MODELS, TF2_MAPS

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
                result = samples.run_one(path, "mdl", "files", before_import=lambda: mount(args.game),
                                         options={"import_include_animations": args.include_animations})
                result["file"] = model
                result["details"].update(summarize_model())
            results.append(result)
            report(result, model)

        for name in maps:
            path = args.game / "maps" / f"{name}.bsp"
            if not path.is_file():
                result = {"file": str(path), "status": "FAIL", "seconds": 0, "created": {}, "details": {},
                          "errors": [], "error_count": 0, "message": "map not found"}
            else:
                result = samples.run_one(path, "bsp", "filepath")
            results.append(result)
            report(result, f"maps/{name}.bsp")

    summary = {status: sum(1 for r in results if r["status"] == status) for status in ("PASS", "WARN", "FAIL")}
    print(f"GAME SUMMARY {summary}")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    if bpy.app.background:
        sys.exit(code)
