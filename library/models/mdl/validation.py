class ModelCompanionError(ValueError):
    pass


def validate_model_companions(mdl, vtx, vvd=None, *, model_path=None):
    prefix = f"{model_path}: " if model_path is not None else ""
    if vtx is None:
        raise ModelCompanionError(f"{prefix}VTX parser returned no model")

    mdl_checksum = mdl.header.checksum & 0xFFFFFFFF
    vtx_checksum = vtx.header.checksum & 0xFFFFFFFF
    if vtx_checksum != mdl_checksum:
        raise ModelCompanionError(
            f"{prefix}VTX checksum {vtx_checksum} does not match MDL checksum {mdl_checksum}"
        )
    if vvd is not None:
        vvd_checksum = vvd.header.checksum & 0xFFFFFFFF
        if vvd_checksum != mdl_checksum:
            raise ModelCompanionError(
                f"{prefix}VVD checksum {vvd_checksum} does not match MDL checksum {mdl_checksum}"
            )

    if len(vtx.body_parts) != len(mdl.body_parts):
        raise ModelCompanionError(
            f"{prefix}VTX has {len(vtx.body_parts)} body parts; MDL has {len(mdl.body_parts)}"
        )

    lod_vertex_count = len(vvd.lod_data[0]) if vvd is not None else None
    for body_index, (body_part, vtx_body_part) in enumerate(
        zip(mdl.body_parts, vtx.body_parts)
    ):
        if len(vtx_body_part.models) != len(body_part.models):
            raise ModelCompanionError(
                f"{prefix}body part {body_index} has {len(vtx_body_part.models)} VTX models; "
                f"MDL has {len(body_part.models)}"
            )

        for model_index, (model, vtx_model) in enumerate(
            zip(body_part.models, vtx_body_part.models)
        ):
            if model.vertex_count == 0:
                continue
            if (
                lod_vertex_count is not None
                and model.vertex_offset + model.vertex_count > lod_vertex_count
            ):
                raise ModelCompanionError(
                    f"{prefix}body part {body_index}, model {model_index} uses VVD vertices "
                    f"{model.vertex_offset}:{model.vertex_offset + model.vertex_count}, "
                    f"but LOD 0 has {lod_vertex_count}"
                )
            if not vtx_model.model_lods:
                raise ModelCompanionError(
                    f"{prefix}body part {body_index}, model {model_index} has no VTX LOD 0"
                )

            vtx_lod = vtx_model.model_lods[0]
            if len(vtx_lod.meshes) != len(model.meshes):
                raise ModelCompanionError(
                    f"{prefix}body part {body_index}, model {model_index} has "
                    f"{len(vtx_lod.meshes)} VTX meshes; MDL has {len(model.meshes)}"
                )

            for mesh_index, (mesh, vtx_mesh) in enumerate(
                zip(model.meshes, vtx_lod.meshes)
            ):
                for group_index, strip_group in enumerate(vtx_mesh.strip_groups):
                    references = strip_group.vertexes[
                        "original_mesh_vertex_index"
                    ].reshape(-1)
                    if not references.size:
                        continue
                    max_reference = int(references.max()) + mesh.vertex_index_start
                    if max_reference >= model.vertex_count:
                        raise ModelCompanionError(
                            f"{prefix}body part {body_index}, model {model_index}, "
                            f"mesh {mesh_index}, strip group {group_index} references "
                            f"vertex {max_reference}, but the MDL model has "
                            f"{model.vertex_count} vertices"
                        )
