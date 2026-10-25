from io import StringIO

import pytest

from SourceIO.library.utils import perf_sampler


@pytest.fixture(autouse=True)
def clean_timing_stats(monkeypatch):
    perf_sampler.reset_stats()
    monkeypatch.setattr(perf_sampler, "CAPTURING_ENABLED", True)
    yield
    perf_sampler.reset_stats()


def test_timed_aggregates_success_and_failure(monkeypatch):
    ticks = iter((1.0, 1.25, 2.0, 2.5))
    monkeypatch.setattr(perf_sampler.time, "perf_counter", lambda: next(ticks))

    @perf_sampler.timed
    def measured(fail=False):
        if fail:
            raise RuntimeError("expected")
        return 7

    assert measured() == 7
    with pytest.raises(RuntimeError, match="expected"):
        measured(True)

    report = perf_sampler.snapshot_stats()
    assert len(report["timers"]) == 1
    summary = report["timers"][0]
    assert summary["name"].endswith(".measured")
    assert summary["count"] == 2
    assert summary["total_seconds"] == pytest.approx(0.75)
    assert summary["min_seconds"] == pytest.approx(0.25)
    assert summary["max_seconds"] == pytest.approx(0.5)


def test_timed_region_and_empty_report(monkeypatch):
    output = StringIO()
    perf_sampler.print_stats(output)
    assert output.getvalue().splitlines() == [
        "SourceIO import performance:",
        "No timing data collected.",
    ]

    ticks = iter((3.0, 3.125))
    monkeypatch.setattr(perf_sampler.time, "perf_counter", lambda: next(ticks))
    with perf_sampler.timed_region("phase"):
        pass

    assert perf_sampler.snapshot_stats()["timers"][0]["total_seconds"] == pytest.approx(0.125)


def test_timer_names_and_report_rows_are_bounded(monkeypatch):
    monkeypatch.setattr(perf_sampler, "MAX_TIMER_NAMES", 4)
    monkeypatch.setattr(perf_sampler, "MAX_REPORT_ROWS", 2)

    for index in range(6):
        perf_sampler.record_timing(f"timer-{index}", index + 1.0)

    report = perf_sampler.snapshot_stats(limit=2)
    assert len(perf_sampler.FUNCTION_TIMES) == 4
    assert len(report["timers"]) == 2
    assert report["overflow_samples"] == 3
    assert report["omitted_timer_rows"] == 2
    assert any(name == "<other timers>" for name in perf_sampler.FUNCTION_TIMES)


def test_report_limit_validation():
    with pytest.raises(TypeError, match="integer"):
        perf_sampler.snapshot_stats(limit=True)
    with pytest.raises(ValueError, match="between"):
        perf_sampler.snapshot_stats(limit=perf_sampler.MAX_REPORT_ROWS + 1)
