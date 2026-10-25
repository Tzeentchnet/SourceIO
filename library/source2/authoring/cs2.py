from __future__ import annotations

import json
import math
import struct

from ..export import (
    DiagnosticSeverity,
    LossKind,
    LossReport,
)
from ..utils.kv3_generator import KV3mdl
from ..workshop import ContentRelativePath, WorkshopGame
from ..workshop.target import Source2StaticAuthoringContract
from .assets import (
    AuthoringMaterial,
    ExtractedAuthoringModel,
    MaterialAlphaMode,
    TextureRole,
)
from .models import (
    AuthoringArtifact,
    AuthoringArtifactKind,
    AuthoringBackendOutput,
    CompileStep,
    Source2AuthoringRequest,
)


def _float(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("CS2 material values must be finite")
    text = format(number, ".9g")
    return "0.0" if text == "-0" else text


def _quoted(value: str) -> str:
    if any(character in value for character in ("\0", "\r", "\n")):
        raise ValueError("CS2 authored strings cannot contain control characters")
    return json.dumps(value, ensure_ascii=True)


def solid_color_tga(
        color: tuple[float, float, float, float],
        *,
        width: int = 4,
        height: int = 4,
) -> bytes:
    if width <= 0 or height <= 0:
        raise ValueError("TGA dimensions must be positive")
    rgba = bytes(
        max(0, min(255, round(float(value) * 255.0)))
        for value in color
    )
    if len(rgba) != 4:
        raise ValueError("Solid color TGA values must contain four channels")
    header = struct.pack(
        "<BBBHHBHHHHBB",
        0,
        0,
        2,
        0,
        0,
        0,
        0,
        0,
        width,
        height,
        32,
        0x28,
    )
    blue, green, red, alpha = rgba[2], rgba[1], rgba[0], rgba[3]
    return header + bytes((blue, green, red, alpha)) * (width * height)


def serialize_vmat(
        material: AuthoringMaterial,
        color_texture: ContentRelativePath,
        *,
        normal_texture: ContentRelativePath | None = None,
        roughness_texture: ContentRelativePath | None = None,
        shader: str = "csgo_complex.vfx",
) -> str:
    tint = " ".join(
        f"{float(value):.6f}"
        for value in (*material.base_color[:3], 0.0)
    )
    lines = [
        "Layer0",
        "{",
        f"    shader {_quoted(shader)}",
        f'    g_vColorTint "[{tint}]"',
        f"    TextureColor {_quoted(color_texture.as_posix())}",
    ]
    if normal_texture is not None:
        lines.append(f"    TextureNormal {_quoted(normal_texture.as_posix())}")
    if roughness_texture is not None:
        lines.append(f"    TextureRoughness {_quoted(roughness_texture.as_posix())}")
    lines.extend(("}", ""))
    return "\n".join(lines)


def _modeldoc(
        model: ExtractedAuthoringModel,
        fbx_path: ContentRelativePath,
        material_paths: dict[str, ContentRelativePath],
        contract: Source2StaticAuthoringContract,
) -> str:
    document = KV3mdl(
        include_default_animation=False,
        modeldoc_version=contract.modeldoc_version,
    )
    document.storage["rootNode"]["importer_notes"] = "Authored by SourceIO"
    document.storage["rootNode"]["model_archetype"] = contract.model_archetype
    mesh_names = tuple(
        str(mesh.metadata.get("blender_mesh", mesh.name))
        for mesh in model.document.meshes
    )
    document.add_render_mesh(
        model.name,
        fbx_path.as_posix(),
        mesh_class=contract.render_mesh_class,
        import_filter=mesh_names if contract.render_mesh_uses_import_filter else None,
        import_scale=1.0,
    )
    if material_paths:
        group = document.add_skin("default", skin_class="DefaultMaterialGroup")
        for source, target in sorted(material_paths.items()):
            document.add_skin_remap(group, source, target.as_posix())
    return document.dump()


class CS2AuthoringBackend:
    def __init__(self, contract: Source2StaticAuthoringContract | None = None):
        self.contract = contract

    def build(
            self,
            request: Source2AuthoringRequest,
            models: tuple[ExtractedAuthoringModel, ...],
            *,
            report: LossReport | None = None,
    ) -> AuthoringBackendOutput:
        loss_report = report if report is not None else LossReport()
        contract = self.contract or request.target.static_authoring_contract
        if request.target.layout.game != WorkshopGame.COUNTER_STRIKE_2:
            loss_report.record(
                "authoring.cs2.target.invalid",
                "The CS2 backend requires a Counter-Strike 2 Workshop target.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
            )
        if contract is None:
            loss_report.record(
                "authoring.cs2.contract.missing",
                "The configured CS2 target has no validated static-authoring contract.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
            )
            return AuthoringBackendOutput.create((), (), (), report=loss_report)
        if not models:
            loss_report.record(
                "authoring.cs2.models.none",
                "The CS2 backend received no extracted models.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.MISSING,
            )

        artifacts: dict[ContentRelativePath, AuthoringArtifact] = {}
        material_steps: dict[ContentRelativePath, CompileStep] = {}
        model_steps: dict[ContentRelativePath, CompileStep] = {}
        material_specs: dict[str, AuthoringMaterial] = {}
        model_paths = []

        def add_artifact(artifact: AuthoringArtifact):
            previous = artifacts.get(artifact.path)
            if previous is not None and previous != artifact:
                loss_report.record(
                    "authoring.cs2.artifact.collision",
                    "Two CS2 assets produce different data at the same output path.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"path": artifact.path.as_posix()},
                )
                return
            artifacts[artifact.path] = artifact

        for model in sorted(models, key=lambda item: (item.name.casefold(), item.name)):
            fbx_path = request.model_directory.joinpath(f"{model.name}.fbx")
            vmdl_path = request.model_directory.joinpath(f"{model.name}.vmdl")
            model_paths.append(vmdl_path)
            add_artifact(AuthoringArtifact(
                fbx_path,
                AuthoringArtifactKind.MESH,
                model.fbx_payload,
            ))

            remaps = {}
            for material in model.materials:
                previous = material_specs.get(material.name)
                if previous is not None and previous != material:
                    loss_report.record(
                        "authoring.cs2.material.collision",
                        "Different materials normalize to the same authored name.",
                        severity=DiagnosticSeverity.ERROR,
                        loss_kind=LossKind.SAFETY,
                        details={"material": material.name},
                    )
                    continue
                material_specs[material.name] = material
                remaps[material.name] = (
                    material.existing_resource
                    if material.existing_resource is not None
                    else request.material_directory.joinpath(f"{material.name}.vmat")
                )

            add_artifact(AuthoringArtifact(
                vmdl_path,
                AuthoringArtifactKind.MODEL,
                _modeldoc(model, fbx_path, remaps, contract),
            ))
            model_steps[vmdl_path] = CompileStep(
                vmdl_path,
                (vmdl_path.compiled(),),
                contract.compiler_arguments if contract is not None else (),
            )

        for material_name, material in sorted(material_specs.items()):
            if material.existing_resource is not None:
                continue
            textures = {
                texture.role: texture
                for texture in material.textures
            }
            if len(textures) != len(material.textures):
                loss_report.record(
                    "authoring.cs2.texture.role_duplicate",
                    "A material cannot author two textures for the same PBR role.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"material": material_name},
                )
            unsupported_roles = sorted(
                role.value for role in textures
                if role not in {
                    TextureRole.BASE_COLOR,
                    TextureRole.NORMAL,
                    TextureRole.ROUGHNESS,
                }
            )
            if unsupported_roles:
                loss_report.record(
                    "authoring.cs2.material.texture_unsupported",
                    "The validated CS2 slice currently authors base-color, normal, and roughness textures.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    details={
                        "material": material_name,
                        "roles": unsupported_roles,
                    },
                )
            if material.metallic != 0.0:
                loss_report.record(
                    "authoring.cs2.material.scalar_pbr_unsupported",
                    "Non-default metallic values are outside the validated CS2 slice.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    details={"material": material_name},
                )
            if material.alpha_mode != MaterialAlphaMode.OPAQUE:
                loss_report.record(
                    "authoring.cs2.material.alpha_unsupported",
                    "Alpha blending and alpha testing are outside the validated CS2 slice.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    details={"material": material_name},
                )

            color_texture = textures.get(TextureRole.BASE_COLOR)
            if color_texture is None:
                texture_name = f"{material_name}_{TextureRole.BASE_COLOR.value}"
                texture_payload = solid_color_tga((1.0, 1.0, 1.0, 1.0))
                texture_extension = contract.source_texture_suffix if contract is not None else ".tga"
            else:
                texture_name = color_texture.name
                texture_payload = color_texture.payload
                texture_extension = color_texture.extension

            def stage_texture(name: str, payload: bytes, extension: str) -> ContentRelativePath:
                if contract is not None and extension != contract.source_texture_suffix:
                    loss_report.record(
                        "authoring.cs2.texture.format_invalid",
                        f"The validated CS2 contract requires {contract.source_texture_suffix} source textures.",
                        severity=DiagnosticSeverity.ERROR,
                        loss_kind=LossKind.SAFETY,
                        details={
                            "material": material_name,
                            "extension": extension,
                        },
                    )
                path = request.texture_directory.joinpath(f"{name}{extension}")
                add_artifact(AuthoringArtifact(
                    path,
                    AuthoringArtifactKind.TEXTURE_SOURCE,
                    payload,
                ))
                return path

            color_source_path = stage_texture(
                texture_name,
                texture_payload,
                texture_extension,
            )
            normal_texture = textures.get(TextureRole.NORMAL)
            normal_source_path = (
                stage_texture(
                    normal_texture.name,
                    normal_texture.payload,
                    normal_texture.extension,
                )
                if normal_texture is not None
                else None
            )
            roughness_texture = textures.get(TextureRole.ROUGHNESS)
            if roughness_texture is not None:
                roughness_source_path = stage_texture(
                    roughness_texture.name,
                    roughness_texture.payload,
                    roughness_texture.extension,
                )
            elif material.roughness != 0.5:
                roughness_source_path = stage_texture(
                    f"{material_name}_{TextureRole.ROUGHNESS.value}",
                    solid_color_tga((
                        material.roughness,
                        material.roughness,
                        material.roughness,
                        1.0,
                    )),
                    contract.source_texture_suffix if contract is not None else ".tga",
                )
            else:
                roughness_source_path = None

            if contract is not None and contract.source_texture_is_compiler_input:
                loss_report.record(
                    "authoring.cs2.texture.contract_unexpected",
                    "The captured CS2 contract requires VMAT-transitive texture compilation.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                )

            material_path = request.material_directory.joinpath(
                f"{material_name}.vmat"
            )
            add_artifact(AuthoringArtifact(
                material_path,
                AuthoringArtifactKind.MATERIAL,
                serialize_vmat(
                    material,
                    color_source_path,
                    normal_texture=normal_source_path,
                    roughness_texture=roughness_source_path,
                    shader=contract.material_shader if contract is not None else "csgo_complex.vfx",
                ),
            ))
            material_steps[material_path] = CompileStep(
                material_path,
                (material_path.compiled(),),
                contract.compiler_arguments if contract is not None else (),
            )

        steps = tuple(
            mapping[path]
            for mapping in (material_steps, model_steps)
            for path in sorted(mapping, key=ContentRelativePath.as_posix)
        )
        return AuthoringBackendOutput.create(
            (
                artifacts[path]
                for path in sorted(artifacts, key=ContentRelativePath.as_posix)
            ),
            steps,
            model_paths,
            report=loss_report,
        )
