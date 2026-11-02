# Task 1 Report: theme.py 设计 token + 测试

## What I implemented
- `theme.py` (leaf module, no imports from selector/editor/app/icon) transcribed verbatim from the brief:
  - `FONT = ("Microsoft YaHei UI", 9)`, `FONT_ICON = ("Microsoft YaHei UI", 10)`
  - Sizes: `ICON_SIZE=18`, `BTN_PAD_X=8`, `BTN_PAD_Y=6`, `TB_RADIUS=8`, `BTN_RADIUS=6`, `SEP_W=1`, `SEP_H=18`
  - `LIGHT` dict (9 keys), `DARK` dict (15 keys) — exact hex values from brief
  - `ACCENT = "#4a90e2"`, `PALETTE` (10 colors, first = default draw color)
  - `_hex_to_rgb(color)` helper
  - `rounded_rect_photo(master, w, h, radius, fill, outline) -> tk.PhotoImage` — PIL-based rounded-rect background with 1px outline, small-size degradation path
- `tests/test_theme.py` with the 5 tests from the brief (light keys, dark keys, hex color validation, palette length, sizes).

## What I tested and results
- `pytest tests/test_theme.py -v` → 5 passed
- Full suite `pytest -q` → 61 passed (no regressions; existing tests unaffected since theme is a new leaf module)

## TDD Evidence
- **RED**: `python -m pytest tests/test_theme.py -v` before implementing →
  ```
  tests\test_theme.py:2: in <module>
      import theme
  E   ModuleNotFoundError: No module named 'theme'
  =========================== 1 error in 0.32s ===============================
  ```
  Expected: test file references `theme` which doesn't exist yet, so collection fails with ModuleNotFoundError.
- **GREEN**: same command after implementing → `5 passed in 0.07s`.

## Files changed
- `theme.py` (new, 84 lines)
- `tests/test_theme.py` (new, 42 lines)

## Self-review findings
- Completeness: all constants + function present; all 5 tests present. ✓
- Quality: names match brief exactly (LIGHT, DARK, PALETTE, ICON_SIZE, TB_RADIUS, BTN_RADIUS, BTN_PAD_X, BTN_PAD_Y, SEP_W, SEP_H, FONT, FONT_ICON, ACCENT, rounded_rect_photo). ✓
- Discipline: code transcribed verbatim; no extra features. ✓
- Testing: tests verify behavior (hex regex validation over LIGHT/DARK/ACCENT/PALETTE, key-set superset, exact sizes, palette length == 10). Output pristine. ✓
- `rounded_rect_photo` is intentionally not exercised in tests (no real Tk in test env), per the brief's note.

## Issues or concerns
None.
