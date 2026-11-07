"""Comprehensive characterization and boundary tests for cislunar_ephemeris."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import cislunar_ephemeris as ce

ROOT = Path(__file__).resolve().parents[1]
OEM_FIXTURE = ROOT / "fixtures" / "synthetic_free_return.oem"
CSV_FIXTURE = ROOT / "fixtures" / "synthetic_free_return.csv"
BASE_EPOCH = datetime(2026, 4, 2, tzinfo=UTC)


def _iso(days: float = 0.0) -> str:
    moment = BASE_EPOCH + timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _row(
    epoch: str,
    x: float | str,
    y: float | str,
    z: float | str,
    vx: float | str = 0.0,
    vy: float | str = 8.0,
    vz: float | str = 0.0,
) -> str:
    return f"{epoch} {x} {y} {z} {vx} {vy} {vz}"


def _valid_rows() -> list[str]:
    return [
        _row(_iso(0.0), 7000.0, 0.0, 0.0, 0.0, 8.0, 0.0),
        _row(_iso(0.5), 7000.0, 345600.0, 0.0, 0.0, 8.0, 0.0),
        _row(_iso(1.0), 7000.0, 691200.0, 0.0, 0.0, 8.0, 0.0),
    ]


def _make_oem(
    rows: list[str] | None = None,
    *,
    header: str = "CCSDS_OEM_VERS = 2.0",
    comments: list[str] | None = None,
    meta_lines: list[str] | None = None,
    trailing_lines: list[str] | None = None,
) -> str:
    lines: list[str] = []
    if header:
        lines.append(header)
    lines.extend(comments or ["COMMENT PROVENANCE: UNIT_TEST_PROFILE"])
    if meta_lines is None:
        meta_lines = [
            "META_START",
            "OBJECT_NAME = TEST_PROBE",
            "OBJECT_ID = TEST-2026_01.A",
            "CENTER_NAME = EARTH",
            "REF_FRAME = EME2000",
            "TIME_SYSTEM = UTC",
            "INTERPOLATION = HERMITE",
            "META_STOP",
        ]
    lines.extend(meta_lines)
    lines.extend(_valid_rows() if rows is None else rows)
    if trailing_lines:
        lines.extend(trailing_lines)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------- epoch & julian date
@pytest.mark.parametrize(
    ("text", "expected_micro"),
    [
        ("2026-04-02T00:00:00Z", 0),
        ("  2026-04-02T00:00:00.5Z  ", 500_000),
        ("2026-04-02T00:00:00.123456Z", 123_456),
        ("2026-04-02T00:00:00.123456789Z", 123_456),
    ],
)
def test_parse_epoch_fractional_precision(text: str, expected_micro: int) -> None:
    parsed = ce.parse_epoch(text)
    assert parsed.tzinfo == UTC
    assert parsed.microsecond == expected_micro


@pytest.mark.parametrize(
    "bad_epoch",
    [
        "",
        "2026-02-29T00:00:00Z",  # 2026 is not a leap year
        "2026-04-31T00:00:00Z",  # April has 30 days
        "2026-13-01T00:00:00Z",
        "2026-04-02T24:00:00Z",
        "2026-04-02T23:59:60Z",
        "2026-04-02T00:00:00+00:00",
        "2026-04-02T00:00:00.1234567890Z",  # > 9 fractional digits
    ],
)
def test_parse_epoch_rejects_invalid_strings(bad_epoch: str) -> None:
    with pytest.raises(ValueError):
        ce.parse_epoch(bad_epoch)


@pytest.mark.parametrize("non_text", [None, 123, True, ["2026-04-02T00:00:00Z"]])
def test_parse_epoch_rejects_non_string_inputs(non_text: object) -> None:
    with pytest.raises(ValueError, match="epoch must be"):
        ce.parse_epoch(non_text)  # type: ignore[arg-type]


def test_julian_date_j2000_and_offset_awareness() -> None:
    j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert ce.julian_date(j2000) == pytest.approx(2451545.0, abs=1e-12)

    plus_two = timezone(timedelta(hours=2))
    same_instant = datetime(2000, 1, 1, 14, 0, 0, tzinfo=plus_two)
    assert ce.julian_date(same_instant) == pytest.approx(2451545.0, abs=1e-12)


def test_julian_date_rejects_naive_and_invalid_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ce.julian_date(datetime(2026, 4, 2, 0, 0, 0))
    with pytest.raises(ValueError, match="datetime"):
        ce.julian_date("2026-04-02T00:00:00Z")  # type: ignore[arg-type]


# --------------------------------------------------- numeric & state contracts
@pytest.mark.parametrize("invalid", [True, False, "1.0", None, [1.0]])
def test_finite_rejects_non_numeric_and_booleans(invalid: object) -> None:
    with pytest.raises(ValueError, match="numeric value required"):
        ce._finite(invalid, "test", 100.0)  # type: ignore[arg-type]


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
def test_finite_rejects_nan_and_inf(non_finite: float) -> None:
    with pytest.raises(ValueError, match="finite value required"):
        ce._finite(non_finite, "test", 100.0)


def test_finite_accepts_exact_bounds_and_rejects_excess() -> None:
    assert ce._finite(100, "bound", 100.0) == 100.0
    assert ce._finite(-100.0, "bound", 100.0) == -100.0
    with pytest.raises(ValueError, match="exceeds supported bound"):
        ce._finite(100.0001, "bound", 100.0)


def test_state_helper_rejects_wrong_component_count_and_non_numeric_tokens() -> None:
    with pytest.raises(ValueError, match="expected six numbers"):
        ce._state(_iso(0.0), ["1", "2", "3"], 1)
    with pytest.raises(ValueError, match="invalid number|could not convert"):
        ce._state(_iso(0.0), ["7000", "bad_float", "0", "0", "8", "0"], 1)


# ------------------------------------------------------- OEM parser edge cases
def test_parse_oem_rejects_oversized_text() -> None:
    with pytest.raises(ValueError, match="OEM input must be a string"):
        ce.parse_oem(None)  # type: ignore[arg-type]
    huge = "COMMENT " + ("x" * 5_000_001)
    with pytest.raises(ValueError, match="exceeds 5 MB"):
        ce.parse_oem(huge)


def test_load_text_rejects_oversized_file(tmp_path: Path) -> None:
    dummy = tmp_path / "oversized.oem"
    dummy.write_text("small", encoding="utf-8")
    fake_stat = dummy.stat()
    with patch.object(Path, "stat") as mock_stat:
        mock_stat.return_value = type("Stat", (), {"st_size": 5_000_001, "st_mode": fake_stat.st_mode})()
        with pytest.raises(ValueError, match="exceeds 5 MB"):
            ce.load_text(dummy)


def test_parse_oem_header_and_meta_structure_errors() -> None:
    # Missing version header
    with pytest.raises(ValueError, match="missing CCSDS_OEM_VERS"):
        ce.parse_oem(_make_oem(header=""))

    # Duplicate version header
    with pytest.raises(ValueError, match="duplicate CCSDS_OEM_VERS"):
        ce.parse_oem("CCSDS_OEM_VERS = 2.0\n" + _make_oem())

    # META_STOP without META_START
    with pytest.raises(ValueError, match="META_STOP without META_START"):
        ce.parse_oem(_make_oem(meta_lines=["META_STOP"]))

    # Multiple META blocks
    two_meta = [
        "META_START",
        "OBJECT_NAME = TEST",
        "META_STOP",
        "META_START",
        "OBJECT_ID = T-1",
        "CENTER_NAME = EARTH",
        "REF_FRAME = EME2000",
        "TIME_SYSTEM = UTC",
        "META_STOP",
    ]
    with pytest.raises(ValueError, match="exactly one META block"):
        ce.parse_oem(_make_oem(meta_lines=two_meta))

    # Unclosed META_START
    unclosed = [
        "META_START",
        "OBJECT_NAME = TEST",
        "OBJECT_ID = T-1",
        "CENTER_NAME = EARTH",
        "REF_FRAME = EME2000",
        "TIME_SYSTEM = UTC",
    ]
    with pytest.raises(ValueError, match="META_START/META_STOP|metadata lines must use"):
        ce.parse_oem(_make_oem(meta_lines=unclosed))

    # Malformed line inside META block (no '=')
    bad_meta_line = [
        "META_START",
        "INVALID_LINE_WITHOUT_EQUALS",
        "META_STOP",
    ]
    with pytest.raises(ValueError, match="metadata lines must use KEY = VALUE"):
        ce.parse_oem(_make_oem(meta_lines=bad_meta_line))

    # Empty key before '='
    empty_key = [
        "META_START",
        " = VALUE",
        "META_STOP",
    ]
    with pytest.raises(ValueError, match="empty keyword"):
        ce.parse_oem(_make_oem(meta_lines=empty_key))

    # Key=Value after state vectors
    with pytest.raises(ValueError, match="header or metadata after state vectors"):
        ce.parse_oem(_make_oem(trailing_lines=["ORIGINATOR = LATE_HEADER"]))


@pytest.mark.parametrize("bad_id", ["BAD ID", "BAD/ID", "BAD$ID", ""])
def test_parse_oem_rejects_unsupported_object_id(bad_id: str) -> None:
    meta = [
        "META_START",
        "OBJECT_NAME = TEST_PROBE",
        f"OBJECT_ID = {bad_id}",
        "CENTER_NAME = EARTH",
        "REF_FRAME = EME2000",
        "TIME_SYSTEM = UTC",
        "META_STOP",
    ]
    with pytest.raises(ValueError, match="OBJECT_ID"):
        ce.parse_oem(_make_oem(meta_lines=meta))


@pytest.mark.parametrize("forbidden", ["COVARIANCE_START", "COVARIANCE_STOP", "META_START_2"])
def test_parse_oem_rejects_all_forbidden_blocks(forbidden: str) -> None:
    with pytest.raises(ValueError, match="outside the supported subset"):
        ce.parse_oem(_make_oem(trailing_lines=[forbidden]))


def test_parse_oem_provenance_defaults_and_first_match() -> None:
    no_prov = _make_oem(comments=["COMMENT plain note without tag"])
    assert ce.parse_oem(no_prov).provenance == "unspecified"

    multi_prov = _make_oem(
        comments=[
            "COMMENT provenance : FIRST_SOURCE",
            "COMMENT PROVENANCE: SECOND_SOURCE",
        ]
    )
    assert ce.parse_oem(multi_prov).provenance == "FIRST_SOURCE"


@pytest.mark.parametrize("bad_val", ["nan", "inf", "-inf", "100.001", "-100.5"])
def test_parse_oem_rejects_invalid_velocities(bad_val: str) -> None:
    rows = _valid_rows()
    rows[1] = _row(_iso(0.5), 7000.0, 345600.0, 0.0, 0.0, bad_val, 0.0)
    with pytest.raises(ValueError):
        ce.parse_oem(_make_oem(rows=rows))


def test_epoch_order_validation_precedence_and_linear_pass() -> None:
    # Disorder before duplicate raises monotonic order error
    disordered_then_dup = [
        _row(_iso(0.0), 7000.0, 0.0, 0.0),
        _row(_iso(1.0), 7000.0, 200.0, 0.0),
        _row(_iso(0.5), 7000.0, 100.0, 0.0),
        _row(_iso(0.5), 7000.0, 100.0, 0.0),
    ]
    with pytest.raises(ValueError, match="strictly increasing epoch order"):
        ce.parse_oem(_make_oem(rows=disordered_then_dup))

    # Duplicate before disorder raises duplicate epoch error
    dup_then_disordered = [
        _row(_iso(0.5), 7000.0, 100.0, 0.0),
        _row(_iso(0.5), 7000.0, 100.0, 0.0),
        _row(_iso(0.0), 7000.0, 0.0, 0.0),
    ]
    with pytest.raises(ValueError, match="duplicate epoch"):
        ce.parse_oem(_make_oem(rows=dup_then_disordered))


# ------------------------------------------------------- CSV parser edge cases
def test_parse_csv_empty_and_malformed_rows() -> None:
    with pytest.raises(ValueError, match="CSV input must be a string"):
        ce.parse_csv(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="CSV text exceeds 5 MB"):
        ce.parse_csv("x" * 5_000_001)
    with pytest.raises(ValueError, match="empty CSV"):
        ce.parse_csv("")

    bad_row_csv = (
        "epoch,x_km,y_km,z_km,vx_kms,vy_kms,vz_kms\n"
        "\n"
        "   ,   ,   \n"
        f"{_iso(0.0)},7000,0,0,0,8\n"
    )
    with pytest.raises(ValueError, match="CSV row 4: expected seven columns"):
        ce.parse_csv(bad_row_csv)


def test_parse_csv_ignores_blank_lines_and_normalizes_header_case() -> None:
    csv_text = (
        " EPOCH , X_KM , Y_KM , Z_KM , VX_KMS , VY_KMS , VZ_KMS \n"
        "\n"
        f"{_iso(0.0)}, 7000.0, 0.0, 0.0, 0.0, 8.0, 0.0\n"
        " , , , , , , \n"
        f"{_iso(0.5)}, 7000.0, 345600.0, 0.0, 0.0, 8.0, 0.0\n"
        f"{_iso(1.0)}, 7000.0, 691200.0, 0.0, 0.0, 8.0, 0.0\n"
    )
    traj = ce.parse_csv(csv_text)
    assert len(traj.states) == 3
    assert traj.provenance == "synthetic CSV replay"


# --------------------------------------------- interpolation & telemetry edges
def test_interpolate_rejects_duplicate_span_and_invalid_time_types() -> None:
    s0 = ce.State(BASE_EPOCH, 0.0, (7000.0, 0.0, 0.0), (0.0, 8.0, 0.0))
    single_traj = ce.Trajectory({}, "test", (s0,))
    with pytest.raises(ValueError, match="at least two states"):
        ce.interpolate(single_traj, 0.0)
    s1 = ce.State(BASE_EPOCH, 0.0, (7000.0, 10.0, 0.0), (0.0, 8.0, 0.0))
    dup_traj = ce.Trajectory({}, "test", (s0, s1))
    with pytest.raises(ValueError, match="duplicate epochs"):
        ce.interpolate(dup_traj, 0.0)

    valid_traj = ce.parse_oem(_make_oem())
    for bad_t in (True, False, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            ce.interpolate(valid_traj, bad_t)  # type: ignore[arg-type]


def test_telemetry_rejects_zero_earth_distance() -> None:
    origin_state = ce.State(BASE_EPOCH, 0.0, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="zero Earth distance"):
        ce.telemetry(origin_state)


# ----------------------------------- continuous Earth screen & track bounds
def test_earth_segment_clear_detects_interior_dips_on_all_axes() -> None:
    for axis in range(3):
        r_left = [0.0, 0.0, 0.0]
        r_right = [0.0, 0.0, 0.0]
        v_left = [0.0, 0.0, 0.0]
        v_right = [0.0, 0.0, 0.0]
        r_left[axis] = 7000.0
        r_right[axis] = 7000.0
        v_left[axis] = -30.0
        v_right[axis] = 30.0
        left = ce.State(
            BASE_EPOCH,
            0.0,
            (r_left[0], r_left[1], r_left[2]),
            (v_left[0], v_left[1], v_left[2]),
        )
        right = ce.State(
            BASE_EPOCH + timedelta(seconds=100),
            100.0,
            (r_right[0], r_right[1], r_right[2]),
            (v_right[0], v_right[1], v_right[2]),
        )
        assert not ce.earth_segment_clear(left, right)


def test_to_payload_and_build_track_point_bounds() -> None:
    traj = ce.parse_oem(_make_oem())

    # Lower valid boundary: 2 points
    payload_min = ce.to_payload(traj, track_points=2)
    assert len(payload_min["track"]) == 2
    assert payload_min["track"][0]["t_s"] == pytest.approx(0.0)
    assert payload_min["track"][-1]["t_s"] == pytest.approx(traj.states[-1].t_s)

    # Upper valid boundary: 100_000 points
    dummy_state = traj.states[0]
    dummy_telemetry = {
        "earth_distance_km": 7000.0,
        "earth_altitude_km": 621.863,
        "moon_distance_km": 380000.0,
        "moon_altitude_km": 378262.6,
        "speed_kms": 8.0,
        "radial_velocity_kms": 0.0,
        "earth_two_body_energy_km2_s2": -24.9,
        "moon_position_km": [-380000.0, 0.0, 0.0],
    }
    with (
        patch.object(ce, "interpolate", return_value=dummy_state),
        patch.object(ce, "telemetry", side_effect=lambda _s: dict(dummy_telemetry)),
    ):
        track_max = ce.build_track(traj, points=100_000)
    assert len(track_max) == 100_000
    assert track_max[0]["t_s"] == pytest.approx(0.0)
    assert track_max[-1]["t_s"] == pytest.approx(traj.states[-1].t_s)

    # Out-of-range integers and non-int types (including bool and float)
    for invalid_points in (0, -1, 1, 100_001, True, False, 2.0, 10.5, "100", None):
        with pytest.raises(ValueError, match="track needs"):
            ce.to_payload(traj, track_points=invalid_points)  # type: ignore[arg-type]


# --------------------------------------------------------------- CLI execution
def test_cli_main_in_process_and_gate_failure_exit_code(tmp_path: Path) -> None:
    # Gate failure writes diagnostic JSON and returns exit code 2
    earth_hit_oem = tmp_path / "impact.oem"
    earth_hit_rows = [_row(_iso(i * 0.5), 6300.0, 0.0, 0.0) for i in range(3)]
    earth_hit_oem.write_text(_make_oem(rows=earth_hit_rows), encoding="utf-8")
    out_json = tmp_path / "impact.json"

    rc = ce.main(["--oem", str(earth_hit_oem), "--out", str(out_json), "--track-points", "5"])
    assert rc == 2
    written = json.loads(out_json.read_text(encoding="utf-8"))
    assert written["gates"]["status"] == "fail"


def test_cli_main_handles_missing_source_with_existing_output(tmp_path: Path) -> None:
    missing_source = tmp_path / "does_not_exist.oem"
    existing_out = tmp_path / "existing.json"
    existing_out.write_text("{}", encoding="utf-8")

    rc = ce.main(["--oem", str(missing_source), "--out", str(existing_out)])
    assert rc == 1


def test_cli_main_handles_invalid_track_points_and_unwritable_output(tmp_path: Path) -> None:
    out_json = tmp_path / "out.json"
    assert ce.main(["--oem", str(OEM_FIXTURE), "--out", str(out_json), "--track-points", "1"]) == 1
    assert ce.main(["--oem", str(OEM_FIXTURE), "--out", str(out_json), "--track-points", "100001"]) == 1

    # Output path is an existing directory -> OSError caught cleanly with exit code 1
    out_dir = tmp_path / "output_is_a_dir"
    out_dir.mkdir()
    assert ce.main(["--oem", str(OEM_FIXTURE), "--out", str(out_dir), "--track-points", "10"]) == 1
