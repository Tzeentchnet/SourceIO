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
from SourceIO.library.source2.compiled_shader import CompiledShaderMetadata  # noqa: E402
from SourceIO.library.source2.compiled_resource import CompiledResource  # noqa: E402
from SourceIO.library.utils import MemoryBuffer, TinyPath  # noqa: E402
from SourceIO.library.utils.pylib import VPKFile  # noqa: E402

TEXTURE = 14  # m_type of texture variables
COMPILED_TEXTURE = 4  # m_registerType of the g_t* slots a material references


def load_features(game: Path, shader: str):
    vpk = VPKFile(TinyPath(game / "shaders_pc_dir.vpk"))
    data = vpk.find_file(TinyPath(f"shaders/vfx/{shader}_pc_50_features.vcs"))
    if data is None:
        raise SystemExit(f"{shader}_pc_50_features.vcs is not in {game / 'shaders_pc_dir.vpk'}")
    resource = CompiledResource.from_buffer(MemoryBuffer(bytes(data)), TinyPath(shader))
    program = resource.get_block(KVBlock, block_name="DATA")["m_programData"]
    metadata = CompiledShaderMetadata.from_program_data(
        program,
        shader_name=shader,
        features_program=True,
    )
    return program, metadata


def packing(slot) -> str:
    parts = []
    for processor in slot.processors:
        inputs = "+".join(processor.input_names)
        outputs = "".join(processor.destination_channels)
        sources = "".join(processor.source_channels)
        mapping = outputs if outputs == sources else f"{outputs} <- {sources}"
        parts.append(f"{mapping} <- {inputs} ({processor.mip_processing_command})")
    return " | ".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, required=True, help="mod folder that holds shaders_pc_dir.vpk")
    parser.add_argument("--shader", required=True, help="shader name without .vfx, e.g. csgo_complex")
    parser.add_argument("--filter", default="", help="regular expression on names and UI groups")
    args = parser.parse_args()
    program, metadata = load_features(args.game, args.shader)
    pattern = re.compile(args.filter, re.IGNORECASE)

    for combo in metadata.feature_combos:
        if pattern.search(combo.name) or pattern.search(combo.alias_name):
            values = " ".join(f"{index}={name}" for index, name in enumerate(combo.state_names))
            print(
                f"COMBO {combo.name:40} {combo.minimum}..{combo.maximum}  "
                f"{combo.alias_name}  {values}".rstrip()
            )

    variables = program["m_variableDescriptionArray"]
    slots = {slot.variable_index: slot for slot in metadata.texture_slots}
    for variable_index, variable in enumerate(variables):
        name, group = variable["m_szName"], variable["m_szUiGroup"]
        if not (pattern.search(name) or pattern.search(group)):
            continue
        if variable["m_type"] == TEXTURE and variable["m_registerType"] == COMPILED_TEXTURE:
            print(f"SLOT  {name:40} {packing(slots[variable_index])}")
            continue
        default = variable["m_intDefault"] if "m_intDefault" in variable else variable.get("m_flDefault")
        default = [round(float(value), 4) for value in default] if default is not None else None
        extra = ""
        if variable["m_type"] == TEXTURE:
            color_space = "sRGB" if variable.get("m_inputColorSpace") == 1 else "linear"
            extra = f" {color_space} _{variable.get('m_szTextureFileEnding', '')}"
        computed = " (computed)" if variable.get("m_pCompiledExpression") else ""
        print(f"VAR   {name:40} default={default}{extra}{computed}  [{group}]")

    for diagnostic in metadata.diagnostics:
        print(f"{diagnostic.severity.value.upper()}: {diagnostic.message}", file=sys.stderr)


if __name__ == "__main__":
    main()
