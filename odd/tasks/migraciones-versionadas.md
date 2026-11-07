# Migraciones versionadas

Objetivo: que el esquema de la base viva en el repositorio y que un cambio de
modelo sin migración falle en CI y en el deploy, no en producción.
Rama: `feat/031-migraciones-versionadas` (desde `dev`).
Delivery: PR a `dev`; Release al promover.
Runner: `python manage.py test` (Django unittest).

## Specs

- **S1.** `git ls-files '*/migrations/*.py'` MUST listar las migraciones de
  `commercial`, `core`, `meteo`, `publications` y `user_auth`.
- **S2.** `python manage.py makemigrations --check --dry-run` MUST salir 0 sobre
  el árbol versionado.
- **S3.** `python manage.py migrate` sobre base vacía MUST crear el esquema, y
  `migrate --check` MUST salir 0.
- **S4.** CI y los dos puntos de deploy MUST ejecutar
  `makemigrations --check --dry-run` antes de `migrate`, y MUST fallar si falta
  una migración.
- **S5.** Ningún documento MUST afirmar que las migraciones no se versionan.
- **S6.** El Release MUST publicarse sobre un tag nuevo; `v0.2.0` MUST NOT
  moverse.

## Tasks

| ID | Specs | Ruta | Estado |
| --- | --- | --- | --- |
| T1 | S1 | inline | hecho |
| T2 | S1, S3 | inline | hecho |
| T3 | S4 | inline | hecho |
| T4 | S4 | inline | hecho |
| T5 | S4 | inline | hecho |
| T6 | S5 | inline | hecho |
| T7 | S2, S3 | inline | hecho (suite 1562 tests OK) |
| T8 | S6 | inline | pendiente: versión sin decidir |

Detalle y evidencia en `openspec/changes/031-migraciones-versionadas/tasks.md`.

## Log

- **L1** (2026-11-07, verbatim): «la convension del repo sobre los tags y
  release se puede modificar, dices que actualmente tenemos 3 tags y 0 release,
  pero yo quiero tener un release, entonces ue hay que hacer. en el 1er release
  ya iria con las migraciones versionadas, este es un cambio necesario para que
  funcione el boton de actualizacion del servidor en elnavbar del admin»
- **L2** (2026-11-07, verbatim): «como aun el deploy no se ha hecho el contenido
  de las BD de dev no es fundametal. podemos empezar con una BD nueva y eliminar
  las migraciones»
- **L3** (2026-11-07): «primero has el cambio de migraciones versionadas para
  que ya salga con el release».
- **Evidencia**: el botón de actualización no depende de las migraciones.
  `apps/dashboard/views/dashboard/update.py:92` usa `git ls-remote`;
  `deploy/deploy.sh:975-978` escribe `/etc/webcmp/deployed`. El vínculo real es
  la revisabilidad del esquema, no el botón.
- **Decisión**: regenerar desde cero (`0001_initial` por app) en lugar de
  commitear las 10 autogeneradas, porque no hay base que preservar y los nombres
  autogenerados no son deterministas. Ver `design.md`.
- **Hallazgo** (2026-11-07): las 10 migraciones viejas estaban desactualizadas
  respecto a los modelos — `core` no tenía `Notification` y `commercial` tenía
  cinco campos corridos (`contract.commercial_registry/date/subscription`,
  `invoice.period_label` vs `period_start/period_end`, `contract.customer/
  start_date/validity_years`). Regenerar no era una preferencia estética: el
  esquema versionado no reproducía el código.
- **Verificación** (2026-11-07): base PostgreSQL 18 efímera en
  `/tmp/opencode/pgsock:55432`. `migrate` desde cero aplica `contenttypes`,
  `auth`, `admin`, `commercial`, `core`, `meteo`, `publications`, `sessions`,
  `user_auth` → OK. `migrate --check` → 0. `makemigrations --check --dry-run` →
  «No changes detected», 0. Suite completa: **1562 tests, OK**. `ruff check .`
  limpio, `ruff format --check .` 381 archivos, `djlint . --lint` 0 errores,
  `bash -n` de los 4 scripts, YAML parseable, `git diff --check` limpio,
  `deploy/tests/run.sh` 65 ok / 0 fallas.
- **Nota de entorno**: el venv tenía `djlint` 1.46.2 y el pin del repo es
  1.49.9; con la versión pinneada el `--reformat --check` solo falla en el
  `landing.html` en curso del usuario y en `staticfiles/` (artefacto local, no
  versionado).
- **Pendiente**: los entornos de `pre-commit` no se pudieron instalar por red
  (fallo de descarga de `ruff-0.17.3`, problema de proxy documentado en
  `AGENTS.md`). Los gates equivalentes se corrieron a mano; CI ejecuta los hooks
  reales.
- **Estado**: implementación y verificación cerradas; falta commit, PR y
  Release.
