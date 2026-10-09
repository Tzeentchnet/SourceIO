"""Print a Source 2 shader's material parameters: defaults, UI groups and how textures are channel-packed.

Usage:  python -I tools/shader_params.py --game "<steam>/common/Counter-Strike Global Offensive/game/csgo"
                  --shader csgo_vertexlitgeneric [--filter REGEX]

Materials store only the parameters an artist changed, so the defaults have to come from the shader.
CS2 ships each shader's ``<name>_pc_50_features.vcs`` in ``shaders_pc_dir.vpk`` as a compiled resource
whose DATA block is KV3. A compiled texture slot (``g_t*``) is listed as the channels each source fills:
``g_tMetalness  G <- TextureMetalness (Box) | B <- TextureCloth (Box)``. A name can appear more than once
(alternative packings). Run with Blender's Python; the script finds SourceIO itself.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from SourceIO.library.source2.blocks.kv3_block import KVBlock  # noqa: E402
from SourceIO.library.source2.compiled_resource import CompiledResource  # noqa: E402
from SourceIO.library.utils import MemoryBuffer, TinyPath  # noqa: E402
from SourceIO.library.utils.pylib import VPKFile  # noqa: E402

CHANNELS = "RGBA"
TEXTURE = 14  # m_type of texture variables
COMPILED_TEXTURE = 4  # m_registerType of the g_t* slots a material references


def load_features(game: Path, shader: str):
    vpk = VPKFile(TinyPath(game / "shaders_pc_dir.vpk"))
    data = vpk.find_file(TinyPath(f"shaders/vfx/{shader}_pc_50_features.vcs"))
    if data is None:
        raise SystemExit(f"{shader}_pc_50_features.vcs is not in {game / 'shaders_pc_dir.vpk'}")
    resource = CompiledResource.from_buffer(MemoryBuffer(bytes(data)), TinyPath(shader))
    return resource.get_block(KVBlock, block_name="DATA")["m_programData"]


def packing(variable, variables, processors) -> str:
    parts = []
    for index in variable["m_nChannelInfoIndex"]:
        if index < 0:
            continue
        processor = processors[index]
        inputs = "+".join(variables[i]["m_szName"] for i in processor["m_nInputTextures"] if i >= 0)
        # One byte per channel: output channel in the high nibble, source channel in the low one; 0xff unused.
        outputs = "".join(CHANNELS[byte >> 4] for byte in bytes(processor["m_nChannelDesc"]) if byte != 0xff)
        parts.append(f"{outputs} <- {inputs} ({processor['m_mipProcessingCommand']})")
    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, required=True, help="mod folder that holds shaders_pc_dir.vpk")
    parser.add_argument("--shader", required=True, help="shader name without .vfx, e.g. csgo_complex")
    parser.add_argument("--filter", default="", help="regular expression on names and UI groups")
    args = parser.parse_args()
    program = load_features(args.game, args.shader)
    pattern = re.compile(args.filter, re.IGNORECASE)

    for combo in program["m_staticComboArray"]:
        alias = combo.get("m_szAliasName", "")
        if pattern.search(combo["m_szName"]) or pattern.search(alias):
            values = " ".join(f"{index}={name}" for index, name in enumerate(combo.get("m_stringArray", [])))
            print(f"COMBO {combo['m_szName']:40} {combo['m_nMin']}..{combo['m_nMax']}  {alias}  {values}".rstrip())

    variables = program["m_variableDescriptionArray"]
    processors = program["m_textureChannelProcessorArray"]
    for variable in variables:
        name, group = variable["m_szName"], variable["m_szUiGroup"]
        if not (pattern.search(name) or pattern.search(group)):
            continue
        if variable["m_type"] == TEXTURE and variable["m_registerType"] == COMPILED_TEXTURE:
            print(f"SLOT  {name:40} {packing(variable, variables, processors)}")
            continue
        default = variable["m_intDefault"] if "m_intDefault" in variable else variable.get("m_flDefault")
        default = [round(float(value), 4) for value in default] if default is not None else None
        extra = ""
        if variable["m_type"] == TEXTURE:
            color_space = "sRGB" if variable.get("m_inputColorSpace") == 1 else "linear"
            extra = f" {color_space} _{variable.get('m_szTextureFileEnding', '')}"
        computed = " (computed)" if variable.get("m_pCompiledExpression") else ""
        print(f"VAR   {name:40} default={default}{extra}{computed}  [{group}]")


if __name__ == "__main__":
    main()
