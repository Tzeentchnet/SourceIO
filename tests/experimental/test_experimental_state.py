import pytest

from SourceIO.blender_bindings.source2.experimental import (
    ExperimentalFeatureDisabled,
    ExperimentalFeatureState,
    ExperimentalFeatureUnavailable,
)
from SourceIO.library.source2.serialization import CapabilityReport


def _report(supported: bool) -> CapabilityReport:
    return CapabilityReport(
        feature="source2_particles",
        operation="render",
        supported=supported,
    )


def test_experimental_feature_state_is_disabled_by_default():
    state = ExperimentalFeatureState.from_report(
        _report(True),
        label="Particle preview",
    )

    assert not state.active
    with pytest.raises(ExperimentalFeatureDisabled):
        state.require_active()


def test_experimental_feature_requires_capability_support():
    state = ExperimentalFeatureState.from_report(
        _report(False),
        label="Particle preview",
        enabled=True,
        available=True,
    )

    assert not state.active
    with pytest.raises(ExperimentalFeatureUnavailable):
        state.require_active()


def test_supported_enabled_feature_is_active_without_importing_bpy():
    state = ExperimentalFeatureState.from_report(
        _report(True),
        label="Particle preview",
        enabled=True,
    )

    assert state.active
    state.require_active()
