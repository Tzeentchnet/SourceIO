from __future__ import annotations

from ...source2.authoring import (
    AuthoringMaterial,
    ExtractedAuthoringModel,
    MaterialAlphaMode,
    TextureRole,
    solid_color_tga,
)
from ...source2.authoring.models import is_valid_asset_name
from ...source2.export import (
    DiagnosticSeverity,
    LossKind,
    LossReport,
)
from ...source2.workshop import ContentRelativePath
from .assets import Source1AuthoringMaterial
from .formats import (
    serialize_qc,
    serialize_smd,
    serialize_vmt,
    serialize_vmt_patch,
    serialize_vtex_settings,
)
from .models import (
    Source1ArtifactKind,
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
    Source1AuthoringRequest,
    Source1BackendOutput,
    Source1CompileOutput,
    Source1CompileStep,
    Source1CompilerKind,
)


class Source1AuthoringBackend:
    def build(
            self,
            request: Source1AuthoringRequest,
            models: tuple[ExtractedAuthoringModel, ...],
            *,
            report: LossReport | None = None,
    ) -> Source1BackendOutput:
        loss_report = report if report is not None else LossReport()
        if not models:
            loss_report.record(
                "authoring.source1.models.none",
                "The Source 1 backend received no extracted models.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.MISSING,
            )

        artifacts: dict[
            tuple[Source1ArtifactRoot, ContentRelativePath],
            Source1AuthoringArtifact,
        ] = {}
        compile_steps: dict[
            tuple[Source1CompilerKind, ContentRelativePath],
            Source1CompileStep,
        ] = {}
        material_specs: dict[str, AuthoringMaterial] = {}
        model_paths = []
        asset_model_source = request.model_source_directory.joinpath(request.asset_name)
        asset_material_source = request.material_source_directory.joinpath(
            request.asset_name
        )
        asset_model_directory = request.model_directory.joinpath(request.asset_name)
        asset_material_directory = request.material_directory.joinpath(
            request.asset_name
        )

        def add_artifact(artifact: Source1AuthoringArtifact):
            key = (artifact.root, artifact.path)
            previous = artifacts.get(key)
            if previous is not None and previous != artifact:
                loss_report.record(
                    "authoring.source1.artifact.collision",
                    "Two Source 1 assets produce different data at the same output path.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={
                        "root": artifact.root.value,
                        "path": artifact.path.as_posix(),
                    },
                )
                return
            artifacts[key] = artifact

        for model in sorted(models, key=lambda item: (item.name.casefold(), item.name)):
            if not is_valid_asset_name(model.name):
                loss_report.record(
                    "authoring.source1.model_name.invalid",
                    "Source 1 model names must contain only lowercase ASCII letters, digits, and underscores.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"model": model.name},
                )
            for material in model.materials:
                previous = material_specs.get(material.name)
                if previous is not None and previous != material:
                    loss_report.record(
                        "authoring.source1.material.collision",
                        "Different materials normalize to the same authored name.",
                        severity=DiagnosticSeverity.ERROR,
                        loss_kind=LossKind.SAFETY,
                        details={"material": material.name},
                    )
                    continue
                material_specs[material.name] = material

            material_names = frozenset(material.name for material in model.materials)
            smd_name = f"{model.name}.smd"
            smd_path = asset_model_source.joinpath(smd_name)
            qc_path = asset_model_source.joinpath(f"{model.name}.qc")
            compiled_model = asset_model_directory.joinpath(f"{model.name}.mdl")
            model_paths.append(compiled_model)
            try:
                smd = serialize_smd(model.document, material_names)
            except ValueError as exc:
                loss_report.record(
                    "authoring.source1.smd.invalid",
                    str(exc),
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"model": model.name},
                )
                smd = ""
            add_artifact(Source1AuthoringArtifact(
                Source1ArtifactRoot.CONTENT,
                smd_path,
                Source1ArtifactKind.MODEL_SOURCE,
                smd,
            ))
            add_artifact(Source1AuthoringArtifact(
                Source1ArtifactRoot.CONTENT,
                qc_path,
                Source1ArtifactKind.MODEL_SCRIPT,
                serialize_qc(
                    compiled_model,
                    smd_name,
                    asset_material_directory,
                    surface_property=request.surface_property,
                ),
            ))
            compiled_stem = compiled_model.as_posix()[:-len(".mdl")]
            compile_steps[(Source1CompilerKind.STUDIOMDL, qc_path)] = (
                Source1CompileStep(
                    Source1CompilerKind.STUDIOMDL,
                    Source1ArtifactRoot.CONTENT,
                    qc_path,
                    (
                        Source1CompileOutput(
                            Source1ArtifactRoot.GAME,
                            compiled_model,
                        ),
                        Source1CompileOutput(
                            Source1ArtifactRoot.GAME,
                            f"{compiled_stem}.vvd",
                        ),
                        Source1CompileOutput(
                            Source1ArtifactRoot.GAME,
                            f"{compiled_stem}.dx90.vtx",
                        ),
                    ),
                )
            )

        for material_name, material in sorted(material_specs.items()):
            if not is_valid_asset_name(material_name):
                loss_report.record(
                    "authoring.source1.material_name.invalid",
                    "Source 1 material names must contain only lowercase ASCII letters, digits, and underscores.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"material": material_name},
                )
            if material.existing_resource is not None:
                loss_report.record(
                    "authoring.source1.material.existing_resource_unsupported",
                    "A Source 2 VMAT resource cannot be reused by the Source 1 backend.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    details={"material": material_name},
                )
            if (
                    isinstance(material, Source1AuthoringMaterial)
                    and material.existing_vmt is not None
            ):
                add_artifact(Source1AuthoringArtifact(
                    Source1ArtifactRoot.GAME,
                    asset_material_directory.joinpath(f"{material_name}.vmt"),
                    Source1ArtifactKind.MATERIAL,
                    serialize_vmt_patch(material.existing_vmt),
                ))
                continue

            textures = {texture.role: texture for texture in material.textures}
            if len(textures) != len(material.textures):
                loss_report.record(
                    "authoring.source1.texture.role_duplicate",
                    "A material cannot author two textures for the same role.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"material": material_name},
                )
            unsupported_roles = sorted(
                role.value for role in textures
                if role not in {
                    TextureRole.ALPHA,
                    TextureRole.BASE_COLOR,
                    TextureRole.NORMAL,
                }
            )
            if unsupported_roles:
                loss_report.record(
                    "authoring.source1.material.texture_unsupported",
                    "The Source 1 static subset authors only base-color, base alpha, and normal textures.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    details={
                        "material": material_name,
                        "roles": unsupported_roles,
                    },
                )
            if material.metallic != 0.0 or material.roughness != 0.5:
                loss_report.record(
                    "authoring.source1.material.pbr_conversion_unsupported",
                    "Roughness and metalness are not implicitly converted to Source 1 phong parameters.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    details={"material": material_name},
                )
            if TextureRole.ALPHA in textures and TextureRole.BASE_COLOR not in textures:
                loss_report.record(
                    "authoring.source1.material.alpha_source_invalid",
                    "Source 1 opacity must be carried by the base-color texture alpha channel.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"material": material_name},
                )
            if (
                    material.alpha_mode == MaterialAlphaMode.OPAQUE
                    and TextureRole.ALPHA in textures
            ):
                loss_report.record(
                    "authoring.source1.material.alpha_texture_unused",
                    "An alpha texture requires alpha-test or blended material mode.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"material": material_name},
                )
            if (
                    material.alpha_mode == MaterialAlphaMode.OPAQUE
                    and material.base_color[3] != 1.0
            ):
                loss_report.record(
                    "authoring.source1.material.alpha_mode_mismatch",
                    "Opacity below one requires alpha-test or blended material mode.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"material": material_name},
                )

            def stage_texture(role: TextureRole, name: str, payload: bytes, extension: str):
                if extension.casefold() != ".tga":
                    loss_report.record(
                        "authoring.source1.texture.format_invalid",
                        "The Source 1 authoring contract requires TGA source textures.",
                        severity=DiagnosticSeverity.ERROR,
                        loss_kind=LossKind.SAFETY,
                        details={
                            "material": material_name,
                            "extension": extension,
                        },
                    )
                source_path = asset_material_source.joinpath(f"{name}{extension}")
                settings_path = ContentRelativePath(
                    f"{source_path.as_posix()[:-len(extension)]}.txt"
                )
                add_artifact(Source1AuthoringArtifact(
                    Source1ArtifactRoot.CONTENT,
                    source_path,
                    Source1ArtifactKind.TEXTURE_SOURCE,
                    payload,
                ))
                add_artifact(Source1AuthoringArtifact(
                    Source1ArtifactRoot.CONTENT,
                    settings_path,
                    Source1ArtifactKind.TEXTURE_SETTINGS,
                    serialize_vtex_settings(
                        role,
                        alpha_mode=material.alpha_mode,
                    ),
                ))
                output_parts = ("materials", *source_path.parts[1:])
                output_path = ContentRelativePath("/".join(output_parts))
                output_path = ContentRelativePath(
                    f"{output_path.as_posix()[:-len(extension)]}.vtf"
                )
                compile_steps[(Source1CompilerKind.VTEX, source_path)] = (
                    Source1CompileStep(
                        Source1CompilerKind.VTEX,
                        Source1ArtifactRoot.CONTENT,
                        source_path,
                        (
                            Source1CompileOutput(
                                Source1ArtifactRoot.GAME,
                                output_path,
                            ),
                        ),
                    )
                )
                return source_path

            color_texture = textures.get(TextureRole.BASE_COLOR)
            if color_texture is None:
                color_name = f"{material_name}_{TextureRole.BASE_COLOR.value}"
                color_payload = solid_color_tga((1.0, 1.0, 1.0, 1.0))
                color_extension = ".tga"
            else:
                color_name = color_texture.name
                color_payload = color_texture.payload
                color_extension = color_texture.extension
            color_path = stage_texture(
                TextureRole.BASE_COLOR,
                color_name,
                color_payload,
                color_extension,
            )

            normal_texture = textures.get(TextureRole.NORMAL)
            normal_path = (
                stage_texture(
                    TextureRole.NORMAL,
                    normal_texture.name,
                    normal_texture.payload,
                    normal_texture.extension,
                )
                if normal_texture is not None
                else None
            )
            vmt_path = asset_material_directory.joinpath(f"{material_name}.vmt")
            add_artifact(Source1AuthoringArtifact(
                Source1ArtifactRoot.GAME,
                vmt_path,
                Source1ArtifactKind.MATERIAL,
                serialize_vmt(
                    material,
                    color_path,
                    normal_texture=normal_path,
                ),
            ))

        return Source1BackendOutput.create(
            (
                artifacts[key]
                for key in sorted(
                    artifacts,
                    key=lambda item: (item[0].value, item[1].as_posix()),
                )
            ),
            (
                compile_steps[key]
                for key in sorted(
                    compile_steps,
                    key=lambda item: (
                        0 if item[0] == Source1CompilerKind.VTEX else 1,
                        item[1].as_posix(),
                    ),
                )
            ),
            sorted(model_paths, key=ContentRelativePath.as_posix),
            report=loss_report,
        )
