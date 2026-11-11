# Archive Report — 026-sistema-notificaciones

**Change**: 026-sistema-notificaciones
**Archived**: 2026-11-11
**Archive location**: `openspec/changes/archive/2026-11-11-026-sistema-notificaciones/`
**Artifact store**: openspec (filesystem)
**Archived by**: `sdd-archive` phase, ejecutada a mano (ver "Composition Method")

## Final State

| Item | Final value | Source |
|------|-------------|--------|
| Tasks complete | **25/25** (0 unchecked) | `grep -c '^- \[x\]' tasks.md` |
| Verify verdict | **no ejecutado** — no existe `verify-report.md` | artefacto ausente |
| Código en producción | `apps/core/notifications.py`, `Notification` en `apps/core/models.py`, fuentes en `apps/commercial/tasks.py` | `a91342f` (#147), `b81c861` (#151); `git grep` sobre `origin/main` |
| Pruebas de implementación | **34**: 32 en `apps/commercial/tests/test_notifications.py` + 2 en `apps/core/tests/test_huey_registry.py` | `grep -c 'def test_'` |

Las 25 tareas cubren las cinco fases del `tasks.md` (decisiones, núcleo, fuentes,
UI, operación). El comportamiento está en producción desde `main`: el módulo de
notificaciones, el modelo con `uuid` y sus cuatro permisos custom, el dedup por
`dedupe_key`, y las tareas Huey de poda.

## Specs Synced

| Canonical | Action | Details |
|-----------|--------|---------|
| `openspec/specs/notificaciones-operativas/spec.md` | **Created** (nueva capacidad) | delta copiado tal cual, con un solo cambio: `## ADDED Requirements` → `## Requirements`. 9 requisitos. |

### Composition Method

`gentle-ai 4.3.1` no expone `sdd-archive-compose`, y el entorno no tiene la skill
`sdd-archive` instalada. Se aplica el caso ya documentado por
`archive/2026-11-11-025-update-web-deploy/archive-report.md`: la capacidad no
tenía canónico, así que el delta se copió byte a byte y solo se reescribió la
cabecera de sección.

## Nota sobre la cabecera del proposal

`proposal.md` abre con «Estado: aprobada el 2026-11-06. Fase 0 cerrada...
**Todavía sin implementar: no hay rama ni tareas marcadas**». Esa frase describe
el momento en que la idea se aprobó, no el estado final: hoy hay 25 tareas
cerradas, dos PRs fusionados y el código en producción. Se deja el texto intacto
—es un artefacto histórico— y la corrección vive aquí, que es donde alguien
miraría antes de reutilizar el cambio.
