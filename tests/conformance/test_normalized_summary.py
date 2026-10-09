from __future__ import annotations

import json
from pathlib import Path

import pytest

from SourceIO.tools.source2_conformance import (
    CAPABILITY_NAMES,
    SCHEMA_ID,
    create_conformance_registry,
    inspect_resource_bytes,
    load_public_contracts,
    merge_public_contracts,
    summarize_fixture_manifest,
    summarize_path,
)


SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "normalized_summaries.json"
SCHEMA = Path(__file__).resolve().parent / "normalized_summary.schema.json"
SUMMARY_KEYS = {
    "$schema",
    "schema_version",
    "identity",
    "blocks",
    "dependencies",
    "dimensions",
    "channels",
    "mesh",
    "skeleton",
    "animation",
    "sound",
    "capabilities",
    "diagnostics",
}


def _assert_subset(actual, expected):
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert key in actual
            _assert_subset(actual[key], value)
    else:
        assert actual == expected


def test_strict_normalized_snapshots(fixture_root: Path):
    expected = json.loads(SNAPSHOT.read_text(encoding="ascii"))
    actual = summarize_fixture_manifest(fixture_root)
    assert actual == expected


def test_normalized_schema_shape(fixture_root: Path, fixture_manifest: dict):
    schema = json.loads(SCHEMA.read_text(encoding="ascii"))
    assert schema["$id"] == SCHEMA_ID
    assert set(schema["required"]) == SUMMARY_KEYS
    for record in fixture_manifest["fixtures"]:
        summary = summarize_path(fixture_root / record["path"])
        assert set(summary) == SUMMARY_KEYS
        assert summary["$schema"] == SCHEMA_ID
        assert summary["schema_version"] == 1
        assert set(summary["identity"]) == set(
            schema["$defs"]["identity"]["required"]
        )
        assert set(summary["capabilities"]["features"]["available"]).isdisjoint(
            summary["capabilities"]["features"]["unavailable"]
        )
        assert (
            set(summary["capabilities"]["features"]["available"])
            | set(summary["capabilities"]["features"]["unavailable"])
        ) == set(CAPABILITY_NAMES)


def test_manifest_semantic_expectations(fixture_root: Path, fixture_manifest: dict):
    for record in fixture_manifest["fixtures"]:
        summary = summarize_path(fixture_root / record["path"])
        expected = dict(record["expected"])
        dependency_count = expected.pop("dependency_count", None)
        resource_type = expected.pop("resource_type")
        assert summary["identity"]["resource_type"] == resource_type
        if dependency_count is not None:
            assert len(summary["dependencies"]) == dependency_count
        _assert_subset(summary, expected)


def test_public_contracts_and_registry_are_consumed(
    fixture_root: Path,
    fixture_manifest: dict,
):
    contracts = load_public_contracts()
    assert set(contracts) == {
        "ResourceIdentity",
        "ResourceCapabilities",
        "Diagnostic",
        "ResourceResolver",
        "ResourceRegistry",
    }

    class NullResolver:
        def resolve(self, _reference):
            return None

    assert isinstance(NullResolver(), contracts["ResourceResolver"])
    registry = create_conformance_registry()
    for record in fixture_manifest["fixtures"]:
        path = fixture_root / record["path"]
        data = path.read_bytes()
        identity = registry.identify(data, path.name)
        resource = registry.parse(data, path.name)
        assert isinstance(identity, contracts["ResourceIdentity"])
        assert isinstance(resource.identity, contracts["ResourceIdentity"])
        assert isinstance(resource.capabilities, contracts["ResourceCapabilities"])
        assert resource.identity == identity


def test_generated_resources_parse_their_payload_blocks(
    fixture_root: Path,
    fixture_manifest: dict,
):
    contracts = load_public_contracts()
    registry = create_conformance_registry()
    for record in fixture_manifest["fixtures"]:
        if record["path"].endswith(".vsnd_c"):
            continue
        path = fixture_root / record["path"]
        resource = registry.parse(path.read_bytes(), path.name)
        assert resource.parse_blocks()


def test_generated_sound_uses_real_vsnd_v4_layout(fixture_root: Path):
    from SourceIO.library.source2.resource_types.compiled_sound_resource import (
        CompiledSoundResource,
    )

    path = fixture_root / "minimal_sound.vsnd_c"
    sound = CompiledSoundResource.from_buffer(path.read_bytes(), path.name)
    assert sound.metadata.sample_rate == 48_000
    assert sound.metadata.channels == 2
    assert sound.metadata.sample_count == 48
    assert sound.metadata.duration == pytest.approx(0.001)
    assert len(sound.raw_payload) == 192


def test_public_contract_normalization(fixture_root: Path):
    contracts = load_public_contracts()
    from SourceIO.library.source2.interfaces import (
        DiagnosticSeverity,
        Maturity,
        ResourceKind,
    )

    path = fixture_root / "minimal_texture.vtex_c"
    base = inspect_resource_bytes(path.read_bytes(), path.name)
    identity = contracts["ResourceIdentity"](
        kind=ResourceKind.TEXTURE,
        resource_version=1,
        header_version=12,
        path=path.name,
        extension=".vtex_c",
        confidence=1.0,
        evidence=("conformance test",),
    )
    capabilities = contracts["ResourceCapabilities"](
        read=Maturity.STABLE,
        extract=Maturity.PARTIAL,
    )
    diagnostic = contracts["Diagnostic"](
        code="test.public_contract",
        message="normalized diagnostic",
        severity=DiagnosticSeverity.INFO,
        offset=16,
    )
    merged = merge_public_contracts(
        base,
        identity=identity,
        capabilities=capabilities,
        diagnostics=(diagnostic,),
    )
    assert merged["identity"]["resource_type"] == "texture"
    assert merged["identity"]["confidence"] == 1.0
    assert merged["capabilities"]["operations"] == {
        "read": "stable",
        "extract": "partial",
        "render": "unsupported",
        "write": "unsupported",
    }
    assert merged["diagnostics"] == [
        {
            "severity": "info",
            "code": "test.public_contract",
            "message": "normalized diagnostic",
            "offset": 16,
        }
    ]
