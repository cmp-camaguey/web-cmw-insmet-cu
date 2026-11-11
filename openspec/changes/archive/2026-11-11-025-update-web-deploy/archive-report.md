# Archive Report — 025-update-web-deploy

**Change**: 025-update-web-deploy
**Archived**: 2026-11-11
**Archive location**: `openspec/changes/archive/2026-11-11-025-update-web-deploy/`
**Artifact store**: openspec (filesystem)
**Archived by**: `sdd-archive` phase

## Final State

Reported at close, per the Final-State Authority hierarchy (persisted tasks
artifact > launch-prompt facts > intermediate snapshots).

| Item | Final value | Source |
|------|-------------|--------|
| Tasks complete | **23/23** (0 unchecked) | persisted `tasks.md`, `grep -c '^- \[x\]'` |
| Verify verdict | **not run** — no `verify-report.md` artifact exists | artifact absent |
| Code changes | merged in `dev`: `d8262be` (widget y botón), `912650f` (commits pendientes), `e0f424b` (cierre de 4.5) | `git log origin/dev` |
| Implementation tests | 50 in `apps/dashboard/tests/test_update.py` | `grep -c 'def test_'` |

The last open task, 4.5, needed the real container: `install.sh` requires systemd
and a `deploy` user that do not exist on the workstation. It ran on 2026-11-06 in
the target Ubuntu container with `REPO_REF=dev PROXY_MODE=external`; the install
finished green and `webcmp-deploy --check` closed with
`Todo lo chequeado esta bien.` (exit 0), with `manage.py check --deploy` free of
issues.

## Specs Synced

| Canonical | Action | Details |
|-----------|--------|---------|
| `openspec/specs/dashboard-update-button/spec.md` | **Created** (new capability) | delta carried over verbatim except the section header (`## ADDED Requirements` → `## Requirements`); 5 requirements, 23 scenarios |

### Composition Method

`gentle-ai 4.3.1` does not expose `sdd-archive-compose`, so the case documented
by `archive/2026-10-11-023-landing-page/archive-report.md` applies: the
capability had **no canonical**, so the delta was copied byte-for-byte and only
its section header was rewritten, the same way the promoted
`public-landing-page` spec reads today.

The only rewritten line is the section header; the promotion was verified with
`diff` against the archived delta after the rewrite (**EMPTY**).

No requirement was removed or weakened, so `rules.archive` ("Warn before merging
destructive deltas") did not trigger.

## Archive Contents

- `proposal.md` ✅ present
- `design.md` ✅ present
- `specs/dashboard-update-button/spec.md` ✅ present
- `tasks.md` ✅ present, 23/23 complete
- `verify-report.md` — absent (verification not run for this change)

## Source of Truth Updated

- `openspec/specs/dashboard-update-button/spec.md` (new)

## Persistence

Filesystem write succeeded and both trees were read back. The Engram mirror is
recorded as `sdd/025-update-web-deploy/archive-report` during the same session
that archived 030.

## SDD Cycle Complete

The change is archived and its delta promoted. Implementation is merged in `dev`
and was exercised on the target container. Verification: **not run** as a
separate phase (no `verify-report.md`); the change closed on 23/23 tasks, the
recorded container run and the 50 tests of `apps.dashboard.tests.test_update`.
