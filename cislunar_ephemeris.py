"""Strict CCSDS OEM subset reader for a synthetic cislunar teaching profile.

Scope:
  * CCSDS OEM 2.0 text with one META block, EME2000, EARTH-centred, UTC, km and km/s.
  * A documented CSV replay equivalent (prior telemetry export format).
  * Cubic Hermite interpolation that uses the supplied velocities.
  * A same-frame analytic lunar ephemeris (truncated ELP2000-82B / Meeus).

It is a teaching parser. It is not a flight-dynamics propagator, not a
navigation tool and not a reader for authenticated NASA products.
"""
from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import logging
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard

logger = logging.getLogger(__name__)

Vec3 = tuple[float, float, float]
CubicCurve = tuple[Vec3, Vec3, Vec3, Vec3]

MU_EARTH_KM3_S2 = 398600.4418
MU_MOON_KM3_S2 = 4902.800066
R_EARTH_KM = 6378.137
R_MOON_KM = 1737.4
MIN_CLEARANCE_KM = 100.0
MAX_ABS_POSITION_KM = 10_000_000.0
MAX_ABS_VELOCITY_KMS = 100.0
MAX_INPUT_BYTES = 5_000_000
MIN_TRACK_POINTS = 2
MAX_TRACK_POINTS = 100_000
MIN_VECTORS = 3

SUPPORTED_OBJECT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
EPOCH_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$"
)
PROVENANCE_RE = re.compile(r"^PROVENANCE\s*:\s*(.+)$", re.IGNORECASE)
REQUIRED_META = ("OBJECT_NAME", "OBJECT_ID", "CENTER_NAME", "REF_FRAME", "TIME_SYSTEM")
FORBIDDEN_BLOCKS = ("COVARIANCE_START", "COVARIANCE_STOP", "META_START_2")
CSV_COLUMNS = ("epoch", "x_km", "y_km", "z_km", "vx_kms", "vy_kms", "vz_kms")


@dataclass(frozen=True)
class State:
    epoch: datetime
    t_s: float
    r: Vec3
    v: Vec3


@dataclass(frozen=True)
class Trajectory:
    metadata: dict[str, str]
    provenance: str
    states: tuple[State, ...]


def _is_str(value: object) -> TypeGuard[str]:
    return type(value) is str


def _is_real_number(value: object) -> TypeGuard[int | float]:
    return type(value) in (int, float)


def _is_exact_int(value: object) -> TypeGuard[int]:
    return type(value) is int


def _is_datetime_instance(value: object) -> TypeGuard[datetime]:
    return type(value) is datetime or (
        isinstance(value, datetime) and type(value).__name__ != "date"
    )


def _format_utc_iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def parse_epoch(text: str) -> datetime:
    if not _is_str(text):
        raise ValueError(f"epoch must be UTC ISO-8601 string with Z, got {text!r}")
    match = EPOCH_RE.match(text.strip())
    if not match:
        raise ValueError(f"epoch must be UTC ISO-8601 with Z, got {text!r}")
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = match.group(7) or ""
    micro = int((fraction + "000000")[:6]) if fraction else 0
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"invalid calendar epoch {text!r}: {exc}") from exc


def _finite(value: object, label: str, limit: float) -> float:
    if not _is_real_number(value):
        raise ValueError(f"{label}: numeric value required")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label}: finite value required")
    if abs(numeric) > limit:
        raise ValueError(f"{label}: |{numeric}| exceeds supported bound {limit:g}")
    return numeric


def _state(epoch_text: str, values: Sequence[str], index: int) -> State:
    if len(values) != 6:
        raise ValueError(f"state vector {index}: expected six numbers, got {len(values)}")
    epoch = parse_epoch(epoch_text)
    numbers: list[float] = []
    for i, token in enumerate(values):
        label = f"state {index} component {i + 1}"
        limit = MAX_ABS_POSITION_KM if i < 3 else MAX_ABS_VELOCITY_KMS
        try:
            raw_float = float(token)
        except ValueError as exc:
            raise ValueError(f"{label}: invalid number {token!r}") from exc
        numbers.append(_finite(raw_float, label, limit))
    position: Vec3 = (numbers[0], numbers[1], numbers[2])
    velocity: Vec3 = (numbers[3], numbers[4], numbers[5])
    return State(epoch, 0.0, position, velocity)


def _finalize(
    states: Sequence[State],
    metadata: dict[str, str],
    provenance: str,
) -> Trajectory:
    if len(states) < MIN_VECTORS:
        raise ValueError(f"at least {MIN_VECTORS} state vectors are required, got {len(states)}")
    for previous, current in itertools.pairwise(states):
        if current.epoch == previous.epoch:
            raise ValueError(f"duplicate epoch: {current.epoch.isoformat()}")
        if current.epoch < previous.epoch:
            raise ValueError("state vectors must be in strictly increasing epoch order")
    start = states[0].epoch
    normalized = tuple(
        State(state.epoch, (state.epoch - start).total_seconds(), state.r, state.v)
        for state in states
    )
    span = normalized[-1].t_s
    if span <= 0:
        raise ValueError("trajectory span must be positive")
    return Trajectory(metadata=dict(metadata), provenance=provenance, states=normalized)


def _record_oem_key_value(
    line: str,
    number: int,
    metadata: dict[str, str],
    seen_version: bool,
    has_states: bool,
) -> bool:
    if has_states:
        raise ValueError(f"line {number}: header or metadata after state vectors")
    key, value = (part.strip() for part in line.split("=", 1))
    if not key:
        raise ValueError(f"line {number}: empty keyword")
    if key == "CCSDS_OEM_VERS":
        if seen_version:
            raise ValueError(f"line {number}: duplicate CCSDS_OEM_VERS")
        if value != "2.0":
            raise ValueError(f"line {number}: only CCSDS_OEM_VERS 2.0 is supported, got {value}")
        return True
    if key in metadata:
        raise ValueError(f"line {number}: duplicate metadata key {key}")
    metadata[key] = value
    return seen_version


def _scan_oem_lines(text: str) -> tuple[dict[str, str], list[str], list[State]]:
    metadata: dict[str, str] = {}
    comments: list[str] = []
    states: list[State] = []
    seen_version = False
    in_meta = False
    meta_blocks = 0

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("COMMENT"):
            comments.append(line[7:].strip())
            continue
        if line in FORBIDDEN_BLOCKS:
            raise ValueError(f"line {number}: block {line} is outside the supported subset")
        if line == "META_START":
            if in_meta or meta_blocks:
                raise ValueError(f"line {number}: exactly one META block is supported")
            in_meta = True
            meta_blocks += 1
            continue
        if line == "META_STOP":
            if not in_meta:
                raise ValueError(f"line {number}: META_STOP without META_START")
            in_meta = False
            continue
        if "=" in line:
            seen_version = _record_oem_key_value(
                line, number, metadata, seen_version, bool(states)
            )
            continue
        if in_meta:
            raise ValueError(f"line {number}: metadata lines must use KEY = VALUE")
        tokens = line.split()
        if len(tokens) != 7:
            raise ValueError(f"line {number}: state vector needs seven columns, got {len(tokens)}")
        states.append(_state(tokens[0], tokens[1:], len(states) + 1))

    if not seen_version:
        raise ValueError("missing CCSDS_OEM_VERS = 2.0 header")
    if in_meta or not meta_blocks:
        raise ValueError("a single META_START/META_STOP block is required")
    return metadata, comments, states


def _validate_oem_metadata(metadata: dict[str, str]) -> None:
    for key in REQUIRED_META:
        if key not in metadata:
            raise ValueError(f"missing required metadata: {key}")
    if metadata["CENTER_NAME"].upper() != "EARTH":
        raise ValueError(f"unsupported center: {metadata['CENTER_NAME']} (only EARTH)")
    if metadata["REF_FRAME"].upper() != "EME2000":
        raise ValueError(f"unsupported reference frame: {metadata['REF_FRAME']} (only EME2000)")
    if metadata["TIME_SYSTEM"].upper() != "UTC":
        raise ValueError(f"unsupported time system: {metadata['TIME_SYSTEM']} (only UTC)")
    if not SUPPORTED_OBJECT_ID.match(metadata["OBJECT_ID"]):
        raise ValueError("OBJECT_ID contains unsupported characters")
    interpolation = metadata.get("INTERPOLATION", "HERMITE").upper()
    if interpolation != "HERMITE":
        raise ValueError(f"unsupported INTERPOLATION: {interpolation} (only HERMITE)")


def _extract_provenance(comments: Sequence[str]) -> str:
    for comment in comments:
        match = PROVENANCE_RE.match(comment)
        if match:
            return match.group(1).strip()
    return "unspecified"


def parse_oem(text: str) -> Trajectory:
    if not _is_str(text):
        raise ValueError("OEM input must be a string")
    if len(text) > MAX_INPUT_BYTES:
        raise ValueError("OEM text exceeds 5 MB")
    metadata, comments, states = _scan_oem_lines(text)
    _validate_oem_metadata(metadata)
    provenance = _extract_provenance(comments)
    return _finalize(states, metadata, provenance)


def parse_csv(text: str) -> Trajectory:
    if not _is_str(text):
        raise ValueError("CSV input must be a string")
    if len(text) > MAX_INPUT_BYTES:
        raise ValueError("CSV text exceeds 5 MB")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [cell.strip().lower() for cell in next(reader)]
    except StopIteration as exc:
        raise ValueError("empty CSV") from exc
    if tuple(header) != CSV_COLUMNS:
        raise ValueError("CSV header must be: " + ",".join(CSV_COLUMNS))
    states: list[State] = []
    for index, row in enumerate(reader, 2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != 7:
            raise ValueError(f"CSV row {index}: expected seven columns, got {len(row)}")
        states.append(_state(row[0].strip(), [cell.strip() for cell in row[1:]], len(states) + 1))
    metadata: dict[str, str] = {
        "OBJECT_NAME": "SYNTHETIC_CSV_REPLAY",
        "OBJECT_ID": "SYNTHETIC-CSV",
        "CENTER_NAME": "EARTH",
        "REF_FRAME": "EME2000",
        "TIME_SYSTEM": "UTC",
        "FORMAT": "CSV_REPLAY_V1",
    }
    return _finalize(states, metadata, "synthetic CSV replay")


# ---------------------------------------------------------------- mechanics
def _find_bracketing_span(states: tuple[State, ...], t_s: float) -> tuple[State, State]:
    low, high = 0, len(states) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if states[middle].t_s <= t_s:
            low = middle
        else:
            high = middle
    return states[low], states[high]


def interpolate(trajectory: Trajectory, t_s: float) -> State:
    if not _is_real_number(t_s) or not math.isfinite(float(t_s)):
        raise ValueError(f"interpolation time must be a finite number, got {t_s!r}")
    query_t = float(t_s)
    states = trajectory.states
    if len(states) < 2:
        raise ValueError("trajectory requires at least two states for interpolation")
    if not states[0].t_s <= query_t <= states[-1].t_s:
        raise ValueError(
            f"interpolation time {query_t} is outside [{states[0].t_s}, {states[-1].t_s}]"
        )
    left, right = _find_bracketing_span(states, query_t)
    span = right.t_s - left.t_s
    if span <= 0:
        raise ValueError("duplicate epochs cannot be interpolated")
    s = (query_t - left.t_s) / span
    s2, s3 = s * s, s * s * s
    h00, h10 = 2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s
    h01, h11 = -2 * s3 + 3 * s2, s3 - s2
    lx, ly, lz = left.r
    lvx, lvy, lvz = left.v
    rx, ry, rz = right.r
    rvx, rvy, rvz = right.v
    position: Vec3 = (
        h00 * lx + h10 * span * lvx + h01 * rx + h11 * span * rvx,
        h00 * ly + h10 * span * lvy + h01 * ry + h11 * span * rvy,
        h00 * lz + h10 * span * lvz + h01 * rz + h11 * span * rvz,
    )
    d00, d10 = (6 * s2 - 6 * s) / span, 3 * s2 - 4 * s + 1
    d01, d11 = (-6 * s2 + 6 * s) / span, 3 * s2 - 2 * s
    velocity: Vec3 = (
        d00 * lx + d10 * lvx + d01 * rx + d11 * rvx,
        d00 * ly + d10 * lvy + d01 * ry + d11 * rvy,
        d00 * lz + d10 * lvz + d01 * rz + d11 * rvz,
    )
    epoch = left.epoch + (right.epoch - left.epoch) * s
    return State(epoch, query_t, position, velocity)


# ------------------------------------------------------- lunar ephemeris
# Truncated ELP2000-82B terms (Meeus, Astronomical Algorithms, table 47.A/B).
_LR_TERMS: tuple[tuple[int, int, int, int, int, int], ...] = (
    (0, 0, 1, 0, 6288774, -20905355), (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968), (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888), (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158), (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733), (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620), (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755), (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0), (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782), (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636), (2, 1, -1, 0, -7888, 24208),
    (2, 1, 0, 0, -6766, 30824), (1, 0, -1, 0, -5163, -8379),
    (1, 1, 0, 0, 4987, -16675), (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445), (4, 0, 0, 0, 3861, -11650),
    (2, 0, -3, 0, 3665, 14403), (0, 1, -2, 0, -2689, -7003),
    (2, 0, -1, 2, -2602, 0), (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322), (2, -2, 0, 0, 2236, -9884),
    (0, 1, 2, 0, -2120, 5751), (0, 2, 0, 0, -2069, 0),
    (2, -2, -1, 0, 2048, -4950), (2, 0, 1, -2, -1773, 4130),
    (2, 0, 0, 2, -1595, 0), (4, -1, -1, 0, 1215, -3958),
    (0, 0, 2, 2, -1110, 0), (3, 0, -1, 0, -892, 3258),
    (2, 1, 1, 0, -810, 2616), (4, -1, -2, 0, 759, -1897),
    (0, 2, -1, 0, -713, -2117), (2, 2, -1, 0, -700, 2354),
)
_B_TERMS: tuple[tuple[int, int, int, int, int], ...] = (
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237), (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198), (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463),
    (2, -1, 0, 1, 2211), (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870),
    (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794), (0, 0, 0, 3, -1749),
    (0, 1, -1, 1, -1565), (1, 0, 0, 1, -1491), (0, 1, 1, 1, -1475),
    (0, 1, 1, -1, -1410), (0, 1, 0, -1, -1344), (1, 0, 0, -1, -1335),
    (0, 0, 3, 1, 1107), (4, 0, 0, -1, 1021), (4, 0, -1, 1, 833),
)
_J2000_EPOCH = datetime(2000, 1, 1, 12, tzinfo=UTC)


def julian_date(moment: datetime) -> float:
    if not _is_datetime_instance(moment):
        raise ValueError("epoch must be a datetime instance")
    if moment.tzinfo is None:
        raise ValueError("epoch must be timezone-aware UTC")
    utc_moment = moment.astimezone(UTC)
    delta = utc_moment - _J2000_EPOCH
    return 2451545.0 + delta.total_seconds() / 86400.0


def _deg_to_rad_mod360(value: float) -> float:
    return math.radians(value % 360.0)


def _lunar_ecliptic_spherical(t: float) -> tuple[float, float, float]:
    """Compute J2000 ecliptic longitude (deg), latitude (deg), and range (km)."""
    lp = 218.3164477 + 481267.88123421 * t - 0.0015786 * t**2 + t**3 / 538841 - t**4 / 65194000
    d = 297.8501921 + 445267.1114034 * t - 0.0018819 * t**2 + t**3 / 545868 - t**4 / 113065000
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t**2 + t**3 / 24490000
    mp = 134.9633964 + 477198.8675055 * t + 0.0087414 * t**2 + t**3 / 69699 - t**4 / 14712000
    f = 93.2720950 + 483202.0175233 * t - 0.0036539 * t**2 - t**3 / 3526000 + t**4 / 863310000
    a1 = 119.75 + 131.849 * t
    a2 = 53.09 + 479264.290 * t
    a3 = 313.45 + 481266.484 * t
    e = 1.0 - 0.002516 * t - 0.0000074 * t**2

    sum_l = sum_r = sum_b = 0.0
    for cd, cm, cmp, cf, coef_l, coef_r in _LR_TERMS:
        argument = _deg_to_rad_mod360(cd * d + cm * m + cmp * mp + cf * f)
        factor = e ** abs(cm)
        sum_l += coef_l * factor * math.sin(argument)
        sum_r += coef_r * factor * math.cos(argument)
    for cd, cm, cmp, cf, coef_b in _B_TERMS:
        argument = _deg_to_rad_mod360(cd * d + cm * m + cmp * mp + cf * f)
        sum_b += coef_b * (e ** abs(cm)) * math.sin(argument)

    sum_l += (
        3958.0 * math.sin(_deg_to_rad_mod360(a1))
        + 1962.0 * math.sin(_deg_to_rad_mod360(lp - f))
        + 318.0 * math.sin(_deg_to_rad_mod360(a2))
    )
    sum_b += (
        -2235.0 * math.sin(_deg_to_rad_mod360(lp))
        + 382.0 * math.sin(_deg_to_rad_mod360(a3))
        + 175.0 * math.sin(_deg_to_rad_mod360(a1 - f))
        + 175.0 * math.sin(_deg_to_rad_mod360(a1 + f))
        + 127.0 * math.sin(_deg_to_rad_mod360(lp - mp))
        - 115.0 * math.sin(_deg_to_rad_mod360(lp + mp))
    )

    longitude = (lp + sum_l / 1_000_000.0) % 360.0
    latitude = sum_b / 1_000_000.0
    distance = 385000.56 + sum_r / 1000.0

    # The series is referred to the mean equinox of date. Rotate the longitude
    # back to J2000 before using the fixed J2000 obliquity, otherwise the result
    # drifts by general precession (~0.36 degrees by 2026).
    precession_arcsec = 5029.0966 * t + 1.11113 * t**2 - 0.000006 * t**3
    longitude = (longitude - precession_arcsec / 3600.0) % 360.0
    return longitude, latitude, distance


def moon_position_eme2000(moment: datetime) -> Vec3:
    """Approximate geocentric lunar position in EME2000 (km).

    Truncated ELP2000-82B series: expect kilometres-to-tens-of-kilometres
    differences from JPL DE440. Not for navigation or occultation timing.
    """
    t = (julian_date(moment) - 2451545.0) / 36525.0
    longitude, latitude, distance = _lunar_ecliptic_spherical(t)
    beta = _deg_to_rad_mod360(latitude)
    lam = _deg_to_rad_mod360(longitude)
    x_ecl = distance * math.cos(beta) * math.cos(lam)
    y_ecl = distance * math.cos(beta) * math.sin(lam)
    z_ecl = distance * math.sin(beta)
    obliquity = math.radians(23.439291111)
    return (
        x_ecl,
        y_ecl * math.cos(obliquity) - z_ecl * math.sin(obliquity),
        y_ecl * math.sin(obliquity) + z_ecl * math.cos(obliquity),
    )


def telemetry(state: State) -> dict[str, Any]:
    rx, ry, rz = state.r
    vx, vy, vz = state.v
    r_earth = math.sqrt(rx * rx + ry * ry + rz * rz)
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)
    moon = moon_position_eme2000(state.epoch)
    dx, dy, dz = rx - moon[0], ry - moon[1], rz - moon[2]
    r_moon = math.sqrt(dx * dx + dy * dy + dz * dz)
    if r_earth <= 0:
        raise ValueError("degenerate geometry: zero Earth distance")
    energy = 0.5 * speed * speed - MU_EARTH_KM3_S2 / r_earth
    radial = (rx * vx + ry * vy + rz * vz) / r_earth
    return {
        "earth_distance_km": r_earth,
        "earth_altitude_km": r_earth - R_EARTH_KM,
        "moon_distance_km": r_moon,
        "moon_altitude_km": r_moon - R_MOON_KM,
        "speed_kms": speed,
        "radial_velocity_kms": radial,
        "earth_two_body_energy_km2_s2": energy,
        "moon_position_km": list(moon),
    }


def _validate_track_points(points: object) -> int:
    if not _is_exact_int(points) or not MIN_TRACK_POINTS <= points <= MAX_TRACK_POINTS:
        raise ValueError(f"track needs {MIN_TRACK_POINTS}..{MAX_TRACK_POINTS} integer points")
    return points


def build_track(trajectory: Trajectory, points: int = 600) -> list[dict[str, Any]]:
    count = _validate_track_points(points)
    start, end = trajectory.states[0].t_s, trajectory.states[-1].t_s
    track: list[dict[str, Any]] = []
    for index in range(count):
        t_s = start + (end - start) * index / (count - 1)
        state = interpolate(trajectory, t_s)
        metrics = telemetry(state)
        track.append({
            "t_s": t_s,
            "epoch": _format_utc_iso(state.epoch),
            "position_km": list(state.r),
            "velocity_kms": list(state.v),
            "moon_position_km": metrics.pop("moon_position_km"),
            **metrics,
        })
    return track


def summarize(trajectory: Trajectory) -> dict[str, Any]:
    measured = [telemetry(state) for state in trajectory.states]
    speeds = [float(item["speed_kms"]) for item in measured]
    earth_distances = [float(item["earth_distance_km"]) for item in measured]
    earth_altitudes = [float(item["earth_altitude_km"]) for item in measured]
    moon_altitudes = [float(item["moon_altitude_km"]) for item in measured]
    energies = [float(item["earth_two_body_energy_km2_s2"]) for item in measured]
    min_moon_alt = min(moon_altitudes)
    flyby_state = trajectory.states[moon_altitudes.index(min_moon_alt)]
    return {
        "vector_count": len(trajectory.states),
        "start_epoch": _format_utc_iso(trajectory.states[0].epoch),
        "end_epoch": _format_utc_iso(trajectory.states[-1].epoch),
        "duration_s": trajectory.states[-1].t_s,
        "duration_days": trajectory.states[-1].t_s / 86400.0,
        "tli_speed_kms": speeds[0],
        "max_earth_distance_km": max(earth_distances),
        "min_earth_altitude_km": min(earth_altitudes),
        "min_moon_altitude_km": min_moon_alt,
        "max_speed_kms": max(speeds),
        "min_speed_kms": min(speeds),
        "earth_two_body_energy_min_km2_s2": min(energies),
        "earth_two_body_energy_max_km2_s2": max(energies),
        "earth_two_body_energy_spread_km2_s2": max(energies) - min(energies),
        "closest_lunar_flyby_epoch": _format_utc_iso(flyby_state.epoch),
    }


def _midpoint(a: Vec3, b: Vec3) -> Vec3:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)


def _aabb_lower_distance_sq(curve: CubicCurve) -> float:
    lower_squared = 0.0
    for axis in range(3):
        c0, c1, c2, c3 = curve[0][axis], curve[1][axis], curve[2][axis], curve[3][axis]
        low = min(c0, c1, c2, c3)
        high = max(c0, c1, c2, c3)
        nearest = low if low > 0 else high if high < 0 else 0.0
        lower_squared += nearest * nearest
    return lower_squared


def _norm_sq(point: Vec3) -> float:
    return point[0] * point[0] + point[1] * point[1] + point[2] * point[2]


def _subdivide_bezier(curve: CubicCurve) -> tuple[CubicCurve, CubicCurve]:
    a = _midpoint(curve[0], curve[1])
    b = _midpoint(curve[1], curve[2])
    c = _midpoint(curve[2], curve[3])
    d = _midpoint(a, b)
    e = _midpoint(b, c)
    f = _midpoint(d, e)
    return (curve[0], a, d, f), (f, e, c, curve[3])


def earth_segment_clear(left: State, right: State) -> bool:
    """Conservative cubic-Bezier convex-hull screen across an entire Hermite span.

    AABB lower bounds outside the exclusion sphere certify each subcurve. An
    unresolved near-tangent case fails closed after bounded subdivision.
    """
    dt = right.t_s - left.t_s
    c1: Vec3 = (
        left.r[0] + dt * left.v[0] / 3.0,
        left.r[1] + dt * left.v[1] / 3.0,
        left.r[2] + dt * left.v[2] / 3.0,
    )
    c2: Vec3 = (
        right.r[0] - dt * right.v[0] / 3.0,
        right.r[1] - dt * right.v[1] / 3.0,
        right.r[2] - dt * right.v[2] / 3.0,
    )
    controls: CubicCurve = (left.r, c1, c2, right.r)
    radius_sq = (R_EARTH_KM + MIN_CLEARANCE_KM) ** 2
    stack: list[tuple[CubicCurve, int]] = [(controls, 0)]
    visits = 0
    while stack:
        curve, depth = stack.pop()
        visits += 1
        if _aabb_lower_distance_sq(curve) > radius_sq + 1e-6:
            continue
        if _norm_sq(curve[0]) < radius_sq or _norm_sq(curve[3]) < radius_sq:
            return False
        if depth >= 24 or visits >= 100_000:
            return False
        left_sub, right_sub = _subdivide_bezier(curve)
        stack.extend([(left_sub, depth + 1), (right_sub, depth + 1)])
    return True


def gates(trajectory: Trajectory) -> dict[str, Any]:
    summary = summarize(trajectory)
    failures: list[str] = []
    if summary["min_earth_altitude_km"] < MIN_CLEARANCE_KM:
        failures.append(f"Earth clearance below {MIN_CLEARANCE_KM:g} km")
    elif not all(
        earth_segment_clear(left, right)
        for left, right in itertools.pairwise(trajectory.states)
    ):
        failures.append("Earth clearance not certified between OEM nodes")
    if summary["min_moon_altitude_km"] < MIN_CLEARANCE_KM:
        failures.append(f"lunar clearance below {MIN_CLEARANCE_KM:g} km")
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "earth_clearance_km": MIN_CLEARANCE_KM,
        "lunar_clearance_km": MIN_CLEARANCE_KM,
        "earth_coverage": "Entire cubic Hermite spans using conservative convex-hull bounds; unresolved cases fail",
        "moon_coverage": "OEM nodes only; not continuous lunar collision clearance",
        "meaning": "Kinematic teaching screen only. Lunar checks are sampled, not continuous. Not a collision probability or mission design review.",
    }


def load_text(path: Path) -> str:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 5 MB")
    return path.read_text(encoding="utf-8")


def parse_file(path: Path) -> Trajectory:
    text = load_text(path)
    if path.suffix.lower() == ".csv":
        return parse_csv(text)
    return parse_oem(text)


def to_payload(trajectory: Trajectory, track_points: int = 600) -> dict[str, Any]:
    track = build_track(trajectory, track_points)
    samples: list[dict[str, Any]] = []
    for state in trajectory.states:
        metrics = telemetry(state)
        moon = metrics.pop("moon_position_km")
        samples.append({
            "t_s": state.t_s,
            "epoch": _format_utc_iso(state.epoch),
            "position_km": list(state.r),
            "velocity_kms": list(state.v),
            "moon_position_km": moon,
            **metrics,
        })
    return {
        "schema": "CISLUNAR_OEM_V1",
        "provenance": {
            "kind": trajectory.provenance,
            "notice": "Synthetic teaching profile unless stated otherwise; not an authenticated NASA product.",
        },
        "metadata": trajectory.metadata,
        "summary": summarize(trajectory),
        "gates": gates(trajectory),
        "samples": samples,
        "track": track,
        "interpolation": {
            "method": "cubic Hermite with supplied velocities",
            "track_points": track_points,
            "sample_points": len(samples),
        },
        "constants": {
            "mu_earth_km3_s2": MU_EARTH_KM3_S2,
            "mu_moon_km3_s2": MU_MOON_KM3_S2,
            "radius_earth_km": R_EARTH_KM,
            "radius_moon_km": R_MOON_KM,
        },
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--oem", type=Path)
    source.add_argument("--csv", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--track-points", type=int, default=600)
    return parser


def _is_same_path(source_path: Path, out_path: Path) -> bool:
    if source_path.resolve() == out_path.resolve():
        return True
    try:
        return out_path.exists() and source_path.exists() and out_path.samefile(source_path)
    except OSError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    source_path: Path = arguments.oem or arguments.csv
    if _is_same_path(source_path, arguments.out):
        parser.error("output must differ from the source")
    try:
        trajectory = parse_file(source_path)
        payload = to_payload(trajectory, arguments.track_points)
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except (ValueError, OSError, UnicodeError) as exc:
        logger.error("CLI execution failed for %s: %s", source_path, exc)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = payload["summary"]
    print(json.dumps({
        "gates": payload["gates"]["status"],
        "vectors": summary["vector_count"],
        "duration_days": round(summary["duration_days"], 3),
        "min_moon_altitude_km": round(summary["min_moon_altitude_km"], 1),
        "min_earth_altitude_km": round(summary["min_earth_altitude_km"], 1),
        "max_earth_distance_km": round(summary["max_earth_distance_km"], 1),
    }, indent=2))
    return 0 if payload["gates"]["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
