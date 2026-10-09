from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..interfaces import Diagnostic, DiagnosticSeverity


MAX_COMBOS = 4096
MAX_VARIABLES = 8192
MAX_CHANNEL_PROCESSORS = 8192
TEXTURE_VARIABLE_TYPE = 14
COMPILED_TEXTURE_REGISTER_TYPE = 4
CHANNEL_NAMES = "RGBA"


def _array(mapping: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = mapping.get(key, ())
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray, memoryview)) or not isinstance(value, Sequence):
        raise TypeError(f"Compiled shader field {key!r} must be an array")
    return value


def _bounded(values: Sequence[Any], maximum: int, label: str) -> Sequence[Any]:
    if len(values) > maximum:
        raise ValueError(f"Compiled shader declares {len(values)} {label}; maximum supported is {maximum}")
    return values


def _int_tuple(value: Any, *, length: int | None = None) -> tuple[int, ...]:
    if value is None:
        result = ()
    elif isinstance(value, (bytes, bytearray, memoryview)):
        result = tuple(bytes(value))
    else:
        result = tuple(int(item) for item in value)
    if length is not None:
        result = (result + (-1,) * length)[:length]
    return result


@dataclass(frozen=True, slots=True)
class ShaderCombo:
    index: int
    name: str
    alias_name: str
    minimum: int
    maximum: int
    state_names: tuple[str, ...]
    combo_type: Any = None
    source_type: int | None = None
    feature_index: int | None = None
    comparison_value: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class TextureChannelProcessor:
    index: int
    source_channels: tuple[str, ...]
    destination_channels: tuple[str, ...]
    input_texture_indices: tuple[int, ...]
    input_names: tuple[str, ...]
    output_color_space: int
    mip_processing_command: str
    raw_channel_description: bytes
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def mappings(self) -> tuple[tuple[str, str], ...]:
        return tuple(zip(self.source_channels, self.destination_channels))


@dataclass(frozen=True, slots=True)
class ShaderTextureSlot:
    variable_index: int
    name: str
    channel_processor_indices: tuple[int, ...]
    processors: tuple[TextureChannelProcessor, ...]
    input_color_space: int | None
    file_suffix: str
    input_processing_command: str
    default_texture: str
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CompiledShaderMetadata:
    shader_name: str
    feature_combos: tuple[ShaderCombo, ...]
    static_combos: tuple[ShaderCombo, ...]
    texture_slots: tuple[ShaderTextureSlot, ...]
    channel_processors: tuple[TextureChannelProcessor, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    raw_program_data: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_resource(cls, resource, *, features_program: bool | None = None) -> CompiledShaderMetadata:
        from ..blocks.kv3_block import KVBlock

        data = resource.get_block(KVBlock, block_name="DATA")
        if not isinstance(data, Mapping):
            raise ValueError("Compiled shader has no KV3 DATA block")
        program_data = data.get("m_programData")
        if not isinstance(program_data, Mapping):
            raise ValueError("Compiled shader DATA has no m_programData object")
        shader_name = str(data.get("m_shaderName") or getattr(resource, "name", ""))
        if features_program is None:
            resource_name = str(getattr(resource, "name", "")).casefold()
            features_program = resource_name.endswith("_features") or "_features." in resource_name
        return cls.from_program_data(program_data, shader_name=shader_name, features_program=features_program)

    @classmethod
    def from_program_data(
            cls,
            program_data: Mapping[str, Any],
            *,
            shader_name: str = "",
            features_program: bool = True,
    ) -> CompiledShaderMetadata:
        diagnostics: list[Diagnostic] = []
        combo_values = _bounded(_array(program_data, "m_staticComboArray"), MAX_COMBOS, "static combos")
        combos = tuple(_parse_combo(index, value) for index, value in enumerate(combo_values))

        variables = _bounded(
            _array(program_data, "m_variableDescriptionArray"),
            MAX_VARIABLES,
            "variables",
        )
        variable_names = tuple(
            str(value.get("m_szName", "")) if isinstance(value, Mapping) else ""
            for value in variables
        )
        processor_values = _bounded(
            _array(program_data, "m_textureChannelProcessorArray"),
            MAX_CHANNEL_PROCESSORS,
            "texture channel processors",
        )
        processors = tuple(
            _parse_processor(index, value, variable_names, diagnostics)
            for index, value in enumerate(processor_values)
        )

        slots = []
        for variable_index, variable in enumerate(variables):
            if not isinstance(variable, Mapping):
                diagnostics.append(Diagnostic(
                    "source2.shader.variable.invalid",
                    f"Shader variable {variable_index} is not an object",
                    DiagnosticSeverity.WARNING,
                ))
                continue
            if int(variable.get("m_type", -1)) != TEXTURE_VARIABLE_TYPE:
                continue
            if int(variable.get("m_registerType", -1)) != COMPILED_TEXTURE_REGISTER_TYPE:
                continue

            processor_indices = tuple(
                index for index in _int_tuple(variable.get("m_nChannelInfoIndex"), length=4)
                if index >= 0
            )
            resolved_processors = []
            for processor_index in processor_indices:
                if processor_index >= len(processors):
                    diagnostics.append(Diagnostic(
                        "source2.shader.channel-processor.missing",
                        f"Texture slot {variable.get('m_szName', '')!r} references missing "
                        f"channel processor {processor_index}",
                        DiagnosticSeverity.WARNING,
                        details={"processor_index": processor_index, "variable_index": variable_index},
                    ))
                    continue
                resolved_processors.append(processors[processor_index])

            slots.append(ShaderTextureSlot(
                variable_index,
                str(variable.get("m_szName", "")),
                processor_indices,
                tuple(resolved_processors),
                int(variable["m_inputColorSpace"]) if "m_inputColorSpace" in variable else None,
                str(variable.get("m_szTextureFileEnding", "")),
                str(variable.get("m_inputProcessingCommand", "")),
                str(variable.get("m_defaultInputTexture", "")),
                variable,
            ))

        return cls(
            shader_name,
            combos if features_program else (),
            () if features_program else combos,
            tuple(slots),
            processors,
            tuple(diagnostics),
            program_data,
        )

    def texture_slot_variants(self, name: str) -> tuple[ShaderTextureSlot, ...]:
        return tuple(slot for slot in self.texture_slots if slot.name == name)


def _parse_combo(index: int, value: Any) -> ShaderCombo:
    if not isinstance(value, Mapping):
        raise TypeError(f"Compiled shader combo {index} is not an object")
    comparison = value.get("m_nCompareValue")
    feature_index = value.get("m_iFeatureIndex")
    source_type = value.get("m_shaderComboSourceType")
    return ShaderCombo(
        index,
        str(value.get("m_szName", "")),
        str(value.get("m_szAliasName", "")),
        int(value.get("m_nMin", 0)),
        int(value.get("m_nMax", 0)),
        tuple(str(item) for item in _array(value, "m_stringArray")),
        value.get("m_comboType"),
        int(source_type) if source_type is not None else None,
        int(feature_index) if feature_index is not None else None,
        int(comparison) if comparison is not None else None,
        value,
    )


def _parse_processor(
        index: int,
        value: Any,
        variable_names: tuple[str, ...],
        diagnostics: list[Diagnostic],
) -> TextureChannelProcessor:
    if not isinstance(value, Mapping):
        raise TypeError(f"Texture channel processor {index} is not an object")

    description_values = _int_tuple(value.get("m_nChannelDesc"))
    raw_description = bytes((description_values + (0xFF,) * 4)[:4])
    source_channels = []
    destination_channels = []
    for packed in raw_description:
        if packed == 0xFF:
            break
        source = packed >> 4
        destination = packed & 0x0F
        if source > 3 or destination > 3:
            diagnostics.append(Diagnostic(
                "source2.shader.channel-mapping.invalid",
                f"Texture channel processor {index} has invalid mapping byte 0x{packed:02x}",
                DiagnosticSeverity.WARNING,
                details={"processor_index": index, "mapping": packed},
            ))
            continue
        source_channels.append(CHANNEL_NAMES[source])
        destination_channels.append(CHANNEL_NAMES[destination])

    input_indices = tuple(
        input_index
        for input_index in _int_tuple(value.get("m_nInputTextures"), length=4)
        if input_index >= 0
    )
    input_names = []
    for input_index in input_indices:
        if input_index >= len(variable_names):
            diagnostics.append(Diagnostic(
                "source2.shader.input-texture.missing",
                f"Texture channel processor {index} references missing variable {input_index}",
                DiagnosticSeverity.WARNING,
                details={"processor_index": index, "variable_index": input_index},
            ))
            input_names.append(f"<missing:{input_index}>")
        else:
            input_names.append(variable_names[input_index])

    return TextureChannelProcessor(
        index,
        tuple(source_channels),
        tuple(destination_channels),
        input_indices,
        tuple(input_names),
        int(value.get("m_outputColorSpace", 0)),
        str(value.get("m_mipProcessingCommand", "")),
        raw_description,
        value,
    )
