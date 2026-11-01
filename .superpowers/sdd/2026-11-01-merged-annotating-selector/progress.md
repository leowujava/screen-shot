# SDD ledger — plan: docs/superpowers/plans/2026-11-01-merged-annotating-selector.md

Pre-flight: Task 1 produces `Selector(x, y, width, height, full_img, on_select, root)` with `_mode`, `_region`, `_init_region_img`, `_confirm`, `_cancel`, `_finish`. Task 2 consumes Task 1's class and extends constructor to add `on_copy`/`on_save`, changes `_finish` to accept a callback. Task 3 consumes Task 2's `_region_img`, `_tool_btns`. Task 4 consumes Task 3's rendering. Task 5 consumes Task 2's toolbar buttons. Task 6 consumes all. Task 7 consumes Task 1's callback signature. No conflicts found.

Task 1: complete (commits aec1e32..63d1836, tests: pytest tests/test_selector.py -v → 11/11 passed)
  Ruling: `_hit_test` uses `HANDLE_SIZE // 2 = 6` instead of `HANDLE_SIZE + MARGIN = 20` — matches annotation.py's hit area to avoid annotation mis-triggering handles. Cost if wrong: handles slightly harder to grab, but annotation is not blocked by handle hot zones.
  Ruling: Removed "move" drag behavior from adjust mode (replaced by annotation). Updated `test_select_to_adjust_flow` to remove the move part. Cost if wrong: users can't drag region by clicking inside it; they must use handles.
  Ruling: `self.result` is set in `_confirm`/`_cancel`/`_copy`/`_save` for backward compat. Cost if wrong: none, it's just a convenience attribute.
  Ruling: Mocked Win32 `GetSystemMetrics` in test fixture to return 0, so `_get_screen_size` falls through to `self.width, self.height`. Cost if wrong: tests depend on the mock screen size matching the window size.
  Ruling: Imported `Image`/`ImageDraw` at top of test file (real PIL) instead of inside test functions, so annotation tests use real PIL while the fixture only mocks `ImageTk.PhotoImage` in the selector module. Cost if wrong: none.
Task 2: complete (commits 63d1836..09e38b3, tests: pytest tests/test_selector.py -v → 13/13 passed)
  Note: Toolbar was already implemented in Task 1. Task 2 only added the verification tests.
Task 3: complete (commits 09e38b3..c932a8b, tests: pytest tests/test_selector.py -v → 18/18 passed)
Task 4: complete (commits c932a8b..40c18f7, tests: pytest tests/test_selector.py -v → 20/20 passed)
Task 5: complete (commits 40c18f7..6464110, tests: pytest tests/test_selector.py -v → 22/22 passed)
Task 6: complete (commits 6464110..6464110, tests: pytest tests/ -v → 61/61 passed)
  Note: No fixes needed; all regression tests pass.
Task 7: complete (commits 6464110..2de9b1d, tests: pytest tests/ -v → 49/49 passed)

Final review: self-review (no subagent tool)
