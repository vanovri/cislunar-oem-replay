"""Unit and pipeline tests for tools/build_viewer.py, tools/verify.py, and support tools."""
from __future__ import annotations

import hashlib
import json
import math
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import cislunar_ephemeris as ce
from tools import build_viewer as builder
from tools import check_ephemeris, generate_fixture, package_release, verify

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------- tools/build_viewer
def test_canonical_rounds_floats_and_preserves_other_types_without_mutation() -> None:
    source = {
        "track": [{"r": 1.234567, "count": 2, "ok": True}],
        "coords": (9.87654, -0.0004),
        "text": "EME2000",
    }
    result = builder.canonical(source)
    assert result == {
        "track": [{"r": 1.235, "count": 2, "ok": True}],
        "coords": [9.877, -0.0],
        "text": "EME2000",
    }
    assert source["track"][0]["r"] == 1.234567  # type: ignore[index]
    assert type(result["track"][0]["count"]) is int
    assert type(result["track"][0]["ok"]) is bool


@pytest.mark.parametrize("bad_float", [math.nan, math.inf, -math.inf])
def test_canonical_rejects_non_finite_floats(bad_float: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        builder.canonical({"bad": bad_float})


def test_sha256_matches_standard_digest(tmp_path: Path) -> None:
    sample = tmp_path / "blob.bin"
    data = b"cislunar-viewer-" * 100_000
    sample.write_bytes(data)
    assert builder.sha256(sample) == hashlib.sha256(data).hexdigest()


def test_oem_render_round_trip_escapes_script_terminators_and_html_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (ROOT / "fixtures" / "synthetic_free_return.oem").read_text(encoding="utf-8")
    source = source.replace(
        "OBJECT_NAME = CISLUNAR_TEACHING_PROBE",
        "OBJECT_NAME = </script><!-- INLINE_THREE -->PROBE",
    )
    oem = tmp_path / "escaped.oem"
    oem.write_text(source, encoding="utf-8")
    monkeypatch.setattr(builder, "OEM", oem)

    html, info = builder.render()
    payload = verify.embedded_payload(html)
    expected = builder.canonical(ce.to_payload(ce.parse_file(oem), builder.TRACK_POINTS))

    assert payload == expected
    assert payload["metadata"]["OBJECT_NAME"] == "</script><!-- INLINE_THREE -->PROBE"
    assert "<\\/script>" in html
    assert "\\u003c!--" in html
    assert info["gates"] == "pass"
    assert len(payload["track"]) == builder.TRACK_POINTS


def test_renderer_refuses_tampered_vendor_and_script_terminator_in_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tampered = tmp_path / "three_tampered.js"
    tampered.write_text("window.THREE = {};", encoding="utf-8")
    monkeypatch.setattr(builder, "THREE", tampered)
    with pytest.raises(SystemExit, match="pinned SHA-256"):
        builder.render()

    evil_vendor = tmp_path / "three_evil.js"
    evil_source = "window.THREE = '</script><script>alert(1)';"
    evil_vendor.write_text(evil_source, encoding="utf-8")
    evil_digest = hashlib.sha256(evil_vendor.read_bytes()).hexdigest()
    monkeypatch.setattr(builder, "THREE", evil_vendor)
    monkeypatch.setattr(builder, "THREE_SHA256", evil_digest)
    with pytest.raises(SystemExit, match="script terminator"):
        builder.render()


def test_renderer_refuses_failed_gates_and_invalid_template_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ambiguous PAYLOAD_TOKEN
    bad_payload_tpl = tmp_path / "bad_payload.html"
    bad_payload_tpl.write_text(builder.PAYLOAD_TOKEN * 2 + builder.THREE_TOKEN, encoding="utf-8")
    monkeypatch.setattr(builder, "TEMPLATE", bad_payload_tpl)
    with pytest.raises(SystemExit, match="exactly one"):
        builder.render()

    # Missing THREE_TOKEN
    bad_three_tpl = tmp_path / "bad_three.html"
    bad_three_tpl.write_text(builder.PAYLOAD_TOKEN, encoding="utf-8")
    monkeypatch.setattr(builder, "TEMPLATE", bad_three_tpl)
    with pytest.raises(SystemExit, match="exactly one"):
        builder.render()

    # Failed referee gates
    monkeypatch.setattr(builder, "TEMPLATE", ROOT / "viewer" / "cislunar_replay.template.html")
    impact_oem = tmp_path / "impact.oem"
    impact_oem.write_text(
        "CCSDS_OEM_VERS = 2.0\n"
        "META_START\n"
        "OBJECT_NAME = IMPACT\n"
        "OBJECT_ID = IMP-1\n"
        "CENTER_NAME = EARTH\n"
        "REF_FRAME = EME2000\n"
        "TIME_SYSTEM = UTC\n"
        "META_STOP\n"
        "2026-04-02T00:00:00Z 6300 0 0 0 1 0\n"
        "2026-04-02T01:00:00Z 6300 100 0 0 1 0\n"
        "2026-04-02T02:00:00Z 6300 200 0 0 1 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(builder, "OEM", impact_oem)
    with pytest.raises(SystemExit, match="referee gates failed"):
        builder.render()


def test_build_writes_and_checks_reproducibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_html = tmp_path / "viewer_out.html"
    monkeypatch.setattr(builder, "OUTPUT", out_html)

    # Missing file fails --check
    with pytest.raises(SystemExit, match="is missing"):
        builder.build(check=True)

    # Normal build writes file and check passes
    built = builder.build(check=False)
    assert out_html.is_file()
    assert built["bytes"] == out_html.stat().st_size

    checked = builder.build(check=True)
    assert checked["checked"] is True
    assert checked["sha256"] == built["sha256"]

    # Stale output fails --check
    out_html.write_text("<html>stale</html>", encoding="utf-8")
    with pytest.raises(SystemExit, match="is stale"):
        builder.build(check=True)


def test_builder_cli_main_and_missing_file_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert builder.main(["--check"]) == 0

    # Reverse token order in template is also supported by _assemble_viewer_html
    reversed_html = builder._assemble_viewer_html(
        f"{builder.THREE_TOKEN}\n{builder.PAYLOAD_TOKEN}",
        builder.PAYLOAD_TOKEN,
        '{"ok":true}',
        builder.THREE_TOKEN,
        "window.THREE={};",
    )
    assert "<script>\nwindow.THREE={};\n</script>\n{\"ok\":true}" == reversed_html

    # Missing vendor file raises SystemExit
    monkeypatch.setattr(builder, "THREE", tmp_path / "missing_three.js")
    with pytest.raises(SystemExit, match="unable to read vendor script"):
        builder.render()

    # Missing template raises SystemExit
    monkeypatch.setattr(builder, "TEMPLATE", tmp_path / "missing_template.html")
    with pytest.raises(SystemExit, match="unable to read viewer template"):
        builder.render()

    # Missing OEM raises SystemExit
    monkeypatch.setattr(builder, "TEMPLATE", ROOT / "viewer" / "cislunar_replay.template.html")
    monkeypatch.setattr(builder, "THREE", ROOT / "vendor" / "three.min.js")
    monkeypatch.setattr(builder, "OEM", tmp_path / "missing.oem")
    with pytest.raises(SystemExit, match="unable to build trajectory payload"):
        builder.render()


# --------------------------------------------------------------- tools/verify
def test_embedded_payload_missing_and_malformed_blocks_raise() -> None:
    with pytest.raises(SystemExit, match="embedded payload block not found"):
        verify.embedded_payload("<html><body>no script</body></html>")
    with pytest.raises(SystemExit, match="invalid JSON"):
        verify.embedded_payload(
            '<script id="trajectory-data" type="application/json">{not_json}</script>'
        )
    with pytest.raises(SystemExit, match="JSON object"):
        verify.embedded_payload(
            '<script id="trajectory-data" type="application/json">[1, 2, 3]</script>'
        )


def test_verify_main_passes_on_committed_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / "reports" / "verification.json"
    monkeypatch.setattr(verify, "REPORT", report_path)
    rc = verify.main()
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["failures"] == []
    assert report["oem_csv_identical"] is True
    assert report["viewer"]["external_assets"] is False
    assert report["viewer"]["network_fetch"] is False


def test_verify_detects_external_assets_and_network_fetch_accurately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / "verification.json"
    bad_html = tmp_path / "bad_viewer.html"
    original_html = (ROOT / "cislunar_replay.html").read_text(encoding="utf-8")
    bad_html.write_text(
        original_html.replace("</body>", '<script src="https://evil.example/x.js"></script></body>'),
        encoding="utf-8",
    )
    fake_root = tmp_path / "fake_root"
    (fake_root / "fixtures").mkdir(parents=True)
    (fake_root / "viewer").mkdir(parents=True)
    (fake_root / "fixtures" / "synthetic_free_return.oem").write_bytes(
        (ROOT / "fixtures" / "synthetic_free_return.oem").read_bytes()
    )
    (fake_root / "fixtures" / "synthetic_free_return.csv").write_bytes(
        (ROOT / "fixtures" / "synthetic_free_return.csv").read_bytes()
    )
    orig_tpl = (ROOT / "viewer" / "cislunar_replay.template.html").read_text(encoding="utf-8")
    (fake_root / "viewer" / "cislunar_replay.template.html").write_text(
        orig_tpl + "\n// fetch('https://telemetry.invalid')\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(verify, "ROOT", fake_root)
    monkeypatch.setattr(verify, "HTML", bad_html)
    monkeypatch.setattr(verify, "REPORT", report_path)
    with patch.object(
        builder,
        "build",
        return_value={
            "sha256": "mismatch",
            "three_version": builder.THREE_VERSION,
            "three_sha256": builder.THREE_SHA256,
        },
    ):
        rc = verify.main()

    assert rc == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert "viewer references an external http(s) asset" in report["failures"]
    assert "viewer application performs a network fetch" in report["failures"]
    assert "committed viewer is not reproducible" in report["failures"]
    assert report["viewer"]["external_assets"] is True
    assert report["viewer"]["network_fetch"] is True


# ------------------------------------------------------ tools/check_ephemeris
def test_check_ephemeris_passes_and_detects_tight_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_root = tmp_path / "ephem_root"
    monkeypatch.setattr(check_ephemeris, "ROOT", fake_root)
    assert check_ephemeris.main() == 0
    report = json.loads((fake_root / "reports" / "moon-ephemeris-check.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["max_error_km"] < check_ephemeris.LIMIT_KM

    monkeypatch.setattr(check_ephemeris, "LIMIT_KM", 0.001)
    assert check_ephemeris.main() == 1


# ----------------------------------------------------- tools/generate_fixture
def test_generate_fixture_helpers_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Degenerate lunar geometry guard when Moon is aligned with +Z
    with (
        patch.object(ce, "moon_position_eme2000", return_value=(0.0, 0.0, 384400.0)),
        pytest.raises(ValueError, match="degenerate lunar geometry"),
    ):
        generate_fixture.basis(datetime(2026, 4, 2, tzinfo=UTC))

    times, points = generate_fixture.waypoints()
    velocities = generate_fixture.derivatives(times, points)
    pos, vel = generate_fixture.hermite(times, points, velocities, times[0])
    assert pos == pytest.approx(points[0], abs=1e-9)
    assert vel == pytest.approx(velocities[0], abs=1e-9)

    monkeypatch.setattr(generate_fixture, "ROOT", tmp_path)
    summary = generate_fixture.build()
    assert summary["vectors"] == len(generate_fixture.PROFILE)
    assert (tmp_path / "fixtures" / "synthetic_free_return.oem").is_file()
    assert (tmp_path / "fixtures" / "synthetic_free_return.csv").is_file()


# ------------------------------------------------------ tools/package_release
def test_package_release_builds_deterministic_zip_and_detects_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_files = ["a.txt", "sub/b.txt"]
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "sub" / "b.txt").write_text("beta\n", encoding="utf-8")

    zip_out = tmp_path / "dist" / "release.zip"
    sums_out = tmp_path / "dist" / "SHA256SUMS.txt"
    monkeypatch.setattr(package_release, "ROOT", tmp_path)
    monkeypatch.setattr(package_release, "FILES", sample_files)
    monkeypatch.setattr(package_release, "ZIP_PATH", zip_out)
    monkeypatch.setattr(package_release, "SUMS_PATH", sums_out)

    result = package_release.build()
    assert result["verified"] is True
    assert result["mismatches"] == []
    with zipfile.ZipFile(zip_out) as zf:
        assert sorted(zf.namelist()) == sorted(sample_files)

    # Missing file raises SystemExit
    (tmp_path / "a.txt").unlink()
    with pytest.raises(SystemExit, match="missing release file"):
        package_release.build()


def test_package_release_main_and_archive_verification_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_files = ["a.txt"]
    (tmp_path / "a.txt").write_text("alpha\n", encoding="utf-8")
    zip_out = tmp_path / "dist" / "release.zip"
    sums_out = tmp_path / "dist" / "SHA256SUMS.txt"
    monkeypatch.setattr(package_release, "ROOT", tmp_path)
    monkeypatch.setattr(package_release, "FILES", sample_files)
    monkeypatch.setattr(package_release, "ZIP_PATH", zip_out)
    monkeypatch.setattr(package_release, "SUMS_PATH", sums_out)

    assert package_release.main() == 0

    mismatches = package_release._verify_archive(
        zip_out, ["a.txt", "extra.txt"], {"a.txt": "wrong_hash"}
    )
    assert "archive entry list differs from the manifest" in mismatches
    assert "archive entry differs: a.txt" in mismatches
