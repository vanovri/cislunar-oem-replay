"""Spot-check the analytic lunar ephemeris against three JPL Horizons vectors.

Reference: Horizons API, target 301 (Moon), center 500@399 (geocentre),
REF_PLANE=FRAME (ICRF), REF_SYSTEM=ICRF, VEC_TABLE=1, OUT_UNITS=KM-S.
The comparison mixes UTC input with TDB-labelled reference epochs, so a few
tens of kilometres of the residual is a time-scale artefact, not a frame error.
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce

logger = logging.getLogger(__name__)

REFERENCE: list[tuple[str, tuple[float, float, float]]] = [
    ("2026-04-02T00:00:00Z", (-385833.8066617213, -60038.52669098664, -46890.7172753297)),
    ("2026-04-06T06:00:00Z", (-183774.3982713328, -314326.6431395597, -175751.8418071775)),
    ("2026-04-10T00:00:00Z", (120306.6445915195, -338769.8714550657, -177114.4085490945)),
]
LIMIT_KM = 100.0


def evaluate_checks() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for label, reference in REFERENCE:
        moment = datetime.fromisoformat(label).astimezone(UTC)
        computed = ce.moon_position_eme2000(moment)
        error = math.dist(computed, reference)
        rows.append({
            "epoch_utc": label,
            "computed_km": [round(value, 3) for value in computed],
            "horizons_icrf_km": list(reference),
            "error_km": round(error, 2),
            "under_limit": error < LIMIT_KM,
        })
    return {
        "source": "JPL Horizons API, target 301, centre 500@399, ICRF, KM-S",
        "method": "truncated ELP2000-82B series (Meeus) with precession to J2000",
        "limit_km": LIMIT_KM,
        "passed": all(bool(row["under_limit"]) for row in rows),
        "max_error_km": max(float(row["error_km"]) for row in rows),
        "checks": rows,
        "notes": "UTC input vs TDB reference epochs contributes up to ~70 km; not a navigation-grade comparison.",
    }


def main() -> int:
    report = evaluate_checks()
    out = ROOT / "reports" / "moon-ephemeris-check.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        logger.error("Failed to write lunar ephemeris check report to %s: %s", out, exc)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": report["passed"], "max_error_km": report["max_error_km"]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
