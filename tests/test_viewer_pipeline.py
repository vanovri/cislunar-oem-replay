"""Behavioral checks for the previously untested parser-to-viewer boundary."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cislunar_ephemeris as ce
from tools import build_viewer as builder
from tools.verify import embedded_payload


def test_canonical_preserves_structure_without_mutating_source():
    source = {'track': [{'r': 1.234567, 'count': 2, 'ok': True}], 'text': 'EME2000'}
    result = builder.canonical(source)
    assert result == {'track': [{'r': 1.235, 'count': 2, 'ok': True}], 'text': 'EME2000'}
    assert source['track'][0]['r'] == 1.234567
    assert type(result['track'][0]['count']) is int


def test_oem_render_round_trip_escapes_script_terminators(tmp_path, monkeypatch):
    source = (ROOT / 'fixtures/synthetic_free_return.oem').read_text(encoding='utf-8')
    source = source.replace('OBJECT_NAME =', 'OBJECT_NAME = </script>')
    oem = tmp_path / 'escaped.oem'
    oem.write_text(source, encoding='utf-8')
    monkeypatch.setattr(builder, 'OEM', oem)
    html, info = builder.render()
    payload = embedded_payload(html)
    expected = builder.canonical(ce.to_payload(ce.parse_file(oem), builder.TRACK_POINTS))
    assert payload == expected
    assert payload['metadata']['OBJECT_NAME'].startswith('</script>')
    assert '<\\/script>' in html
    assert info['gates'] == 'pass'
    assert len(payload['track']) == builder.TRACK_POINTS


def test_renderer_refuses_tampered_vendor_before_output(tmp_path, monkeypatch):
    vendor = tmp_path / 'three.js'
    vendor.write_text('window.THREE = {};', encoding='utf-8')
    monkeypatch.setattr(builder, 'THREE', vendor)
    with pytest.raises(SystemExit, match='pinned SHA-256'):
        builder.render()


def test_renderer_refuses_ambiguous_payload_slot(tmp_path, monkeypatch):
    template = tmp_path / 'template.html'
    template.write_text(builder.PAYLOAD_TOKEN * 2 + builder.THREE_TOKEN, encoding='utf-8')
    monkeypatch.setattr(builder, 'TEMPLATE', template)
    with pytest.raises(SystemExit, match='exactly one'):
        builder.render()
