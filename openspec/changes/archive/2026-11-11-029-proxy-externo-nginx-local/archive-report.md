# Archive Report — 029-proxy-externo-nginx-local

**Change**: 029-proxy-externo-nginx-local
**Archived**: 2026-11-11
**Archive location**: `openspec/changes/archive/2026-11-11-029-proxy-externo-nginx-local/`
**Artifact store**: openspec (filesystem)
**Archived by**: `sdd-archive` phase, ejecutada a mano (ver "Composition Method")

## Final State

| Item | Final value | Source |
|------|-------------|--------|
| Tasks complete | **7/7** (0 unchecked) | `grep -c '^- \[x\]' tasks.md` |
| Verify verdict | **no ejecutado** — no existe `verify-report.md`; la comprobación fue la corrida real en el contenedor | `e0f424b` |
| Código en producción | `deploy/install.sh` con `PROXY_MODE` (16 apariciones), `deploy/nginx/` con dos ejemplos de configuración | `git grep "PROXY_MODE" origin/main` |
| Pruebas | `deploy/tests/` — pruebas de los scripts de despliegue (`make test-deploy`) | `deploy/tests/test_deploy_sh_backup.sh`, `test_install_sh_blocks.sh`, `test_shell_scripts.sh` |

La última tarea (T7) no se cerró con mocks: se corrió en el contenedor Ubuntu
destino con `REPO_REF=dev PROXY_MODE=external` y quedó registrada en
`e0f424b` («cerrar 025-4.5 y 029-T7 con la corrida en el contenedor»). El
despliegue con proxy externo no se puede validar en el equipo de trabajo porque
`install.sh` exige systemd y un usuario `deploy` que ahí no existen.

## Specs Synced

| Canonical | Action | Details |
|-----------|--------|---------|
| `openspec/specs/proxy-externo-nginx-local/spec.md` | **Created** (nueva capacidad) | delta copiado tal cual, sin reescritura de cabecera: el spec ya venía en forma de requisitos (`## Requisito: ...`). 5 requisitos. |

### Composition Method

Mismo caso que 025, 026 y 028 en cuanto al origen (`gentle-ai 4.3.1` sin
`sdd-archive-compose` y sin skill `sdd-archive`), con una diferencia: este delta
no tenía la sección `## ADDED Requirements`, así que se copió sin transformar.
