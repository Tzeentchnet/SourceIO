from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from SourceIO.tools.build_source2_fixtures import (
    generated_files,
    self_test,
    write_generated_fixtures,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_generated_fixture_tree_is_current(fixture_root: Path):
    assert write_generated_fixtures(fixture_root, check=True) == []


def test_fixture_generation_is_byte_deterministic():
    first = generated_files()
    second = generated_files()
    assert first.keys() == second.keys()
    assert all(first[path] == second[path] for path in first)
    self_test()


def test_manifest_proves_local_generation(fixture_root: Path, fixture_manifest: dict):
    provenance = fixture_manifest["provenance"]
    assert provenance == {
        "generator": "tools/build_source2_fixtures.py",
        "kind": "generated",
        "network_required": False,
        "upstream_fixture_content": False,
    }
    for record in fixture_manifest["fixtures"] + fixture_manifest["malformed"]:
        data = (fixture_root / record["path"]).read_bytes()
        assert len(data) == record["byte_size"]
        assert hashlib.sha256(data).hexdigest() == record["sha256"]


def test_tool_imports_do_not_execute_subprocesses_or_write(
    monkeypatch,
    tmp_path: Path,
):
    def forbidden_subprocess(*_args, **_kwargs):
        raise AssertionError("tool import executed a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.rglob("*"))
    for index, relative_path in enumerate(
        (
            "tools/build_source2_fixtures.py",
            "tools/source2_conformance.py",
            "tools/vrf_oracle.py",
        )
    ):
        module_name = f"_source2_import_safety_{index}"
        spec = importlib.util.spec_from_file_location(
            module_name,
            REPOSITORY_ROOT / relative_path,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)
    assert tuple(tmp_path.rglob("*")) == before


def test_manifest_is_stable_ascii_json(fixture_root: Path):
    raw = (fixture_root / "manifest.json").read_bytes()
    assert raw.endswith(b"\n")
    assert raw.decode("ascii")
    assert json.loads(raw) == json.loads(raw.decode("ascii"))
