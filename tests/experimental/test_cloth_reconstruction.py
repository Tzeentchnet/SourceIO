from SourceIO.library.source2.cloth import (
    ClothBackendCapability,
    ClothBackendResult,
    ClothLossSeverity,
    ClothReconstructionLoss,
    ClothReconstructor,
    reconstruct_cloth,
)


def test_cloth_reconstruction_is_default_off():
    source = {"compiled": "cloth"}

    result = reconstruct_cloth(source)

    assert not result.attempted
    assert not result.supported
    assert result.geometry is None
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "cloth.reconstruction.disabled"
    }
    assert not result.losses


def test_enabled_cloth_without_backend_returns_blocking_loss_not_fallback_geometry():
    result = reconstruct_cloth({"compiled": "cloth"}, enabled=True)

    assert not result.attempted
    assert not result.supported
    assert result.geometry is None
    assert result.losses[0].severity is ClothLossSeverity.BLOCKING
    assert result.losses[0].code == "cloth.geometry.not_reconstructed"


def test_lossy_backend_without_loss_accounting_is_rejected():
    backend = ClothBackendCapability(
        "test-lossy",
        lambda source, provenance, resolver: ClothBackendResult(geometry={"vertices": []}),
        evidence=("test",),
        lossless=False,
    )

    result = ClothReconstructor(enabled=True, backend=backend).reconstruct(object())

    assert result.attempted
    assert not result.supported
    assert result.geometry is None
    assert "cloth.reconstruction.missing_loss_accounting" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_backend_output_keeps_enumerated_nonblocking_losses():
    loss = ClothReconstructionLoss(
        "cloth.constraint.approximated",
        "One constraint was approximated.",
        ClothLossSeverity.DEGRADING,
        ("m_Constraints[0]",),
    )
    backend = ClothBackendCapability(
        "test-accounted",
        lambda source, provenance, resolver: ClothBackendResult(
            geometry={"vertices": [(0, 0, 0)]},
            losses=(loss,),
        ),
        evidence=("test",),
        lossless=False,
    )

    result = reconstruct_cloth(object(), enabled=True, backend=backend)

    assert result.supported
    assert result.geometry == {"vertices": [(0, 0, 0)]}
    assert result.losses == (loss,)
