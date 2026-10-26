"""Re-import one generated Source SDK 2013 fixture in Blender and verify it.

Usage:
    blender -b --factory-startup --python tests/source1_toolchain/run_source_sdk_2013_reimport.py -- \
        --game-root TEMP_GAME --asset s1sdk_sp --json REPORT.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

E2E_ROOT = Path(__file__).resolve().parents[1] / "e2e"
sys.path.insert(0, str(E2E_ROOT))
import run_sample_imports as samples  # noqa: E402


def _mesh_positions(obj) -> np.ndarray:
    return np.asarray(
        [
            tuple(obj.matrix_world @ vertex.co)
            for vertex in obj.data.vertices
        ],
        dtype=np.float64,
    )


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)

    game_root = args.game_root.expanduser().resolve(strict=True)
    gameinfo = game_root / "gameinfo.txt"
    if not gameinfo.is_file():
        raise FileNotFoundError(f"Fixture gameinfo.txt is unavailable: {gameinfo}")

    samples.load_addon()
    from SourceIO.blender_bindings.models import import_model
    from SourceIO.blender_bindings.operators.import_settings_base import ModelOptions
    from SourceIO.library.shared.content_manager import ContentManager
    from SourceIO.library.utils.tiny_path import TinyPath

    bpy.ops.wm.read_homefile(use_empty=True)
    model_path = TinyPath(
        f"models/sourceio/{args.asset}/{args.asset}.mdl"
    )
    content_manager = ContentManager()
    content_manager.clean()
    model_buffer = None
    try:
        content_manager.scan_for_content(TinyPath(gameinfo))
        model_buffer = content_manager.find_file(model_path)
        if model_buffer is None:
            raise FileNotFoundError(
                f"Compiled fixture model is unavailable: {model_path}"
            )
        container = import_model(
            model_path,
            model_buffer,
            content_manager,
            ModelOptions.default(),
        )
        model_buffer.close()
        model_buffer = None
        if container is None:
            raise RuntimeError("SourceIO returned no model container")
        imported_meshes = [
            imported
            for imported in container.objects
            if imported.type == "MESH"
        ]
        if len(imported_meshes) != 1:
            raise AssertionError(
                f"Expected one imported mesh, got {len(imported_meshes)}"
            )
        imported = imported_meshes[0]
        expected_positions = np.asarray(
            (
                (0.0, 0.0, 0.0),
                (0.3048, 0.0, 0.0),
                (0.0, 0.1524, 0.0),
            ),
            dtype=np.float64,
        )
        actual_positions = _mesh_positions(imported)
        if actual_positions.shape != expected_positions.shape:
            raise AssertionError(
                f"Imported positions have shape {actual_positions.shape}, "
                f"expected {expected_positions.shape}"
            )
        maximum_position_error = max(
            float(np.linalg.norm(actual_positions - expected, axis=1).min())
            for expected in expected_positions
        )
        if maximum_position_error > 1e-5:
            raise AssertionError(
                f"Imported positions differ by {maximum_position_error}"
            )
        if imported.data.uv_layers.active is None:
            raise AssertionError("Imported fixture has no active UV layer")
        actual_uvs = {
            tuple(round(component, 5) for component in loop.uv)
            for loop in imported.data.uv_layers.active.data
        }
        expected_uvs = {(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)}
        if actual_uvs != expected_uvs:
            raise AssertionError(
                f"Imported UVs are {sorted(actual_uvs)}, "
                f"expected {sorted(expected_uvs)}"
            )
        if len(imported.data.materials) != 1:
            raise AssertionError(
                f"Expected one imported material, got "
                f"{len(imported.data.materials)}"
            )
        imported_material = imported.data.materials[0]
        material_path = str(
            imported_material.get("full_path", "")
        ).split("?", 1)[0]
        expected_material_path = f"models/sourceio/{args.asset}/probe"
        if material_path != expected_material_path:
            raise AssertionError(
                f"Imported material path is {material_path!r}, "
                f"expected {expected_material_path!r}"
            )
        if not imported.data.polygons:
            raise AssertionError("Imported fixture has no polygons")
        normal_dot = float(
            Vector(imported.data.polygons[0].normal).dot(Vector((0, 0, 1)))
        )
        if normal_dot <= 0.99:
            raise AssertionError(
                f"Imported polygon normal dot product is {normal_dot}"
            )
        report = {
            "schema": "sourceio.source-sdk-2013-fixture-reimport",
            "schema_version": 1,
            "status": "PASS",
            "asset": args.asset,
            "model": model_path.as_posix(),
            "mesh_count": len(imported_meshes),
            "vertex_count": len(imported.data.vertices),
            "polygon_count": len(imported.data.polygons),
            "material_path": material_path,
            "image_count": len(bpy.data.images),
            "maximum_position_error": maximum_position_error,
            "uvs": [list(uv) for uv in sorted(actual_uvs)],
            "normal_dot": normal_dot,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0
    finally:
        if model_buffer is not None and not model_buffer.closed:
            model_buffer.close()
        content_manager.clean()


if __name__ == "__main__":
    raise SystemExit(main())
