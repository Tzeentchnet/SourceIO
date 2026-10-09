from __future__ import annotations

import json
from pathlib import Path

import pytest

from SourceIO.tools import build_source2_fixtures
from SourceIO.tools import source2_conformance
from SourceIO.tools import vrf_oracle


def test_owned_tool_self_tests(fixture_root: Path):
    build_source2_fixtures.self_test()
    assert source2_conformance.self_test(fixture_root)["status"] == "passed"
    assert vrf_oracle.self_test()["status"] == "passed"


def test_snapshot_helper_always_fails_on_mismatch():
    with pytest.raises(source2_conformance.SnapshotMismatch):
        source2_conformance.assert_snapshot(
            {"strict": True, "values": [1, 2]},
            {"strict": True, "values": [1, 3]},
        )


def test_differential_cli_reports_unconfigured_state(
    fixture_root: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("SOURCEIO_VRF_CLI", raising=False)
    monkeypatch.delenv("SOURCEIO_VRF_CLI_SHA256", raising=False)
    exit_code = source2_conformance.main(
        [
            "differential",
            str(fixture_root / "minimal_texture.vtex_c"),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 3
    assert report["status"] == "skipped"


def test_fixture_check_cli_is_non_mutating(fixture_root: Path):
    assert build_source2_fixtures.main(
        ["--output", str(fixture_root), "--check"]
    ) == 0
