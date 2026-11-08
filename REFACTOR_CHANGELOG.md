# Refactoring & Verification Changelog (`cislunar-oem-replay`)

## 1. Branch & Git Safety Summary

- **Repository:** `.`
- **Original Branch:** `main`
- **Target Branch:** `codex/auto-refactor-and-tests`
- **Backup Snapshot Stash:** Pre-existing untracked analysis artifacts (`ARCHITECTURE.md`, `TECH_DEBT.md`, `graphify-out/`) were preserved via `git stash push --include-untracked -m "pre-refactor-backup-snapshot"` before branching and kept out of the committed refactoring changeset.
- **Backward Compatibility:** 100% preserved across all public classes, functions, constants, CLI flags (`--oem`, `--csv`, `--out`, `--track-points`, `--check`), JSON output schemas (`CISLUNAR_OEM_V1`), and committed standalone HTML SHA-256 (`6b1729346f6f57b2382a6d076c5b93df2f9760d19ede675b434a9cac622a8acb`).

---

## 2. Summary of Modified & Added Files

| File | Action | Purpose |
| :--- | :--- | :--- |
| `cislunar_ephemeris.py` | Modified | Added full static typing (`Vec3`, `CubicCurve`, `TypeGuard` validators), decomposed `parse_oem`, `moon_position_eme2000`, `earth_segment_clear`, and `main` into <50-line helpers, replaced `O(N log N)` sort with single-pass `O(N)` `itertools.pairwise` epoch validation, enforced `2 <= points <= 100_000` (`type(points) is int`), eliminated per-sample generator allocations in 3D math, and hardened CLI `OSError` resilience with `logging`. |
| `tools/__init__.py` | Added | Explicit package marker resolving dual-module path ambiguity in `mypy` (`tools.build_viewer` vs `build_viewer`). |
| `tools/build_viewer.py` | Modified | Added full type annotations, rejected non-finite floats in `canonical`, hardened `serialize_embedded_json` to escape both `</` (`<\/`) and `<!--` (`\u003c!--`), replaced sequential `.replace()` with single-pass `_assemble_viewer_html` preventing cross-slot token injection, and added `main(argv)` CLI entry point + `logging`. |
| `tools/verify.py` | Modified | Removed dead `tempfile.TemporaryDirectory()` filesystem I/O and unused `tmp` variable, pre-compiled security scan regexes, decomposed `main()` into `_evaluate_fixture_agreement`, `_evaluate_viewer_artifact`, and `_compose_verification_report`, and fixed hardcoded `external_assets: False` / `network_fetch: False` reporting bug. |
| `tools/check_ephemeris.py` | Modified | Added full type annotations, extracted `evaluate_checks()`, modernized UTC ISO parsing (`datetime.fromisoformat`), and added structured `OSError` handling + `logging`. |
| `tools/generate_fixture.py` | Modified | Added strict `Vec3` 3-tuple typing, decomposed 63-line `build()` into `_format_oem_text`, `_format_csv_text`, and `_max_node_reconstruction_error`, and replaced generator-based 3D vector norms with direct arithmetic. |
| `tools/package_release.py` | Modified | Added full type annotations, decomposed `build()` into `_write_deterministic_archive` and `_verify_archive`, and added `main()` + `logging`. |
| `tests/test_cislunar_ephemeris.py` | Modified | Updated `timezone.utc` to `datetime.UTC`, added explicit `check=False` in `subprocess.run`, and removed unused `# noqa` comments for clean Ruff compliance while preserving all 43 original tests. |
| `tests/test_continuous_earth.py` | Modified | Updated `timezone.utc` to `datetime.UTC` for Ruff `UP017` compliance while preserving all original tests. |
| `tests/test_oem_and_interpolation_edges.py` | Added | 58 unit and boundary tests covering epoch parsing, Julian date timezone conversion, `NaN`/`Inf`/`bool` guards, OEM/CSV structural errors, 5 MB size caps, epoch disorder/duplicate error precedence, Hermite interpolation edge cases, 3-axis continuous Earth-impact screening, track point bounds (`2..100_000`), and CLI error paths. |
| `tests/test_viewer_and_tools.py` | Added | 15 unit and integration tests covering `tools/build_viewer.py`, `tools/verify.py`, `tools/check_ephemeris.py`, `tools/generate_fixture.py`, and `tools/package_release.py` completely offline in isolated `tmp_path` sandboxes. |
| `tests/test_viewer_pipeline.py` | Added | 4 focused pipeline boundary tests for `canonical` immutability, `</script>` round-trip escaping, tampered vendor hash rejection, and ambiguous template slot rejection. |

---

## 3. Architectural & Code Quality Improvements

### 3.1 Type Safety & Contracts
- **Strict Static Typing:** Annotated every function, method, and module constant across `cislunar_ephemeris.py` and `tools/*.py` (`Vec3 = tuple[float, float, float]`, `CubicCurve = tuple[Vec3, Vec3, Vec3, Vec3]`, `TypeGuard` helpers). Fixed all `tuple[float, ...]` vs `tuple[float, float, float]` mismatches so `mypy cislunar_ephemeris.py tools --ignore-missing-imports` passes with zero errors.
- **Caller-Controlled Track Allocation Bounds:** Enforced `type(points) is int and 2 <= points <= 100_000` in `_validate_track_points` / `build_track` (`cislunar_ephemeris.py`), rejecting `0`, `1`, `100_001`, `bool` (`True`/`False`), and `float` inputs with `ValueError("track needs 2..100000 integer points")`.
- **Boundary Input Guards:** Rejected non-string inputs in `parse_epoch`, `parse_oem`, and `parse_csv`; rejected naive or non-`datetime` inputs in `julian_date`; rejected `bool` (`True`/`False`) and non-finite `t_s` in `interpolate`; rejected non-finite floats (`NaN`, `Inf`, `-Inf`) in `build_viewer.canonical`.

### 3.2 Modularity & Clean Architecture
- Decomposed all functions exceeding 50 lines into single-responsibility helpers:
  - `cislunar_ephemeris.parse_oem` (was 81 lines) -> `_record_oem_key_value`, `_scan_oem_lines`, `_validate_oem_metadata`, `_extract_provenance`, `parse_oem`.
  - `cislunar_ephemeris.moon_position_eme2000` (was 56 lines) -> `_deg_to_rad_mod360`, `_lunar_ecliptic_spherical`, `moon_position_eme2000`.
  - `cislunar_ephemeris.earth_segment_clear` -> `_midpoint`, `_aabb_lower_distance_sq`, `_norm_sq`, `_subdivide_bezier`, `earth_segment_clear`.
  - `tools/verify.main` (was 74 lines) -> `_evaluate_fixture_agreement`, `_evaluate_viewer_artifact`, `_compose_verification_report`, `main`.
  - `tools/generate_fixture.build` (was 63 lines) -> `_format_oem_text`, `_format_csv_text`, `_max_node_reconstruction_error`, `build`.

### 3.3 Performance
- **Single-Pass `O(N)` Epoch Ordering Validation:** Replaced `ordered = sorted(states, key=lambda s: s.epoch)` (`O(N log N)` time + `O(N)` list allocation + `states[1:]` slice copy) in `_finalize` with a single `O(N)` pass over `itertools.pairwise(states)` using `O(1)` auxiliary space.
- **Generator-Free 3D Vector Arithmetic:** Replaced per-call generator expressions inside `interpolate`, `telemetry`, and `_aabb_lower_distance_sq` with direct 3-tuple arithmetic, eliminating 5 generator frame allocations per interpolated track sample.
- **Pre-Compiled Regular Expressions & Elimination of Dead Disk I/O:** Pre-compiled `PROVENANCE_RE` in `cislunar_ephemeris.py` and `_PAYLOAD_SCRIPT_RE`, `_EXTERNAL_ASSET_RE`, `_NETWORK_FETCH_RE` in `tools/verify.py`, and removed the unused `tempfile.TemporaryDirectory()` filesystem creation/cleanup in `tools/verify.py`.

### 3.4 Error Resilience & Concrete Bugs Fixed
1. **Cross-Slot Template Token Injection & HTML Comment Breakout (`tools/build_viewer.py`):** Previously, `render()` escaped only `</` and executed sequential `template.replace(PAYLOAD_TOKEN, embedded)` followed by `html.replace(THREE_TOKEN, ...)`. If an OEM metadata value contained `<!-- INLINE_THREE -->` (or `<!--`), `three.min.js` was spliced directly inside the `<script id="trajectory-data" type="application/json">` JSON block, corrupting the JSON payload. Fixed by escaping `<!--` to `\u003c!--` in `serialize_embedded_json` and performing single-pass index slicing in `_assemble_viewer_html`.
2. **False-Negative Security Flags in Verification Report (`tools/verify.py`):** Previously, `report["viewer"]["external_assets"]` and `report["viewer"]["network_fetch"]` were hardcoded to `False` even when `_EXTERNAL_ASSET_RE` or `_NETWORK_FETCH_RE` matched and appended to `failures`. Fixed to record the actual boolean scan results.
3. **Uncaught `FileNotFoundError` / `OSError` in CLI (`cislunar_ephemeris.py`):** Previously, `arguments.out.samefile(source_path)` ran before `try:` whenever `arguments.out.exists()` was true (crashing with an unhandled `FileNotFoundError` traceback if `source_path` did not exist), and `arguments.out.write_text(...)` ran outside `try:` (crashing on unwritable output paths). Both are now safely guarded and logged via `logging.getLogger(__name__)`, returning exit code `1`.

---

## 4. Before vs. After Test Suite & Coverage

### 4.1 Summary Metrics

| Metric | Before | After | Delta |
| :--- | ---: | ---: | ---: |
| **Total Tests** | 45 | **120** | **+75 (+166.7%)** |
| **Passing Tests** | 45 (100%) | **120 (100%)** | **+75** |
| **Total Statements** | 626 | 826 | +200 |
| **Missed Statements** | 323 | **23** | **-300** |
| **Total Line Coverage** | 48% | **97%** | **+49 percentage points** |

### 4.2 Per-Module Coverage Breakdown

| Module | Before Stmts | Before Miss | Before Cover | After Stmts | After Miss | After Cover |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `cislunar_ephemeris.py` | 348 | 45 | 87% | 441 | 6 | **99%** |
| `tools/__init__.py` | — | — | — | 1 | 0 | **100%** |
| `tools/build_viewer.py` | 67 | 67 | 0% | 105 | 2 | **98%** |
| `tools/check_ephemeris.py` | 26 | 26 | 0% | 37 | 6 | **84%** |
| `tools/generate_fixture.py` | 93 | 93 | 0% | 109 | 2 | **98%** |
| `tools/package_release.py` | 41 | 41 | 0% | 57 | 2 | **96%** |
| `tools/verify.py` | 51 | 51 | 0% | 76 | 5 | **93%** |
| **TOTAL** | **626** | **323** | **48%** | **826** | **23** | **97%** |

---

## 5. Verification Commands & Exit Codes

| Check | Command | Result | Exit Code |
| :--- | :--- | :--- | ---: |
| **Unit Tests & Coverage** | `PYTHONPATH=. <QA_PYTHON> -m pytest --cov=cislunar_ephemeris --cov=tools --cov-report=term-missing` | `120 passed`, `97%` total coverage | `0` |
| **Ruff Linter** | `<RUFF> check cislunar_ephemeris.py tools tests` | `All checks passed!` | `0` |
| **Mypy Type-Checker** | `<MYPY> cislunar_ephemeris.py tools --ignore-missing-imports` | `Success: no issues found in 7 source files` | `0` |
| **Viewer Reproducibility** | `<QA_PYTHON> tools/build_viewer.py --check` | `checked: true`, SHA-256 matches committed `cislunar_replay.html` | `0` |
| **End-to-End Verification** | `<QA_PYTHON> tools/verify.py` | `ok: true`, `failures: []` | `0` |
