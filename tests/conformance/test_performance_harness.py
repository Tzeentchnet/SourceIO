from __future__ import annotations

import json
from pathlib import Path

from SourceIO.tools.source2_conformance import benchmark_paths


def test_benchmark_report_uses_raw_samples_without_budget(fixture_root: Path):
    ticks = iter((100, 110, 200, 230, 300, 350))
    report = benchmark_paths(
        (
            fixture_root / "minimal_texture.vtex_c",
            fixture_root / "minimal_mesh.vmesh_c",
        ),
        iterations=3,
        warmups=1,
        clock=lambda: next(ticks),
    )
    assert report["samples_ns"] == [10, 30, 50]
    assert report["min_ns"] == 10
    assert report["median_ns"] == 30
    assert report["mean_ns"] == 30
    assert report["p95_ns"] == 50
    assert report["max_ns"] == 50
    assert report["budget_asserted"] is False
    assert report["resource_count"] == 2
    assert json.loads(json.dumps(report)) == report


def test_benchmark_rejects_invalid_iteration_counts(fixture_root: Path):
    path = fixture_root / "minimal_texture.vtex_c"
    for iterations in (0, -1):
        try:
            benchmark_paths((path,), iterations=iterations)
        except ValueError as error:
            assert "iterations" in str(error)
        else:
            raise AssertionError("invalid iteration count was accepted")
