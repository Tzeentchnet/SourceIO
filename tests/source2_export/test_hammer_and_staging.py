import json

import pytest

from SourceIO.library.source2.export import (
    AuthoredFileExistsError,
    EntityConnection,
    HammerEntity,
    HammerMapDocument,
    HammerMapExporter,
    HammerProp,
    Transform,
    UnsafeExportPathError,
    WorldLayer,
    stage_text_outputs,
)
from SourceIO.library.utils import datamodel


def test_hammer_slice_preserves_entities_io_props_and_layers():
    document = HammerMapDocument(
        "test_map",
        entities=(HammerEntity(
            "logic_relay",
            {"classname": "logic_relay", "targetname": "relay", "spawnflags": 1},
            Transform(origin=(1.0, 2.0, 3.0)),
            (EntityConnection("OnTrigger", "door", "Open", "", 0.5, 1),),
            layer="gameplay",
        ),),
        props=(HammerProp(
            "models/props/crate.vmdl_c",
            Transform(origin=(10.0, 20.0, 30.0)),
            layer="art",
        ),),
        world_layers=(WorldLayer("gameplay"), WorldLayer("art")),
    )

    bundle = HammerMapExporter().build(document)

    assert set(bundle.files) == {
        "test_map.vmap",
        "test_map.sourceio.json",
        "test_map.loss.json",
    }
    vmap = bundle.files["test_map.vmap"]
    assert vmap.startswith("<!-- dmx encoding keyvalues2 4 format vmap 29 -->")
    assert '"connectionsData" "element_array"' in vmap
    assert '"outputName" "string" "OnTrigger"' in vmap
    assert '"worldLayerName" "string" "gameplay"' in vmap
    assert "models/props/crate.vmdl_c" not in vmap
    assert "models/props/crate.vmdl" in vmap
    assert datamodel.parse(vmap).format == "vmap"

    losses = json.loads(bundle.files["test_map.loss.json"])
    codes = {item["code"] for item in losses["diagnostics"]}
    assert "map.prop.authored_model_path" in codes
    assert "map.entity.property.typed" in codes


def test_atomic_staging_protects_authored_files_by_default(tmp_path):
    existing = tmp_path / "crate.vmdl"
    existing.write_text("authored", encoding="utf-8")

    with pytest.raises(AuthoredFileExistsError):
        stage_text_outputs(tmp_path, {
            "crate.vmdl": "generated",
            "crate.dmx": "mesh",
        })

    assert existing.read_text(encoding="utf-8") == "authored"
    assert not (tmp_path / "crate.dmx").exists()


def test_atomic_staging_can_explicitly_overwrite(tmp_path):
    existing = tmp_path / "crate.vmdl"
    existing.write_text("authored", encoding="utf-8")

    written = stage_text_outputs(
        tmp_path,
        {"crate.vmdl": "generated", "crate.dmx": "mesh"},
        overwrite=True,
    )

    assert {path.name for path in written} == {"crate.vmdl", "crate.dmx"}
    assert existing.read_text(encoding="utf-8") == "generated"


def test_staging_rejects_compiled_and_escaping_paths(tmp_path):
    with pytest.raises(UnsafeExportPathError):
        stage_text_outputs(tmp_path, {"models/crate.vmdl_c": "compiled"})
    with pytest.raises(UnsafeExportPathError):
        stage_text_outputs(tmp_path, {"../crate.vmdl": "escape"})


def test_staging_rejects_linked_parent_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    linked = root / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")

    with pytest.raises(UnsafeExportPathError):
        stage_text_outputs(root, {"linked/crate.vmdl": "escape"})

    assert not (outside / "crate.vmdl").exists()
