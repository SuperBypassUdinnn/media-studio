"""Unit tests for the damped harmonic oscillator spring physics (offline)."""

from __future__ import annotations

from media_studio.effectcraft import calc_spring


def test_calc_spring_starts_at_zero():
    assert calc_spring(0.0) == 0.0


def test_calc_spring_settles_to_one_at_and_beyond_duration():
    assert calc_spring(1.0) == 1.0
    assert calc_spring(2.0) == 1.0
    assert calc_spring(10.0) == 1.0


def test_calc_spring_underdamped_overshoots_past_one():
    # Defaults (damping=12, stiffness=100, mass=1) give zeta = 0.6 < 1, so the
    # spring naturally overshoots past 1.0 before settling back to 1.0.
    peak = max(calc_spring(t / 60.0) for t in range(0, int(2.0 * 60)))
    assert peak > 1.0, f"expected spring overshoot > 1.0, got peak {peak}"


def test_calc_spring_returns_within_bounds_before_settle():
    # Values during the motion should stay in a sane range (no NaN, no explosion).
    for t in (0.0, 0.1, 0.3, 0.5, 0.8, 1.0):
        value = calc_spring(t)
        assert value == value  # not NaN
        assert abs(value) < 2.0


def test_calc_spring_high_damping_no_overshoot():
    # Heavy damping (zeta >= 1) settles monotonically without exceeding 1.0.
    values = [calc_spring(t / 60.0, damping=40.0, stiffness=100.0, mass=1.0) for t in range(0, 121)]
    assert all(value <= 1.0 for value in values)
    assert values[-1] == 1.0


def test_calc_spring_clamps_negative_time_to_zero():
    assert calc_spring(-1.0) == calc_spring(0.0) == 0.0
