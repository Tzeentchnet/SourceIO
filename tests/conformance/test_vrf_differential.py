from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from SourceIO.tools.source2_conformance import summarize_path
from SourceIO.tools.vrf_oracle import (
    compare_summaries,
    compare_with_vrf,
    parse_vrf_output,
    resolve_vrf_cli,
)


def test_unconfigured_vrf_is_reported_as_skipped(fixture_root: Path):
    source = summarize_path(fixture_root / "minimal_texture.vtex_c")
    report = compare_with_vrf(
        fixture_root / "minimal_texture.vtex_c",
        source,
        environ={},
    )
    assert report["status"] == "skipped"
    assert "SOURCEIO_VRF_CLI" in report["reason"]
    assert report["vrf_cli"]["configured"] is False


def test_invalid_configured_vrf_is_an_error(tmp_path: Path):
    state = resolve_vrf_cli(tmp_path / "missing-vrf.exe", environ={})
    assert state.status == "invalid"
    assert state.configured is True
    assert state.available is False


def test_vrf_binary_can_be_sha256_pinned(tmp_path: Path):
    executable = tmp_path / "Decompiler.exe"
    executable.write_bytes(b"pinned-vrf-test-double")
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()
    ready = resolve_vrf_cli(
        executable,
        expected_sha256=expected,
        environ={},
    )
    assert ready.status == "ready"
    assert ready.sha256 == expected
    mismatch = resolve_vrf_cli(
        executable,
        expected_sha256="0" * 64,
        environ={},
    )
    assert mismatch.status == "invalid"
    assert "SHA-256" in mismatch.reason


def test_human_vrf_output_normalizes_required_fields():
    parsed = parse_vrf_output(
        """
Resource Type: Texture
Header Version: 12
Resource Version: 1
File Size: 128
Block: RERL
Block: DATA
Width: 2
Height: 2
Depth: 1
Mipmap Count: 1
Channel Count: 4
"""
    )
    assert parsed["identity"]["resource_type"] == "texture"
    assert parsed["blocks"] == ["RERL", "DATA"]
    assert parsed["dimensions"] == {
        "width": 2,
        "height": 2,
        "depth": 1,
        "mip_count": 1,
    }
    assert {"identity.resource_type", "blocks"} <= set(parsed["observed"])


def test_differential_mismatch_is_strict(fixture_root: Path):
    source = summarize_path(fixture_root / "minimal_texture.vtex_c")
    oracle = parse_vrf_output(
        """
Resource Type: Texture
Block: RERL
Block: DATA
Width: 4
Height: 2
Depth: 1
Mipmap Count: 1
"""
    )
    mismatches = compare_summaries(source, oracle)
    assert mismatches == [
        {
            "field": "dimensions.width",
            "sourceio": 2,
            "vrf": 4,
        }
    ]


def test_configured_vrf_differential(fixture_root: Path):
    state = resolve_vrf_cli()
    if state.status == "unconfigured":
        pytest.skip(f"VRF differential disabled: {state.reason}")
    if not state.available:
        pytest.fail(f"VRF differential misconfigured: {state.reason}")
    path = fixture_root / "minimal_texture.vtex_c"
    report = compare_with_vrf(path, summarize_path(path))
    assert report["status"] == "matched", report
