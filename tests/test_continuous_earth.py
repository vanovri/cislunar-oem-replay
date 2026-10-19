from datetime import datetime, timedelta, timezone

import cislunar_ephemeris as ce


def state(t, x, vx=0):
    return ce.State(datetime(2026, 4, 2, tzinfo=timezone.utc) + timedelta(seconds=t), t, (x, 0, 0), (vx, 0, 0))


def test_endpoints_safe_but_hermite_interior_hits_earth():
    left, right = state(0, 7000, -30), state(100, 7000, 30)
    assert left.r[0] - ce.R_EARTH_KM > ce.MIN_CLEARANCE_KM
    assert right.r[0] - ce.R_EARTH_KM > ce.MIN_CLEARANCE_KM
    assert not ce.earth_segment_clear(left, right)
    trajectory = ce.Trajectory({}, 'SYNTHETIC_TEST', (left, right, state(200, 9000)))
    assert ce.gates(trajectory)['status'] == 'fail'


def test_stationary_clear_segment_and_unresolved_tangent():
    assert ce.earth_segment_clear(state(0, 7000), state(100, 7000))
    radius = ce.R_EARTH_KM + ce.MIN_CLEARANCE_KM
    assert not ce.earth_segment_clear(state(0, radius), state(100, radius))
