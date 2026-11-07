"""Generate the synthetic cislunar teaching profile (OEM + CSV).

The path is a designed figure-eight reference geometry, not a propagated
free-return solution. Every waypoint is placed relative to the same-frame
analytic lunar ephemeris used by the parser, so lunar-range telemetry is
consistent. Node velocities are cubic-spline derivatives, which makes the
parser's Hermite reconstruction exact at the nodes and smooth between them.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce

logger = logging.getLogger(__name__)

Vec3 = tuple[float, float, float]

EPOCH0 = datetime(2026, 4, 2, tzinfo=UTC)
MINUTES = 1.0 / 1440.0
HOURS = 1.0 / 24.0
R_EARTH = ce.R_EARTH_KM

# (days, radius km, lateral offset km, radial offset km, moon_relative)
PROFILE: list[tuple[float, float, float, float, bool]] = [
    (0.0, 6578.0, 0.0, 0.0, False),
    (5 * MINUTES, 9800.0, 0.0, 0.0, False),
    (20 * MINUTES, 17000.0, 0.0, 0.0, False),
    (1.5 * HOURS, 32000.0, 0.0, 0.0, False),
    (6 * HOURS, 60000.0, 0.0, 0.0, False),
    (0.5, 130000.0, 0.0, 0.0, False),
    (1.0, 225000.0, 0.0, 0.0, False),
    (1.5, 290000.0, 0.0, 0.0, False),
    (2.0, 330000.0, 0.0, 0.0, False),
    (2.5, 350000.0, 0.0, 0.0, False),
    (3.0, 360000.0, 0.0, 0.0, False),
    (3.5, 365000.0, 0.0, 0.0, False),
    (4.0, 368000.0, 0.0, 0.0, False),
    (4.2, 369000.0, 0.0, 0.0, False),
    (4.3, 0.0, 15000.0, 12000.0, True),
    (4.4, 0.0, 7000.0, 11000.0, True),
    (4.5, 0.0, 0.0, 10000.0, True),
    (4.55, 0.0, 0.0, 9000.0, True),
    (4.6, 0.0, 0.0, 10000.0, True),
    (4.7, 0.0, -7000.0, 11000.0, True),
    (4.8, 0.0, -15000.0, 12000.0, True),
    (4.9, 369000.0, 0.0, 0.0, False),
    (5.2, 360000.0, 0.0, 0.0, False),
    (5.8, 330000.0, 0.0, 0.0, False),
    (6.5, 270000.0, 0.0, 0.0, False),
    (7.3, 190000.0, 0.0, 0.0, False),
    (8.0, 110000.0, 0.0, 0.0, False),
    (8.6, 52000.0, 0.0, 0.0, False),
    (9.0, 14000.0, 0.0, 0.0, False),
    (9.2, 6578.0, 0.0, 0.0, False),
]


def basis(moment: datetime) -> tuple[Vec3, Vec3]:
    mx, my, mz = ce.moon_position_eme2000(moment)
    norm = math.sqrt(mx * mx + my * my + mz * mz)
    u: Vec3 = (mx / norm, my / norm, mz / norm)
    cross: Vec3 = (u[1], -u[0], 0.0)
    length = math.sqrt(cross[0] * cross[0] + cross[1] * cross[1] + cross[2] * cross[2])
    if length < 1e-9:
        raise ValueError("degenerate lunar geometry")
    v: Vec3 = (cross[0] / length, cross[1] / length, cross[2] / length)
    return u, v


def waypoints() -> tuple[list[float], list[Vec3]]:
    times: list[float] = []
    points: list[Vec3] = []
    for days, radius, lateral, radial, moon_relative in PROFILE:
        moment = EPOCH0 + timedelta(days=days)
        moon = ce.moon_position_eme2000(moment)
        u, v = basis(moment)
        if moon_relative:
            position: Vec3 = (
                moon[0] + radial * u[0] + lateral * v[0],
                moon[1] + radial * u[1] + lateral * v[1],
                moon[2] + radial * u[2] + lateral * v[2],
            )
        else:
            position = (
                radius * u[0] + lateral * v[0],
                radius * u[1] + lateral * v[1],
                radius * u[2] + lateral * v[2],
            )
        times.append(days * 86400.0)
        points.append(position)
    return times, points


def derivatives(times: Sequence[float], points: Sequence[Vec3]) -> list[Vec3]:
    velocities: list[Vec3] = []
    count = len(points)
    for index in range(count):
        if index == 0:
            left_idx, right_idx = 0, 1
        elif index == count - 1:
            left_idx, right_idx = count - 2, count - 1
        else:
            left_idx, right_idx = index - 1, index + 1
        span = times[right_idx] - times[left_idx]
        p_left, p_right = points[left_idx], points[right_idx]
        velocities.append((
            (p_right[0] - p_left[0]) / span,
            (p_right[1] - p_left[1]) / span,
            (p_right[2] - p_left[2]) / span,
        ))
    return velocities


def hermite(
    times: Sequence[float],
    points: Sequence[Vec3],
    velocities: Sequence[Vec3],
    t_s: float,
) -> tuple[Vec3, Vec3]:
    index = max(
        0,
        min(len(times) - 2, next(i for i in range(len(times) - 1) if times[i + 1] >= t_s)),
    )
    span = times[index + 1] - times[index]
    s = (t_s - times[index]) / span
    s2, s3 = s * s, s * s * s
    h00, h10 = 2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s
    h01, h11 = -2 * s3 + 3 * s2, s3 - s2
    p0, p1 = points[index], points[index + 1]
    v0, v1 = velocities[index], velocities[index + 1]
    position: Vec3 = (
        h00 * p0[0] + h10 * span * v0[0] + h01 * p1[0] + h11 * span * v1[0],
        h00 * p0[1] + h10 * span * v0[1] + h01 * p1[1] + h11 * span * v1[1],
        h00 * p0[2] + h10 * span * v0[2] + h01 * p1[2] + h11 * span * v1[2],
    )
    d00, d10 = (6 * s2 - 6 * s) / span, 3 * s2 - 4 * s + 1
    d01, d11 = (-6 * s2 + 6 * s) / span, 3 * s2 - 2 * s
    velocity: Vec3 = (
        d00 * p0[0] + d10 * v0[0] + d01 * p1[0] + d11 * v1[0],
        d00 * p0[1] + d10 * v0[1] + d01 * p1[1] + d11 * v1[1],
        d00 * p0[2] + d10 * v0[2] + d01 * p1[2] + d11 * v1[2],
    )
    return position, velocity


def _format_oem_text(
    times: Sequence[float],
    points: Sequence[Vec3],
    velocities: Sequence[Vec3],
) -> str:
    oem_lines = [
        "CCSDS_OEM_VERS = 2.0",
        "COMMENT Synthetic teaching profile. Not an authenticated NASA product.",
        "COMMENT PROVENANCE: SYNTHETIC_TEACHING_PROFILE_V1",
        "CREATION_DATE = 2026-10-19T00:00:00.000Z",
        "ORIGINATOR = LOCAL_TEACHING_TOOL",
        "",
        "META_START",
        "OBJECT_NAME = CISLUNAR_TEACHING_PROBE",
        "OBJECT_ID = SYN-2026-001",
        "CENTER_NAME = EARTH",
        "REF_FRAME = EME2000",
        "TIME_SYSTEM = UTC",
        "INTERPOLATION = HERMITE",
        "META_STOP",
        "",
    ]
    for t_s, position, velocity in zip(times, points, velocities, strict=True):
        epoch = (EPOCH0 + timedelta(seconds=t_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        oem_lines.append(
            f"{epoch}  {position[0]:.6f}  {position[1]:.6f}  {position[2]:.6f}  "
            f"{velocity[0]:.6f}  {velocity[1]:.6f}  {velocity[2]:.6f}"
        )
    return "\n".join(oem_lines) + "\n"


def _format_csv_text(
    times: Sequence[float],
    points: Sequence[Vec3],
    velocities: Sequence[Vec3],
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["epoch", "x_km", "y_km", "z_km", "vx_kms", "vy_kms", "vz_kms"])
    for t_s, position, velocity in zip(times, points, velocities, strict=True):
        epoch = (EPOCH0 + timedelta(seconds=t_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        writer.writerow([
            epoch,
            *[f"{value:.6f}" for value in position],
            *[f"{value:.6f}" for value in velocity],
        ])
    return buffer.getvalue()


def _max_node_reconstruction_error(trajectory: ce.Trajectory) -> float:
    max_node_error = 0.0
    for state in trajectory.states:
        rebuilt = ce.interpolate(trajectory, state.t_s)
        max_node_error = max(
            max_node_error,
            max(abs(rebuilt.r[i] - state.r[i]) for i in range(3)),
        )
    return max_node_error


def build() -> dict[str, Any]:
    times, points = waypoints()
    velocities = derivatives(times, points)
    oem_text = _format_oem_text(times, points, velocities)
    csv_text = _format_csv_text(times, points, velocities)

    fixtures_dir = ROOT / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    (fixtures_dir / "synthetic_free_return.oem").write_text(
        oem_text, encoding="utf-8", newline="\n"
    )
    (fixtures_dir / "synthetic_free_return.csv").write_text(
        csv_text, encoding="utf-8", newline="\n"
    )

    trajectory = ce.parse_oem(oem_text)
    max_node_error = _max_node_reconstruction_error(trajectory)
    mid = (trajectory.states[0].t_s + trajectory.states[-1].t_s) / 2.0
    mid_state = ce.interpolate(trajectory, mid)
    summary = ce.summarize(trajectory)
    return {
        "oem": "fixtures/synthetic_free_return.oem",
        "csv": "fixtures/synthetic_free_return.csv",
        "vectors": len(trajectory.states),
        "duration_days": summary["duration_days"],
        "min_moon_altitude_km": summary["min_moon_altitude_km"],
        "min_earth_altitude_km": summary["min_earth_altitude_km"],
        "max_earth_distance_km": summary["max_earth_distance_km"],
        "max_speed_kms": summary["max_speed_kms"],
        "min_speed_kms": summary["min_speed_kms"],
        "midpoint_speed_kms": math.sqrt(sum(c * c for c in mid_state.v)),
        "node_reconstruction_max_km": max_node_error,
        "provenance": trajectory.provenance,
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
