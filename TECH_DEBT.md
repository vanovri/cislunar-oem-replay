# Technical Debt & Performance Ledger — `cislunar-oem-replay`

All items below are verified against the current repository source files on disk with exact 1-based line numbers. Production code has not been speculatively modified.

---

## 1. Redundant Triple Evaluation of 74-Term Lunar Ephemeris in `to_payload()`

- **Location**: `cislunar_ephemeris.py` (lines `613-615`)
- **Category**: Algorithmic Efficiency & Redundant Computation
- **Severity**: Medium

### Technical Analysis & Impact
`to_payload(trajectory, track_points)` (`cislunar_ephemeris.py:649-672`) evaluates `telemetry(state)` (which runs the 74-term ELP2000-82B trigonometric series in `moon_position_eme2000`) across all `trajectory.states` **three separate times**: once when building `samples`, a second time inside `summarize(trajectory)` (`line 670`), and a third time inside `gates(trajectory)` (`line 671`, which calls `summarize(trajectory)` again at `line 614`). Allowing `gates()` to accept a precomputed `summary` eliminates one full pass over all state vectors immediately.

### Proposed Patch (`diff`)
```diff
--- a/cislunar_ephemeris.py
+++ b/cislunar_ephemeris.py
@@ -610,8 +610,9 @@
     return True
 
 
-def gates(trajectory: Trajectory) -> dict[str, Any]:
-    summary = summarize(trajectory)
+def gates(trajectory: Trajectory, summary: dict[str, Any] | None = None) -> dict[str, Any]:
+    if summary is None:
+        summary = summarize(trajectory)
     failures: list[str] = []
     if summary["min_earth_altitude_km"] < MIN_CLEARANCE_KM:
         failures.append(f"Earth clearance below {MIN_CLEARANCE_KM:g} km")
```

### Verification
Run `C:\Python314\python.exe -m pytest -q` and confirm `to_payload()` output is bit-identical.

---

## 2. Repeated Per-Term Modulo/Radians Conversion & Exponentiation in `_lunar_ecliptic_spherical()`

- **Location**: `cislunar_ephemeris.py` (lines `415-423`)
- **Category**: Numerical Hot-Loop Optimization
- **Severity**: Medium

### Technical Analysis & Impact
`_lunar_ecliptic_spherical(t)` runs 600 times per `build_track()` call (44 `_LR_TERMS` + 30 `_B_TERMS` = 74 terms per point, or 44,400 loop iterations per track). Inside both loops, `e ** abs(cm)` is recomputed 74 times per epoch even though `abs(cm)` only ever takes values in `{0, 1, 2}`. Precomputing `e_pow = (1.0, e, e * e)` avoids 44,400 float power calls per payload build.

### Proposed Patch (`diff`)
```diff
--- a/cislunar_ephemeris.py
+++ b/cislunar_ephemeris.py
@@ -413,14 +413,15 @@
     e = 1.0 - 0.002516 * t - 0.0000074 * t**2
 
     sum_l = sum_r = sum_b = 0.0
+    e_pow = (1.0, e, e * e)
     for cd, cm, cmp, cf, coef_l, coef_r in _LR_TERMS:
         argument = _deg_to_rad_mod360(cd * d + cm * m + cmp * mp + cf * f)
-        factor = e ** abs(cm)
+        factor = e_pow[abs(cm)]
         sum_l += coef_l * factor * math.sin(argument)
         sum_r += coef_r * factor * math.cos(argument)
     for cd, cm, cmp, cf, coef_b in _B_TERMS:
         argument = _deg_to_rad_mod360(cd * d + cm * m + cmp * mp + cf * f)
-        sum_b += coef_b * (e ** abs(cm)) * math.sin(argument)
+        sum_b += coef_b * e_pow[abs(cm)] * math.sin(argument)
 
     sum_l += (
         3958.0 * math.sin(_deg_to_rad_mod360(a1))
```

### Verification
Run `C:\Python314\python.exe -m pytest -q` and `C:\Python314\python.exe tools/check_ephemeris.py`.
