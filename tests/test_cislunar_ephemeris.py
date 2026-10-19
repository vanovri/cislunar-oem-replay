"""Referee tests for the CCSDS OEM subset, interpolation, lunar ephemeris and gates.

These are consistency tests on a synthetic teaching profile. They do not validate
flight dynamics, navigation accuracy or a real mission.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce  # noqa: E402

OEM = ROOT / "fixtures" / "synthetic_free_return.oem"
CSV = ROOT / "fixtures" / "synthetic_free_return.csv"
SCRIPT = ROOT / "cislunar_ephemeris.py"
BASE = datetime(2026, 4, 2, tzinfo=timezone.utc)


def iso(days: float = 0.0) -> str:
    moment = BASE + timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_oem(rows, *, version="2.0", center="EARTH", frame="EME2000",
              time_system="UTC", interpolation=None, include_meta=True, extra_meta=""):
    lines = [f"CCSDS_OEM_VERS = {version}", "COMMENT test fixture"]
    if include_meta:
        lines += [
            "META_START",
            "OBJECT_NAME = TEST_OBJECT",
            "OBJECT_ID = TEST-001",
            f"CENTER_NAME = {center}",
            f"REF_FRAME = {frame}",
            f"TIME_SYSTEM = {time_system}",
        ]
        if interpolation:
            lines.append(f"INTERPOLATION = {interpolation}")
        if extra_meta:
            lines.append(extra_meta)
        lines.append("META_STOP")
    lines += [",".join(row) if isinstance(row, (list, tuple)) else row for row in rows]
    return "\n".join(lines) + "\n"


def row(epoch, x, y, z, vx=0.0, vy=0.0, vz=0.0):
    return f"{epoch} {x} {y} {z} {vx} {vy} {vz}"


def straight_rows():
    return [
        row(iso(0.0), 7000.0, 0.0, 0.0, 0.0, 8.0, 0.0),
        row(iso(0.5), 7000.0, 345600.0, 0.0, 0.0, 8.0, 0.0),
        row(iso(1.0), 7000.0, 691200.0, 0.0, 0.0, 8.0, 0.0),
    ]


# ---------------------------------------------------------------- OEM parsing
def test_oem_fixture_metadata():
    trajectory = ce.parse_oem(OEM.read_text(encoding="utf-8"))
    assert trajectory.metadata["REF_FRAME"] == "EME2000"
    assert trajectory.metadata["CENTER_NAME"] == "EARTH"
    assert trajectory.metadata["TIME_SYSTEM"] == "UTC"
    assert trajectory.provenance == "SYNTHETIC_TEACHING_PROFILE_V1"
    assert len(trajectory.states) == 30


def test_every_state_has_finite_numbers_and_increasing_time():
    trajectory = ce.parse_oem(OEM.read_text(encoding="utf-8"))
    times = [state.t_s for state in trajectory.states]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    for state in trajectory.states:
        assert all(math.isfinite(value) for value in state.r + state.v)


def test_csv_replay_matches_oem():
    from_oem = ce.parse_oem(OEM.read_text(encoding="utf-8"))
    from_csv = ce.parse_file(CSV)
    assert len(from_oem.states) == len(from_csv.states)
    for left, right in zip(from_oem.states, from_csv.states):
        assert left.epoch == right.epoch
        assert left.r == pytest.approx(right.r, abs=1e-3)
        assert left.v == pytest.approx(right.v, abs=1e-5)
    assert from_csv.metadata["FORMAT"] == "CSV_REPLAY_V1"


def test_comments_and_blank_lines_are_ignored():
    text = OEM.read_text(encoding="utf-8")
    noisy = "\n\nCOMMENT another comment\n" + text.replace("META_START", "COMMENT note\nMETA_START", 1)
    trajectory = ce.parse_oem(noisy)
    assert len(trajectory.states) == 30


def test_duplicate_metadata_key_is_rejected():
    text = build_oem(straight_rows(), extra_meta="REF_FRAME = EME2000")
    with pytest.raises(ValueError, match="duplicate metadata key REF_FRAME"):
        ce.parse_oem(text)


def test_unsupported_version_is_rejected():
    with pytest.raises(ValueError, match="only CCSDS_OEM_VERS 2.0"):
        ce.parse_oem(build_oem(straight_rows(), version="3.0"))


def test_unsupported_center_is_rejected():
    with pytest.raises(ValueError, match="unsupported center"):
        ce.parse_oem(build_oem(straight_rows(), center="MOON"))


def test_unsupported_frame_is_rejected():
    with pytest.raises(ValueError, match="unsupported reference frame"):
        ce.parse_oem(build_oem(straight_rows(), frame="ITRF"))


def test_unsupported_time_system_is_rejected():
    with pytest.raises(ValueError, match="unsupported time system"):
        ce.parse_oem(build_oem(straight_rows(), time_system="TAI"))


def test_linear_interpolation_is_rejected():
    with pytest.raises(ValueError, match="unsupported INTERPOLATION"):
        ce.parse_oem(build_oem(straight_rows(), interpolation="LINEAR"))


def test_missing_meta_block_is_rejected():
    with pytest.raises(ValueError, match="META_START/META_STOP"):
        ce.parse_oem(build_oem(straight_rows(), include_meta=False))


def test_missing_required_metadata_is_rejected():
    text = build_oem(straight_rows()).replace("OBJECT_ID = TEST-001\n", "")
    with pytest.raises(ValueError, match="missing required metadata: OBJECT_ID"):
        ce.parse_oem(text)


def test_state_row_with_wrong_column_count_is_rejected():
    text = build_oem([
        f"{iso(0)} 7000 0 0 0 8",
        f"{iso(0.5)} 7000 1 0 0 8",
        f"{iso(1)} 7000 2 0 0 8",
    ])
    with pytest.raises(ValueError, match="needs seven columns"):
        ce.parse_oem(text)


def test_non_monotonic_epochs_are_rejected():
    rows = straight_rows()
    rows[1], rows[2] = rows[2], rows[1]
    with pytest.raises(ValueError, match="strictly increasing epoch order"):
        ce.parse_oem(build_oem(rows))


def test_duplicate_epochs_are_rejected():
    rows = straight_rows()
    rows[2] = row(iso(0.5), 7000.0, 345600.0, 0.0, 0.0, 8.0, 0.0)
    with pytest.raises(ValueError, match="duplicate epoch"):
        ce.parse_oem(build_oem(rows))


def test_two_vectors_are_not_enough():
    with pytest.raises(ValueError, match="at least 3 state vectors"):
        ce.parse_oem(build_oem(straight_rows()[:2]))


def test_non_finite_velocity_is_rejected():
    text = build_oem(straight_rows()).replace("0.0 8.0 0.0\n", "0.0 nan 0.0\n", 1)
    with pytest.raises(ValueError, match="finite value required"):
        ce.parse_oem(text)


def test_out_of_bounds_position_is_rejected():
    text = build_oem(straight_rows()).replace("7000.0 0.0 0.0", "70000000.0 0.0 0.0", 1)
    with pytest.raises(ValueError, match="exceeds supported bound"):
        ce.parse_oem(text)


def test_bad_epoch_format_is_rejected():
    text = build_oem(straight_rows()).replace(iso(1.0), "2026-04-03T00:00:00", 1)
    with pytest.raises(ValueError, match="epoch must be UTC ISO-8601"):
        ce.parse_oem(text)


def test_covariance_block_is_outside_subset():
    text = build_oem(straight_rows()).replace("META_STOP", "META_STOP\nCOVARIANCE_START", 1)
    with pytest.raises(ValueError, match="outside the supported subset"):
        ce.parse_oem(text)


def test_csv_header_is_checked():
    text = "epoch,x_km,y_km,z_km,vx_kms,vy_kms\n" + "\n".join(
        ["2026-04-02T00:00:00.000Z,1,2,3,4,5"] * 3
    )
    with pytest.raises(ValueError, match="CSV header must be"):
        ce.parse_csv(text)


# ---------------------------------------------------------------- interpolation
def test_interpolation_reproduces_every_node():
    trajectory = ce.parse_file(OEM)
    for state in trajectory.states:
        rebuilt = ce.interpolate(trajectory, state.t_s)
        assert rebuilt.r == pytest.approx(state.r, abs=1e-6)
        assert rebuilt.v == pytest.approx(state.v, abs=1e-6)


def test_interpolation_reproduces_a_cubic_exactly():
    times = [0.0, 2.0]
    states = []
    for t in times:
        states.append(ce.State(BASE + timedelta(seconds=t), t, (t**3, 2 * t**3, -t**3), (3 * t**2, 6 * t**2, -3 * t**2)))
    trajectory = ce.Trajectory({"REF_FRAME": "EME2000"}, "test", tuple(states))
    for t in (0.25, 0.5, 1.0, 1.75):
        state = ce.interpolate(trajectory, t)
        assert state.r[0] == pytest.approx(t**3, abs=1e-9)
        assert state.v[0] == pytest.approx(3 * t**2, abs=1e-9)


def test_constant_velocity_line_is_exact():
    trajectory = ce.parse_oem(build_oem(straight_rows()))
    middle = ce.interpolate(trajectory, 0.25 * 86400.0)
    assert middle.r[1] == pytest.approx(172800.0, abs=1e-6)
    assert middle.v[1] == pytest.approx(8.0, abs=1e-9)


def test_interpolation_outside_span_is_rejected():
    trajectory = ce.parse_file(OEM)
    with pytest.raises(ValueError, match="outside"):
        ce.interpolate(trajectory, -1.0)
    with pytest.raises(ValueError, match="outside"):
        ce.interpolate(trajectory, trajectory.states[-1].t_s + 1.0)


def test_interpolation_keeps_epoch_consistent():
    trajectory = ce.parse_file(OEM)
    t_s = 3.3 * 86400.0
    state = ce.interpolate(trajectory, t_s)
    assert (state.epoch - trajectory.states[0].epoch).total_seconds() == pytest.approx(t_s, abs=1e-3)


# ---------------------------------------------------------------- lunar ephemeris
def test_lunar_distance_stays_in_physical_range():
    for day in range(0, 28):
        moment = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
        x, y, z = ce.moon_position_eme2000(moment)
        distance = math.sqrt(x * x + y * y + z * z)
        assert 356_000.0 <= distance <= 407_000.0


def test_lunar_ephemeris_is_continuous_over_one_hour():
    first = ce.moon_position_eme2000(BASE)
    second = ce.moon_position_eme2000(BASE + timedelta(hours=1))
    step = math.dist(first, second)
    assert 1000.0 < step < 5000.0  # roughly 1.02 km/s around the Earth


def test_lunar_ephemeris_is_deterministic():
    assert ce.moon_position_eme2000(BASE) == ce.moon_position_eme2000(BASE)


def test_lunar_ephemeris_matches_a_horizons_spot_check():
    # JPL Horizons, Moon 301, geocentric ICRF, 2026-Apr-02 00:00 TDB.
    reference = (-385833.8066617213, -60038.52669098664, -46890.7172753297)
    ours = ce.moon_position_eme2000(datetime(2026, 4, 2, tzinfo=timezone.utc))
    assert math.dist(ours, reference) < 100.0


def test_lunar_declination_stays_physical():
    # z/r is the equatorial declination, which spans roughly +/-28.6 degrees.
    for day in range(0, 28, 3):
        moment = BASE + timedelta(days=day)
        x, y, z = ce.moon_position_eme2000(moment)
        declination = math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z)))
        assert abs(declination) <= 30.0


# ---------------------------------------------------------------- telemetry
def test_telemetry_formulas_for_a_known_state():
    state = ce.State(BASE, 0.0, (7000.0, 0.0, 0.0), (0.0, 10.0, 0.0))
    metrics = ce.telemetry(state)
    assert metrics["earth_distance_km"] == pytest.approx(7000.0)
    assert metrics["earth_altitude_km"] == pytest.approx(7000.0 - ce.R_EARTH_KM)
    assert metrics["speed_kms"] == pytest.approx(10.0)
    assert metrics["earth_two_body_energy_km2_s2"] == pytest.approx(50.0 - ce.MU_EARTH_KM3_S2 / 7000.0)
    assert metrics["radial_velocity_kms"] == pytest.approx(0.0)


def test_fixture_altitudes_and_speed_are_plausible():
    summary = ce.summarize(ce.parse_file(OEM))
    assert summary["min_earth_altitude_km"] > 100.0
    assert summary["min_moon_altitude_km"] > 1000.0
    assert 10.0 < summary["tli_speed_kms"] < 11.5
    assert summary["max_earth_distance_km"] > 380_000.0


def test_two_body_energy_is_diagnostic_not_constant():
    trajectory = ce.parse_file(OEM)
    energies = [ce.telemetry(state)["earth_two_body_energy_km2_s2"] for state in trajectory.states]
    assert max(energies) - min(energies) > 1.0  # must not be advertised as conserved


def test_track_has_requested_length_and_endpoints():
    payload = ce.to_payload(ce.parse_file(OEM), track_points=120)
    track = payload["track"]
    assert len(track) == 120
    assert track[0]["epoch"] == payload["samples"][0]["epoch"]
    assert track[-1]["epoch"] == payload["samples"][-1]["epoch"]
    assert track[0]["t_s"] == pytest.approx(0.0)


def test_track_stays_clear_of_both_bodies():
    payload = ce.to_payload(ce.parse_file(OEM), track_points=400)
    assert min(point["earth_altitude_km"] for point in payload["track"]) > 100.0
    assert min(point["moon_altitude_km"] for point in payload["track"]) > 1000.0


def test_gate_fails_for_earth_impact():
    rows = [row(iso(i), 6300.0, 0.0, 0.0, 0.0, 1.0, 0.0) for i in range(3)]
    trajectory = ce.parse_oem(build_oem(rows))
    result = ce.gates(trajectory)
    assert result["status"] == "fail"
    assert any("Earth clearance" in failure for failure in result["failures"])


def test_gate_fails_for_lunar_impact():
    rows = []
    for index in range(3):
        moment = BASE + timedelta(days=0.5 * index)
        x, y, z = ce.moon_position_eme2000(moment)
        epoch = moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        rows.append(row(epoch, x, y, z, 0.0, 0.0, 0.0))
    trajectory = ce.parse_oem(build_oem(rows))
    result = ce.gates(trajectory)
    assert result["status"] == "fail"
    assert any("lunar clearance" in failure for failure in result["failures"])


# ---------------------------------------------------------------- CLI
def run_cli(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=ROOT, capture_output=True, text=True)


def test_cli_writes_payload_and_exits_zero(tmp_path):
    out = tmp_path / "trajectory.json"
    proc = run_cli("--oem", str(OEM), "--out", str(out), "--track-points", "90")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "CISLUNAR_OEM_V1"
    assert len(payload["track"]) == 90
    assert payload["gates"]["status"] == "pass"


def test_cli_accepts_csv_route(tmp_path):
    out = tmp_path / "trajectory.json"
    proc = run_cli("--csv", str(CSV), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["metadata"]["FORMAT"] == "CSV_REPLAY_V1"


def test_cli_refuses_to_overwrite_its_source():
    proc = run_cli("--oem", str(OEM), "--out", str(OEM))
    assert proc.returncode == 2
    assert OEM.exists()


def test_cli_reports_bad_frame_without_traceback(tmp_path):
    bad = tmp_path / "bad.oem"
    bad.write_text(build_oem(straight_rows(), frame="ITRF"), encoding="utf-8")
    proc = run_cli("--oem", str(bad), "--out", str(tmp_path / "out.json"))
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "error:" in proc.stderr.lower()


def test_payload_has_provenance_and_constants():
    payload = ce.to_payload(ce.parse_file(OEM), track_points=10)
    assert "synthetic" in payload["provenance"]["notice"].lower()
    assert payload["provenance"]["kind"] == "SYNTHETIC_TEACHING_PROFILE_V1"
    assert payload["constants"]["radius_earth_km"] == pytest.approx(6378.137)
    assert payload["summary"]["vector_count"] == 30
