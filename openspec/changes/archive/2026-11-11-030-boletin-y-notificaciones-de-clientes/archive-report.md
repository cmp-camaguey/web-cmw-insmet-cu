# Archive Report — 030-boletin-y-notificaciones-de-clientes

**Change**: 030-boletin-y-notificaciones-de-clientes
**Archived**: 2026-11-11
**Archive location**: `openspec/changes/archive/2026-11-11-030-boletin-y-notificaciones-de-clientes/`
**Artifact store**: openspec (filesystem)
**Archived by**: manual archive — `gentle-ai 4.3.1` does not expose `sdd-archive`
or `sdd-archive-compose`

## Final State

Reported at close, per the Final-State Authority hierarchy (persisted tasks
artifact > launch-prompt facts > intermediate snapshots).

| Item | Final value | Source |
|------|-------------|--------|
| Tasks complete | **14/14** (0 unchecked; T1–T13 plus T4b) | persisted `tasks.md`, `grep -c '^- \[x\]'` |
| Verify verdict | **not run** — no `verify-report.md` artifact exists | artifact absent |
| Code changes | merged in `dev`: PR #184, merge commit `0e4516a8ed18b4e990d2bfff2c16845a3903fd1a` (2026-11-11T21:36:06Z) | `gh pr view 184` |
| Implementation tests | full suite green; `tasks.md` records 1708 at T13, the close run of 2026-11-11 recorded **1734 tests OK** (496.9 s) | `tasks.md`, local run |

The change closed on its recorded batteries: `manage.py check` clean,
`makemigrations --check --dry-run` clean, `ruff check` / `format --check` green,
`djlint . --lint` 0 errors, and the full suite green at close.

### Scope note on T10–T13

The client notification tasks (T10–T13: in-app notice, per-user isolation,
source-at-the-fact emitters, preferences) are **not** promoted to a canonical
spec with this change. They belong to `notificaciones-operativas` and travel
with the archive of change 026, whose delta already carries all nine
requirements (35 scenarios, including `NOTIF-AUDIENCIA` and
`NOTIF-PREFERENCIAS`). This report archives 030 only; its delta is
`boletin-y-suscripcion`.

## Specs Synced

| Canonical | Action | Details |
|-----------|--------|---------|
| `openspec/specs/boletin-y-suscripcion/spec.md` | **Created** (new capability) | delta copied byte-for-byte; no section-header rewrite was needed; 9 requirements, 24 scenarios |

### Requirements promoted

`CONSENTIMIENTO-PUERTA-UNICA`, `ALTA-DOBLE-OPT-IN`, `CONFIRMACION-TOKEN`,
`BAJA-IDEMPOTENTE`, `PERFIL-SIN-DOBLE-OPT-IN`, `AUDIENCIA-AUTOMATICA`,
`ENVIO-EXPLICITO`, `RECORDATORIO-Y-PODA`, `LANDING-FORMULARIO-REAL`.

### Composition Method

The capability had **no canonical**, so the delta was written from the
implemented behavior and copied verbatim. Unlike 025, the delta already used
the `## Requirements` section header, so nothing was rewritten: the promotion
was verified with `diff` between the archived delta and the canonical file and
returned **EMPTY**.

The delta was derived from the merged code (`apps/core/subscriptions.py`,
`apps/core/audiences.py`, `apps/core/notifications.py`,
`apps/home/views/suscripcion/views.py`), **not** from `design.md`, which still
describes a `Newsletter` model discarded by T8 and names
`NEWSLETTER_SUBSCRIBE_RATE` where the implementation ships
`NEWSLETTER_SUBSCRIBE_LIMIT`.

No requirement was removed or weakened, so `rules.archive` (warn before
merging destructive deltas) did not trigger.

## Archive Contents

- `proposal.md` ✅ present
- `design.md` ✅ present (historical; superseded by the implemented behavior where they differ)
- `specs/boletin-y-suscripcion/spec.md` ✅ present
- `tasks.md` ✅ present, 14/14 complete
- `verify-report.md` — absent (verification not run as a separate phase)

## Source of Truth Updated

- `openspec/specs/boletin-y-suscripcion/spec.md` (new)

## Persistence

Filesystem write succeeded and both trees were read back. The Engram mirror is
recorded as `sdd/030-boletin-y-notificaciones-de-clientes/archive-report`.

## SDD Cycle Complete

The change is archived and its delta promoted. Implementation is merged in `dev`
via PR #184 and closed on 14/14 tasks. Verification: **not run** as a separate
phase (no `verify-report.md`); the change closed on recorded batteries and the
full suite.
