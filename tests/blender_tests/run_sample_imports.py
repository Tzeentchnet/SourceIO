"""Import every sample asset headlessly and report what each importer produced.

Usage:
    python tests/fetch_samples.py
    blender -b --factory-startup --python tests/blender_tests/run_sample_imports.py -- [--samples DIR] [--filter TEXT] [--json FILE]

Each file is imported into an empty scene through the same operator the UI uses. A file PASSES
when the operator finishes, creates data and logs no errors; WARN means it finished but logged
errors or created nothing; FAIL means the operator raised or was cancelled. Exit code is 1 if
anything failed.
"""
import argparse
import importlib
import json
import sys
import time
import traceback
from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parents[2]

# (samples sub-folder, extension) -> (operator, how the operator receives the file)
IMPORTERS = {
    ("source1/models", ".mdl"): ("mdl", "files"),
    ("source1/textures", ".vtf"): ("vtf", "files"),
    ("source1/textures", ".vmt"): ("vmt", "files"),
    ("source1/maps", ".bsp"): ("bsp", "filepath"),
    ("goldsrc/models", ".mdl"): ("mdl", "files"),
    ("goldsrc/maps", ".bsp"): ("gbsp", "filepath_files"),
    ("source2/models", ".vmdl_c"): ("vmdl", "files"),
    ("source2/materials", ".vmat_c"): ("vmat", "files"),
    ("source2/textures", ".vtex_c"): ("vtex", "files"),
    ("source2/physics", ".vphys_c"): ("vphys", "files"),
    ("source2/maps", ".vpk"): ("vmap_vpk", "filepath"),
}

COUNTED = ("objects", "meshes", "materials", "images", "armatures", "actions", "lights", "collections")

captured_errors: list[str] = []


def load_addon():
    sys.path.insert(0, str(REPO_ROOT.parent))
    importlib.import_module(REPO_ROOT.name)
    from SourceIO.blender_bindings import bindings
    from SourceIO.blender_bindings.utils import logging_impl

    def capture(original):
        def wrapper(self, message, *args, **kwargs):
            captured_errors.append(f"{self.name}: {message}")
            return original(self, message, *args, **kwargs)

        return wrapper

    logging_impl.BPYLogger.error = capture(logging_impl.BPYLogger.error)
    logging_impl.BPYLogger.exception = capture(logging_impl.BPYLogger.exception)

    print_exc = traceback.print_exc

    def print_exc_wrapper(*args, **kwargs):
        captured_errors.append("traceback: " + traceback.format_exc().strip().splitlines()[-1])
        return print_exc(*args, **kwargs)

    traceback.print_exc = print_exc_wrapper
    bindings.register()


def snapshot():
    return {name: set(getattr(bpy.data, name)) for name in COUNTED}


def run_one(path: Path, operator: str, mode: str) -> dict:
    bpy.ops.wm.read_homefile(use_empty=True)
    before = snapshot()
    captured_errors.clear()

    kwargs = {"filepath": str(path)}
    if mode == "files":
        kwargs.update(directory=str(path.parent) + "/", files=[{"name": path.name}])
    elif mode == "filepath_files":
        kwargs.update(files=[{"name": path.name}])
    if operator == "mdl":
        kwargs["import_animations"] = True

    status, message = "PASS", ""
    started = time.perf_counter()
    try:
        result = getattr(bpy.ops.sourceio, operator)('EXEC_DEFAULT', **kwargs)
        if 'FINISHED' not in result:
            status, message = "FAIL", f"operator returned {set(result)}"
    except Exception as ex:
        lines = [line for line in str(ex).strip().splitlines() if line.strip() and not line.startswith("Location:")]
        status, message = "FAIL", lines[-1] if lines else type(ex).__name__
    elapsed = time.perf_counter() - started

    created = {name: [item for item in getattr(bpy.data, name) if item not in before[name]] for name in COUNTED}
    counts = {name: len(items) for name, items in created.items() if items}
    meshes = created["meshes"]
    materials = created["materials"]
    details = {
        "meshes_with_custom_normals": sum(1 for mesh in meshes if mesh.has_custom_normals),
        "blended_materials": sum(1 for mat in materials if mat.surface_render_method == 'BLENDED'),
        "empty_materials": sum(1 for mat in materials if mat.node_tree is None or len(mat.node_tree.nodes) <= 2),
    }

    errors = list(dict.fromkeys(captured_errors))
    if status == "PASS" and (errors or not counts):
        status = "WARN"
        message = errors[0] if errors else "nothing was created"

    return {"file": path.as_posix(), "operator": operator, "status": status, "seconds": round(elapsed, 3),
            "created": counts, "details": details, "errors": errors[:20], "error_count": len(errors),
            "message": message}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=REPO_ROOT / "samples")
    parser.add_argument("--filter", default="")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if not args.samples.is_dir():
        print(f"No samples at {args.samples}; run tests/fetch_samples.py first")
        return 1

    load_addon()
    results = []
    for (folder, extension), (operator, mode) in IMPORTERS.items():
        for path in sorted((args.samples / folder).glob(f"*{extension}")):
            if args.filter and args.filter not in path.as_posix():
                continue
            result = run_one(path, operator, mode)
            results.append(result)
            created = ", ".join(f"{k}={v}" for k, v in result["created"].items()) or "-"
            print(f"\nSAMPLE {result['status']:4} {result['seconds']:7.2f}s  {folder}/{path.name}  [{created}]"
                  + (f"  :: {result['message']}" if result["message"] else ""))

    summary = {status: sum(1 for r in results if r["status"] == status) for status in ("PASS", "WARN", "FAIL")}
    print(f"SAMPLE SUMMARY {summary}")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    if bpy.app.background:
        sys.exit(code)
