from types import SimpleNamespace

import numpy as np
import pytest

from SourceIO.library.models.mdl.validation import (
    ModelCompanionError,
    validate_model_companions,
)


def _header(checksum):
    return SimpleNamespace(checksum=checksum)


def _companions(*, mdl_checksum=-1, vtx_checksum=0xFFFFFFFF, vvd_checksum=0xFFFFFFFF,
                reference=3, vertex_count=4):
    strip_group = SimpleNamespace(
        vertexes=np.array(
            [(reference,)],
            dtype=[("original_mesh_vertex_index", np.uint16)],
        )
    )
    mesh = SimpleNamespace(vertex_index_start=0)
    model = SimpleNamespace(
        vertex_count=vertex_count,
        vertex_offset=0,
        meshes=[mesh],
    )
    body_part = SimpleNamespace(models=[model])
    vtx_mesh = SimpleNamespace(strip_groups=[strip_group])
    vtx_lod = SimpleNamespace(meshes=[vtx_mesh])
    vtx_model = SimpleNamespace(model_lods=[vtx_lod])
    vtx_body_part = SimpleNamespace(models=[vtx_model])
    mdl = SimpleNamespace(header=_header(mdl_checksum), body_parts=[body_part])
    vtx = SimpleNamespace(header=_header(vtx_checksum), body_parts=[vtx_body_part])
    vvd = SimpleNamespace(
        header=_header(vvd_checksum),
        lod_data=[np.empty(vertex_count)],
    )
    return mdl, vtx, vvd


def test_companion_checksums_compare_as_unsigned_32_bit():
    validate_model_companions(*_companions())


def test_companion_checksum_mismatch_identifies_file_type_and_model():
    mdl, vtx, vvd = _companions(vtx_checksum=7)

    with pytest.raises(
        ModelCompanionError,
        match=r"models/test\.mdl: VTX checksum 7 does not match MDL checksum 4294967295",
    ):
        validate_model_companions(mdl, vtx, vvd, model_path="models/test.mdl")


def test_out_of_range_vtx_reference_is_rejected_before_mesh_import():
    mdl, vtx, vvd = _companions(reference=4)

    with pytest.raises(
        ModelCompanionError,
        match=r"references vertex 4, but the MDL model has 4 vertices",
    ):
        validate_model_companions(mdl, vtx, vvd)
