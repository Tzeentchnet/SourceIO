from SourceIO.library.source2.keyvalues3.types import Array, Object, String
from SourceIO.library.source2.particles import (
    ParticleCapabilityLevel,
    ParticleCapabilityRegistry,
    ParticleComponentKind,
    report_particle_capabilities,
)


_COMPONENTS = (
    (ParticleComponentKind.EMITTER, "m_Emitters", "CEmit"),
    (ParticleComponentKind.INITIALIZER, "m_Initializers", "CInit"),
    (ParticleComponentKind.OPERATOR, "m_Operators", "COp"),
    (ParticleComponentKind.FORCE, "m_ForceGenerators", "CForce"),
    (ParticleComponentKind.CONSTRAINT, "m_Constraints", "CConstraint"),
    (ParticleComponentKind.PRE_EMISSION, "m_PreEmissionOperators", "CPre"),
    (ParticleComponentKind.RENDERER, "m_Renderers", "CRender"),
)


def _particle_with_every_component() -> Object:
    return Object({
        member: Array([Object({"_class": String(class_name), "retained": String("yes")})])
        for _, member, class_name in _COMPONENTS
    })


def test_capability_report_lists_every_component_and_refuses_execution():
    source = _particle_with_every_component()

    report = report_particle_capabilities(source)

    assert not report.supported
    assert len(report.components) == len(_COMPONENTS)
    assert {
        (component.kind, component.class_name)
        for component in report.components
    } == {
        (kind, class_name)
        for kind, _, class_name in _COMPONENTS
    }
    assert all(component.preserved for component in report.components)
    assert all(not component.supported for component in report.components)
    assert len([
        diagnostic
        for diagnostic in report.diagnostics
        if diagnostic.code == "particle.component.unsupported"
    ]) == len(_COMPONENTS)
    assert all(entries[0]["retained"] == "yes" for entries in source.values())


def test_only_explicit_component_and_resource_declarations_enable_support():
    source = _particle_with_every_component()
    registry = ParticleCapabilityRegistry()
    for kind, _, class_name in _COMPONENTS:
        level = (
            ParticleCapabilityLevel.RENDERABLE
            if kind is ParticleComponentKind.RENDERER
            else ParticleCapabilityLevel.SIMULATABLE
        )
        registry.declare_component(kind, class_name, level)
    registry.declare_resource_simulation(evidence=("verified-test-adapter",))

    report = report_particle_capabilities(source, registry=registry)

    assert report.supported
    assert not report.unsupported_components


def test_missing_class_is_reported_without_dropping_data():
    entry = Object({"custom": String("retained")})
    source = Object({"m_Emitters": Array([entry])})

    report = report_particle_capabilities(source)

    assert report.components[0].class_name == "<missing-class>"
    assert report.components[0].preserved
    assert source["m_Emitters"][0]["custom"] == "retained"
