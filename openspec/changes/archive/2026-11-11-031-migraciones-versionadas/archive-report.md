# Archive Report — 031-migraciones-versionadas

**Change**: 031-migraciones-versionadas
**Archived**: 2026-11-11
**Archive location**: `openspec/changes/archive/2026-11-11-031-migraciones-versionadas/`
**Artifact store**: openspec (filesystem)
**Archived by**: manual archive — `gentle-ai 4.3.1` does not expose `sdd-archive`
or `sdd-archive-compose`, and the environment has no `sdd-archive` skill

## Final State

| Item | Final value | Source |
|------|-------------|--------|
| Tasks complete | **8/8** (0 unchecked) | persisted `tasks.md`, `grep -c '^- \[x\]'` |
| Verify verdict | **not run** — no `verify-report.md` artifact exists; the checks ran as repository batteries per task | artifact absent |
| Code changes | `scripts/check_migrations.py`, job `Migrations safety` in `.github/workflows/ci.yml`, `deploy/tests/` | `7f00115` (#166), `9b432c5` (#192), `a45371e` (#193) |
| Implementation tests | **24** in `apps/core/tests/test_check_migrations_script.py` | `grep -c 'def test_'` |
| Release | **v0.4.0**, annotated tag on `7fc600c`, published 2026-11-11 | `gh release view v0.4.0` |

This was the last open change. With it archived, `openspec/changes/` contains
only `archive/` and `.gitkeep`.

## T8 — the task this archive completes

T8 asked for an annotated tag on the promotion commit plus `gh release create`,
and explicitly left the version number to the user. Closed as **v0.4.0**: the
previous tag was `v0.3.4`, and this release carries a feature (030), so the
choice was a minor bump. The tag points at `7fc600c`, the merge commit of
PR #190, and `v0.2.0` was not moved.

The promotion itself returned to `dev` through PR #194, so `dev` contains
`main` and the next promotion will not report `BEHIND`.

## Specs Synced

| Canonical | Action | Details |
|-----------|--------|---------|
| `openspec/specs/migraciones-versionadas/spec.md` | **Created** (new capability) | delta copied byte-for-byte; 3 requirements, 5 scenarios |

### Composition Method

The capability had no canonical, so the delta was copied verbatim. Unlike 025,
026 and 028, there was no section header to rewrite: this delta already used
`## Requirement 1: ...` form. The promotion was verified with `diff` between the
archived delta and the canonical file and returned **EMPTY**.

### Requirements promoted

`Requirement 1: migraciones versionadas`,
`Requirement 2: gate de migraciones faltantes`,
`Requirement 3: documentación coherente`.

## Note on the gate this change introduced

The gate shipped in `7f00115` only ran on pull requests whose base is `main`.
That meant the migrations of change 030 entered `dev` unexamined and the first
block appeared in the release PR, when the only way out was to sign the loss.
`a45371e` (#193) moved it to run on every pull request; `9b432c5` (#192) fixed
a false positive in its parser where `default=False` and `default=0` were
reported as missing defaults. Both are part of this change's story even though
they were filed as fixes.

## Archive Contents

- `proposal.md` ✅ present
- `design.md` ✅ present
- `tasks.md` ✅ present, 8/8
- `specs/migraciones-versionadas/spec.md` ✅ present
