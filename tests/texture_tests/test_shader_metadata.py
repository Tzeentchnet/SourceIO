import os

os.environ["NO_BPY"] = "1"

import pytest

from SourceIO.library.source2.compiled_shader import CompiledShaderMetadata
from SourceIO.library.source2.materials import MaterialSemantics


def processor(mapping, input_index, command="Box"):
    return {
        "m_nChannelDesc": [mapping, 0xFF, 0xFF, 0xFF],
        "m_nInputTextures": [input_index, -1, -1, -1],
        "m_outputColorSpace": 0,
        "m_mipProcessingCommand": command,
    }


def texture_slot(name, processor_index):
    return {
        "m_szName": name,
        "m_type": 14,
        "m_registerType": 4,
        "m_nChannelInfoIndex": [processor_index, -1, -1, -1],
        "m_inputColorSpace": 0,
        "m_szTextureFileEnding": "metal",
        "m_inputProcessingCommand": "",
        "m_defaultInputTexture": "",
    }


def program_data(*, ambiguous=False):
    variables = [
        {"m_szName": "TextureMetalness", "m_type": 0, "m_registerType": 0},
        texture_slot("g_tMetalness", 0),
    ]
    processors = [processor(0x01, 0)]  # source R in the high nibble, destination G in the low nibble
    if ambiguous:
        variables.append(texture_slot("g_tMetalness", 1))
        processors.append(processor(0x02, 0))
    return {
        "m_staticComboArray": [{
            "m_szName": "F_METALNESS_TEXTURE",
            "m_szAliasName": "",
            "m_nMin": 0,
            "m_nMax": 1,
            "m_stringArray": ["off", "on"],
        }],
        "m_variableDescriptionArray": variables,
        "m_textureChannelProcessorArray": processors,
    }


def material_data():
    return {
        "m_shaderName": "csgo_complex.vfx",
        "m_intParams": [{"m_name": "F_METALNESS_TEXTURE", "m_nValue": 1}],
        "m_textureParams": [{
            "m_name": "g_tMetalness",
            "m_pValue": "materials/test/packed_metal.vtex",
        }],
    }


def test_feature_combos_and_channel_processors_are_bounded_metadata():
    metadata = CompiledShaderMetadata.from_program_data(
        program_data(),
        shader_name="csgo_complex",
        features_program=True,
    )
    assert metadata.feature_combos[0].name == "F_METALNESS_TEXTURE"
    assert metadata.feature_combos[0].state_names == ("off", "on")
    channel = metadata.channel_processors[0]
    assert channel.mappings == (("R", "G"),)
    assert channel.input_names == ("TextureMetalness",)

    static_metadata = CompiledShaderMetadata.from_program_data(
        program_data(),
        shader_name="csgo_complex",
        features_program=False,
    )
    assert static_metadata.static_combos[0].name == "F_METALNESS_TEXTURE"
    assert not static_metadata.feature_combos


def test_compiled_shader_metadata_rejects_unbounded_arrays():
    oversized = program_data()
    oversized["m_staticComboArray"] = oversized["m_staticComboArray"] * 4097
    with pytest.raises(ValueError, match="maximum supported"):
        CompiledShaderMetadata.from_program_data(oversized)


def test_material_semantics_reconstruct_evidence_backed_packed_channel():
    metadata = CompiledShaderMetadata.from_program_data(program_data())
    semantics = MaterialSemantics.from_material_data(material_data(), metadata)
    packed = semantics.packed_channels["g_tMetalness"]
    assert len(packed) == 1
    assert packed[0].packed_channel == "G"
    assert packed[0].source_texture == "TextureMetalness"
    assert packed[0].source_channel == "R"
    assert semantics.feature_values == {"F_METALNESS_TEXTURE": 1}
    assert not semantics.diagnostics


def test_ambiguous_shader_variants_are_diagnostic_not_guessed():
    metadata = CompiledShaderMetadata.from_program_data(program_data(ambiguous=True))
    semantics = MaterialSemantics.from_material_data(material_data(), metadata)
    assert "g_tMetalness" not in semantics.packed_channels
    assert "source2.material.texture-slot-ambiguous" in {
        diagnostic.code for diagnostic in semantics.diagnostics
    }


def test_missing_shader_metadata_is_explicit():
    semantics = MaterialSemantics.from_material_data(material_data())
    assert "source2.material.shader-metadata-missing" in {
        diagnostic.code for diagnostic in semantics.diagnostics
    }
