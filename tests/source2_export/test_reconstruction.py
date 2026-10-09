from SourceIO.library.source2.export import (
    CornerData,
    HammerMapExporter,
    LossReport,
    MeshFace,
    MeshVertex,
    StaticMesh,
)
from SourceIO.library.source2.export.map_reconstruction import hammer_document_from_compiled
from SourceIO.library.source2.export.model_reconstruction import model_document_from_compiled
from SourceIO.library.source2.provenance import ResourceProvenance, to_json_safe
from SourceIO.library.utils import datamodel


def triangle_mesh():
    return StaticMesh(
        "decoded",
        (
            MeshVertex((0.0, 0.0, 0.0), CornerData(normal=(0.0, 0.0, 1.0))),
            MeshVertex((1.0, 0.0, 0.0), CornerData(normal=(0.0, 0.0, 1.0))),
            MeshVertex((0.0, 1.0, 0.0), CornerData(normal=(0.0, 0.0, 1.0))),
        ),
        (MeshFace((0, 1, 2), "materials/test.vmat"),),
    )


class FakeMeshResource:
    name = "external_mesh"
    _filepath = "models/external_mesh.vmesh_c"

    def get_block(self, _block_type, *, block_name=None, block_id=None):
        return {} if block_name == "DATA" else None

    def has_block(self, _name):
        return False


class FakeModelResource:
    name = "compiled_model"

    def __init__(self):
        self.data = {
            "m_name": "models/compiled_model.vmdl",
            "m_lodGroupSwitchDistances": (0.0,),
            "m_meshGroups": ("body", "hat"),
            "m_refMeshes": ("models/first.vmesh", "models/second.vmesh"),
            "m_refMeshGroupMasks": (1,),
            "m_refLODGroupMasks": (4,),
            "m_refPhysicsData": (),
            "m_refAnimGroups": ("animations/compiled_model.vagrp",),
            "m_refMorphs": ("morphs/compiled_model.vmorf",),
        }
        self.mesh_resource = FakeMeshResource()

    def get_block(self, _block_type, *, block_name=None, block_id=None):
        if block_name == "DATA":
            return self.data
        return None

    def get_child_resource(self, _reference, _content_manager, _resource_type):
        return self.mesh_resource

    def get_resource_provenance(self):
        return ResourceProvenance("models/compiled_model.vmdl_c")

    def has_block(self, name):
        return name == "MRPH"


def test_model_reconstruction_preserves_high_lods_and_reports_missing_masks(monkeypatch):
    monkeypatch.setattr(
        "SourceIO.library.source2.export.model_reconstruction.decode_compiled_mesh",
        lambda _resource, *, report: (triangle_mesh(),),
    )
    report = LossReport()

    document = model_document_from_compiled(
        FakeModelResource(),
        content_manager=object(),
        report=report,
    )

    assert [lod.index for lod in document.lods] == [0, 2]
    assert document.lods[1].switch_distance == 0.0
    assert document.bodygroups[0].choices == (("decoded_ref0_0", "decoded_ref1_0"),)
    assert document.bodygroups[1].choices == (("decoded_ref1_0",),)
    assert [reference.path for reference in document.animation_references] == [
        "animations/compiled_model.vagrp",
    ]
    assert [reference.path for reference in document.flex_references] == [
        "morphs/compiled_model.vmorf",
    ]
    codes = {diagnostic.code for diagnostic in report}
    assert "model.bodygroup.mask.missing" in codes
    assert "model.lod.mask.missing" in codes
    assert "model.lod.switch_distance.missing" in codes
    assert "model.flex.embedded.deferred" in codes


class FakeWorldNode:
    def __init__(self, aggregate):
        self.aggregate = aggregate

    def get_scene_objects(self):
        return []

    def get_aggregate_scene_objects(self):
        return [self.aggregate]


class FakeMapResource:
    name = "compiled_map"

    def __init__(self, node):
        self.node = node

    def get_worldnode(self, _prefix, _content_manager):
        return self.node

    def get_resource_provenance(self):
        return ResourceProvenance("maps/compiled_map.vmap_c")


class FakeWorldResource:
    name = "compiled_world"
    _filepath = "maps/compiled_map.vwrld_c"

    def __init__(self):
        self.data_block = {
            "m_worldNodes": [{
                "m_layerName": "art",
                "m_worldNodePrefix": "maps/compiled_map/worldnodes/node000",
            }],
            "m_entityLumps": (),
        }


def test_map_reconstruction_expands_aggregates_without_fragment_transforms():
    aggregate = {
        "m_renderableModel": "models/props/crate.vmdl_c",
        "m_fragmentTransforms": (),
        "m_aggregateMeshes": (
            {
                "m_bHasTransform": False,
                "m_nDrawCallIndex": 3,
                "m_vTintColor": (10, 20, 30, 255),
            },
            {
                "m_bHasTransform": True,
                "m_nDrawCallIndex": 7,
                "m_vTintColor": (40, 50, 60, 255),
            },
        ),
    }
    report = LossReport()

    document = hammer_document_from_compiled(
        FakeMapResource(FakeWorldNode(aggregate)),
        FakeWorldResource(),
        object(),
        report=report,
    )

    assert len(document.props) == 2
    assert [prop.tint for prop in document.props] == [
        (10.0, 20.0, 30.0, 255.0),
        (40.0, 50.0, 60.0, 255.0),
    ]
    assert document.props[0].source_id != document.props[1].source_id
    assert document.metadata["compiled_scene_objects"][0]["data"] == to_json_safe(aggregate)
    codes = {diagnostic.code for diagnostic in report}
    assert "map.aggregate.expanded" in codes
    assert "map.aggregate.transform.missing" in codes

    vmap = HammerMapExporter().build(document, report=report).files["compiled_map.vmap"]
    assert datamodel.parse(vmap).format == "vmap"
