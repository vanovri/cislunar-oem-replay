"""Verification report for the OEM -> payload -> viewer chain."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce
from tools import build_viewer

logger = logging.getLogger(__name__)

HTML = ROOT / "cislunar_replay.html"
REPORT = ROOT / "reports" / "verification.json"

_PAYLOAD_SCRIPT_RE = re.compile(
    r'<script id="trajectory-data" type="application/json">(.*?)</script>',
    re.DOTALL,
)
_EXTERNAL_ASSET_RE = re.compile(r'(src|href)\s*=\s*["\']https?:', re.IGNORECASE)
_NETWORK_FETCH_RE = re.compile(r"\bfetch\s*\(")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def embedded_payload(html: str) -> dict[str, Any]:
    match = _PAYLOAD_SCRIPT_RE.search(html)
    if not match:
        raise SystemExit("embedded payload block not found")
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        logger.error("Embedded trajectory payload is invalid JSON: %s", exc)
        raise SystemExit(f"embedded payload is invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("embedded payload root must be a JSON object")
    return parsed


def _evaluate_fixture_agreement(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    failures: list[str] = []
    oem_payload: dict[str, Any] = build_viewer.canonical(
        ce.to_payload(ce.parse_file(root / "fixtures" / "synthetic_free_return.oem"), 240)
    )
    csv_payload: dict[str, Any] = build_viewer.canonical(
        ce.to_payload(ce.parse_file(root / "fixtures" / "synthetic_free_return.csv"), 240)
    )
    if oem_payload["samples"] != csv_payload["samples"]:
        failures.append("OEM and CSV replay disagree")
    if oem_payload["gates"]["status"] != "pass":
        failures.append("referee gates failed")
    return oem_payload, csv_payload, failures


def _evaluate_viewer_artifact(
    root: Path,
    html_path: Path,
    oem_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool, bool, list[str]]:
    failures: list[str] = []
    html = html_path.read_text(encoding="utf-8")
    template = (root / "viewer" / "cislunar_replay.template.html").read_text(encoding="utf-8")
    payload = embedded_payload(html)
    if payload["samples"] != oem_payload["samples"]:
        failures.append("embedded payload differs from the parser output")
    has_external_assets = bool(_EXTERNAL_ASSET_RE.search(html))
    if has_external_assets:
        failures.append("viewer references an external http(s) asset")
    has_network_fetch = bool(_NETWORK_FETCH_RE.search(template))
    if has_network_fetch:
        failures.append("viewer application performs a network fetch")
    build_info = build_viewer.build(check=True)
    if build_info["sha256"] != sha256(html_path):
        failures.append("committed viewer is not reproducible")
    return payload, build_info, has_external_assets, has_network_fetch, failures


def _compose_verification_report(
    *,
    failures: list[str],
    oem_payload: Mapping[str, Any],
    csv_payload: Mapping[str, Any],
    payload: Mapping[str, Any],
    build_info: Mapping[str, Any],
    html_path: Path,
    has_external_assets: bool,
    has_network_fetch: bool,
) -> dict[str, Any]:
    track = payload["track"]
    summary = payload["summary"]
    samples_match_oem = payload["samples"] == oem_payload["samples"]
    return {
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
            "bytes": html_path.stat().st_size,
            "sha256": build_info["sha256"],
            "three_version": build_info["three_version"],
            "three_sha256": build_info["three_sha256"],
            "embedded_payload_matches": samples_match_oem,
            "external_assets": has_external_assets,
            "network_fetch": has_network_fetch,
        },
        "claims": {
            "authenticated_nasa_data": False,
            "propagated_trajectory": False,
            "time_dependent_energy_conservation": False,
            "note": "Synthetic reference geometry with spline-derived velocities. Clearance and energy values are diagnostics, not mission analysis.",
        },
    }


def main() -> int:
    oem_payload, csv_payload, fixture_failures = _evaluate_fixture_agreement(ROOT)
    (
        payload,
        build_info,
        has_external_assets,
        has_network_fetch,
        viewer_failures,
    ) = _evaluate_viewer_artifact(ROOT, HTML, oem_payload)
    failures = [*fixture_failures, *viewer_failures]
    report = _compose_verification_report(
        failures=failures,
        oem_payload=oem_payload,
        csv_payload=csv_payload,
        payload=payload,
        build_info=build_info,
        html_path=HTML,
        has_external_assets=has_external_assets,
        has_network_fetch=has_network_fetch,
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "failures": failures}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
