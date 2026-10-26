import numpy as np

from SourceIO.library.source1.vtf import load_texture_tth, reconstruct_texture_tth
from SourceIO.library.utils import MemoryBuffer
from SourceIO.tests.texture_tests.vtf_fixtures import build_tth_pair, build_vtf, checker


def test_tth_pair_reconstructs_and_decodes_the_original_vtf():
    pixels = checker()
    vtf = build_vtf(pixels)
    tth, ttz = build_tth_pair(vtf)

    reconstructed = reconstruct_texture_tth(MemoryBuffer(tth), MemoryBuffer(ttz))
    decoded, height, width = load_texture_tth(MemoryBuffer(tth), MemoryBuffer(ttz), normalized=False)

    assert reconstructed == vtf
    assert (height, width) == pixels.shape[:2]
    np.testing.assert_array_equal(decoded, pixels)


def test_invalid_tth_header_is_rejected():
    assert reconstruct_texture_tth(MemoryBuffer(b"BAD"), MemoryBuffer(b"")) is None
    assert load_texture_tth(MemoryBuffer(b"BAD"), MemoryBuffer(b"")) == (None, 0, 0)
