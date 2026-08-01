# SnitchQL — Fix & Feature Backlog

Priority key: **P0** = critical (app hangs/unresponsive), **P1** = high (broken functionality), **P2** = medium (UX/cosmetic), **P3** = low (nice-to-have/cleanup)

---

## Performance

- [x] **P0** — Initial database open is very slow; causes SnitchQL to become unresponsive (not responding)
- [x] **P0** — Edit mode is slow/broken; causes SnitchQL to become unresponsive (not responding)
- [x] **P0** — After saving, app becomes unresponsive (not responding)
- [x] **P1** — "Quick" filter is too slow; needs major speed improvement or should be removed entirely

---

## Bugs

- [x] **P1** — Unexpected terminal-like window opens during use (build now windowed; stdout/stderr -> SnitchQL.log)
- [x] **P1** — Cannot edit dates at all same with integers or boolean values.
- [ ] **P2** — Blob error message header is not displaying properly
- [x] **P2** — Save changes count does not clear/reset after file has been written
- [ ] **P2** — Top row of buttons is not aligned with the bottom row of buttons

---

## Feature Additions

- [x] **P0** — Add custom SQL query option (available only in Single View)
- [x] **P1** — Add date picker control to allow editing dates (ties into "cannot edit dates" bug above)
- [x] **P1** — Add ability to select which fields/columns are visible in the view
- [x] **P2** — Insert full directory path under the db.dat name (currently only shows name)

---

## Cosmetics / UI

- [x] **P1** — Header bar is not respecting dark mode
- [ ] **P2** — Set Dark Mode as the default theme
- [ ] **P2** — Set Single View as the default view
- [ ] **P2** — Rename prompt window header to something more descriptive (name TBD)
- [x] **P2** — Rename "Quick" (filter) to something clearer, e.g. "Quick Filter" (see also Performance item above)

---

## Documentation / Cleanup

- [ ] **P3** — Remove all references to "all dats" throughout docs and code
- [ ] **P3** — Set default directory to Desktop or C:\ (replacing "all dats" default path logic)

---

### Suggested fix order (dependency-aware)
1. **P0 performance/hang issues** (db open, edit mode, post-save) — DONE
2. **P0 custom SQL query option** (Single View only) — DONE
3. **P1 date bugs + date picker** — DONE (scalars editable + date picker)
4. **P1 quick filter** — DONE (rename + speed)
5. **P1 field/column selection** feature — NEXT
6. **P1/P2 UI/dark mode/header bar** fixes
7. **P2 cosmetic** cleanup (alignment open; save counter + naming DONE)
8. **P3 docs/default path cleanup** last, since it's low-risk and non-blocking
