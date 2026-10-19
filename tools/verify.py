"""Verification report for the OEM -> payload -> viewer chain."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce  # noqa: E402
from tools import build_viewer  # noqa: E402

HTML = ROOT / "cislunar_replay.html"
REPORT = ROOT / "reports" / "verification.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def embedded_payload(html: str) -> dict:
    match = re.search(r'<script id="trajectory-data" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        raise SystemExit("embedded payload block not found")
    return json.loads(match.group(1))


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp = Path(temp_dir)
        oem_payload = ce.to_payload(ce.parse_file(ROOT / "fixtures" / "synthetic_free_return.oem"), 240)
        csv_payload = ce.to_payload(ce.parse_file(ROOT / "fixtures" / "synthetic_free_return.csv"), 240)
    if oem_payload["samples"] != csv_payload["samples"]:
        failures.append("OEM and CSV replay disagree")
    if oem_payload["gates"]["status"] != "pass":
        failures.append("referee gates failed")

    html = HTML.read_text(encoding="utf-8")
    template = (ROOT / "viewer" / "cislunar_replay.template.html").read_text(encoding="utf-8")
    payload = embedded_payload(html)
    if payload["samples"] != oem_payload["samples"]:
        failures.append("embedded payload differs from the parser output")
    if re.search(r'(src|href)\s*=\s*["\']https?:', html, re.I):
        failures.append("viewer references an external http(s) asset")
    if re.search(r"\bfetch\s*\(", template):
        failures.append("viewer application performs a network fetch")

    build_info = build_viewer.build(check=True)
    if build_info["sha256"] != sha256(HTML):
        failures.append("committed viewer is not reproducible")

    track = payload["track"]
    summary = payload["summary"]
    report = {
        "ok": not failures,
        "failures": failures,
        "inputs": {
            "oem": "fixtures/synthetic_free_return.oem",
            "csv": "fixtures/synthetic_free_return.csv",
            "vectors": summary["vector_count"],
        },
        "oem_csv_identical": oem_payload["samples"] == csv_payload["samples"],
        "provenance": payload["provenance"],
        "gates": payload["gates"],
        "summary": {
            "duration_days": summary["duration_days"],
            "min_earth_altitude_km": summary["min_earth_altitude_km"],
            "min_moon_altitude_km": summary["min_moon_altitude_km"],
            "max_earth_distance_km": summary["max_earth_distance_km"],
            "max_speed_kms": summary["max_speed_kms"],
            "earth_two_body_energy_spread_km2_s2": summary["earth_two_body_energy_spread_km2_s2"],
        },
        "track": {
            "points": len(track),
            "min_earth_altitude_km": min(point["earth_altitude_km"] for point in track),
            "min_moon_altitude_km": min(point["moon_altitude_km"] for point in track),
            "max_speed_kms": max(point["speed_kms"] for point in track),
        },
        "viewer": {
            "file": "cislunar_replay.html",
            "bytes": HTML.stat().st_size,
            "sha256": build_info["sha256"],
            "three_version": build_info["three_version"],
            "three_sha256": build_info["three_sha256"],
            "embedded_payload_matches": payload["samples"] == oem_payload["samples"],
            "external_assets": False,
            "network_fetch": False,
        },
        "claims": {
            "authenticated_nasa_data": False,
            "propagated_trajectory": False,
            "time_dependent_energy_conservation": False,
            "note": "Synthetic reference geometry with spline-derived velocities. Clearance and energy values are diagnostics, not mission analysis.",
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "failures": failures}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
