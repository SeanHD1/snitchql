# SnitchQL — Fix & Feature Backlog

Priority key: **P0** = critical (app hangs/unresponsive), **P1** = high (broken functionality), **P2** = medium (UX/cosmetic), **P3** = low (nice-to-have/cleanup)

---

## Performance

- [ ] **P1** — Quick filter should search via indexed fields only, to speed up searching on large .dat files

---

## Bugs

- [ ] **P1** — Compare feature needs to be reworked
- [ ] **P2** — Blob error message header still not appearing correctly
- [ ] **P2** — Clear filter button does not auto-apply; pressing it should immediately re-apply/refresh the (now cleared) filter state

---

## Feature Additions

- [ ] **P0** — Imports need to be functional (CSV and JSON) — flagged as a major feature
- [ ] **P1** — Hidden columns (not marked visible) should not appear in the filter builder dropdown or in exported documents
- [ ] **P2** — Add a "search unindexed field" tick box, with a pop-up warning that this type of search will be slower
- [ ] **P1** — Add rebuild indexes, verify table, and repair table maintenance options

---

## Cosmetics / UI

- [ ] **P2** — Columns with longer names are getting cut off; set column width to match header width
- [ ] **P3** — Replace default icons with custom assets from `/home/alsaher/Projects/dbisam-tool/assets/`
- [ ] **P2** — SQL query builder should open empty; no default query should be pre-filled

---

## Documentation / Cleanup

- [ ] **P3** — README still contains references to "all dats" — needs cleanup

---

### Suggested fix order (dependency-aware)
1. **P0 imports (CSV/JSON)** — flagged as major, tackle first
2. **P1 quick filter fix** — indexed-field search
3. **P1 compare rework**
4. **P1 hidden columns** excluded from filter dropdown/export
5. **P1 index/table maintenance tools** (rebuild indexes, verify table, repair table)
6. **P2 bug fixes** (blob error header, clear-filter auto-apply, unindexed search tick box)
7. **P2 cosmetic** cleanup (column width, SQL builder default state)
8. **P3 cleanup** (icon assets, README references) last, since it's low-risk and non-blocking
