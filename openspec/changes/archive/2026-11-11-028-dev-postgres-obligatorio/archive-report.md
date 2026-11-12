# Archive Report — 028-dev-postgres-obligatorio

**Change**: 028-dev-postgres-obligatorio
**Archived**: 2026-11-11
**Archive location**: `openspec/changes/archive/2026-11-11-028-dev-postgres-obligatorio/`
**Artifact store**: openspec (filesystem)
**Archived by**: `sdd-archive` phase, ejecutada a mano (ver "Composition Method")

## Final State

| Item | Final value | Source |
|------|-------------|--------|
| Tasks complete | **22/22** (0 unchecked) | `grep -c '^- \[x\]' tasks.md` |
| Verify verdict | **no ejecutado** — no existe `verify-report.md` | artefacto ausente |
| Código en producción | `config/settings/base.py` rechaza `DB_ENGINE=sqlite3` | `git grep "DB_ENGINE=sqlite3" origin/main` |
| Pruebas de implementación | `apps/core/tests/test_settings_profiles.py` (59 pruebas; incluye `DB_ENGINE`) | `grep -c 'def test_'` |

La tarea 6.5 («`gentle-ai sdd-archive` cuando esté en `main`») era la única
abierta. El cambio ya está en `main` desde la promoción que siguió al squash
`d5febab` (PR #133), así que archivar **es** la ejecución de 6.5: se marca y el
`tasks.md` archivado queda sin pendientes. No es una tarea que se dé por hecha
sin hacerla; es la tarea que este archivo cumple.

## Specs Synced

| Canonical | Action | Details |
|-----------|--------|---------|
| `openspec/specs/dev-postgres-obligatorio/spec.md` | **Created** (nueva capacidad) | delta copiado tal cual, con un solo cambio: `## ADDED Requirements` → `## Requirements`. 4 requisitos. |

### Composition Method

Mismo caso que 026 y 025: `gentle-ai 4.3.1` no expone `sdd-archive-compose` y no
hay skill `sdd-archive` instalada, así que la capacidad se crea copiando el
delta byte a byte y reescribiendo solo la cabecera de sección.

## Relación con 027

`archive/2026-11-06-027-dev-postgres-superseded/` contiene el cambio anterior:
027 dejaba PostgreSQL disponible en desarrollo detrás de `DEV_USE_DB_ENGINE=True`.
028 lo sustituye por un único motor en los tres perfiles, sin opt-in. Los dos
quedan archivados y en el orden correcto: primero el superado, después el que lo
reemplaza.
