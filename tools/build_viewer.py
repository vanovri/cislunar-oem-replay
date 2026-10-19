"""Build the standalone cislunar replay viewer.

Embeds the pinned three.js build and the parser-produced trajectory payload so the
HTML opens from disk with no server, CDN or network access.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce  # noqa: E402

TEMPLATE = ROOT / "viewer" / "cislunar_replay.template.html"
THREE = ROOT / "vendor" / "three.min.js"
OEM = ROOT / "fixtures" / "synthetic_free_return.oem"
OUTPUT = ROOT / "cislunar_replay.html"
PAYLOAD_TOKEN = "__TRAJECTORY_PAYLOAD__"
THREE_TOKEN = "<!-- INLINE_THREE -->"
THREE_VERSION = "0.128.0"
THREE_SHA256 = "9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2"
TRACK_POINTS = 600


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render() -> tuple[str, dict]:
    template = TEMPLATE.read_text(encoding="utf-8")
    three_source = THREE.read_text(encoding="utf-8")
    if sha256(THREE) != THREE_SHA256:
        raise SystemExit("vendor/three.min.js does not match the pinned SHA-256")
    if "</script" in three_source.lower():
        raise SystemExit("three.js source contains a script terminator")

    payload = ce.to_payload(ce.parse_file(OEM), track_points=TRACK_POINTS)
    if payload["gates"]["status"] != "pass":
        raise SystemExit("referee gates failed; refusing to build the viewer")
    embedded = json.dumps(payload, ensure_ascii=True, allow_nan=False).replace("</", "<\\/")

    if template.count(PAYLOAD_TOKEN) != 1:
        raise SystemExit(f"template must contain exactly one {PAYLOAD_TOKEN} token")
    if template.count(THREE_TOKEN) != 1:
        raise SystemExit(f"template must contain exactly one {THREE_TOKEN} token")

    html = template.replace(PAYLOAD_TOKEN, embedded)
    html = html.replace(THREE_TOKEN, "<script>\n" + three_source + "\n</script>")
    info = {
        "three_version": THREE_VERSION,
        "three_sha256": THREE_SHA256,
        "track_points": len(payload["track"]),
        "vectors": payload["summary"]["vector_count"],
        "min_moon_altitude_km": payload["summary"]["min_moon_altitude_km"],
        "min_earth_altitude_km": payload["summary"]["min_earth_altitude_km"],
        "gates": payload["gates"]["status"],
        "provenance": payload["provenance"]["kind"],
    }
    return html, info


def build(check: bool = False) -> dict:
    html, info = render()
    encoded = html.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if check:
        if not OUTPUT.exists():
            raise SystemExit(f"{OUTPUT.name} is missing; run python tools/build_viewer.py")
        if OUTPUT.read_bytes() != encoded:
            raise SystemExit(f"{OUTPUT.name} is stale; rebuild it")
    else:
        OUTPUT.write_bytes(encoded)
    return {"output": OUTPUT.name, "bytes": len(encoded), "sha256": digest, "checked": check, **info}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(build(check=arguments.check), indent=2))
