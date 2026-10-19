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
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce  # noqa: E402

EPOCH0 = datetime(2026, 4, 2, tzinfo=timezone.utc)
MINUTES = 1.0 / 1440.0
HOURS = 1.0 / 24.0
R_EARTH = ce.R_EARTH_KM

# (days, radius km, lateral offset km, radial offset km)
PROFILE = [
    # (days, radius_km, lateral_km, radial_km, moon_relative)
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


def basis(moment: datetime) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    moon = ce.moon_position_eme2000(moment)
    norm = math.sqrt(sum(component * component for component in moon))
    u = tuple(component / norm for component in moon)
    z = (0.0, 0.0, 1.0)
    cross = (u[1] * z[2] - u[2] * z[1], u[2] * z[0] - u[0] * z[2], u[0] * z[1] - u[1] * z[0])
    length = math.sqrt(sum(component * component for component in cross))
    if length < 1e-9:
        raise ValueError("degenerate lunar geometry")
    v = tuple(component / length for component in cross)
    return u, v


def waypoints() -> tuple[list[float], list[tuple[float, float, float]]]:
    times, points = [], []
    for days, radius, lateral, radial, moon_relative in PROFILE:
        moment = EPOCH0 + timedelta(days=days)
        moon = ce.moon_position_eme2000(moment)
        u, v = basis(moment)
        if moon_relative:
            position = tuple(moon[i] + radial * u[i] + lateral * v[i] for i in range(3))
        else:
            position = tuple(radius * u[i] + lateral * v[i] for i in range(3))
        times.append(days * 86400.0)
        points.append(position)
    return times, points


def derivatives(times: list[float], points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    velocities = []
    count = len(points)
    for index in range(count):
        if index == 0:
            span = times[1] - times[0]
            velocity = tuple((points[1][i] - points[0][i]) / span for i in range(3))
        elif index == count - 1:
            span = times[-1] - times[-2]
            velocity = tuple((points[-1][i] - points[-2][i]) / span for i in range(3))
        else:
            span = times[index + 1] - times[index - 1]
            velocity = tuple((points[index + 1][i] - points[index - 1][i]) / span for i in range(3))
        velocities.append(velocity)
    return velocities


def hermite(times, points, velocities, t_s: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    index = max(0, min(len(times) - 2, next(i for i in range(len(times) - 1) if times[i + 1] >= t_s)))
    span = times[index + 1] - times[index]
    s = (t_s - times[index]) / span
    s2, s3 = s * s, s * s * s
    h00, h10 = 2 * s3 - 3 * s2 + 1, s3 - 2 * s2 + s
    h01, h11 = -2 * s3 + 3 * s2, s3 - s2
    position = tuple(
        h00 * points[index][i] + h10 * span * velocities[index][i]
        + h01 * points[index + 1][i] + h11 * span * velocities[index + 1][i]
        for i in range(3)
    )
    d00, d10 = (6 * s2 - 6 * s) / span, 3 * s2 - 4 * s + 1
    d01, d11 = (-6 * s2 + 6 * s) / span, 3 * s2 - 2 * s
    velocity = tuple(
        d00 * points[index][i] + d10 * velocities[index][i]
        + d01 * points[index + 1][i] + d11 * velocities[index + 1][i]
        for i in range(3)
    )
    return position, velocity


def build() -> dict:
    times, points = waypoints()
    velocities = derivatives(times, points)

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
    for t_s, position, velocity in zip(times, points, velocities):
        epoch = (EPOCH0 + timedelta(seconds=t_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        oem_lines.append(
            f"{epoch}  {position[0]:.6f}  {position[1]:.6f}  {position[2]:.6f}  "
            f"{velocity[0]:.6f}  {velocity[1]:.6f}  {velocity[2]:.6f}"
        )

    oem_text = "\n".join(oem_lines) + "\n"
    (ROOT / "fixtures").mkdir(exist_ok=True)
    (ROOT / "fixtures" / "synthetic_free_return.oem").write_text(oem_text, encoding="utf-8", newline="\n")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["epoch", "x_km", "y_km", "z_km", "vx_kms", "vy_kms", "vz_kms"])
    for t_s, position, velocity in zip(times, points, velocities):
        epoch = (EPOCH0 + timedelta(seconds=t_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        writer.writerow([epoch, *[f"{value:.6f}" for value in position], *[f"{value:.6f}" for value in velocity]])
    (ROOT / "fixtures" / "synthetic_free_return.csv").write_text(buffer.getvalue(), encoding="utf-8", newline="\n")

    # Self-check: reconstruct every node and a mid-point before writing the report.
    trajectory = ce.parse_oem(oem_text)
    max_node_error = 0.0
    for state in trajectory.states:
        rebuilt = ce.interpolate(trajectory, state.t_s)
        max_node_error = max(max_node_error, max(abs(rebuilt.r[i] - state.r[i]) for i in range(3)))
    mid = (trajectory.states[0].t_s + trajectory.states[-1].t_s) / 2
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
