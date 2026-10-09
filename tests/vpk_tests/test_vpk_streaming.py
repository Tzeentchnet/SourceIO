import os

import pytest

os.environ["NO_BPY"] = "1"

from SourceIO.library.shared.content_manager import UnsafeResourcePath
from SourceIO.library.shared.content_manager.providers.vpk_provider import VPKContentProvider
from SourceIO.library.shared.content_manager.vpk_archive import FileSliceBuffer
from SourceIO.library.utils import TinyPath

from .helpers import write_vpk


def test_embedded_vpk_streams_and_matches_case(tmp_path):
    path = write_vpk(
        tmp_path / "pak01_dir.vpk",
        {"Materials/MixedCase.vmat_c": b"material-data"},
    )
    provider = VPKContentProvider(TinyPath(path))

    stream = provider.open_stream(TinyPath("materials/mixedcase.vmat_c"))

    assert isinstance(stream, FileSliceBuffer)
    assert stream.read(8) == b"material"
    assert stream.read() == b"-data"
    assert provider.resolve_path(TinyPath("MATERIALS/MIXEDCASE.VMAT_C")) == TinyPath(
        "Materials/MixedCase.vmat_c"
    )


def test_split_vpk_streams_from_numbered_archive(tmp_path):
    path = write_vpk(
        tmp_path / "pak01_dir.vpk",
        {"models/split.vmdl_c": b"split-payload"},
        split=True,
    )
    provider = VPKContentProvider(TinyPath(path))

    stream = provider.open_stream(TinyPath("models/split.vmdl_c"))

    assert isinstance(stream, FileSliceBuffer)
    assert stream.read() == b"split-payload"


def test_vpk_rejects_traversal_at_archive_boundary(tmp_path):
    path = write_vpk(tmp_path / "pak01_dir.vpk", {"safe/file.bin": b"safe"})
    provider = VPKContentProvider(TinyPath(path))

    with pytest.raises(UnsafeResourcePath):
        provider.open_stream(TinyPath("../safe/file.bin"))
    with pytest.raises(UnsafeResourcePath):
        provider.open_stream(TinyPath("safe\\..\\file.bin"))


def test_case_collision_is_diagnosed_and_stable(tmp_path):
    path = write_vpk(
        tmp_path / "pak01_dir.vpk",
        {
            "materials/Shared.vmat_c": b"first",
            "materials/shared.vmat_c": b"second",
        },
    )
    provider = VPKContentProvider(TinyPath(path))

    assert provider.open_stream(TinyPath("materials/shared.vmat_c")).read() == b"first"
    assert provider.collision_diagnostics[0].candidates == (
        "materials/Shared.vmat_c",
        "materials/shared.vmat_c",
    )
