from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..compiled_shader import CompiledShaderMetadata, ShaderTextureSlot, TextureChannelProcessor
from ..interfaces import Diagnostic, DiagnosticSeverity


@dataclass(frozen=True, slots=True)
class PackedChannelSemantic:
    packed_texture: str
    packed_channel: str
    source_texture: str
    source_channel: str
    processor: str
    evidence: str = "compiled-shader texture channel processor"


@dataclass(frozen=True, slots=True)
class MaterialSemantics:
    """Material feature state and evidence-backed packed texture meanings."""

    shader_name: str
    feature_values: Mapping[str, int]
    static_values: Mapping[str, int]
    texture_paths: Mapping[str, str]
    packed_channels: Mapping[str, tuple[PackedChannelSemantic, ...]]
    diagnostics: tuple[Diagnostic, ...] = ()
    raw_material_data: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    shader_metadata: CompiledShaderMetadata | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_resource(
            cls,
            material_resource,
            shader_metadata: CompiledShaderMetadata | None = None,
    ) -> MaterialSemantics:
        data = material_resource.data_block
        if not isinstance(data, Mapping):
            raise ValueError("Compiled material has no DATA mapping")
        return cls.from_material_data(data, shader_metadata)

    @classmethod
    def from_material_data(
            cls,
            data: Mapping[str, Any],
            shader_metadata: CompiledShaderMetadata | None = None,
    ) -> MaterialSemantics:
        shader_name = str(data.get("m_shaderName", ""))
        int_values = {
            str(item["m_name"]): int(item["m_nValue"])
            for item in data.get("m_intParams", ())
            if isinstance(item, Mapping) and "m_name" in item and "m_nValue" in item
        }
        feature_values = {name: value for name, value in int_values.items() if name.startswith("F_")}
        static_values = {name: value for name, value in int_values.items() if name.startswith("S_")}
        texture_paths = {
            str(item["m_name"]): str(item["m_pValue"])
            for item in data.get("m_textureParams", ())
            if isinstance(item, Mapping) and "m_name" in item and "m_pValue" in item
        }

        diagnostics: list[Diagnostic] = []
        packed_channels: dict[str, tuple[PackedChannelSemantic, ...]] = {}
        if shader_metadata is None:
            if texture_paths:
                diagnostics.append(Diagnostic(
                    "source2.material.shader-metadata-missing",
                    f"Compiled shader metadata for {shader_name!r} is unavailable; "
                    "packed texture channels cannot be safely reversed",
                    DiagnosticSeverity.WARNING,
                ))
        else:
            diagnostics.extend(shader_metadata.diagnostics)
            for texture_name in texture_paths:
                variants = shader_metadata.texture_slot_variants(texture_name)
                if not variants:
                    diagnostics.append(Diagnostic(
                        "source2.material.texture-slot-missing",
                        f"Texture {texture_name!r} is not present in compiled shader metadata "
                        f"for {shader_name!r}",
                        DiagnosticSeverity.WARNING,
                    ))
                    continue

                unique_variants = _unique_slot_variants(variants)
                if len(unique_variants) > 1:
                    diagnostics.append(Diagnostic(
                        "source2.material.texture-slot-ambiguous",
                        f"Texture {texture_name!r} has {len(unique_variants)} distinct compiled "
                        "channel layouts; static-combo write metadata is required to choose safely",
                        DiagnosticSeverity.WARNING,
                        details={"texture": texture_name, "variant_count": len(unique_variants)},
                    ))
                    continue

                semantics: list[PackedChannelSemantic] = []
                for processor in unique_variants[0].processors:
                    semantics.extend(_processor_semantics(texture_name, processor, diagnostics))
                if semantics:
                    packed_channels[texture_name] = tuple(semantics)

        return cls(
            shader_name,
            feature_values,
            static_values,
            texture_paths,
            packed_channels,
            tuple(diagnostics),
            data,
            shader_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "shader": self.shader_name,
            "features": dict(self.feature_values),
            "statics": dict(self.static_values),
            "textures": dict(self.texture_paths),
            "packed_channels": {
                name: [
                    {
                        "packed_channel": semantic.packed_channel,
                        "source_texture": semantic.source_texture,
                        "source_channel": semantic.source_channel,
                        "processor": semantic.processor,
                        "evidence": semantic.evidence,
                    }
                    for semantic in semantics
                ]
                for name, semantics in self.packed_channels.items()
            },
            "diagnostics": [
                {
                    "code": diagnostic.code,
                    "severity": diagnostic.severity.value,
                    "message": diagnostic.message,
                }
                for diagnostic in self.diagnostics
            ],
        }


def _slot_signature(slot: ShaderTextureSlot) -> tuple[Any, ...]:
    return tuple(
        (
            processor.mappings,
            processor.input_names,
            processor.output_color_space,
            processor.mip_processing_command,
        )
        for processor in slot.processors
    )


def _unique_slot_variants(variants: tuple[ShaderTextureSlot, ...]) -> tuple[ShaderTextureSlot, ...]:
    unique: dict[tuple[Any, ...], ShaderTextureSlot] = {}
    for variant in variants:
        unique.setdefault(_slot_signature(variant), variant)
    return tuple(unique.values())


def _processor_semantics(
        texture_name: str,
        processor: TextureChannelProcessor,
        diagnostics: list[Diagnostic],
) -> list[PackedChannelSemantic]:
    command = processor.mip_processing_command
    inputs = processor.input_names
    if command in {"HemiOctIsoRoughness_RG_B", "AnisoNormal"} and inputs:
        semantics = [
            PackedChannelSemantic(texture_name, channel, inputs[0], channel, command)
            for channel in "RGB"
        ]
        if len(inputs) == 2:
            semantics.append(PackedChannelSemantic(texture_name, "A", inputs[1], "R", command))
        return semantics

    if command == "AnisoRoughness_RG" and len(inputs) > 1:
        diagnostics.append(Diagnostic(
            "source2.material.generated-channel-layout",
            f"{texture_name!r} uses generated {command} data from multiple inputs; "
            "the original channels cannot be safely reconstructed",
            DiagnosticSeverity.WARNING,
            details={"texture": texture_name, "inputs": inputs, "processor": command},
        ))
        return []

    if len(inputs) != 1:
        diagnostics.append(Diagnostic(
            "source2.material.channel-input-ambiguous",
            f"{texture_name!r} channel processor {processor.index} has {len(inputs)} inputs; "
            "channel ownership cannot be safely inferred",
            DiagnosticSeverity.WARNING,
            details={"texture": texture_name, "inputs": inputs, "processor_index": processor.index},
        ))
        return []

    return [
        PackedChannelSemantic(texture_name, destination, inputs[0], source, command or "copy")
        for source, destination in processor.mappings
    ]
