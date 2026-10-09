from __future__ import annotations

import json
from pathlib import Path

import pytest

from SourceIO.library.source2.exceptions import Source2Error
from SourceIO.tools.source2_conformance import (
    error_codes,
    inspect_resource_bytes,
    load_public_contracts,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "source2_generated"
MANIFEST = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="ascii"))
MALFORMED_RECORDS = MANIFEST["malformed"]


@pytest.mark.parametrize(
    "record",
    MALFORMED_RECORDS,
    ids=[record["path"].split("/")[-1] for record in MALFORMED_RECORDS],
)
def test_malformed_case_reports_expected_diagnostic(record: dict):
    path = FIXTURE_ROOT / record["path"]
    summary = inspect_resource_bytes(path.read_bytes(), path)
    assert record["expected_code"] in error_codes(summary)
    assert any(
        diagnostic["severity"] == "error"
        for diagnostic in summary["diagnostics"]
    )


def test_malformed_matrix_covers_required_boundaries():
    names = {record["path"].split("/")[-1] for record in MALFORMED_RECORDS}
    required_fragments = {
        "header_truncated",
        "block_table_truncated",
        "block_offset_into_table",
        "block_offset_beyond_eof",
        "compressed_payload_truncated_0000",
        "compressed_payload_truncated_0004",
        "compressed_payload_truncated_0031",
        "block_count_excessive",
        "block_size_excessive",
        "texture_depth_excessive",
        "structured_value_depth_excessive",
    }
    assert all(any(fragment in name for name in names) for fragment in required_fragments)


@pytest.mark.parametrize(
    "case_name",
    (
        "header_truncated_15.vtex_c",
        "block_table_beyond_eof.vtex_c",
        "block_offset_beyond_eof.vtex_c",
        "block_size_excessive.vtex_c",
    ),
)
def test_resource_registry_rejects_structural_corruption(case_name: str):
    contracts = load_public_contracts()
    registry = contracts["ResourceRegistry"]()
    path = FIXTURE_ROOT / "malformed" / case_name
    with pytest.raises(Source2Error):
        registry.identify(path.read_bytes(), path.name)


def test_resource_registry_rejects_truncated_compressed_payload():
    contracts = load_public_contracts()
    registry = contracts["ResourceRegistry"]()
    path = (
        FIXTURE_ROOT
        / "malformed"
        / "compressed_payload_truncated_0031.vdata_c"
    )
    resource = registry.parse(path.read_bytes(), path.name)
    with pytest.raises(Source2Error):
        resource.parse_blocks()
