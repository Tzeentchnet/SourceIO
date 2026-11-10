import os

os.environ["NO_BPY"] = "1"

import numpy as np
import pytest

from SourceIO.library.source1 import vtf
from SourceIO.library.utils import MemoryBuffer
from SourceIO.tests.texture_tests.vtf_fixtures import RGBA16161616F, build_compressed_vtf


def fail_native_decoder(_data):
    raise RuntimeError("unsupported by native decoder")


@pytest.mark.parametrize("version", [(7, 2), (7, 4)])
def test_rgba16f_cubemap_fallback_reads_the_largest_mip_first_face(monkeypatch, version):
    expected = np.array(
        [
            [[2.5, 0.5, -1.0, 1.0], [4.0, 2.0, 1.0, 0.25]],
            [[0.0, 0.125, 8.0, 1.0], [-2.0, 1.5, 3.0, 0.75]],
        ],
        dtype=np.float16,
    )
    top_face_size = expected.nbytes
    top_mip = expected.tobytes() + b"\0" * (top_face_size * 6)
    small_mip = b"\0" * (4 * np.dtype(np.float16).itemsize * 7)
    data = build_compressed_vtf(
        2,
        2,
        RGBA16161616F,
        (top_mip, small_mip),
        version=version,
        flags=0x00004000,
    )
    monkeypatch.setattr(vtf, "load_vtf_texture", fail_native_decoder)

    actual, height, width = vtf.load_texture(MemoryBuffer(data), normalized=False)

    assert (width, height) == (2, 2)
    assert actual.dtype == np.float32
    np.testing.assert_array_equal(actual, expected.astype(np.float32))


def test_rgba16f_frame_fallback_returns_each_frame(monkeypatch):
    expected = [
        np.array([[[1.0, 2.0, 3.0, 0.5]]], dtype=np.float16),
        np.array([[[-1.0, 4.0, 0.25, 1.0]]], dtype=np.float16),
    ]
    data = build_compressed_vtf(
        1,
        1,
        RGBA16161616F,
        (b"".join(frame.tobytes() for frame in expected),),
        frame_count=2,
    )
    monkeypatch.setattr(vtf, "load_vtf_texture_frames", fail_native_decoder)

    frames, height, width = vtf.load_texture_frames(MemoryBuffer(data))

    assert (width, height) == (1, 1)
    assert len(frames) == 2
    for actual, expected_frame in zip(frames, expected):
        assert actual.dtype == np.float32
        np.testing.assert_array_equal(actual, expected_frame.astype(np.float32))
