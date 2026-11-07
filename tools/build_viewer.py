"""Build the standalone cislunar replay viewer.

Embeds the pinned three.js build and the parser-produced trajectory payload so the
HTML opens from disk with no server, CDN or network access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cislunar_ephemeris as ce

logger = logging.getLogger(__name__)

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


def canonical(value: Any) -> Any:
    """Round floats so the embedded JSON is byte-stable across platforms.

    libm implementations can differ by one ULP; three decimals is far finer than
    any visual or teaching requirement and removes the platform noise.
    """
    if type(value) is bool:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("canonical payload requires finite floats")
        return round(value, 3)
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def serialize_embedded_json(payload: Mapping[str, Any]) -> str:
    """Serialize payload as ASCII JSON with HTML script-context escaping."""
    raw = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    return raw.replace("</", "<\\/").replace("<!--", "\\u003c!--")


def _validate_vendor_script(three_path: Path, expected_sha256: str) -> str:
    try:
        three_source = three_path.read_text(encoding="utf-8")
        actual_sha256 = sha256(three_path)
    except (OSError, UnicodeError) as exc:
        logger.error("Failed to read vendor script %s: %s", three_path, exc)
        raise SystemExit(f"unable to read vendor script {three_path}: {exc}") from exc
    if actual_sha256 != expected_sha256:
        raise SystemExit("vendor/three.min.js does not match the pinned SHA-256")
    if "</script" in three_source.lower():
        raise SystemExit("three.js source contains a script terminator")
    return three_source


def _assemble_viewer_html(
    template: str,
    payload_token: str,
    embedded_json: str,
    three_token: str,
    three_source: str,
) -> str:
    if template.count(payload_token) != 1:
        raise SystemExit(f"template must contain exactly one {payload_token} token")
    if template.count(three_token) != 1:
        raise SystemExit(f"template must contain exactly one {three_token} token")
    payload_pos = template.index(payload_token)
    three_pos = template.index(three_token)
    three_block = "<script>\n" + three_source + "\n</script>"
    if payload_pos < three_pos:
        return (
            template[:payload_pos]
            + embedded_json
            + template[payload_pos + len(payload_token) : three_pos]
            + three_block
            + template[three_pos + len(three_token) :]
        )
    return (
        template[:three_pos]
        + three_block
        + template[three_pos + len(three_token) : payload_pos]
        + embedded_json
        + template[payload_pos + len(payload_token) :]
    )


def render() -> tuple[str, dict[str, Any]]:
    try:
        template = TEMPLATE.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.error("Failed to read viewer template %s: %s", TEMPLATE, exc)
        raise SystemExit(f"unable to read viewer template {TEMPLATE}: {exc}") from exc

    three_source = _validate_vendor_script(THREE, THREE_SHA256)

    try:
        payload: dict[str, Any] = canonical(
            ce.to_payload(ce.parse_file(OEM), track_points=TRACK_POINTS)
        )
    except (ValueError, OSError, UnicodeError) as exc:
        logger.error("Failed to build trajectory payload from %s: %s", OEM, exc)
        raise SystemExit(f"unable to build trajectory payload: {exc}") from exc

    if payload["gates"]["status"] != "pass":
        raise SystemExit("referee gates failed; refusing to build the viewer")
    embedded = serialize_embedded_json(payload)
    html = _assemble_viewer_html(
        template, PAYLOAD_TOKEN, embedded, THREE_TOKEN, three_source
    )
    info: dict[str, Any] = {
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


def build(check: bool = False) -> dict[str, Any]:
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
    return {
        "output": OUTPUT.name,
        "bytes": len(encoded),
        "sha256": digest,
        "checked": check,
        **info,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(build(check=arguments.check), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
