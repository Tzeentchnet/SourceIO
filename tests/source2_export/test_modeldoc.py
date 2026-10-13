import io
import json

import pytest

from SourceIO.library.source2.export import (
    AssetReference,
    Attachment,
    AttachmentInfluence,
    BodyGroup,
    CornerData,
    Hitbox,
    LODLevel,
    MaterialRemap,
    MeshFace,
    MeshVertex,
    ModelDocExporter,
    ModelDocument,
    PhysicsShape,
    Skin,
    StaticMesh,
)
from SourceIO.library.utils import datamodel
from SourceIO.library.utils.s2_keyvalues import KeyValues


def test_modeldoc_static_vertical_slice_is_editable_and_sidecar_complete():
    mesh = StaticMesh(
        "crate_LOD0",
        (
            MeshVertex((0.0, 0.0, 0.0), CornerData((0.0, 0.0, 1.0), texcoords=((0.0, 0.0),))),
            MeshVertex((1.0, 0.0, 0.0), CornerData((0.0, 0.0, 1.0), texcoords=((1.0, 0.0),))),
            MeshVertex((0.0, 1.0, 0.0), CornerData((0.0, 0.0, 1.0), texcoords=((0.0, 1.0),))),
        ),
        (MeshFace((0, 1, 2), "materials/crate.vmat"),),
        bodygroups=("body",),
        lods=(0,),
    )
    document = ModelDocument(
        name="crate",
        meshes=(mesh,),
        bodygroups=(BodyGroup("body", (("crate_LOD0",),)),),
        lods=(LODLevel(0, 0.0, ("crate_LOD0",)),),
        skins=(Skin("default", (MaterialRemap(
            "materials/crate.vmat",
            "materials/crate_blue.vmat",
        ),), True),),
        attachments=(Attachment("muzzle", (AttachmentInfluence(
            "",
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0, 1.0),
        ),)),),
        physics_shapes=(PhysicsShape(
            "collision",
            "sphere",
            center=(0.0, 0.0, 0.0),
            radius=1.0,
        ),),
        hitboxes=(Hitbox(
            "crate",
            "default",
            "",
            (-1.0, -1.0, -1.0),
            (1.0, 1.0, 1.0),
        ),),
        flex_references=(AssetReference("flex", "morphs/crate.vmorf"),),
    )

    bundle = ModelDocExporter().build(document)

    assert set(bundle.files) == {
        "crate.vmdl",
        "crate.dmx",
        "crate.sourceio.json",
        "crate.loss.json",
    }
    assert bundle.files["crate.vmdl"].startswith("<!-- KV3 encoding:text:")
    assert "_class = \"RenderMeshFile\"" in bundle.files["crate.vmdl"]
    assert "_class = \"Attachment\"" in bundle.files["crate.vmdl"]
    assert "_class = \"PhysicsShapeSphere\"" in bundle.files["crate.vmdl"]
    assert "_class = \"Hitbox\"" in bundle.files["crate.vmdl"]
    assert bundle.files["crate.dmx"].startswith("<!-- dmx encoding keyvalues2 4 format model 22 -->")

    dmx = datamodel.parse(bundle.files["crate.dmx"])
    assert dmx.root.name == "crate"
    _, modeldoc = KeyValues.read_data(io.StringIO(bundle.files["crate.vmdl"]))
    assert modeldoc["rootNode"]["_class"] == "RootNode"
    sidecar = json.loads(bundle.files["crate.sourceio.json"])
    assert sidecar["document"]["flex_references"][0]["path"] == "morphs/crate.vmorf"
    losses = json.loads(bundle.files["crate.loss.json"])
    assert any(item["code"] == "model.flex.reference.sidecar" for item in losses["diagnostics"])


def test_modeldoc_uses_explicit_nested_content_asset_path_for_mesh_reference():
    document = ModelDocument(
        name="crate",
        meshes=(StaticMesh(
            "crate",
            (
                MeshVertex((0.0, 0.0, 0.0)),
                MeshVertex((1.0, 0.0, 0.0)),
                MeshVertex((0.0, 1.0, 0.0)),
            ),
            (MeshFace((0, 1, 2), "materials/crate.vmat"),),
        ),),
    )

    bundle = ModelDocExporter().build(
        document,
        asset_path=r"models\sourceio\nested\crate.vmdl",
    )

    _, modeldoc = KeyValues.read_data(io.StringIO(bundle.files["crate.vmdl"]))
    render_mesh_list = next(
        child for child in modeldoc["rootNode"]["children"]
        if child["_class"] == "RenderMeshList"
    )
    assert render_mesh_list["children"][0]["filename"] == (
        "models/sourceio/nested/crate.dmx"
    )
    sidecar = json.loads(bundle.files["crate.sourceio.json"])
    assert sidecar["asset_path"] == "models/sourceio/nested/crate.vmdl"


def test_modeldoc_rejects_escaping_or_mismatched_asset_paths():
    document = ModelDocument("crate", ())

    with pytest.raises(ValueError):
        ModelDocExporter().build(document, asset_path="../crate.vmdl")
    with pytest.raises(ValueError, match="must end with"):
        ModelDocExporter().build(document, asset_path="models/other.vmdl")
