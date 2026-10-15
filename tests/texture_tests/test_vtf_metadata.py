import os

os.environ["NO_BPY"] = "1"

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from SourceIO.library.shared.sprite_sheet import SpriteSheetImage, SpriteSheetMetadata
from SourceIO.library.source1.vtf.editable_export import (
    export_vtf_for_editing,
    sheet_image_pixel_rect,
    write_editable_texture,
)
from SourceIO.library.source1.vtf.metadata import (
    RESOURCE_CRC,
    RESOURCE_KEYVALUES,
    RESOURCE_LOD,
    RESOURCE_SETTINGS_EX,
    RESOURCE_SHEET,
    parse_sheet_resource,
    read_vtf_metadata,
)
from SourceIO.tests.texture_tests.vtf_fixtures import NO_DATA, build_vtf, cell, checker, read_tga, sheet_blob

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source1" / "textures"


def test_header_and_inline_resources():
    keyvalues = b'"Information"\n{\n\t"Author" "SourceIO"\n}\n\0'
    data = build_vtf(checker(), [
        (RESOURCE_CRC, NO_DATA, 0xDEADBEEF),
        (RESOURCE_LOD, NO_DATA, struct.unpack("<I", bytes([10, 9, 8, 7]))[0]),
        (RESOURCE_SETTINGS_EX, NO_DATA, 0x01),
        (RESOURCE_KEYVALUES, 0, keyvalues),
        (b"ZZZ", 0, b"custom"),
    ], flags=0x2000)

    metadata = read_vtf_metadata(data)

    assert metadata.header.version == (7, 3)
    assert (metadata.header.width, metadata.header.height) == (8, 4)
    assert metadata.header.flags == 0x2000
    assert metadata.header.low_res_format == -1
    assert metadata.crc == 0xDEADBEEF
    assert metadata.lod_clamp == (10, 9)
    assert metadata.settings_ex == 1
    assert metadata.keyvalues.startswith('"Information"')
    assert metadata.resource(b"ZZZ").data == b"custom"
    assert metadata.sprite_sheet is None
    assert not metadata.warnings
    assert json.loads(json.dumps(metadata.to_dict()))["lod_clamp"] == [10, 9]


def test_versions_before_7_3_have_no_resources():
    metadata = read_vtf_metadata(build_vtf(checker(), version=(7, 2)))
    assert metadata.header.version == (7, 2)
    assert metadata.resources == ()


def test_sheet_v1_reads_clamp_as_int_and_drops_unused_images():
    blob = sheet_blob(1, [
        (0, 1, [(1.0, [cell(0)]), (0.5, [cell(4)] * 4)]),
        (3, 0, [(2.0, [cell(0), cell(4)])]),
    ])
    # Valve writes clamp as a little-endian int: 01 00 00 00.
    assert blob[12:16] == b"\x01\0\0\0"

    version, sheet = parse_sheet_resource(blob)

    assert version == 1
    first, second = sheet.sequences
    assert (first.sequence_id, first.clamp, len(first.frames)) == (0, True, 2)
    assert first.total_time == pytest.approx(1.5)
    assert [len(frame.images) for frame in first.frames] == [1, 1]
    assert (second.sequence_id, second.clamp) == (3, False)
    assert len(second.frames[0].images) == 2
    assert sheet.raw_data == blob


def test_sheet_v0_has_one_image_per_frame():
    version, sheet = parse_sheet_resource(sheet_blob(0, [(0, 0, [(1.0, [cell(0)]), (1.0, [cell(4)])])]))
    assert version == 0
    assert [frame.images[0].uncropped_min for frame in sheet.sequences[0].frames] == [
        pytest.approx(cell(0)[:2]), pytest.approx(cell(4)[:2]),
    ]


def test_bad_sheet_is_a_warning_not_an_error():
    bad_sheet = struct.pack("<iI", 1, 65)
    metadata = read_vtf_metadata(build_vtf(checker(), [
        (RESOURCE_SHEET, 0, bad_sheet),
        (RESOURCE_CRC, NO_DATA, 5),
    ]))
    assert metadata.sprite_sheet is None
    assert metadata.crc == 5
    assert len(metadata.warnings) == 1 and "65 sequences" in metadata.warnings[0]


def test_resource_outside_file_raises():
    data = bytearray(build_vtf(checker(), [(RESOURCE_KEYVALUES, 0, b"x")]))
    struct.pack_into("<I", data, 80 + 4, len(data) + 100)
    with pytest.raises(ValueError, match="outside"):
        read_vtf_metadata(bytes(data))


def test_not_a_vtf_raises():
    with pytest.raises(ValueError):
        read_vtf_metadata(b"garbage")


def test_strata_hotspot_sample():
    path = SAMPLES / "hotspot.vtf"
    if not path.is_file():
        pytest.skip("hotspot.vtf not fetched (tests/fetch_samples.py)")
    metadata = read_vtf_metadata(path.read_bytes())
    assert metadata.header.version == (7, 5)
    assert metadata.hotspot is not None
    assert metadata.hotspot.version == 1
    assert len(metadata.hotspot.rects) == 4
    assert all(rect.max_x > rect.min_x and rect.max_y > rect.min_y for rect in metadata.hotspot.rects)


def test_pixel_rect_handles_inset_and_edge_uvs():
    inset = SpriteSheetImage(*(cell(4)[:2], cell(4)[2:]) * 2)
    edges = SpriteSheetImage((0.5, 0.0), (1.0, 1.0), (0.5, 0.0), (1.0, 1.0))
    assert sheet_image_pixel_rect(inset, 8, 4) == (4, 0, 8, 4)
    assert sheet_image_pixel_rect(edges, 8, 4) == (4, 0, 8, 4)


def test_sprite_sheet_dict_round_trip():
    _, sheet = parse_sheet_resource(sheet_blob(1, [(2, 1, [(0.25, [cell(0), cell(4)])])]))
    assert SpriteSheetMetadata.from_dict(json.loads(json.dumps(sheet.to_dict()))) == sheet


def test_export_writes_atlas_frames_mks_and_sidecar(tmp_path):
    pixels = checker()
    blob = sheet_blob(1, [
        (0, 0, [(1.0, [cell(0)]), (0.5, [cell(4)])]),
        (1, 1, [(2.0, [cell(4)])]),
    ])
    data = build_vtf(pixels, [(RESOURCE_SHEET, 0, blob)])

    result = export_vtf_for_editing(data, tmp_path, "puff")

    assert [path.name for path in result.images] == ["puff.tga"]
    assert [path.name for path in result.frame_images] == ["puff_000.tga", "puff_001.tga"]
    np.testing.assert_array_equal(read_tga(tmp_path / "puff.tga"), pixels)
    # vtex attaches a .sht next to its input verbatim, so the original resource keeps the original layout.
    assert result.sht == tmp_path / "puff.sht"
    assert (tmp_path / "puff.sht").read_bytes() == blob
    np.testing.assert_array_equal(read_tga(tmp_path / "puff_000.tga"), pixels[:, 0:4])
    np.testing.assert_array_equal(read_tga(tmp_path / "puff_001.tga"), pixels[:, 4:8])
    mks = (tmp_path / "puff.mks").read_text().splitlines()
    assert [line for line in mks if not line.startswith("//")] == [
        "",
        "sequence 0",
        "loop",
        "frame puff_000.tga 1",
        "frame puff_001.tga 0.5",
        "",
        "sequence 1",
        "frame puff_001.tga 2",
    ]
    sidecar = json.loads((tmp_path / "puff.vtf.json").read_text())
    assert sidecar["sprite_sheet_version"] == 1
    assert sidecar["sprite_sheet"]["sequences"][0]["frames"][1]["images"][0]["pixel_rect"] == [4, 0, 8, 4]
    assert sidecar["sprite_sheet"]["sequences"][1]["frames"][0]["files"] == ["puff_001.tga"]

    with pytest.raises(FileExistsError):
        export_vtf_for_editing(data, tmp_path, "puff")
    export_vtf_for_editing(data, tmp_path, "puff", "png", overwrite=True)
    assert (tmp_path / "puff_000.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_export_without_sheet_writes_only_the_image(tmp_path):
    result = export_vtf_for_editing(build_vtf(checker(), version=(7, 2)), tmp_path, "plain")
    assert result.mks is None and result.frame_images == ()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["plain.tga", "plain.vtf.json"]


def test_float_pixels_and_dict_metadata_match_the_vtf_export(tmp_path):
    """The Blender operator passes float pixels and the JSON stored on the image."""
    pixels = checker()
    data = build_vtf(pixels, [(RESOURCE_SHEET, 0, sheet_blob(0, [(0, 0, [(1.0, [cell(4)])])]))])
    stored = json.loads(json.dumps(read_vtf_metadata(data).to_dict()))

    write_editable_texture([pixels.astype(np.float32) / 255], tmp_path / "blender", "puff",
                           metadata=stored, hdr=False)
    export_vtf_for_editing(data, tmp_path / "vtf", "puff")

    for name in ("puff.tga", "puff_000.tga", "puff.mks", "puff.sht"):
        assert (tmp_path / "blender" / name).read_bytes() == (tmp_path / "vtf" / name).read_bytes()


def test_rgb_alpha_packed_sheet_is_flagged(tmp_path):
    """packmode rgb+a packs the RGB and alpha frames independently, so their rectangles overlap."""
    data = build_vtf(checker(), [(RESOURCE_SHEET, 0, sheet_blob(1, [
        (0, 0, [(1.0, [cell(0)])]),
        (1, 0, [(1.0, [cell(0, size=2)])]),
    ]))])

    result = export_vtf_for_editing(data, tmp_path, "smoke")

    assert any("packmode rgb+a" in warning for warning in result.warnings)
    assert "WARNING" in (tmp_path / "smoke.mks").read_text()
    assert json.loads((tmp_path / "smoke.vtf.json").read_text())["sprite_sheet_split_channels"] is True


def test_shared_frames_are_not_flagged(tmp_path):
    data = build_vtf(checker(), [(RESOURCE_SHEET, 0, sheet_blob(1, [
        (0, 0, [(1.0, [cell(0)]), (1.0, [cell(4)])]),
        (1, 0, [(1.0, [cell(4)])]),
    ]))])

    result = export_vtf_for_editing(data, tmp_path, "puff")

    assert not result.warnings
    assert "WARNING" not in (tmp_path / "puff.mks").read_text()
