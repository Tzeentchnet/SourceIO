"""Disassemble a Source 2 shader's compiled DX11 bytecode, with material parameter names on the constants.

Usage:  python -I tools/shader_disasm.py --game "<steam>/common/dota 2 beta/game/dota" --shader crystal --list
        python -I tools/shader_disasm.py --game ... --shader crystal --static 496 --dynamic 35 [--stage vs]

For shaders VRF has no port of. ``<shader>_pc_50_<stage>.vcs`` (in ``shaders_pc_dir.vpk``) is a compiled
resource whose DATA block lists the static and dynamic combos, followed by one zstd frame per static combo
holding its DXBC programs. The frames use a dictionary (id 0x2bc2fa87, 65536 bytes) embedded in the game's
``bin/win64/materialsystem2.dll``; ``d3dcompiler_47.dll`` from the same folder disassembles them (Windows only).
Combo ids are mixed-radix: ``--list`` shows which flags each compiled static combo sets.

Pixel shaders are stripped of names. ``cb0`` is the material's constants: each static combo lists
(variable index << 16 | float offset) per dynamic combo, which this tool writes after every ``cb0`` operand.
A "(computed)" variable's m_pCompiledExpression is applied before upload: ``1d <murmur2(name)> 06 1f 00``
is SrgbGammaToLinear(name), ``07 <1.0f> 1d <hash> 16 00`` is 1 / name (VRF's VfxEval lists the opcodes).
Vertex shaders keep their reflection data, so their disassembly names everything. Run with Blender's Python.
"""
import argparse
import ctypes
import re
import struct
import sys
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from SourceIO.library.source2.blocks.kv3_block import KVBlock  # noqa: E402
from SourceIO.library.source2.compiled_resource import CompiledResource  # noqa: E402
from SourceIO.library.utils import MemoryBuffer, TinyPath  # noqa: E402
from SourceIO.library.utils.pylib import VPKFile  # noqa: E402

ZSTD_DICTIONARY_MAGIC = 0xEC30A437
ZSTD_DICTIONARY_SIZE = 65536


def load_shader(game: Path, shader: str, stage: str):
    vpk = VPKFile(TinyPath(game / "shaders_pc_dir.vpk"))
    raw = vpk.find_file(TinyPath(f"shaders/vfx/{shader}_pc_50_{stage}.vcs"))
    if raw is None:
        raise SystemExit(f"{shader}_pc_50_{stage}.vcs is not in {game / 'shaders_pc_dir.vpk'}")
    raw = bytes(raw)
    size = struct.unpack_from("<I", raw, 0)[0]
    data = CompiledResource.from_buffer(MemoryBuffer(raw[:size]), TinyPath(shader)).get_block(
        KVBlock, block_name="DATA")
    frames, position = [], size
    while position + 12 <= len(raw):
        _, _, packed_size = struct.unpack_from("<iII", raw, position)
        frames.append(raw[position + 12:position + 12 + packed_size])
        position += 12 + packed_size
    return data, frames


def decompressor(game: Path, frame: bytes) -> zstandard.ZstdDecompressor:
    dictionary_id = zstandard.get_frame_parameters(frame).dict_id
    binary = (game.parent / "bin" / "win64" / "materialsystem2.dll").read_bytes()
    start = binary.find(struct.pack("<II", ZSTD_DICTIONARY_MAGIC, dictionary_id))
    if start < 0:
        raise SystemExit(f"zstd dictionary {dictionary_id:#x} is not in materialsystem2.dll")
    dictionary = zstandard.ZstdCompressionDict(binary[start:start + ZSTD_DICTIONARY_SIZE])
    return zstandard.ZstdDecompressor(dict_data=dictionary)


def disassemble(game: Path, code: bytes) -> str:
    compiler = ctypes.WinDLL(str(game.parent / "bin" / "win64" / "d3dcompiler_47.dll"))
    blob = ctypes.c_void_p()
    if compiler.D3DDisassemble(ctypes.c_char_p(code), ctypes.c_size_t(len(code)), 0, None, ctypes.byref(blob)):
        raise SystemExit("D3DDisassemble failed")
    vtable = ctypes.cast(ctypes.cast(blob, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    pointer = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vtable[3])(blob)
    size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vtable[4])(blob)
    return ctypes.string_at(pointer, size).decode("latin1").replace("\x00", "")


def combo_values(combos, combo_id: int) -> list[str]:
    values, stride = [], 1
    for combo in combos:
        count = combo["m_nMax"] - combo["m_nMin"] + 1
        value = combo_id // stride % count + combo["m_nMin"]
        if value:
            values.append(combo["m_szName"] if count == 2 else f"{combo['m_szName']}={value}")
        stride *= count
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, required=True, help="mod folder that holds shaders_pc_dir.vpk")
    parser.add_argument("--shader", required=True, help="shader name without .vfx, e.g. crystal")
    parser.add_argument("--stage", default="ps", choices=("ps", "vs"))
    parser.add_argument("--list", action="store_true", help="list the compiled static combos")
    parser.add_argument("--static", type=int, default=0, help="static combo id")
    parser.add_argument("--dynamic", type=int, default=0, help="dynamic combo id")
    args = parser.parse_args()

    data, frames = load_shader(args.game, args.shader, args.stage)
    static_ids = [int(i) for i in data["m_staticComboIDs"]]
    if args.list:
        print("dynamic combos:", ", ".join(f"{c['m_szName']}" for c in data["m_dynamicComboArray"]))
        for static_id in static_ids:
            print(f"{static_id:6}  {' '.join(combo_values(data['m_staticComboArray'], static_id))}")
        return
    if args.static not in static_ids:
        raise SystemExit(f"static combo {args.static} is not compiled, see --list")
    combo = data["m_staticComboData"][static_ids.index(args.static)]
    dynamic_ids = [int(i) for i in combo["m_dynamicComboIDs"]] or list(range(len(combo["m_byteCodeIndex"])))
    if args.dynamic not in dynamic_ids:
        raise SystemExit(f"dynamic combo {args.dynamic} is not compiled for static combo {args.static}")
    dynamic = dynamic_ids.index(args.dynamic)

    frame = frames[int(combo["m_nByteCodeDataIdx"])]
    blob = decompressor(args.game, frame).decompress(frame, max_output_size=1 << 26)
    offsets = list(data["m_byteCodeData"][int(combo["m_nByteCodeDataIdx"])]["m_offs"])
    index = int(combo["m_byteCodeIndex"][dynamic])
    code = blob[offsets[index]:offsets[index + 1]]
    text = disassemble(args.game, code[code.find(b"DXBC"):])

    names = [variable["m_szName"] for variable in data["m_variableDescriptionArray"]]
    reference = combo["m_dynamicComboVarsRef"][dynamic]
    start = int(reference["m_indexAndRegisterOffsetStart"])
    variables = list(combo["m_dynamicComboVars"])[start:start + int(reference["m_indexAndRegisterOffsetCount"])]
    layout = {packed & 0xFFFF: names[packed >> 16]
              for packed in variables[int(reference["m_nFirstConstantElement"]):] if packed & 0xFFFF != 0xFFFF}

    def name_operand(match):
        register = int(match.group(1))
        parts = []
        for component in match.group(2):
            offset = register * 4 + "xyzw".index(component)
            first = max((o for o in layout if o <= offset), default=None)
            parts.append("?" if first is None else layout[first] + (f"[{offset - first}]" if offset > first else ""))
        return f"{match.group(0)}<{','.join(parts)}>"

    print(f"// {args.shader} {args.stage} static {args.static} "
          f"({' '.join(combo_values(data['m_staticComboArray'], args.static)) or 'none'}), dynamic {args.dynamic} "
          f"({' '.join(combo_values(data['m_dynamicComboArray'], args.dynamic)) or 'none'})")
    print("// bound:", ", ".join(names[packed >> 16] for packed in variables))
    print(re.sub(r"cb0\[(\d+)\]\.([xyzw]+)", name_operand, text))


if __name__ == "__main__":
    main()
