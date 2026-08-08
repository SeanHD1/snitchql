# SnitchQL — Fix & Feature Backlog

Priority key: **P0** = critical (app hangs/unresponsive), **P1** = high (broken functionality), **P2** = medium (UX/cosmetic), **P3** = low (nice-to-have/cleanup)

---

## Performance

- [x] **P1** — Quick filter should search via indexed fields only, to speed up searching on large .dat files

---

## Bugs

- [x] **P2** — `SnitchQL.log` is created next to the exe on every launch, even on a clean run with nothing logged (stdout/stderr redirect opens the file in append mode at startup). Make the log lazily-created: only open/create it the first time something is actually written, so a clean run leaves no stray file while crashes/tracebacks are still captured.
- [x] **P1** — Compare feature needs to be reworked
- [x] **P2** — Blob error message header still not appearing correctly
- [x] **P2** — Clear filter button does not auto-apply; pressing it should immediately re-apply/refresh the (now cleared) filter state

---

## Feature Additions

- [x] **P0** — Imports need to be functional (CSV and JSON) — flagged as a major feature
- [x] **P1** — Hidden columns (not marked visible) should not appear in the filter builder dropdown or in exported documents
- [x] **P2** — Add a "search unindexed field" tick box, with a pop-up warning that this type of search will be slower

---

## Cosmetics / UI

- [x] **P2** — Columns with longer names are getting cut off; set column width to match header width
- [x] **P3** — Replace default icons with custom assets from `/home/alsaher/Projects/dbisam-tool/assets/`
- [x] **P2** — SQL query builder should open empty; no default query should be pre-filled

---

## Documentation / Cleanup

- [x] **P3** — README still contains references to "all dats" — needs cleanup

---

### Notes (2026-08-08 session)
- Index/table maintenance tools (rebuild/verify/repair) were REMOVED from this list — parked on the
  back-burner per Damion's direction (native engine source for 4.48 not available; dbsys wrapper rejected).
  The parked modules (index_tools.py, index_tools_dialog.py, idx_writer.py) remain on disk for later.
- P0 Imports implemented as: parse CSV/JSON -> display as a read-only virtual table in the pane
  (Quick Filter / compare / column-select / export all work on imported data). Editing/saving imported
  data is intentionally disabled (no backing .dat). Writing imported data *into* a new .dat is a separate,
  riskier milestone not done here.
- Quick filter now defaults to indexed-column-only scanning (detected from sibling .idx files via
  idx_writer.read_index_defs); the "Search unindexed" tick box opts into a full-column scan with a
  one-time slowdown warning.
- Compare rework: per Damion's note, the row background is NO LONGER painted; instead the font colour is
  tinted with the compare colour (block = dim grey 225,225,230; present = soft pastel) so emphasis is on
  the text, not a filled background.
