"""``blender -c sourceio`` command line interface.

    blender -c sourceio import [--scale S] [--no-materials] [--animations] [--clips PATTERNS] [--output out.blend]
                               FILE [FILE ...]

Imports each file with the same operator the File > Import menu uses, picked by extension, then
optionally saves the result as a .blend file.
"""
import argparse
import struct
from pathlib import Path

import bpy

from ..library.source2.exceptions import Source2Error
from ..library.source2.resource_types.compiled_sound_resource import CompiledSoundResource
from ..library.source2.sound import SoundError
from ..library.utils import FileBuffer

# extension -> operator name under bpy.ops.sourceio
OPERATORS = {
    ".mdl": "mdl",
    ".md3": "mdl",
    ".vtf": "vtf",
    ".vmt": "vmt",
    ".vmdl_c": "vmdl",
    ".vmat_c": "vmat",
    ".vtex_c": "vtex",
    ".vphys_c": "vphys",
    ".vmap_c": "vmap",
    ".vpk": "vmap_vpk",
    ".vanim_c": "source2_animation",
    ".vagrp_c": "source2_animation",
    ".vsnd_c": "vsnd",
    ".dmx": "dmx_camera",
}
GOLDSRC_BSP_VERSION = 30


def _operator_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".bsp":
        with open(path, "rb") as f:
            header = f.read(4)
        return "gbsp" if len(header) == 4 and struct.unpack("<i", header)[0] == GOLDSRC_BSP_VERSION else "bsp"
    return OPERATORS.get(suffix)


def _import(path: Path, args) -> bool:
    operator_name = _operator_for(path)
    if operator_name is None:
        print(f"sourceio: unsupported file type: {path}")
        return False
    operator = getattr(bpy.ops.sourceio, operator_name)
    properties = operator.get_rna_type().properties.keys()

    kwargs = {"filepath": str(path)}
    if "files" in properties:
        kwargs["files"] = [{"name": path.name}]
    if "directory" in properties:
        kwargs["directory"] = str(path.parent) + "/"
    if args.scale is not None and "scale" in properties:
        kwargs["scale"] = args.scale
    for name in ("import_textures", "import_materials"):
        if name in properties:
            kwargs[name] = not args.no_materials
    if "import_animations" in properties:
        kwargs["import_animations"] = args.animations or bool(args.clips)
    if args.clips and "animation_clips" in properties:
        kwargs["animation_clips"] = args.clips
    if "texture_mip_level" in properties:
        kwargs["texture_mip_level"] = args.mip_level
    if "decode_packed_channels" in properties:
        kwargs["decode_packed_channels"] = not args.no_packed_channels

    try:
        result = operator('EXEC_DEFAULT', **kwargs)
    except RuntimeError as ex:
        print(f"sourceio: failed to import {path}: {str(ex).strip().splitlines()[-1]}")
        return False
    if 'FINISHED' not in result:
        print(f"sourceio: import of {path} returned {set(result)}")
        return False
    print(f"sourceio: imported {path}")
    return True


def _extract_sound(path: Path, output: Path, overwrite: bool) -> bool:
    try:
        with FileBuffer(path) as buffer:
            resource = CompiledSoundResource.from_buffer(buffer, path)
            outputs = resource.extract_to(output, overwrite=overwrite)
    except (OSError, SoundError, Source2Error, ValueError) as ex:
        print(f"sourceio: failed to extract {path}: {ex}")
        return False
    print(f"sourceio: extracted {path} -> {', '.join(str(item) for item in outputs)}")
    return True


def _export(operator_name: str, output: Path, overwrite: bool) -> bool:
    operator = getattr(bpy.ops.sourceio, operator_name)
    try:
        result = operator(
            "EXEC_DEFAULT",
            filepath=str(output.resolve()),
            overwrite=overwrite,
        )
    except RuntimeError as ex:
        print(f"sourceio: export failed: {str(ex).strip().splitlines()[-1]}")
        return False
    if "FINISHED" not in result:
        print(f"sourceio: export returned {set(result)}")
        return False
    print(f"sourceio: exported {output}")
    return True


def execute(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="blender -c sourceio",
        description="Import or extract GoldSrc/Source/Source 2 assets.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    import_parser = commands.add_parser("import", help="import one or more files")
    import_parser.add_argument("files", nargs="+", type=Path)
    import_parser.add_argument("--scale", type=float, help="world scale (defaults to each importer's own)")
    import_parser.add_argument("--no-materials", action="store_true", help="skip materials and textures")
    import_parser.add_argument("--animations", action="store_true", help="import animations where supported")
    import_parser.add_argument("--clips", default="", metavar="PATTERNS",
                               help="Source 2 models: also import the animation graph clips matching these "
                                    "comma-separated name patterns (implies --animations)")
    import_parser.add_argument("--mip-level", type=int, default=0,
                               help="Source 2 textures: mip level to decode (0 is highest resolution)")
    import_parser.add_argument("--no-packed-channels", action="store_true",
                               help="do not apply Source 2 packed-channel metadata")
    import_parser.add_argument("--output", type=Path, help="save the result to this .blend file")

    sound_parser = commands.add_parser(
        "extract-sound",
        help="extract VSND audio and compiler-compatible companion files",
    )
    sound_parser.add_argument("files", nargs="+", type=Path)
    sound_parser.add_argument("--output", type=Path, default=Path.cwd(), help="destination directory")
    sound_parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")

    modeldoc_parser = commands.add_parser(
        "export-modeldoc",
        help="export selected scene meshes to an editable static ModelDoc/DMX bundle",
    )
    modeldoc_parser.add_argument("output", type=Path, help="target .vmdl path")
    modeldoc_parser.add_argument("--overwrite", action="store_true", help="replace all generated outputs")

    hammer_parser = commands.add_parser(
        "export-hammer",
        help="export the active scene collection to an editable static Hammer map bundle",
    )
    hammer_parser.add_argument("output", type=Path, help="target .vmap path")
    hammer_parser.add_argument("--overwrite", action="store_true", help="replace all generated outputs")
    args = parser.parse_args(argv)

    if args.command == "extract-sound":
        extracted = sum(
            _extract_sound(path.resolve(), args.output.resolve(), args.overwrite)
            for path in args.files
        )
        failed = len(args.files) - extracted
        print(f"sourceio: {extracted} extracted, {failed} failed")
        return 1 if failed else 0

    if args.command == "export-modeldoc":
        return 0 if _export("source2_modeldoc_export", args.output, args.overwrite) else 1
    if args.command == "export-hammer":
        return 0 if _export("source2_hammer_export", args.output, args.overwrite) else 1

    if args.mip_level < 0:
        parser.error("--mip-level must be non-negative")
    imported = sum(_import(path.resolve(), args) for path in args.files)
    failed = len(args.files) - imported
    if args.output is not None and imported:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()), check_existing=False)
        print(f"sourceio: saved {args.output}")
    print(f"sourceio: {imported} imported, {failed} failed")
    return 1 if failed else 0
