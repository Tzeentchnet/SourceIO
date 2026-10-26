from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from SourceIO.tests.source1_toolchain.source_sdk_2013 import (
    SOURCE_SDK_2013_PROFILES,
    configured_installation,
    create_disposable_fixture,
    execute_disposable_fixture,
)


@pytest.mark.parametrize(
    "profile",
    SOURCE_SDK_2013_PROFILES,
    ids=lambda profile: profile.key,
)
def test_source_sdk_2013_fixture_compiles_reimports_and_recompiles(profile):
    installation = configured_installation(profile)
    if installation is None:
        pytest.skip(f"{profile.environment_variable} is not configured")
    with tempfile.TemporaryDirectory(
            prefix=f"sourceio_{profile.key}_",
    ) as temporary_root:
        workspace = Path(temporary_root) / "fixture"
        fixture = create_disposable_fixture(installation, workspace)

        report = execute_disposable_fixture(fixture)

        assert report["status"] == "PASS"
        assert (
            report["reimport"]["mdl_version"]
            in profile.expected_mdl_versions
        )
        assert report["outputs"]
        assert all(entry["exists"] for entry in report["outputs"].values())
    assert not workspace.exists()
