import pytest

from app.config import Settings
from app.services.compression_calibrator import (
    FALLBACK_THROUGHPUT, ThroughputModel, percentile, steal_between,
)
from tests.conftest import requires_ffmpeg


def model(tmp_path, **kw):
    kw.setdefault("compression_throughput_override", None)
    cfg = Settings(downloads_dir=tmp_path / "dl", cookies_dir=tmp_path / "ck", **kw)
    return ThroughputModel(state_file=tmp_path / "calibration.json", cfg=cfg)


def test_fallback_is_pessimistic_without_data(tmp_path):
    m = model(tmp_path)
    assert m.base_throughput() == FALLBACK_THROUGHPUT
    assert m.two_pass_factor() == pytest.approx(1.6)


def test_calibration_is_discounted_until_real_samples(tmp_path):
    m = model(tmp_path)
    m.calibration = 100e6
    assert m.base_throughput() == pytest.approx(80e6)
    # one slower real job pulls it down immediately
    m.observe(work_units=60e6 * 10, pass1_seconds=6, pass2_seconds=10, steal=0)
    assert m.base_throughput() == pytest.approx(60e6)


def test_real_samples_use_conservative_aggregate(tmp_path):
    m = model(tmp_path)
    for rate in (100e6, 50e6, 100e6, 100e6, 100e6):
        m.observe(work_units=rate * 10, pass1_seconds=6, pass2_seconds=10, steal=0)
    # never above the 20th percentile of samples
    assert m.base_throughput() <= percentile(m.samples, 0.2) + 1


def test_steal_normalization_and_penalty(tmp_path):
    m = model(tmp_path)
    m.observe(work_units=50e6 * 10, pass1_seconds=6, pass2_seconds=10, steal=0.5)
    # observed 50 Mpx/s under 50% steal -> 100 Mpx/s steal-free
    assert m.samples[-1] == pytest.approx(100e6)
    m.steal.recent.extend([0.0, 0.3])
    assert m.perf().throughput == pytest.approx(m.base_throughput() * 0.7)


def test_two_pass_factor_is_learned(tmp_path):
    m = model(tmp_path)
    for _ in range(5):
        m.observe(work_units=1e9, pass1_seconds=5, pass2_seconds=10, steal=0)
    # pass 1 took half of pass 2: ratio 1.5, EWMA moving down from the 1.6 start
    assert 1.5 <= m.two_pass_factor() < 1.53


def test_state_survives_restart(tmp_path):
    m = model(tmp_path)
    m.calibration = 70e6
    m.observe(work_units=40e6 * 10, pass1_seconds=6, pass2_seconds=10, steal=0)
    again = model(tmp_path)
    assert again.calibration == 70e6 and list(again.samples) == list(m.samples)


def test_override_disables_model(tmp_path):
    m = model(tmp_path, compression_throughput_override=12345.0)
    m.steal.recent.append(0.5)
    assert m.perf().throughput == 12345.0
    assert m.calibrate() is None


def test_steal_between():
    assert steal_between((10, 1000), (60, 1100)) == pytest.approx(0.5)
    assert steal_between(None, (1, 2)) == 0.0


@requires_ffmpeg
def test_calibration_runs(tmp_path):
    m = model(tmp_path)
    raw = m.calibrate()
    assert raw and raw > 1e6
    assert (tmp_path / "calibration.json").exists()
