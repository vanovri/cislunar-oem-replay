"""Create a deterministic release ZIP and checksum manifest, then verify both."""
from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
ZIP_PATH = ROOT / "dist" / f"cislunar-oem-replay-v{VERSION}.zip"
SUMS_PATH = ROOT / "dist" / f"SHA256SUMS-v{VERSION}.txt"
FIXED_TIME: tuple[int, int, int, int, int, int] = (2026, 10, 19, 0, 0, 0)

FILES: list[str] = [
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "README.md",
    "cislunar_ephemeris.py",
    "cislunar_replay.html",
    "data/trajectory.json",
    "docs/browser-qa.json",
    "docs/preview-chase.png",
    "docs/preview-flyby.png",
    "docs/preview-moon.png",
    "docs/preview.png",
    "fixtures/synthetic_free_return.csv",
    "fixtures/synthetic_free_return.oem",
    "package-lock.json",
    "package.json",
    "reports/moon-ephemeris-check.json",
    "reports/verification.json",
    "tests/test_cislunar_ephemeris.py",
    "tests/test_continuous_earth.py",
    ".github/workflows/verify.yml",
    "tools/browser_qa.mjs",
    "tools/build_viewer.py",
    "tools/check_ephemeris.py",
    "tools/generate_fixture.py",
    "tools/package_release.py",
    "tools/verify.py",
    "vendor/three.min.js",
    "viewer/cislunar_replay.template.html",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_deterministic_archive(
    root: Path,
    zip_path: Path,
    files: Sequence[str],
) -> dict[str, str]:
    for relative in files:
        if not (root / relative).is_file():
            logger.error("Missing required release file: %s", relative)
            raise SystemExit(f"missing release file: {relative}")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in sorted(files):
            payload = (root / relative).read_bytes()
            checksums[relative] = sha256_bytes(payload)
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)
    return checksums


def _verify_archive(
    zip_path: Path,
    files: Sequence[str],
    checksums: Mapping[str, str],
) -> list[str]:
    mismatches: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if sorted(names) != sorted(files):
            mismatches.append("archive entry list differs from the manifest")
        if archive.testzip() is not None:
            mismatches.append("archive failed its CRC test")
        for name in names:
            if sha256_bytes(archive.read(name)) != checksums.get(name):
                mismatches.append(f"archive entry differs: {name}")
    return mismatches


def build() -> dict[str, Any]:
    checksums = _write_deterministic_archive(ROOT, ZIP_PATH, FILES)
    SUMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMS_PATH.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
        encoding="utf-8",
        newline="\n",
    )
    mismatches = _verify_archive(ZIP_PATH, FILES, checksums)
    return {
        "version": VERSION,
        "zip": str(ZIP_PATH.relative_to(ROOT)),
        "zip_bytes": ZIP_PATH.stat().st_size,
        "zip_sha256": sha256_bytes(ZIP_PATH.read_bytes()),
        "sums": str(SUMS_PATH.relative_to(ROOT)),
        "sums_sha256": sha256_bytes(SUMS_PATH.read_bytes()),
        "files": len(FILES),
        "verified": not mismatches,
        "mismatches": mismatches,
    }


def main() -> int:
    result = build()
    print(json.dumps(result, indent=2))
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
