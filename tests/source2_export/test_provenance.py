import json
from types import SimpleNamespace

from SourceIO.library.source2.blocks.resource_edit_info.dependencies.additional_related_file import \
    AdditionalRelatedFile
from SourceIO.library.source2.blocks.resource_edit_info.dependencies.child_resource import ChildResource
from SourceIO.library.source2.blocks.resource_edit_info.dependencies.custom_dependency import CustomDependency
from SourceIO.library.source2.blocks.resource_edit_info.dependencies.input_dependency import InputDependency
from SourceIO.library.source2.blocks.resource_external_reference_list import (
    ResourceExternalReference,
    ResourceExternalReferenceList,
)
from SourceIO.library.source2.keyvalues3.types import Object, String
from SourceIO.library.source2.provenance import (
    DependencyKind,
    ResourceDependency,
    ResourceProvenance,
    build_dependency_graph,
    dependencies_from_edit_info,
    dependencies_from_rerl,
)


def dependency(source, target):
    return ResourceDependency(source, target, DependencyKind.CHILD, "test")


def test_dependency_graph_is_deterministic_and_stops_cycles():
    graph = {
        "models/a.vmdl": [
            dependency("models/a.vmdl", "models/c.vmdl"),
            dependency("models/a.vmdl", "models/b.vmdl"),
        ],
        "models/b.vmdl": [dependency("models/b.vmdl", "models/a.vmdl")],
        "models/c.vmdl": [dependency("models/c.vmdl", "models/b.vmdl")],
    }

    provenance = build_dependency_graph("models/a.vmdl", graph)

    assert provenance.resources == ("models/a.vmdl", "models/b.vmdl", "models/c.vmdl")
    assert [item.target for item in provenance.dependencies] == [
        "models/b.vmdl",
        "models/c.vmdl",
        "models/a.vmdl",
        "models/b.vmdl",
    ]
    assert provenance.cycles == (("models/a.vmdl", "models/b.vmdl", "models/a.vmdl"),)
    assert json.loads(provenance.to_json()) == provenance.to_dict()


def test_rerl_and_edit_info_normalize_without_parser_objects():
    rerl = ResourceExternalReferenceList()
    rerl.extend([
        ResourceExternalReference(7, 2, "materials/b.vmat", 0),
        ResourceExternalReference(3, 1, "models/a.vmdl", 9),
    ])
    edit_info = SimpleNamespace(
        inputs=[InputDependency("mesh.fbx", "CONTENT", 123, 4)],
        additional_inputs=[InputDependency("mesh.tga", "GAME", 456, 8)],
        child_resources=[ChildResource(22, "models/child.vmdl", 6)],
        additional_files=[AdditionalRelatedFile("notes.txt", "CONTENT")],
        custom_deps=[CustomDependency(Object({
            "m_RelativeFilename": String("scripts/custom.txt"),
            "m_Mode": String("copy"),
        }))],
        arguments=[],
        special_deps=[],
    )

    dependencies = (
        *dependencies_from_rerl(rerl, "models/root.vmdl_c"),
        *dependencies_from_edit_info(edit_info, "models/root.vmdl_c", origin="RED2"),
    )
    serialized = [item.to_dict() for item in dependencies]

    assert [item["target"] for item in serialized] == [
        "materials/b.vmat",
        "models/a.vmdl",
        "mesh.fbx",
        "mesh.tga",
        "models/child.vmdl",
        "notes.txt",
        "scripts/custom.txt",
    ]
    assert {item["kind"] for item in serialized} == {
        "external_reference",
        "input",
        "additional_input",
        "child",
        "related",
        "custom",
    }
    json.dumps(serialized, allow_nan=False)


def test_provenance_dictionary_round_trip():
    original = ResourceProvenance(
        root_resource="models/root.vmdl_c",
        resources=("models/root.vmdl_c", "models/mesh.vmesh"),
        dependencies=(ResourceDependency(
            "models/root.vmdl_c",
            "models/mesh.vmesh",
            DependencyKind.EXTERNAL_REFERENCE,
            "RERL",
            identifier=10,
            metadata={"resource_id": 5},
        ),),
        cycles=(),
        metadata={"asset_kind": "model"},
    )

    restored = ResourceProvenance.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()


def test_dependency_availability_is_part_of_deterministic_identity():
    dependencies = (
        ResourceDependency(
            "models/root.vmdl_c",
            "models/mesh.vmesh_c",
            DependencyKind.CHILD,
            "test",
            available=False,
        ),
        ResourceDependency(
            "models/root.vmdl_c",
            "models/mesh.vmesh_c",
            DependencyKind.CHILD,
            "test",
            available=True,
        ),
    )

    provenance = build_dependency_graph(
        "models/root.vmdl_c",
        {"models/root.vmdl_c": dependencies},
    )

    assert [dependency.available for dependency in provenance.dependencies] == [False, True]
