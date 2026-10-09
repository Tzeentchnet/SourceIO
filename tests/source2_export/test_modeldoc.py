import io
import json

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
